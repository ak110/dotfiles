"""agents_serverのsession状態をClaude Codeのstatusline向けに出力する。

Claude backendの委譲先は所有sessionと自身のClaude Code sessionを持つ。
この対応はClaude Code 2.1.261及びclaude-agent-sdk 0.2系で確認した。
Codex backendの委譲先自身のシェルは、所有sessionと自身のCodex threadを持つ。
2026年9月7日にCodex CLI 0.153.4の`start_shell`で起動したシェルに
`CODEX_THREAD_ID`が存在することを確認した。

Codex CLIが直接起動するMCPサーバープロセスへは、Codex App Server自身の環境が
継承されない。Codex CLI 0.153.4で2026年9月9日に確認した。`thread/start`の
`config.mcp_servers.agents_server`へ完全な定義を渡す場合だけ、`env`の識別子が
当該プロセスへ届く。ホスト、CLI又はSDKを更新した時点では、同じ起動形で
MCPサーバーの環境変数と起動通知を確認する。

上り通知の配送媒体は本モジュールが定める共有状態ディレクトリとする。Codexの委譲先にはagents_server系のMCPツールもフックの発火機構も公開されず、Claudeの委譲先へ公開されるagents_server系のMCPツールは委譲元のsession登録簿を共有しないため、engineに依存しない媒体が他に無い。2026年9月6日に両engineの委譲先を1件ずつ起動して実測した。この前提が崩れた場合は、片方のengineの委譲先から送った通知が委譲元へ届かない事象として現れる。
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json
import pathlib
import re
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server.state import (
    RESULT_RETENTION_SECONDS,
    SessionResumeState,
    SessionState,
    has_uncollected_result,
    terminal_result_payload,
)
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common.atomic_file import atomic_write

_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")


@dataclasses.dataclass(frozen=True)
class StatusFileIdentity:
    """状態ファイルのルートと書込主体を表す。"""

    root_session_id: str
    file_name: str
    host_session_id: str | None


def resolve_root_session_id(environment: Mapping[str, str]) -> str | None:
    """環境変数から読取対象のルートsessionを解決する。"""
    owner = environment.get("AGENT_TOOLKIT_OWNER_SESSION") or environment.get("CLAUDE_CODE_SESSION_ID")
    return owner if owner is not None and valid_session_id(owner) else None


def resolve_status_file_identity(environment: Mapping[str, str]) -> StatusFileIdentity | None:
    """環境変数から状態ファイルの書込主体を解決し、識別できない場合は`None`を返す。"""
    owner = resolve_root_session_id(environment)
    if owner is None:
        return None

    host_session_id: str | None = None
    if environment.get("AGENT_TOOLKIT_DELEGATED_SESSION"):
        host_session_id = environment.get("CLAUDE_CODE_SESSION_ID")
    elif environment.get("CODEX_THREAD_ID"):
        host_session_id = environment.get("CODEX_THREAD_ID")
    elif environment.get("AGENT_TOOLKIT_STATUS_HOST_SESSION"):
        host_session_id = environment.get("AGENT_TOOLKIT_STATUS_HOST_SESSION")
    if host_session_id is not None and not valid_session_id(host_session_id):
        return None
    if host_session_id is None and environment.get("AGENT_TOOLKIT_OWNER_SESSION"):
        return None

    file_name = "root.json" if host_session_id is None else f"{host_session_id}.json"
    return StatusFileIdentity(owner, file_name, host_session_id)


def status_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """ルートsessionの状態ファイルディレクトリを返す。"""
    root = _atk_config.state_dir() if state_root is None else state_root
    return root / "agents-server" / root_session_id


def list_status_files(root_session_id: str, state_root: pathlib.Path | None = None) -> list[pathlib.Path]:
    """書込主体ごとの状態ファイルを絶対パスの安定順で返す。

    書込主体ごとに`root.json`と`<host_session_id>.json`へ分かれるため、
    読取主体は単一のファイル名を組み立てない。ファイル名の規則の正本は
    `resolve_status_file_identity`である。
    """
    directory = status_directory(root_session_id, state_root)
    try:
        paths = [path.absolute() for path in directory.iterdir() if path.suffix == ".json" and path.is_file()]
    except OSError:
        return []
    return sorted(paths)


def aliases_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    """現行session識別子からルートsession識別子を引く索引ディレクトリを返す。"""
    root = _atk_config.state_dir() if state_root is None else state_root
    return root / "agents-server" / "aliases"


def resolve_conversation_root_session_id(environment: Mapping[str, str], state_root: pathlib.Path | None = None) -> str | None:
    """現行会話のsession識別子を索引経由でルートsession識別子へ解決する。"""
    current_session_id = resolve_root_session_id(environment)
    if current_session_id is None:
        return None
    alias_path = aliases_directory(state_root) / f"{current_session_id}.json"
    try:
        payload = json.loads(alias_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return current_session_id
    root_session_id = payload.get("root_session_id") if isinstance(payload, dict) else None
    if (
        isinstance(payload, dict)
        and payload.get("version") == 1
        and isinstance(root_session_id, str)
        and valid_session_id(root_session_id)
        and status_directory(root_session_id, state_root).is_dir()
    ):
        return root_session_id
    return current_session_id


def write_root_alias(
    current_session_id: str,
    root_session_id: str,
    state_root: pathlib.Path | None = None,
) -> None:
    """現行session識別子のルート索引を書き、参照先を失った索引を回収する。"""
    if not valid_session_id(current_session_id) or not valid_session_id(root_session_id):
        raise ValueError("invalid session_id")
    directory = aliases_directory(state_root)
    if directory.exists():
        for path in directory.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            target = payload.get("root_session_id") if isinstance(payload, dict) else None
            if isinstance(target, str) and valid_session_id(target) and not status_directory(target, state_root).is_dir():
                path.unlink(missing_ok=True)
    payload = {"version": 1, "root_session_id": root_session_id}
    atomic_write(directory / f"{current_session_id}.json", json.dumps(payload, ensure_ascii=False) + "\n")


def valid_session_id(session_id: str) -> bool:
    """session識別子が状態ファイル名へ使用できる形式かを返す。"""
    return bool(session_id and _SESSION_ID_PATTERN.fullmatch(session_id))


def results_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """ルートsessionの終端結果ディレクトリを返す。"""
    return status_directory(root_session_id, state_root) / "results"


def notices_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """ルートsession宛ての未回収通知ディレクトリを返す。"""
    return status_directory(root_session_id, state_root) / "notices"


def hosts_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """書込主体から起動元threadへの索引ディレクトリを返す。"""
    return status_directory(root_session_id, state_root) / "hosts"


def write_host_alias(
    root_session_id: str,
    writer_session_id: str,
    host_session_id: str,
    state_root: pathlib.Path | None = None,
) -> None:
    """書込主体を起動元threadへ対応付ける索引を書く。"""
    if not all(valid_session_id(value) for value in (root_session_id, writer_session_id, host_session_id)):
        raise ValueError("invalid session_id")
    payload = {"version": 1, "host_session_id": host_session_id}
    atomic_write(
        hosts_directory(root_session_id, state_root) / f"{writer_session_id}.json",
        json.dumps(payload, ensure_ascii=False) + "\n",
    )


def take_notices(
    root_session_id: str,
    session_id: str,
    state_root: pathlib.Path | None = None,
) -> list[dict[str, str]]:
    """待機対象sessionの正常な通知を回収し、送信時刻順に返す。"""
    directory = notices_directory(root_session_id, state_root)
    try:
        paths = tuple(directory.iterdir())
    except FileNotFoundError:
        return []
    matched: list[tuple[str, str, dict[str, str]]] = []
    for path in paths:
        if not path.is_file() or path.suffix != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            if path.name.startswith(f"{session_id}."):
                path.unlink(missing_ok=True)
                matched.append(("", path.name, {"sent_at": "", "body": f"破損した上り通知を削除しました: {path}: {exc}"}))
            continue
        if (
            not isinstance(payload, dict)
            or payload.get("version") != 1
            or payload.get("session_id") != session_id
            or not isinstance(payload.get("sent_at"), str)
            or not isinstance(payload.get("body"), str)
        ):
            if path.name.startswith(f"{session_id}."):
                path.unlink(missing_ok=True)
                matched.append(
                    ("", path.name, {"sent_at": "", "body": f"破損した上り通知を削除しました: {path}: 必須項目が不正です"})
                )
            continue
        notice = {"sent_at": payload["sent_at"], "body": payload["body"]}
        matched.append((payload["sent_at"], path.name, notice))
    matched.sort(key=lambda item: (item[0], item[1]))
    taken: list[dict[str, str]] = []
    for _sent_at, file_name, notice in matched:
        if notice["sent_at"] == "":
            taken.append(notice)
            continue
        try:
            (directory / file_name).unlink()
        except FileNotFoundError:
            continue
        taken.append(notice)
    return taken


def normalize_label(value: str) -> str:
    """依頼本文又はコマンドの最初の空でない行を表示用に正規化する。"""
    line = next((line for line in value.splitlines() if line.strip()), "")
    return " ".join(line.split())[:200]


class StatusFileWriter:
    """共有session辞書をstatusline向け状態ファイルへ集約して書く。"""

    def __init__(
        self,
        sessions: dict[str, SessionState],
        identity: StatusFileIdentity,
        *,
        state_root: pathlib.Path | None = None,
        aggregate_seconds: float = 1.0,
    ) -> None:
        self._sessions = sessions
        self._identity = identity
        self._state_root = state_root
        self._directory = status_directory(identity.root_session_id, state_root)
        self._path = self._directory / identity.file_name
        self._aggregate_seconds = aggregate_seconds
        self._flush_handle: asyncio.TimerHandle | None = None
        self._retention_handle: asyncio.TimerHandle | None = None
        self._result_deadlines: dict[str, float] = {}
        self._projected_host_session_id: str | None = None
        self._active = False

    @property
    def path(self) -> pathlib.Path:
        """自身が所有する状態ファイルのパスを返す。"""
        return self._path

    @property
    def root_session_id(self) -> str:
        """自身が状態ファイルを書き込むルートsession識別子を返す。"""
        return self._identity.root_session_id

    @property
    def sessions(self) -> dict[str, SessionState]:
        """射影元の共有session辞書を返す。"""
        return self._sessions

    def activate(self) -> None:
        """書込を有効化し、前回プロセスの残存状態を初期化する。"""
        self._active = True
        self._remove_owned_and_expired_files()
        self.flush()

    def schedule(self) -> None:
        """実行中loopで集約時間後の全置換を予約する。"""
        if not self._active or self._flush_handle is not None:
            return
        loop = asyncio.get_running_loop()
        self._flush_handle = loop.call_later(self._aggregate_seconds, self.flush)

    def flush(self) -> None:
        """表示対象sessionをJSONへ射影して原子的に全置換する。"""
        if not self._active:
            return
        self._flush_handle = None
        now = asyncio.get_running_loop().time()
        self._remove_expired_results(now)
        self._write_terminal_results(now)
        visible = [
            session
            for session in self._sessions.values()
            if session.announced
            and (session.retention_deadline is None or session.retention_deadline > now)
            and (
                not session.result_available
                or has_uncollected_result(session, self.result_state(session.session_id) == "consumed")
            )
        ]
        visible.sort(key=lambda session: session.started_at)
        payload: dict[str, Any] = {
            "version": 1,
            "host_session_id": self._resolve_host_session_id(),
            "updated_at": _updated_at(visible),
            "sessions": [_serialize_session(session) for session in visible],
        }
        atomic_write(self._path, json.dumps(payload, ensure_ascii=False) + "\n")
        self._schedule_retention(visible, now)

    def deactivate(self) -> None:
        """予約を解除し、自身が所有する状態ファイルを削除する。"""
        self._active = False
        for handle in (self._flush_handle, self._retention_handle):
            if handle is not None:
                handle.cancel()
        self._flush_handle = None
        self._retention_handle = None
        self._remove_owned_and_expired_files()
        self._result_deadlines.clear()
        if self._directory.exists() and not any(self._directory.iterdir()):
            self._directory.rmdir()

    def retain_result(self, session: SessionState | SessionResumeState) -> None:
        """保持中又は退避済みsessionの未回収終端結果を残す。"""
        if (
            session.finalized_at is not None
            and session.status in {"completed", "failed", "interrupted"}
            and not session.result_delivered
        ):
            self._write_terminal_result(session)

    def delete_result(self, session_id: str) -> None:
        """回収済み又は所有解除するsessionの終端結果を削除する。"""
        if not valid_session_id(session_id):
            raise ValueError(f"invalid session_id: {session_id}")
        path = results_directory(self._identity.root_session_id, self._state_root) / f"{session_id}.json"
        path.unlink(missing_ok=True)
        self._result_deadlines.pop(session_id, None)

    def result_exists(self, session_id: str) -> bool:
        """指定sessionの終端結果ファイルが存在するかを返す。"""
        if not valid_session_id(session_id):
            raise ValueError(f"invalid session_id: {session_id}")
        return (results_directory(self._identity.root_session_id, self._state_root) / f"{session_id}.json").is_file()

    def result_state(self, session_id: str) -> str:
        """自プロセスが公開した終端結果の公開状態を返す。"""
        if session_id not in self._result_deadlines:
            return "unpublished"
        return "published" if self.result_exists(session_id) else "consumed"

    def take_notices(self, session_id: str) -> list[dict[str, str]]:
        """待機対象sessionの正常な通知を回収する。"""
        return take_notices(self._identity.root_session_id, session_id, self._state_root)

    def _write_terminal_results(self, now: float) -> None:
        for session in self._sessions.values():
            if session.result_delivered:
                self.delete_result(session.session_id)
                continue
            if not session.result_available:
                continue
            if session.retention_deadline is not None and session.retention_deadline <= now:
                session.result_delivered = True
                self.delete_result(session.session_id)
                continue
            if self.result_state(session.session_id) == "consumed":
                session.result_delivered = True
                self._result_deadlines.pop(session.session_id, None)
                continue
            self._write_terminal_result(session)

    def _remove_expired_results(self, now: float) -> None:
        expired = [session_id for session_id, deadline in self._result_deadlines.items() if deadline <= now]
        for session_id in expired:
            self.delete_result(session_id)

    def _write_terminal_result(self, session: SessionState | SessionResumeState) -> None:
        assert session.finalized_at is not None
        assert session.retention_deadline is not None
        payload = terminal_result_payload(session)
        directory = results_directory(self._identity.root_session_id, self._state_root)
        atomic_write(directory / f"{session.session_id}.json", json.dumps(payload, ensure_ascii=False) + "\n")
        self._result_deadlines[session.session_id] = session.retention_deadline

    def _remove_owned_and_expired_files(self) -> None:
        """自身の状態ファイルと保持期限を超えた共有ファイルだけを削除する。"""
        self._path.unlink(missing_ok=True)
        for path in self._directory.glob(f".{self._path.name}.*.tmp"):
            path.unlink()
        cutoff = datetime.datetime.now(datetime.UTC).timestamp() - RESULT_RETENTION_SECONDS
        directories = (
            results_directory(self.root_session_id, self._state_root),
            notices_directory(self.root_session_id, self._state_root),
            hosts_directory(self.root_session_id, self._state_root),
        )
        for directory in directories:
            if not directory.exists():
                continue
            for path in directory.iterdir():
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            if not any(directory.iterdir()):
                directory.rmdir()

    def _resolve_host_session_id(self) -> str | None:
        """書込主体に対応する起動元threadを一度だけ状態ファイルへ射影する。"""
        if self._projected_host_session_id is not None:
            return self._projected_host_session_id
        writer_session_id = self._identity.host_session_id
        if writer_session_id is None:
            return None
        path = hosts_directory(self.root_session_id, self._state_root) / f"{writer_session_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return writer_session_id
        host_session_id = payload.get("host_session_id") if isinstance(payload, dict) else None
        if (
            isinstance(payload, dict)
            and payload.get("version") == 1
            and isinstance(host_session_id, str)
            and valid_session_id(host_session_id)
        ):
            self._projected_host_session_id = host_session_id
            return host_session_id
        return writer_session_id

    def _schedule_retention(self, sessions: list[SessionState], now: float) -> None:
        if self._retention_handle is not None:
            self._retention_handle.cancel()
        deadlines = [
            session.retention_deadline
            for session in sessions
            if session.retention_deadline is not None and session.retention_deadline > now
        ]
        deadlines.extend(deadline for deadline in self._result_deadlines.values() if deadline > now)
        self._retention_handle = None
        if deadlines:
            self._retention_handle = asyncio.get_running_loop().call_at(min(deadlines), self.flush)


def _serialize_session(session: SessionState) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "engine": session.engine,
        "model": session.model,
        "effort": session.effort,
        "model_type": session.model_type,
        "launch_kind": session.launch_kind,
        "status": session.status,
        "progress": session.progress,
        "label": session.label,
        "started_at": session.started_at,
        "updated_at": session.updated_at,
    }


def _updated_at(sessions: list[SessionState]) -> str:
    if sessions:
        return max(session.updated_at for session in sessions)
    return datetime.datetime.now(datetime.UTC).isoformat()
