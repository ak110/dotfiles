#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.28.1,<2"]
# ///
"""Codex App ServerをClaude Codeへ非同期MCPとして公開する。

このMCPは、Claude CodeのMCPプロセスと同じ寿命で
``codex app-server --stdio``を1つだけ所有する。App ServerのJSON-RPC通信は
標準ライブラリで扱い、MCP層へ公開する操作を開始・状態照会・待機・結果回収・
同一threadの継続の5つに限定する。

App Serverのwire protocolはJSON-RPC 2.0に準拠するが、stdioの各行では
``jsonrpc``フィールドを省略できる（OpenAI公式資料）。この実装では送信時に
``jsonrpc``を省略し、受信時は存在していても受理する。

公式資料: https://developers.openai.com/codex/app-server
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import json
import logging
import os
import pathlib
import sys
import warnings
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from typing import Any

from mcp.server.fastmcp import FastMCP

try:
    from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning
except ImportError:  # pragma: no cover - mcpの依存版が警告型を公開しない場合
    IncompleteFieldDefinitionWarning = None  # type: ignore[assignment,misc]

_LOG = logging.getLogger("agent-toolkit.codex-app-server")

APP_SERVER_COMMAND = ("codex", "app-server", "--stdio")
DEFAULT_WAIT_TIMEOUT = 300.0
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})
PUBLIC_STATUSES = frozenset({"running", *TERMINAL_STATUSES})

# Codex CLI 0.148.0のServerRequest schemaで確認した全server-initiated request。
# 承認用の公開MCP toolは設けず、readerで必ず応答してturnを止めない。
SERVER_REQUEST_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/tool/requestUserInput",
        "mcpServer/elicitation/request",
        "item/permissions/requestApproval",
        "item/tool/call",
        "account/chatgptAuthTokens/refresh",
        "attestation/generate",
        "applyPatchApproval",
        "execCommandApproval",
    }
)


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class AppServerError(RuntimeError):
    """App Serverとの通信又は要求検証に失敗した。"""


@dataclasses.dataclass
class SessionState:
    """MCPから観測できる1つのCodex threadと最新turnの状態。"""

    session_id: str
    cwd: str
    model: str | None = None
    effort: str | None = None
    turn_id: str = ""
    status: str = "running"
    plan: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    current_item: dict[str, Any] | None = None
    commentary: str = ""
    diff_changed: bool = False
    error: Any = None
    agent_message: str = ""
    protocol_warnings: list[str] = dataclasses.field(default_factory=list)
    result_retrieved: bool = False
    reply_attempted: bool = False
    reply_turn_started: bool = False
    reply_retryable: bool = False
    updated_at: str = dataclasses.field(default_factory=_utc_now)
    reply_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock, repr=False)

    @property
    def terminal(self) -> bool:
        """最新turnが終端状態であるかを返す。"""
        return self.status in TERMINAL_STATUSES

    def touch(self) -> None:
        """更新時刻をUTC ISO 8601へ更新する。"""
        self.updated_at = _utc_now()

    def public_status(self) -> dict[str, Any]:
        """公開MCP応答用に内部状態を必要最小限へ射影する。"""
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "status": self.status,
            "plan": list(self.plan),
            "current_item": self.current_item,
            "commentary": self.commentary,
            "diff_changed": self.diff_changed,
            "error": self.error,
            "protocol_warnings": list(self.protocol_warnings),
            "updated_at": self.updated_at,
        }


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _thread_id_from(params: Any) -> str | None:
    if not isinstance(params, dict):
        return None
    for key in ("threadId", "thread_id", "session_id"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _turn_id_from(params: Any) -> str | None:
    if not isinstance(params, dict):
        return None
    for key in ("turnId", "turn_id"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class JsonRpcProcess:
    """stdio App Serverとの要求・応答を多重化するクライアント。"""

    def __init__(
        self,
        on_notification: Callable[[dict[str, Any]], Awaitable[None]],
        on_server_request: Callable[[dict[str, Any]], Awaitable[None]],
        on_failure: Callable[[BaseException], Awaitable[None]] | None = None,
    ) -> None:
        self._on_notification = on_notification
        self._on_server_request = on_server_request
        self._on_failure = on_failure
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._reader_failure: BaseException | None = None

    async def start(self) -> None:
        """子プロセスを起動し、initialize/initializedを完了する。"""
        if self.process is not None:
            return
        try:
            self.process = await asyncio.create_subprocess_exec(
                *APP_SERVER_COMMAND,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise AppServerError(f"failed to start {' '.join(APP_SERVER_COMMAND)}: {exc}") from exc
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        try:
            await self.request(
                "initialize",
                {
                    "clientInfo": {"name": "agent-toolkit-codex-app-server", "version": "1.0"},
                    "capabilities": {},
                },
            )
            await self.notify("initialized", {})
        except Exception:
            await self.close()
            raise

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """JSON-RPC requestを送り、対応するIDの応答を返す。"""
        if self.process is None or self.process.stdin is None:
            raise AppServerError("Codex App Server is not running")
        if self._closed or self._reader_failure is not None:
            raise AppServerError("Codex App Server client is closed")
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params or {}})
            response = await future
        finally:
            self._pending.pop(request_id, None)
        if "error" in response:
            error = response.get("error")
            if isinstance(error, dict):
                message = _as_text(error.get("message")) or "JSON-RPC request failed"
            else:
                message = "JSON-RPC request failed"
            raise AppServerError(f"{method}: {message}")
        result = response.get("result", {})
        return result if isinstance(result, dict) else {}

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """JSON-RPC notificationを送る。"""
        await self._send({"method": method, "params": params or {}})

    async def _send(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None or self._closed:
            raise AppServerError("Codex App Server stdin is unavailable")
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                self.process.stdin.write(encoded)
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionError) as exc:
                raise AppServerError(f"failed to write to Codex App Server: {exc}") from exc

    @property
    def closed(self) -> bool:
        """子プロセスとの接続が終了処理済みであるかを返す。"""
        return self._closed

    @property
    def reader_failure(self) -> BaseException | None:
        """Stdout readerが検出した失敗を返す。"""
        return self._reader_failure

    async def send(self, message: dict[str, Any]) -> None:
        """JSON-RPC応答を接続へ送る。"""
        await self._send(message)

    async def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while True:
                raw_line = await self.process.stdout.readline()
                if not raw_line:
                    raise AppServerError("Codex App Server stdout closed")
                try:
                    message = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AppServerError(f"invalid Codex App Server JSON line: {exc}") from exc
                if not isinstance(message, dict):
                    continue
                if "id" in message and ("result" in message or "error" in message):
                    request_id = message.get("id")
                    future = self._pending.get(request_id) if isinstance(request_id, int) else None
                    if future is not None and not future.done():
                        future.set_result(message)
                    continue
                if "id" in message and isinstance(message.get("method"), str):
                    await self._on_server_request(message)
                    continue
                if isinstance(message.get("method"), str):
                    await self._on_notification(message)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._reader_failure = exc
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(AppServerError(str(exc)))
            if self._on_failure is not None:
                with contextlib.suppress(Exception):
                    await self._on_failure(exc)

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    return
                print(
                    line.decode("utf-8", errors="replace"),
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
        except OSError as exc:
            _LOG.debug("Codex App Server stderrの読取を終了しました: %s", exc)

    async def close(self) -> None:
        """自身が起動した子プロセスだけを終了し、関連taskを回収する。"""
        if self._closed and self.process is None:
            return
        self._closed = True
        process = self.process
        tasks = tuple(
            task for task in (self._reader_task, self._stderr_task) if task is not None and task is not asyncio.current_task()
        )
        self._reader_task = None
        self._stderr_task = None
        for task in tasks:
            if not task.done():
                task.cancel()
        if process is not None:
            with contextlib.suppress(OSError, ProcessLookupError):
                if process.returncode is None:
                    process.terminate()
            if process.returncode is None:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=5)
            if process.returncode is None:
                with contextlib.suppress(OSError, ProcessLookupError):
                    process.kill()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(process.wait(), timeout=5)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.process = None
        error = AppServerError("Codex App Server client closed")
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()


class AppServerManager:
    """App Server clientと公開session状態を管理する。"""

    def __init__(self) -> None:
        self.client: JsonRpcProcess | None = None
        self.sessions: dict[str, SessionState] = {}
        self._condition = asyncio.Condition()
        self._lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[None]] = set()

    def _schedule(self, awaitable: Coroutine[Any, Any, None]) -> None:
        """同じイベントループで回収する管理対象taskを登録する。"""
        task: asyncio.Task[None] = asyncio.create_task(awaitable)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _ensure_client(self) -> JsonRpcProcess:
        async with self._lock:
            if self.client is not None and not self.client.closed and self.client.reader_failure is None:
                return self.client
            old_client = self.client
            self.client = None
            if old_client is not None:
                await old_client.close()
            client = JsonRpcProcess(
                self._handle_notification,
                self._handle_server_request,
                self._handle_client_failure,
            )
            self.client = client
            try:
                await client.start()
            except Exception:
                if self.client is client:
                    self.client = None
                raise
            return client

    async def start(self, prompt: str, cwd: str, model: str | None = None, effort: str | None = None) -> dict[str, Any]:
        """新しいthreadとturnを開始し、直ちにsession状態を返す。"""
        _validate_prompt(prompt)
        _validate_cwd(cwd)
        _validate_model_effort(model, effort)
        client = await self._ensure_client()
        params: dict[str, Any] = {
            "cwd": cwd,
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
        }
        if model is not None:
            params["model"] = model
        thread_response = await client.request("thread/start", params)
        thread = thread_response.get("thread")
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or not thread["id"]:
            raise AppServerError("thread/start returned no thread.id")
        session_id = thread["id"]
        session = SessionState(session_id=session_id, cwd=cwd, model=model, effort=effort)
        self.sessions[session_id] = session
        try:
            await self._start_turn(session, prompt)
        except Exception:
            self.sessions.pop(session_id, None)
            raise
        return session.public_status()

    async def start_reply(self, session_id: str, prompt: str) -> dict[str, Any]:
        """結果取得済みのthreadを再開して新しいturnを開始する。"""
        _validate_prompt(prompt)
        session = self._get_session(session_id)
        async with session.reply_lock:
            if not session.terminal:
                raise ValueError("the previous Codex turn is still running")
            if not session.result_retrieved:
                raise ValueError("codex_result must be called before starting a reply")
            if (
                session.status == "failed"
                and session.reply_attempted
                and not session.reply_turn_started
                and not session.reply_retryable
            ):
                raise ValueError("codex_start_reply cannot retry an ambiguous turn/start failure")
            self._begin_reply(session)
            try:
                client = await self._ensure_client()
                resume_params: dict[str, Any] = {
                    "threadId": session.session_id,
                    "cwd": session.cwd,
                    "approvalPolicy": "never",
                    "sandbox": "danger-full-access",
                }
                if session.model is not None:
                    resume_params["model"] = session.model
                resume_response = await client.request("thread/resume", resume_params)
                resumed_thread = resume_response.get("thread")
                if not isinstance(resumed_thread, dict) or resumed_thread.get("id") != session.session_id:
                    raise AppServerError("thread/resume returned an unexpected thread.id")
            except Exception as exc:
                await self._mark_failed(session, exc, retryable=True)
                raise
            try:
                await self._start_turn(session, prompt)
            except Exception as exc:
                await self._mark_failed(session, exc, retryable=False)
                raise
            return session.public_status()

    @staticmethod
    def _begin_reply(session: SessionState) -> None:
        """直前turnの値を公開状態から除去し、新しいreply開始を準備する。"""
        session.turn_id = ""
        session.status = "running"
        session.plan = []
        session.current_item = None
        session.commentary = ""
        session.diff_changed = False
        session.error = None
        session.agent_message = ""
        session.protocol_warnings = []
        session.result_retrieved = False
        session.reply_attempted = True
        session.reply_turn_started = False
        session.reply_retryable = False
        session.touch()

    async def _mark_failed(
        self,
        session: SessionState,
        error: BaseException,
        *,
        retryable: bool,
    ) -> None:
        """要求開始の失敗を終端状態へ反映し、待機者を起床する。

        `retryable`が真の場合だけ、失敗結果の回収後に同じreplyを明示的に再試行できる。
        """
        session.turn_id = ""
        session.status = "failed"
        session.plan = []
        session.current_item = None
        session.commentary = ""
        session.diff_changed = False
        session.error = {"message": str(error) or error.__class__.__name__}
        session.agent_message = ""
        session.protocol_warnings = []
        session.result_retrieved = False
        session.reply_turn_started = False
        session.reply_retryable = retryable
        session.touch()
        await self._notify_waiters()

    async def _start_turn(self, session: SessionState, prompt: str) -> None:
        client = await self._ensure_client()
        params: dict[str, Any] = {
            "threadId": session.session_id,
            "input": [{"type": "text", "text": prompt}],
            "cwd": session.cwd,
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "dangerFullAccess"},
        }
        if session.model is not None:
            params["model"] = session.model
        if session.effort is not None:
            params["effort"] = session.effort
        response = await client.request("turn/start", params)
        turn = response.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise AppServerError("turn/start returned no turn.id")
        session.turn_id = turn_id
        session.status = "running"
        if session.reply_attempted:
            session.reply_turn_started = True
        session.touch()
        await self._notify_waiters()

    def status(self, session_id: str) -> dict[str, Any]:
        """指定sessionの最新状態を返す。"""
        return self._get_session(session_id).public_status()

    async def wait(self, session_id: str, timeout: float = DEFAULT_WAIT_TIMEOUT) -> dict[str, Any]:
        """指定sessionが終端するまで待機し、タイムアウト時は現状態を返す。"""
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        session = self._get_session(session_id)
        if not session.terminal:
            try:
                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: session.terminal),
                        timeout=timeout,
                    )
            except TimeoutError:
                pass
        return session.public_status()

    def result(self, session_id: str) -> dict[str, Any]:
        """終端したsessionの結果を返し、取得済みとして記録する。"""
        session = self._get_session(session_id)
        if not session.terminal:
            raise ValueError("the Codex turn has not completed")
        session.result_retrieved = True
        session.touch()
        return {
            "session_id": session.session_id,
            "turn_id": session.turn_id,
            "status": session.status,
            "agent_message": session.agent_message,
            "error": session.error,
            "updated_at": session.updated_at,
        }

    def _get_session(self, session_id: str) -> SessionState:
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise ValueError(f"unknown Codex session: {session_id}") from exc

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def _handle_notification(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return
        session = self._find_session(params)
        if session is None:
            return
        turn = params.get("turn")
        if method == "turn/started":
            if isinstance(turn, dict):
                turn_id = turn.get("id")
                if isinstance(turn_id, str):
                    session.turn_id = turn_id
            session.status = "running"
        elif method == "turn/completed":
            if isinstance(turn, dict):
                turn_id = turn.get("id")
                if isinstance(turn_id, str):
                    session.turn_id = turn_id
                session.status = _public_turn_status(turn.get("status"))
                session.error = turn.get("error")
                self._consume_items(session, turn.get("items"))
            else:
                session.status = "failed"
                session.error = "turn/completed did not contain turn"
            if session.status not in TERMINAL_STATUSES:
                session.status = "failed"
        elif method == "turn/plan/updated":
            plan = params.get("plan")
            if isinstance(plan, list):
                session.plan = [item for item in plan if isinstance(item, dict)]
        elif method == "turn/diff/updated":
            diff = params.get("diff")
            if isinstance(diff, str) and diff:
                session.diff_changed = True
        elif method == "item/started":
            item = params.get("item")
            session.current_item = item if isinstance(item, dict) else None
            if isinstance(item, dict) and item.get("type") == "fileChange":
                session.diff_changed = True
        elif method == "item/completed":
            item = params.get("item")
            if isinstance(item, dict):
                session.current_item = None
                self._consume_item(session, item)
        elif method in {"item/agentMessage/delta", "item/plan/delta"}:
            delta = params.get("delta")
            if isinstance(delta, str):
                session.commentary = _append_bounded(session.commentary, delta)
        elif method in {"item/fileChange/outputDelta", "item/fileChange/patchUpdated"}:
            session.diff_changed = True
        session.touch()
        await self._notify_waiters()

    async def _handle_server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")
        request_id = message.get("id")
        if not isinstance(method, str):
            return
        client = self.client
        if client is None:
            return
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            # IDなしの異常なserver requestへはJSON-RPC応答を返せないため、
            # 接続上のactive turnをfailedへ遷移してwaiterを解放する。
            await self._fail_for_request(params, method)
            return
        if method == "mcpServer/elicitation/request":
            response: dict[str, Any] = {"action": "cancel", "content": None, "_meta": None}
            await client.send({"id": request_id, "result": response})
            session = self._find_session(params)
            if session is not None:
                session.protocol_warnings.append("mcpServer/elicitation/request was cancelled")
                session.touch()
                await self._notify_waiters()
            return
        await self._fail_for_request(params, method)
        await client.send(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Unsupported non-interactive server request: {method}",
                },
            }
        )

    async def _fail_for_request(self, params: Any, method: str) -> None:
        session = self._find_session(params)
        sessions = [session] if session is not None else [item for item in self.sessions.values() if not item.terminal]
        for active in sessions:
            active.status = "failed"
            active.error = {"message": f"Codex requested interactive server input: {method}"}
            active.protocol_warnings.append(f"unsupported server request: {method}")
            active.touch()
        await self._notify_waiters()
        # reader task cannot await a request response for turn/interrupt: that would
        # deadlock the same reader. Schedule it after marking the state terminal.
        for active in sessions:
            if active.turn_id and self.client is not None:
                self._schedule(self._interrupt(active.session_id, active.turn_id))

    async def _interrupt(self, session_id: str, turn_id: str) -> None:
        client = self.client
        if client is None or client.closed:
            return
        with contextlib.suppress(Exception):
            await client.request("turn/interrupt", {"threadId": session_id, "turnId": turn_id})

    async def _handle_client_failure(self, error: BaseException) -> None:
        """reader異常時に全active turnをfailedへ遷移させて待機者を起こす。"""
        detail = str(error) or error.__class__.__name__
        changed = False
        for session in self.sessions.values():
            if not session.terminal:
                session.status = "failed"
                session.error = {"message": f"Codex App Server stopped: {detail}"}
                session.touch()
                changed = True
        if changed:
            await self._notify_waiters()

    def _find_session(self, params: Any) -> SessionState | None:
        thread_id = _thread_id_from(params)
        if thread_id is not None and thread_id in self.sessions:
            return self.sessions[thread_id]
        turn_id = _turn_id_from(params)
        if turn_id is not None:
            return next((item for item in self.sessions.values() if item.turn_id == turn_id), None)
        return None

    @staticmethod
    def _consume_items(session: SessionState, items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, dict):
                AppServerManager._consume_item(session, item)

    @staticmethod
    def _consume_item(session: SessionState, item: dict[str, Any]) -> None:
        item_type = item.get("type")
        if item_type == "agentMessage":
            text = item.get("text")
            if isinstance(text, str):
                session.agent_message = text
        elif item_type == "plan":
            text = item.get("text")
            if isinstance(text, str):
                session.plan = [{"text": text, "status": "completed"}]
        elif item_type == "fileChange":
            session.diff_changed = True

    async def close(self) -> None:
        """自身が起動したApp Server接続を終了する。"""
        current_task = asyncio.current_task()
        tasks = tuple(task for task in self._background_tasks if task is not current_task)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        client, self.client = self.client, None
        if client is not None:
            await client.close()


def _append_bounded(existing: str, delta: str, limit: int = 4000) -> str:
    value = existing + delta
    return value if len(value) <= limit else value[-limit:]


def _public_turn_status(status: Any) -> str:
    if status == "inProgress":
        return "running"
    if status in TERMINAL_STATUSES:
        return status
    return "failed"


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


_MANAGER = AppServerManager()


@contextlib.asynccontextmanager
async def _mcp_lifespan(_server: FastMCP[Any]) -> AsyncIterator[None]:
    """MCPと同じイベントループでApp Serverの終了・task回収を行う。"""
    try:
        yield
    finally:
        await _MANAGER.close()


with warnings.catch_warnings():
    # mcp 1.28系のFastMCP Settingsが未解決の汎用型を警告するが、stdio運用では
    # lifespan設定を使わないため実行へ影響しない。依存版が型を公開する場合だけ抑制する。
    if IncompleteFieldDefinitionWarning is not None:
        warnings.simplefilter("ignore", IncompleteFieldDefinitionWarning)
    mcp = FastMCP(
        "codex_app_server",
        instructions="非同期のCodex App Server委譲。承認・停止・一覧操作は公開しない。",
        lifespan=_mcp_lifespan,
    )


@mcp.tool(name="codex_start", structured_output=True)
async def codex_start(
    prompt: str,
    cwd: str,
    model: str | None = None,
    effort: str | None = None,
) -> dict[str, Any]:
    """Codex turnを開始し、完了を待たず状態を返す。"""
    return await _MANAGER.start(prompt, cwd, model, effort)


@mcp.tool(name="codex_status", structured_output=True)
async def codex_status(session_id: str) -> dict[str, Any]:
    """Codex sessionの最新状態を返す。"""
    return _MANAGER.status(session_id)


@mcp.tool(name="codex_wait", structured_output=True)
async def codex_wait(session_id: str, timeout: float = DEFAULT_WAIT_TIMEOUT) -> dict[str, Any]:
    """Codex sessionの終端又はtimeoutまで待つ。timeout到達はエラーにしない。"""
    return await _MANAGER.wait(session_id, timeout)


@mcp.tool(name="codex_result", structured_output=True)
async def codex_result(session_id: str) -> dict[str, Any]:
    """終端済みCodex turnの最終agentMessageを回収する。"""
    return _MANAGER.result(session_id)


@mcp.tool(name="codex_start_reply", structured_output=True)
async def codex_start_reply(session_id: str, prompt: str) -> dict[str, Any]:
    """結果回収済みthreadへ同じ会話の次turnを開始する。"""
    return await _MANAGER.start_reply(session_id, prompt)


def main() -> None:
    """MCP stdio transportを起動する。"""
    logging.basicConfig(level=os.environ.get("AGENT_TOOLKIT_CODEX_LOG_LEVEL", "WARNING"))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
