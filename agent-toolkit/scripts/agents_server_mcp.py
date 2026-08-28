#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.28.1,<2", "claude-agent-sdk>=0.2.144,<0.3"]
# ///
"""CodexとClaudeの委譲先を非同期MCPとして公開する。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import warnings
from collections.abc import AsyncIterator, Sequence
from typing import Any

import _agents_server_claude as claude_backend
import _agents_server_codex as codex_backend
from _agents_server_state import (
    SessionState,
    _validate_cwd,
    _validate_model_effort,
    _validate_prompt,
)
from mcp.server.fastmcp import FastMCP

try:
    from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning
except ImportError:  # pragma: no cover - mcpの依存版が警告型を公開しない場合
    IncompleteFieldDefinitionWarning = None  # type: ignore[assignment,misc]

DEFAULT_WAIT_TIMEOUT = 240.0
DEFAULT_KILL_TIMEOUT = 300.0
SUPPORTED_ENGINES = frozenset({"claude", "codex"})
REPLY_DELIVERIES = frozenset({"reply_started", "reply_failed", "reply_ambiguous"})


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
                self._codex = codex_backend.AppServerManager(self.sessions, self._condition)
            return self._codex
        if engine == "claude":
            if self._claude is None:
                self._claude = claude_backend.ClaudeServerManager(
                    self.sessions,
                    self._condition,
                    expire_session=self._expire_session,
                )
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
        async with session.turn_control_lock:
            if session.interrupt_requested and not session.terminal:
                raise ValueError(f"session is being interrupted: {session_id}")
        result = await backend.send_message(session, prompt)
        delivery = result["delivery"]
        if delivery in {"reply_started", "reply_ambiguous"}:
            session.reset_progress()
        response: dict[str, Any] = {"delivery": delivery, **session.public_status()}
        if delivery in REPLY_DELIVERIES:
            response["previous_result"] = result["previous_result"]
        return response

    async def kill(self, session_id: str, timeout: float = DEFAULT_KILL_TIMEOUT) -> dict[str, Any]:
        """実行中turnへ中断を要求し、指定時間まで終端を待つ。"""
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            raise ValueError("timeout must be non-negative")
        session = self._get_session(session_id)
        started_terminal = session.terminal
        requested_before_call = session.interrupt_requested
        if started_terminal:
            response = session.public_status(include_result=True)
            response["kill_requested"] = False
            return response

        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout) if timeout > 0 else None
        requested = requested_before_call
        backend = self._backend(session.engine)
        try:
            if deadline is None:
                await session.turn_control_lock.acquire()
            else:
                await asyncio.wait_for(
                    session.turn_control_lock.acquire(),
                    timeout=max(0.0, deadline - loop.time()),
                )
        except TimeoutError as exc:
            raise TimeoutError(f"kill timed out: {session_id}") from exc
        try:
            if session.terminal:
                requested = requested or requested_before_call or session.interrupt_requested
            elif session.interrupt_requested:
                requested = True
            else:
                if session.engine == "codex" and not session.turn_id:
                    if timeout == 0:
                        raise ValueError("the active Codex turn has no turn_id")
                    try:
                        async with self._condition:
                            await asyncio.wait_for(
                                self._condition.wait_for(lambda: bool(session.turn_id) or session.terminal),
                                timeout=max(0.0, deadline - loop.time()) if deadline is not None else None,
                            )
                    except TimeoutError as exc:
                        raise TimeoutError(f"kill timed out: {session_id}") from exc
                    if session.terminal:
                        response = session.public_status(include_result=True)
                        response["kill_requested"] = False
                        return response
                session.interrupt_requested = True
                session.touch()
                try:
                    interrupt = backend.interrupt(session)
                    if deadline is None:
                        await interrupt
                    else:
                        await asyncio.wait_for(interrupt, timeout=max(0.0, deadline - loop.time()))
                except TimeoutError:
                    await self._notify_waiters()
                    raise TimeoutError(f"kill timed out: {session_id}") from None
                except Exception:
                    session.interrupt_requested = False
                    session.touch()
                    await self._notify_waiters()
                    raise
                requested = True
        finally:
            session.turn_control_lock.release()

        if not requested:
            response = session.public_status(include_result=True)
            response["kill_requested"] = False
            return response
        if timeout > 0:
            try:
                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: session.result_available),
                        timeout=max(0.0, deadline - loop.time()) if deadline is not None else None,
                    )
            except TimeoutError as exc:
                raise TimeoutError(f"kill timed out: {session_id}") from exc
        response = session.public_status(include_result=session.result_available)
        response["kill_requested"] = True
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
    """委譲先の終端を待ち、終端時だけ結果本文を返す。

    通常の既定は240秒である。固有のtimeout要件がなければ引数を省略して通常既定を使う。
    `timeout=0`は待機せず現状態を返す。
    """
    return await _MANAGER.wait(session_id, timeout)


@mcp.tool(name="send_message", structured_output=True)
async def send_message(session_id: str, prompt: str) -> dict[str, Any]:
    """実行中turnへ追加指示を送り、終端済みなら同じsessionでreplyを開始する。"""
    return await _MANAGER.send_message(session_id, prompt)


@mcp.tool(name="kill", structured_output=True)
async def kill(session_id: str, timeout: float = DEFAULT_KILL_TIMEOUT) -> dict[str, Any]:
    """実行中turnへ中断を要求し、指定時間まで終端を待つ。"""
    return await _MANAGER.kill(session_id, timeout)


def main(argv: Sequence[str] | None = None) -> int:
    """引数に応じて依存検査またはMCP stdio transportを起動する。"""
    logging.basicConfig(level=os.environ.get("AGENT_TOOLKIT_AGENTS_LOG_LEVEL", "WARNING"))
    parser = argparse.ArgumentParser(description="CodexとClaudeの委譲先を非同期MCPとして公開する。")
    parser.add_argument(
        "--check-dependencies",
        action="store_true",
        help="Claude Agent SDKの依存を読み込み、options構築まで検査する。",
    )
    args = parser.parse_args(argv)
    if args.check_dependencies:
        claude_backend.check_dependencies()
        return 0
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
