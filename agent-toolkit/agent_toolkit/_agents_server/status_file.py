"""agents_serverのsession状態を、Claude Codeのstatuslineと`atk agents`のCLIが読む状態ファイルへ出力する。

状態ファイルは書込主体ごとに`<状態ディレクトリ>/<ルートsession識別子>/<書込主体>.json`へ置き、生存の印を定期的に更新する。
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import pathlib
from typing import Any

from agent_toolkit._agents_server.notice_inbox import take_notices
from agent_toolkit._agents_server.retained_results import read_retained_result, take_result
from agent_toolkit._agents_server.shared_layout import (
    hosts_directory,
    list_root_session_ids,
    list_status_files,
    notices_directory,
    results_directory,
    status_directory,
    valid_session_id,
    wait_targets_directory,
)
from agent_toolkit._agents_server.shared_roots import PROCESS_ROOT_PREFIX, StatusFileIdentity
from agent_toolkit._agents_server.shared_sweep import sweep_stale_shared_state
from agent_toolkit._agents_server.state import (
    RESULT_RETENTION_SECONDS,
    TERMINAL_STATUSES,
    SessionResumeState,
    SessionState,
    fast_mode_fields,
    has_uncollected_result,
    terminal_result_payload,
)
from agent_toolkit._agents_server.wait_targets import release_wait_target
from agent_toolkit._common.atomic_file import atomic_write

_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")

HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_EXPIRY_SECONDS = 120


def live_writer_holds_session(session_id: str, state_root: pathlib.Path | None = None) -> bool:
    """生存の印が失効していないいずれかのrootの状態ファイルが、指定sessionを載せているかを返す。

    再起動前の登録簿の記録を別のMCPサーバーが引き継ぐ前に、その記録を書ける所有者の不在を確かめるために使う。
    生存の印を持たない状態ファイルは、失効を判定できないため生存しているものとして扱う。
    """
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=HEARTBEAT_EXPIRY_SECONDS)
    for root_session_id in list_root_session_ids(state_root):
        for path in list_status_files(root_session_id, state_root):
            entry = _read_live_status_file(path, cutoff)
            if entry is not None and any(child == session_id for child, _ in entry[1]):
                return True
    return False


def normalize_label(value: str) -> str:
    """依頼本文またはコマンドの最初の空でない行を表示用に正規化する。"""
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
        expired_sessions: dict[str, SessionResumeState] | None = None,
    ) -> None:
        self._sessions = sessions
        self._expired_sessions: dict[str, SessionResumeState] = {} if expired_sessions is None else expired_sessions
        self._identity = identity
        self._state_root = state_root
        self._directory = status_directory(identity.root_session_id, state_root)
        self._path = self._directory / identity.file_name
        self._aggregate_seconds = aggregate_seconds
        self._flush_handle: asyncio.TimerHandle | None = None
        self._retention_handle: asyncio.TimerHandle | None = None
        self._published_results: set[str] = set()
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

    def launcher_session_id(self) -> str | None:
        """このMCPサーバーが作成するsessionの委譲元sessionの識別子を返す。

        状態ファイルの`host_session_id`として射影する委譲元を優先し、無ければ所有ルートを返す。
        プロセス専用ルートは会話を指さないため返さない。
        """
        host_session_id = self._resolve_host_session_id()
        if host_session_id is not None:
            return host_session_id
        root_session_id = self.root_session_id
        return None if root_session_id.startswith(PROCESS_ROOT_PREFIX) else root_session_id

    @property
    def sessions(self) -> dict[str, SessionState]:
        """射影元の共有session辞書を返す。"""
        return self._sessions

    @property
    def expired_sessions(self) -> dict[str, SessionResumeState]:
        """保持期限で本体を解放したsessionの再開状態を返す。稼働中の子孫を持つ祖先の表示に使う。"""
        return self._expired_sessions

    def activate(self) -> None:
        """書込を有効化し、前回プロセスの残存状態を初期化する。"""
        try:
            sweep_stale_shared_state(
                keep_root_session_id=self.root_session_id,
                state_root=self._state_root,
            )
        except OSError as exc:
            _LOG.warning("共有状態の期限掃引を開始できませんでした: error=%s", exc)
        self._active = True
        self._remove_owned_and_expired_files()
        self._remove_stale_status_files()
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
        self._remove_stale_status_files()
        now = asyncio.get_running_loop().time()
        self._write_terminal_results()
        # 稼働中の子孫を持つsessionは、自身の結果回収と表示期限によらず祖先の行として残す。
        # 結果の再公開や期限の延長はせず、子孫の終端後の次回flushで通常の判定へ戻る。
        live_hosts = _live_descendant_hosts(self._directory, self._path)
        visible: list[SessionState | SessionResumeState] = [
            session
            for session in self._sessions.values()
            if session.announced
            and (
                session.session_id in live_hosts
                or (
                    (session.retention_deadline is None or session.retention_deadline > now)
                    and (
                        not session.result_available
                        or has_uncollected_result(session, self.result_state(session.session_id) == "consumed")
                    )
                )
            )
        ]
        visible.extend(
            session
            for session in self._expired_sessions.values()
            if session.session_id in live_hosts and session.session_id not in self._sessions and session.started_at is not None
        )
        visible.sort(key=lambda session: session.started_at or "")
        payload: dict[str, Any] = {
            "version": 1,
            "host_session_id": self._resolve_host_session_id(),
            "heartbeat_at": datetime.datetime.now(datetime.UTC).isoformat(),
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
        self._published_results.clear()
        if self._directory.exists() and not any(self._directory.iterdir()):
            self._directory.rmdir()

    def retain_result(self, session: SessionState | SessionResumeState) -> None:
        """保持中または退避済みsessionの未回収終端結果を残す。"""
        if (
            session.finalized_at is not None
            and session.status in {"completed", "failed", "interrupted"}
            and not session.result_delivered
        ):
            self._write_terminal_result(session)

    def delete_result(self, session_id: str, *, collector: str) -> None:
        """回収済みまたは所有解除するsessionの終端結果を削除する。"""
        if not valid_session_id(session_id):
            raise ValueError(f"invalid session_id: {session_id}")
        path = results_directory(self._identity.root_session_id, self._state_root) / f"{session_id}.json"
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        else:
            _LOG.info(
                "result_deleted session_id=%s writer=%s collector=%s",
                session_id,
                self._identity.file_name,
                collector,
            )
        self._published_results.discard(session_id)

    def delete_wait_target(self, session_id: str) -> None:
        """自身の書込主体が登録した待機対象を明示破棄する。"""
        release_wait_target(self.root_session_id, self._identity.file_name, session_id, self._state_root)

    def result_exists(self, session_id: str) -> bool:
        """指定sessionの終端結果ファイルが存在するかを返す。"""
        if not valid_session_id(session_id):
            raise ValueError(f"invalid session_id: {session_id}")
        return (results_directory(self._identity.root_session_id, self._state_root) / f"{session_id}.json").is_file()

    def read_result(self, session_id: str) -> dict[str, Any] | None:
        """指定sessionの保存済み終端結果を検証して返す。"""
        if not valid_session_id(session_id):
            raise ValueError(f"invalid session_id: {session_id}")
        path = results_directory(self._identity.root_session_id, self._state_root) / f"{session_id}.json"
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("status") not in TERMINAL_STATUSES
            or not isinstance(payload.get("agent_message"), str)
            or not isinstance(payload.get("turn_seq"), int)
            or isinstance(payload.get("turn_seq"), bool)
            or not isinstance(payload.get("finalized_at"), str)
        ):
            return None
        return payload

    def take_result(self, session_id: str, *, collector: str) -> tuple[dict[str, Any] | None, str | None]:
        """自身が公開した終端結果を一度だけ回収する。"""
        result = take_result(
            self.root_session_id,
            session_id,
            self._identity.file_name,
            collector=collector,
            state_root=self._state_root,
        )
        if result[0] is not None:
            self._published_results.discard(session_id)
        return result

    def result_state(self, session_id: str) -> str:
        """自プロセスが公開した終端結果の公開状態を返す。"""
        if session_id not in self._published_results:
            return "unpublished"
        return "published" if self.result_exists(session_id) else "consumed"

    def take_notices(self, session_id: str) -> list[dict[str, str]]:
        """待機対象sessionの正常な通知を回収する。"""
        return take_notices(self._identity.root_session_id, session_id, self._state_root)

    def _write_terminal_results(self) -> None:
        for session in self._sessions.values():
            if session.result_delivered:
                self.delete_result(session.session_id, collector="status-sync")
                continue
            if not session.result_available:
                continue
            if self.result_state(session.session_id) == "consumed":
                session.result_delivered = True
                self._published_results.discard(session.session_id)
                continue
            self._write_terminal_result(session)

    def _write_terminal_result(self, session: SessionState | SessionResumeState) -> None:
        assert session.finalized_at is not None
        assert session.retention_deadline is not None
        payload = terminal_result_payload(session)
        payload["owner_status_file"] = self._identity.file_name
        payload["session"] = _serialize_retained_session(session)
        directory = results_directory(self._identity.root_session_id, self._state_root)
        atomic_write(directory / f"{session.session_id}.json", json.dumps(payload, ensure_ascii=False) + "\n")
        self._published_results.add(session.session_id)
        _LOG.info("result_written session_id=%s writer=%s", session.session_id, self._identity.file_name)

    def _remove_owned_and_expired_files(self) -> None:
        """自身の状態ファイルと保持期限を超えた通知だけを削除する。"""
        self._path.unlink(missing_ok=True)
        for path in self._directory.glob(f".{self._path.name}.*.tmp"):
            path.unlink()
        results = results_directory(self.root_session_id, self._state_root)
        if results.exists() and not any(results.iterdir()):
            results.rmdir()
        cutoff = datetime.datetime.now(datetime.UTC).timestamp() - RESULT_RETENTION_SECONDS
        notices = notices_directory(self.root_session_id, self._state_root)
        if notices.exists():
            for path in notices.iterdir():
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            if not any(notices.iterdir()):
                notices.rmdir()
        hosts = hosts_directory(self.root_session_id, self._state_root)
        if hosts.exists():
            retained_owners = {
                payload.get("owner_status_file")
                for path in results.glob("*.json")
                if (payload := read_retained_result(self.root_session_id, path.stem, self._state_root)) is not None
            }
            for path in hosts.iterdir():
                if not path.is_file() or path.stat().st_mtime >= cutoff:
                    continue
                owner = path.name
                if owner in retained_owners or (self._directory / owner).is_file():
                    continue
                targets = wait_targets_directory(self.root_session_id, owner, self._state_root)
                if targets.is_dir() and any(targets.glob("*.json")):
                    continue
                path.unlink()
            if not any(hosts.iterdir()):
                hosts.rmdir()

    def _resolve_host_session_id(self) -> str | None:
        """書込主体に対応する委譲元threadを一度だけ状態ファイルへ射影する。"""
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

    def _remove_stale_status_files(self) -> None:
        """生存の印が失効した他の書込主体の状態ファイルを削除する。"""
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=HEARTBEAT_EXPIRY_SECONDS)
        for path in self._directory.glob("*.json"):
            if path == self._path:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                heartbeat_at = payload.get("heartbeat_at") if isinstance(payload, dict) else None
                heartbeat = datetime.datetime.fromisoformat(heartbeat_at) if isinstance(heartbeat_at, str) else None
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                continue
            if heartbeat is not None and heartbeat.tzinfo is not None and heartbeat < cutoff:
                path.unlink(missing_ok=True)

    def _schedule_retention(self, sessions: list[SessionState | SessionResumeState], now: float) -> None:
        if self._retention_handle is not None:
            self._retention_handle.cancel()
        deadlines = [
            session.retention_deadline
            for session in sessions
            if session.retention_deadline is not None and session.retention_deadline > now
        ]
        self._retention_handle = None
        if deadlines:
            self._retention_handle = asyncio.get_running_loop().call_at(min(deadlines), self.flush)


def _live_descendant_hosts(directory: pathlib.Path, own_path: pathlib.Path) -> set[str]:
    """他の書込主体の状態ファイルから、稼働中の子孫を持つsession識別子を集める。

    状態ファイルの`host_session_id`は、そのファイルのsessionを起動した親sessionを指す。
    直下の子が終端していても、さらに先の子孫が稼働していれば祖先として数える。
    生存の印が失効したファイルは稼働の根拠にしない。
    """
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=HEARTBEAT_EXPIRY_SECONDS)
    children: dict[str, list[tuple[str, str]]] = {}
    for path in sorted(directory.glob("*.json")):
        if path == own_path:
            continue
        entry = _read_child_statuses(path, cutoff)
        if entry is not None:
            children.setdefault(entry[0], []).extend(entry[1])
    live: set[str] = set()
    changed = True
    while changed:
        changed = False
        for host, entries in children.items():
            if host not in live and any(status == "running" or child in live for child, status in entries):
                live.add(host)
                changed = True
    return live


def _read_child_statuses(path: pathlib.Path, cutoff: datetime.datetime) -> tuple[str, list[tuple[str, str]]] | None:
    """状態ファイルから親session識別子と、各sessionの識別子・状態を読む。"""
    entry = _read_live_status_file(path, cutoff)
    if entry is None or entry[0] is None:
        return None
    return entry[0], entry[1]


def _read_live_status_file(path: pathlib.Path, cutoff: datetime.datetime) -> tuple[str | None, list[tuple[str, str]]] | None:
    """生存の印が失効していない状態ファイルから、親session識別子（root直下の書込主体では`None`）と各sessionの状態を読む。

    読めないファイルと失効したファイルは`None`を返す。生存の印を持たないファイルは失効を判定できないため読む。
    """
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    host = payload.get("host_session_id")
    sessions = payload.get("sessions")
    if (host is not None and not isinstance(host, str)) or not isinstance(sessions, list):
        return None
    heartbeat_at = payload.get("heartbeat_at")
    if heartbeat_at is not None:
        try:
            heartbeat = datetime.datetime.fromisoformat(heartbeat_at) if isinstance(heartbeat_at, str) else None
        except ValueError:
            return None
        if heartbeat is None or heartbeat.tzinfo is None or heartbeat < cutoff:
            return None
    return host, [
        (item["session_id"], item["status"])
        for item in sessions
        if isinstance(item, dict) and isinstance(item.get("session_id"), str) and isinstance(item.get("status"), str)
    ]


def _serialize_session(session: SessionState | SessionResumeState) -> dict[str, Any]:
    if isinstance(session, SessionResumeState):
        # 本体を解放したsessionは進捗を持たないため、起動情報と終端状態だけを表示する。
        return {**_serialize_retained_session(session), "progress": "", "last_action": ""}
    serialized = {
        **_serialize_retained_session(session),
        "progress": session.progress,
        "last_action": session.last_action,
    }
    if session.status == "running" and session.api_error is not None:
        serialized["api_error"] = session.api_error
    return serialized


def _serialize_retained_session(session: SessionState | SessionResumeState) -> dict[str, Any]:
    """表示期限後もCLIの詳細表示へ必要な起動情報を結果と共に残す。"""
    return {
        "session_id": session.session_id,
        "cwd": session.cwd,
        "engine": session.engine,
        "model": session.model,
        "effort": session.effort,
        **fast_mode_fields(session.engine, session.fast_mode),
        "model_type": session.model_type,
        "launch_kind": session.launch_kind,
        "prompt": session.prompt,
        "status": session.status,
        "label": session.label,
        "created_at": session.created_at,
        "started_at": session.started_at,
        "updated_at": session.updated_at,
        "output_updated_at": session.output_updated_at,
    }


def _updated_at(sessions: list[SessionState | SessionResumeState]) -> str:
    updated = [session.updated_at for session in sessions if session.updated_at is not None]
    if updated:
        return max(updated)
    return datetime.datetime.now(datetime.UTC).isoformat()
