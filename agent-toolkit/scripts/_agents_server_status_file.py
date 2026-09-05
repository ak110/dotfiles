"""agents_serverのsession状態をClaude Codeのstatusline向けに出力する。

Claude backendの委譲先は所有sessionと自身のClaude Code sessionを持つ。
Codex backendの委譲先は所有sessionと自身のCodex threadを持つ。この対応は
Claude Code 2.1.261、Codex CLI 0.153.2及びclaude-agent-sdk 0.2系で確認した。
各ホスト又はSDKの更改時は、委譲先の環境変数を再取得して対応を検証する。
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

import _atk_config
from _agents_server_state import SessionState
from _atomic_file import atomic_write

_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")


@dataclasses.dataclass(frozen=True)
class StatusFileIdentity:
    """状態ファイルのルートと書込主体を表す。"""

    root_session_id: str
    file_name: str
    host_session_id: str | None


def resolve_status_file_identity(environment: Mapping[str, str]) -> StatusFileIdentity | None:
    """環境変数から状態ファイルの書込主体を解決する。"""
    owner = environment.get("AGENT_TOOLKIT_OWNER_SESSION") or environment.get("CLAUDE_CODE_SESSION_ID")
    if owner is None or _SESSION_ID_PATTERN.fullmatch(owner) is None:
        return None

    host_session_id: str | None = None
    if environment.get("AGENT_TOOLKIT_DELEGATED_SESSION"):
        host_session_id = environment.get("CLAUDE_CODE_SESSION_ID")
    elif environment.get("CODEX_THREAD_ID"):
        host_session_id = environment.get("CODEX_THREAD_ID")
    if host_session_id is not None and _SESSION_ID_PATTERN.fullmatch(host_session_id) is None:
        return None

    file_name = "root.json" if host_session_id is None else f"{host_session_id}.json"
    return StatusFileIdentity(owner, file_name, host_session_id)


def status_directory(root_session_id: str, state_root: pathlib.Path | None = None) -> pathlib.Path:
    """ルートsessionの状態ファイルディレクトリを返す。"""
    root = _atk_config.state_dir() if state_root is None else state_root
    return root / "agents-server" / root_session_id


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
        self._directory = status_directory(identity.root_session_id, state_root)
        self._path = self._directory / identity.file_name
        self._aggregate_seconds = aggregate_seconds
        self._flush_handle: asyncio.TimerHandle | None = None
        self._retention_handle: asyncio.TimerHandle | None = None
        self._active = False

    @property
    def path(self) -> pathlib.Path:
        """自身が所有する状態ファイルのパスを返す。"""
        return self._path

    @property
    def sessions(self) -> dict[str, SessionState]:
        """射影元の共有session辞書を返す。"""
        return self._sessions

    def activate(self) -> None:
        """書込を有効化し、前回プロセスの残存状態を初期化する。"""
        self._active = True
        if self._identity.host_session_id is None and self._directory.exists():
            for path in self._directory.iterdir():
                if path.is_file() and (path.suffix == ".json" or path.name.endswith(".tmp")):
                    path.unlink()
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
        visible = [
            session
            for session in self._sessions.values()
            if session.announced
            and not session.result_delivered
            and (session.retention_deadline is None or session.retention_deadline > now)
        ]
        visible.sort(key=lambda session: session.started_at)
        payload: dict[str, Any] = {
            "version": 1,
            "host_session_id": self._identity.host_session_id,
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
        if self._path.exists():
            self._path.unlink()
        if self._identity.host_session_id is None and self._directory.exists():
            for path in self._directory.iterdir():
                if path.is_file() and (path.suffix == ".json" or path.name.endswith(".tmp")):
                    path.unlink()
        if self._directory.exists() and not any(self._directory.iterdir()):
            self._directory.rmdir()

    def _schedule_retention(self, sessions: list[SessionState], now: float) -> None:
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
    }


def _updated_at(sessions: list[SessionState]) -> str:
    if sessions:
        return max(session.updated_at for session in sessions)
    return datetime.datetime.now(datetime.UTC).isoformat()
