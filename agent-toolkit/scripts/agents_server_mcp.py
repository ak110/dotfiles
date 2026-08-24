#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.28.1,<2", "claude-agent-sdk>=0.2.144,<0.3"]
# ///
"""CodexとClaudeの委譲先を非同期MCPとして公開する。"""

# バックエンドはengine選択時に遅延読込するため、意図的に循環するモジュール構成である。
# pylint: disable=cyclic-import,import-outside-toplevel

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import logging
import os
import pathlib
import warnings
from collections.abc import AsyncIterator
from typing import Any

from mcp.server.fastmcp import FastMCP

try:
    from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning
except ImportError:  # pragma: no cover - mcpの依存版が警告型を公開しない場合
    IncompleteFieldDefinitionWarning = None  # type: ignore[assignment,misc]

DEFAULT_WAIT_TIMEOUT = 300.0
RESULT_RETENTION_SECONDS = 1800.0
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})
SUPPORTED_ENGINES = frozenset({"claude", "codex"})
REPLY_DELIVERIES = frozenset({"reply_started", "reply_failed", "reply_ambiguous"})


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _progress_excerpt(text: str) -> str:
    """テキストを改行なしの末尾80文字へ正規化する。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    if len(normalized) <= 80:
        return normalized
    return f"…{normalized[-80:]}"


def _nonempty_error(error: Any) -> bool:
    return error is not None and error != "" and error != {}


def _append_bounded(existing: str, delta: str, limit: int = 4000) -> str:
    value = existing + delta
    return value if len(value) <= limit else value[-limit:]


@dataclasses.dataclass
class SessionState:
    """MCPから観測できる1つの委譲先sessionと最新turnの共有状態。"""

    session_id: str
    cwd: str
    model: str | None = None
    effort: str | None = None
    engine: str = "codex"
    turn_id: str = ""
    status: str = "running"
    plan: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    current_item: dict[str, Any] | None = None
    commentary: str = ""
    diff_changed: bool = False
    error: Any = None
    agent_message: str = ""
    protocol_warnings: list[str] = dataclasses.field(default_factory=list)
    reply_attempted: bool = False
    reply_turn_started: bool = False
    reply_retryable: bool = False
    turn_start_ambiguous: bool = False
    interrupt_requested: bool = False
    turn_completed: bool = False
    failure_pending_completion: bool = False
    updated_at: str = dataclasses.field(default_factory=_utc_now)
    retention_deadline: float | None = None
    turn_control_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock, repr=False)
    _progress_text: str = dataclasses.field(default="", repr=False)
    progress_items: dict[str, str] = dataclasses.field(default_factory=dict, repr=False)

    @property
    def terminal(self) -> bool:
        """最新turnが終端状態であるかを返す。"""
        return self.status in TERMINAL_STATUSES

    @property
    def result_available(self) -> bool:
        """終端結果を返せる状態であるかを返す。"""
        return self.terminal and self.turn_completed and not self.turn_start_ambiguous

    @property
    def progress(self) -> str:
        """最新テキスト出力の公開用抜粋を返す。"""
        return _progress_excerpt(self._progress_text)

    def set_progress(self, text: str) -> None:
        """最新テキスト出力を更新する。"""
        self._progress_text = text
        self.touch()

    def reset_progress(self) -> None:
        """現在turnの進捗を初期化する。"""
        self._progress_text = ""
        self.progress_items.clear()

    def touch(self) -> None:
        """状態の更新時刻を現在時刻へ更新する。"""
        self.updated_at = _utc_now()
        if self.result_available:
            if self.retention_deadline is None:
                self.retention_deadline = asyncio.get_running_loop().time() + RESULT_RETENTION_SECONDS
        else:
            self.retention_deadline = None

    def public_status(self, *, include_result: bool = False) -> dict[str, Any]:
        """公開契約へ状態を射影する。内部のturn識別子は含めない。"""
        result: dict[str, Any] = {
            "session_id": self.session_id,
            "engine": self.engine,
            "status": self.status,
            "progress": self.progress,
        }
        if include_result and self.result_available:
            result["agent_message"] = self.agent_message
            if _nonempty_error(self.error):
                result["error"] = self.error
        return result

    def previous_result(self) -> dict[str, Any]:
        """継続入力の応答へ退避する直前turnの結果を返す。"""
        result: dict[str, Any] = {
            "session_id": self.session_id,
            "engine": self.engine,
            "status": self.status,
            "agent_message": self.agent_message,
        }
        if _nonempty_error(self.error):
            result["error"] = self.error
        return result


def _initialize_turn(session: SessionState, *, reset_progress: bool = True) -> None:
    """新しいturnの開始前に共有状態を初期化する。"""
    session.turn_id = ""
    session.status = "running"
    session.plan = []
    session.current_item = None
    session.commentary = ""
    session.diff_changed = False
    session.error = None
    session.agent_message = ""
    session.protocol_warnings = []
    session.reply_retryable = False
    session.turn_start_ambiguous = False
    session.interrupt_requested = False
    session.turn_completed = False
    session.failure_pending_completion = False
    session.retention_deadline = None
    if reset_progress:
        session.reset_progress()
    session.touch()


def _begin_reply(session: SessionState) -> None:
    """終端済みsessionの新しいturnを開始する準備をする。"""
    _initialize_turn(session, reset_progress=False)
    session.reply_attempted = True
    session.reply_turn_started = False
    session.touch()


def _validate_prompt(prompt: str) -> None:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")


def _validate_cwd(cwd: str) -> None:
    if not isinstance(cwd, str) or not cwd or not pathlib.PurePath(cwd).is_absolute():
        raise ValueError("cwd must be a non-empty absolute path")
    if not pathlib.Path(cwd).is_dir():
        raise ValueError(f"cwd is not an existing directory: {cwd}")


def _validate_model_effort(model: str | None, effort: str | None) -> None:
    if (model is None) != (effort is None):
        raise ValueError("model and effort must be provided together")
    if model is not None and (not model.strip() or not effort or not effort.strip()):
        raise ValueError("model and effort must be non-empty strings")


class AgentsServerManager:
    """エンジン別バックエンドと共有session状態を管理する。"""

    def __init__(self) -> None:
        self.sessions: dict[str, SessionState] = {}
        self.expired_session_ids: set[str] = set()
        self._condition = asyncio.Condition()
        self._codex: Any = None
        self._claude: Any = None

    def _backend(self, engine: str) -> Any:
        if engine == "codex":
            if self._codex is None:
                from _agents_server_codex import AppServerManager

                self._codex = AppServerManager(self.sessions, self._condition)
            return self._codex
        if engine == "claude":
            if self._claude is None:
                from _agents_server_claude import ClaudeServerManager

                self._claude = ClaudeServerManager(self.sessions, self._condition, expire_session=self._expire_session)
            return self._claude
        raise ValueError(f"unsupported engine: {engine}")

    def _get_session(self, session_id: str) -> SessionState:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        try:
            session = self.sessions[session_id]
        except KeyError as exc:
            if session_id in self.expired_session_ids:
                raise ValueError(f"session retention expired: {session_id}") from exc
            raise ValueError(f"unknown session: {session_id}") from exc
        if session.retention_deadline is not None and asyncio.get_running_loop().time() >= session.retention_deadline:
            self._expire_session(session_id)
            raise ValueError(f"session retention expired: {session_id}")
        return session

    def _expire_session(self, session_id: str) -> None:
        """期限切れsessionの結果本体を破棄し、識別子だけを保持する。"""
        self.sessions.pop(session_id, None)
        self.expired_session_ids.add(session_id)

    async def start(
        self,
        engine: str,
        prompt: str,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        """指定したエンジンのturnを開始する。"""
        if engine not in SUPPORTED_ENGINES:
            raise ValueError(f"unsupported engine: {engine}")
        _validate_prompt(prompt)
        _validate_cwd(cwd)
        _validate_model_effort(model, effort)
        backend = self._backend(engine)
        session = await backend.start(prompt, cwd, model, effort)
        session.engine = engine
        return session.public_status()

    async def wait(self, session_id: str, timeout: float = DEFAULT_WAIT_TIMEOUT) -> dict[str, Any]:
        """sessionの終端を待ち、終端時だけ結果本文を返す。"""
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            raise ValueError("timeout must be non-negative")
        session = self._get_session(session_id)
        if not session.result_available:
            try:
                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: session.result_available),
                        timeout=float(timeout),
                    )
            except TimeoutError:
                pass
        return session.public_status(include_result=session.result_available)

    async def send_message(self, session_id: str, prompt: str) -> dict[str, Any]:
        """実行中turnを継続し、終端済みなら同じsessionでreplyを開始する。"""
        _validate_prompt(prompt)
        session = self._get_session(session_id)
        backend = self._backend(session.engine)
        result = await backend.send_message(session, prompt)
        delivery = result["delivery"]
        if delivery in {"reply_started", "reply_ambiguous"}:
            session.reset_progress()
        response: dict[str, Any] = {"delivery": delivery, **session.public_status()}
        if delivery in REPLY_DELIVERIES:
            response["previous_result"] = result["previous_result"]
        return response

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def close(self) -> None:
        """初期化済みバックエンドを停止する。"""
        backends = tuple(backend for backend in (self._codex, self._claude) if backend is not None)
        for backend in backends:
            await backend.close()


_MANAGER = AgentsServerManager()


@contextlib.asynccontextmanager
async def _mcp_lifespan(_server: FastMCP[Any]) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await _MANAGER.close()


with warnings.catch_warnings():
    if IncompleteFieldDefinitionWarning is not None:
        warnings.simplefilter("ignore", IncompleteFieldDefinitionWarning)
    mcp = FastMCP(
        "agents_server",
        instructions="CodexまたはClaudeへの非同期委譲。承認・停止・一覧操作は公開しない。",
        lifespan=_mcp_lifespan,
    )


@mcp.tool(name="start", structured_output=True)
async def start(
    engine: str,
    prompt: str,
    cwd: str,
    model: str | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    """指定したエンジンの委譲先turnを開始する。"""
    return await _MANAGER.start(engine, prompt, cwd, model, effort)


@mcp.tool(name="wait", structured_output=True)
async def wait(session_id: str, timeout: float = DEFAULT_WAIT_TIMEOUT) -> dict[str, Any]:
    """委譲先の終端を待ち、終端時だけ結果本文を返す。"""
    return await _MANAGER.wait(session_id, timeout)


@mcp.tool(name="send_message", structured_output=True)
async def send_message(session_id: str, prompt: str) -> dict[str, Any]:
    """実行中turnへ追加指示を送り、終端済みなら同じsessionでreplyを開始する。"""
    return await _MANAGER.send_message(session_id, prompt)


def main() -> None:
    """MCP stdio transportを起動する。"""
    logging.basicConfig(level=os.environ.get("AGENT_TOOLKIT_AGENTS_LOG_LEVEL", "WARNING"))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
