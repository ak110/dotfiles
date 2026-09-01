#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# # このモジュールはagents_server_mcp.pyから読み込まれ、単独実行時の依存関係を持たない。
# ///
"""Codex App ServerとのJSON-RPC通信を担当する。

App Serverのwire protocolはJSON-RPC 2.0に準拠するが、stdioの各行では
``jsonrpc``フィールドを省略できる（OpenAI公式資料）。この実装では送信時に
``jsonrpc``を省略し、受信時は存在していても受理する。

公式資料: https://developers.openai.com/codex/app-server
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

from _agents_server_state import (
    EXPLORE_SYSTEM_PROMPT,
    TERMINAL_STATUSES,
    ModelCandidate,
    ResumePrompt,
    SessionState,
    _append_bounded,
    _begin_reply,
    _initialize_turn,
    _validate_cwd,
    _validate_model_effort,
    _validate_prompt,
)

_LOG = logging.getLogger("agent-toolkit.agents-server.codex")

APP_SERVER_COMMAND = ("codex", "app-server", "--stdio")
# Codexホストでは親の作業ディレクトリが版数付きplugin cacheとなり、
# plugin更新で消失すると生存中のApp Serverが設定を読み込めないため継承しない。
APP_SERVER_WORKING_DIRECTORY = str(Path.home())
DEFAULT_WAIT_TIMEOUT = 300.0
# App ServerのJSONL通知用StreamReader上限（バイト）。
# asyncioの既定値は64KiBで、turnのplan・diffなどの有効な通知が上限を超えると
# readline()がValueErrorを送出してreaderが停止するため、8MiBまで読み取れるようにする。
APP_SERVER_STREAM_LIMIT_BYTES = 8 * 1024 * 1024
APP_SERVER_STDERR_LIMIT_CHARS = 4000
APP_SERVER_EXIT_DIAGNOSTIC_TIMEOUT = 1.0


class AppServerError(RuntimeError):
    """App Serverとの通信又は要求検証に失敗した。"""


class JsonRpcResponseError(AppServerError):
    """App ServerがJSON-RPC error responseを返した。"""

    def __init__(self, method: str, code: Any, message: str, data: Any = None) -> None:
        super().__init__(f"{method}: {message}")
        self.method = method
        self.code = code
        self.data = data


class TurnStartResponseError(AppServerError):
    """turn/startの応答形式が不正である。要求拒否を確認できないため、client生存中は受理状態が曖昧である。"""


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


def _response_turn_id(response: Any) -> str | None:
    """JSON-RPC responseからsteer対象turn IDを取り出す。"""
    if not isinstance(response, dict):
        return None
    turn_id = response.get("turnId")
    return turn_id if isinstance(turn_id, str) and turn_id else None


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
        self._stderr_text = ""

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
                limit=APP_SERVER_STREAM_LIMIT_BYTES,
                cwd=APP_SERVER_WORKING_DIRECTORY,
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
                raise JsonRpcResponseError(method, error.get("code"), message, error.get("data"))
            message = "JSON-RPC request failed"
            raise JsonRpcResponseError(method, None, message)
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
                    error = await self._stdout_closed_error()
                    _LOG.error("%s", error)
                    raise error
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
                text = line.decode("utf-8", errors="replace")
                self._stderr_text = _append_bounded(
                    self._stderr_text,
                    text,
                    APP_SERVER_STDERR_LIMIT_CHARS,
                )
                print(
                    text,
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
        except OSError as exc:
            _LOG.debug("Codex App Server stderrの読取を終了しました: %s", exc)

    async def _stdout_closed_error(self) -> AppServerError:
        """子プロセス終了時の診断情報を有界に収集する。"""
        assert self.process is not None
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                self.process.wait(),
                timeout=APP_SERVER_EXIT_DIAGNOSTIC_TIMEOUT,
            )
        stderr_task = self._stderr_task
        if stderr_task is not None and stderr_task is not asyncio.current_task():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(stderr_task),
                    timeout=APP_SERVER_EXIT_DIAGNOSTIC_TIMEOUT,
                )

        command = " ".join(APP_SERVER_COMMAND)
        message = f"Codex App Server stdout closed: command={command}; returncode={self.process.returncode}"
        stderr = self._stderr_text.strip()
        if stderr:
            message = f"{message}; stderr={stderr}"
        return AppServerError(message)

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
    """Codex App Serverと共有session状態を管理する。"""

    def __init__(
        self,
        sessions: dict[str, SessionState] | None = None,
        condition: asyncio.Condition | None = None,
    ) -> None:
        self.client: JsonRpcProcess | None = None
        self.sessions = sessions if sessions is not None else {}
        self._condition = condition if condition is not None else asyncio.Condition()
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

    async def start(
        self,
        prompt: str,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
        *,
        model_type: str | None = None,
        explore: bool = False,
        excluded_candidates: frozenset[ModelCandidate] = frozenset(),
    ) -> SessionState:
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
        if explore:
            params["config"] = {"project_doc_max_bytes": 0}
            params["developerInstructions"] = EXPLORE_SYSTEM_PROMPT
        thread_response = await client.request("thread/start", params)
        thread = thread_response.get("thread")
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or not thread["id"]:
            raise AppServerError("thread/start returned no thread.id")
        session_id = thread["id"]
        session = SessionState(
            session_id=session_id,
            cwd=cwd,
            model_type=model_type,
            explore=explore,
            excluded_candidates=excluded_candidates,
            model=model,
            effort=effort,
            engine="codex",
        )
        self.sessions[session_id] = session
        _initialize_turn(session)
        try:
            await self._start_turn(session, prompt, client)
        except Exception as exc:
            if self._turn_start_response_is_ambiguous(client, exc):
                await self._mark_turn_start_ambiguous(session, exc)
            else:
                await self._mark_failed(session, exc, retryable=False)
            return session
        return session

    async def resume(
        self,
        session_id: str,
        prompt: ResumePrompt,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
        *,
        model_type: str | None = None,
        explore: bool = False,
        excluded_candidates: frozenset[ModelCandidate] = frozenset(),
    ) -> SessionState:
        """保存済みthreadを再開して新しいturnを開始する。"""
        _validate_cwd(cwd)
        _validate_model_effort(model, effort)
        client = await self._ensure_client()
        session = SessionState(
            session_id=session_id,
            cwd=cwd,
            model_type=model_type,
            explore=explore,
            excluded_candidates=excluded_candidates,
            model=model,
            effort=effort,
            engine="codex",
        )
        self.sessions[session_id] = session
        _initialize_turn(session)
        try:
            await self._resume_thread(session, client)
            await prompt.deliver(lambda value: self._start_turn(session, value, client))
        except Exception as exc:
            if self._turn_start_response_is_ambiguous(client, exc):
                await self._mark_turn_start_ambiguous(session, exc)
            else:
                await self._mark_failed(session, exc, retryable=False)
        return session

    async def send_message(self, session: SessionState, prompt: str) -> dict[str, Any]:
        """実行中turnへ追加指示を送り、終端競合時は同じthreadのreplyを開始する。"""
        _validate_prompt(prompt)
        async with session.turn_control_lock:
            if session.terminal:
                previous_result = self._capture_result(session)
                delivery, status, error = await self._start_reply_locked(session, prompt)
                if error is not None and delivery not in {"reply_failed", "reply_ambiguous"}:
                    raise error from None
                result = {
                    "delivery": delivery,
                    "previous_result": previous_result,
                    **status,
                }
                return result
            if session.interrupt_requested:
                raise ValueError("the active Codex turn is being interrupted")
            if not session.turn_id:
                raise ValueError("the active Codex turn has no turn_id")
            client = self.client
            if client is None or getattr(client, "closed", False) or getattr(client, "reader_failure", None) is not None:
                raise AppServerError("Codex App Server client is unavailable for steering")
            expected_turn_id = session.turn_id
            try:
                response = await client.request(
                    "turn/steer",
                    {
                        "threadId": session.session_id,
                        "expectedTurnId": expected_turn_id,
                        "input": [{"type": "text", "text": prompt}],
                    },
                )
            except JsonRpcResponseError as exc:
                outcome = await self._wait_after_steer_rejection(session, expected_turn_id, client)
                if outcome == "completed":
                    previous_result = self._capture_result(session)
                    delivery, status, error = await self._start_reply_locked(session, prompt)
                    if error is not None and delivery not in {"reply_failed", "reply_ambiguous"}:
                        raise error from None
                    return {
                        "delivery": delivery,
                        "previous_result": previous_result,
                        **status,
                    }
                raise exc from None
            response_turn_id = _response_turn_id(response)
            if response_turn_id != expected_turn_id:
                raise AppServerError(
                    f"turn/steer returned an unexpected turn.id: expected {expected_turn_id}, got {response_turn_id or 'none'}"
                )
            session.touch()
            return {
                "delivery": "steered",
                **session.public_status(),
            }

    async def interrupt(self, session: SessionState) -> None:
        """公開killから対象turnへ中断要求を送り、受理を待つ。"""
        if session.terminal:
            return
        if not session.turn_id:
            raise ValueError("the active Codex turn has no turn_id")
        client = self.client
        if client is None or getattr(client, "closed", False) or getattr(client, "reader_failure", None) is not None:
            raise AppServerError("Codex App Server client is unavailable for interrupt")
        try:
            await client.request(
                "turn/interrupt",
                {"threadId": session.session_id, "turnId": session.turn_id},
            )
        except JsonRpcResponseError:
            if session.terminal:
                return
            raise

    async def _start_reply_locked(
        self,
        session: SessionState,
        prompt: str,
    ) -> tuple[str, dict[str, Any], BaseException | None]:
        """lock取得済みのsessionへreplyを開始し、公開状態と失敗分類を返す。"""
        self._begin_reply(session)
        try:
            client = await self._ensure_client()
            await self._resume_thread(session, client)
        except Exception as exc:
            await self._mark_failed(session, exc, retryable=True)
            return "reply_failed", session.public_status(), exc
        try:
            await self._start_turn(session, prompt, client)
        except Exception as exc:
            if self._turn_start_response_is_ambiguous(client, exc):
                await self._mark_turn_start_ambiguous(session, exc)
                return "reply_ambiguous", session.public_status(), None
            await self._mark_failed(session, exc, retryable=False)
            return "reply_failed", session.public_status(), exc
        return "reply_started", session.public_status(), None

    @staticmethod
    async def _resume_thread(session: SessionState, client: Any) -> None:
        """保存済みCodex threadを現在の実行条件で再開する。"""
        resume_params: dict[str, Any] = {
            "threadId": session.session_id,
            "cwd": session.cwd,
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
        }
        if session.model is not None:
            resume_params["model"] = session.model
        if session.explore:
            resume_params["config"] = {"project_doc_max_bytes": 0}
            resume_params["developerInstructions"] = EXPLORE_SYSTEM_PROMPT
        resume_response = await client.request("thread/resume", resume_params)
        resumed_thread = resume_response.get("thread")
        if not isinstance(resumed_thread, dict) or resumed_thread.get("id") != session.session_id:
            raise AppServerError("thread/resume returned an unexpected thread.id")

    @staticmethod
    def _capture_result(session: SessionState) -> dict[str, Any]:
        """継続入力へ直前turnの結果を退避する。"""
        return session.previous_result()

    async def _wait_after_steer_rejection(
        self,
        session: SessionState,
        expected_turn_id: str,
        client: Any,
    ) -> str:
        """steer拒否後に終端競合だけを待ち、優先順位付きの判定結果を返す。"""
        timed_out = False

        def _changed() -> bool:
            return bool(
                getattr(client, "closed", False)
                or getattr(client, "reader_failure", None) is not None
                or session.turn_id != expected_turn_id
                or session.result_available
            )

        try:
            async with self._condition:
                await asyncio.wait_for(self._condition.wait_for(_changed), timeout=DEFAULT_WAIT_TIMEOUT)
        except TimeoutError:
            timed_out = True
        if getattr(client, "closed", False) or getattr(client, "reader_failure", None) is not None:
            return "client_failure"
        if session.turn_id != expected_turn_id:
            return "turn_changed"
        if timed_out:
            return "timeout"
        if session.result_available:
            return "completed"
        return "timeout"

    @staticmethod
    def _initialize_turn(session: SessionState) -> None:
        _initialize_turn(session)

    @staticmethod
    def _begin_reply(session: SessionState) -> None:
        _begin_reply(session)

    async def _mark_failed(
        self,
        session: SessionState,
        error: BaseException,
        *,
        retryable: bool,
    ) -> None:
        """要求開始の失敗を終端状態へ反映し、待機者を起床する。

        `retryable`が真の場合だけ、同じreplyを再試行できる内部状態にする。
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
        session.reply_turn_started = False
        session.reply_retryable = retryable
        session.turn_start_ambiguous = False
        session.interrupt_requested = False
        session.turn_completed = True
        session.failure_pending_completion = False
        session.touch()
        await self._notify_waiters()

    async def _mark_turn_start_ambiguous(self, session: SessionState, error: BaseException) -> None:
        """turn/start応答喪失を非終端状態へ反映する。"""
        if session.terminal:
            return
        session.status = "running"
        session.error = {"message": str(error) or error.__class__.__name__}
        session.reply_retryable = False
        session.turn_start_ambiguous = True
        session.interrupt_requested = False
        session.turn_completed = False
        session.failure_pending_completion = False
        session.touch()
        await self._notify_waiters()

    @staticmethod
    def _turn_start_response_is_ambiguous(client: Any, error: BaseException) -> bool:
        """turn/startの失敗が実行状態を判定できない応答喪失であるかを返す。"""
        if isinstance(error, JsonRpcResponseError):
            return False
        if bool(getattr(client, "closed", False)) or getattr(client, "reader_failure", None) is not None:
            return False
        process = getattr(client, "process", None)
        return process is None or getattr(process, "returncode", None) is None

    async def _start_turn(self, session: SessionState, prompt: str, client: JsonRpcProcess | None = None) -> None:
        if client is None:
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
            raise TurnStartResponseError("turn/start returned no turn.id")
        session.turn_id = turn_id
        if session.reply_attempted:
            session.reply_turn_started = True
        session.touch()
        await self._notify_waiters()

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
        notification_turn_id = self._notification_turn_id(params)
        if notification_turn_id is not None and session.turn_id and notification_turn_id != session.turn_id:
            return
        turn = params.get("turn")
        if method == "turn/started":
            if isinstance(turn, dict):
                turn_id = turn.get("id")
                if isinstance(turn_id, str):
                    session.turn_id = turn_id
                    if session.reply_attempted:
                        session.reply_turn_started = True
                    session.turn_start_ambiguous = False
            if not session.failure_pending_completion:
                session.status = "running"
        elif method == "turn/completed":
            failure_pending_completion = session.failure_pending_completion
            if isinstance(turn, dict):
                turn_id = turn.get("id")
                if isinstance(turn_id, str):
                    session.turn_id = turn_id
                if not failure_pending_completion:
                    session.status = _public_turn_status(turn.get("status"))
                turn_error = turn.get("error")
                if turn_error is not None or not failure_pending_completion:
                    session.error = turn_error
                self._consume_items(session, turn.get("items"))
                session.turn_start_ambiguous = False
            else:
                session.status = "failed"
                session.error = "turn/completed did not contain turn"
                session.turn_start_ambiguous = False
            session.turn_completed = True
            session.failure_pending_completion = False
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
        elif method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str):
                item_id = params.get("itemId")
                if not isinstance(item_id, str) or not item_id:
                    current = session.current_item
                    item_id = current.get("id") if isinstance(current, dict) else None
                item_id = item_id if isinstance(item_id, str) and item_id else "__current__"
                session.progress_items[item_id] = _append_bounded(session.progress_items.get(item_id, ""), delta)
                session.commentary = session.progress_items[item_id]
                session.set_progress(session.commentary)
        elif method in {"item/fileChange/outputDelta", "item/fileChange/patchUpdated"}:
            session.diff_changed = True
        session.touch()
        await self._notify_waiters()

    # Codex CLI 0.148.0のServerRequest schemaで確認した全server-initiated request:
    # item/commandExecution/requestApproval・item/fileChange/requestApproval・
    # item/tool/requestUserInput・mcpServer/elicitation/request・
    # item/permissions/requestApproval・item/tool/call・
    # account/chatgptAuthTokens/refresh・attestation/generate・
    # applyPatchApproval・execCommandApproval。
    # 承認用の公開MCP toolは設けず、readerで必ず応答して非対話要求をfailedへ記録する。
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
            # 接続上のactive turnをfailedへ遷移させ、turn完了通知を待つ。
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
        if session is not None:
            sessions = [session] if not session.terminal else []
        else:
            sessions = [item for item in self.sessions.values() if not item.terminal]
        interrupt_targets: list[tuple[str, str]] = []
        for active in sessions:
            has_active_turn = bool(active.turn_id)
            active.status = "failed"
            active.error = {"message": f"Codex requested interactive server input: {method}"}
            active.protocol_warnings.append(f"unsupported server request: {method}")
            active.turn_completed = not has_active_turn
            active.turn_start_ambiguous = False
            active.failure_pending_completion = has_active_turn
            if has_active_turn and not active.interrupt_requested:
                interrupt_targets.append((active.session_id, active.turn_id))
            active.interrupt_requested = has_active_turn
            active.touch()
        await self._notify_waiters()
        # reader task cannot await a request response for turn/interrupt: that would
        # deadlock the same reader. Schedule it after publishing failed and waking waiters.
        for session_id, turn_id in interrupt_targets:
            if self.client is not None:
                self._schedule(self._interrupt(session_id, turn_id))

    async def _interrupt(self, session_id: str, turn_id: str) -> None:
        client = self.client
        if client is None or client.closed:
            return
        try:
            await client.request("turn/interrupt", {"threadId": session_id, "turnId": turn_id})
        except JsonRpcResponseError as exc:
            await self._handle_interrupt_response_error(session_id, turn_id, exc)
        except Exception as exc:
            await self._handle_client_failure(exc)

    async def _handle_interrupt_response_error(self, session_id: str, turn_id: str, error: JsonRpcResponseError) -> None:
        """turn/interruptのJSON-RPC errorを対象turnだけへ記録する。"""
        session = self.sessions.get(session_id)
        if session is None or session.turn_completed or session.turn_id != turn_id:
            return
        session.error = {"message": str(error) or error.__class__.__name__}
        session.protocol_warnings.append(f"turn/interrupt failed: {error}")
        session.touch()
        await self._notify_waiters()

    async def _handle_client_failure(self, error: BaseException) -> None:
        """reader異常時に全active turnをfailedへ遷移させて待機者を起こす。"""
        detail = str(error) or error.__class__.__name__
        changed = False
        for session in self.sessions.values():
            if not session.terminal or session.failure_pending_completion:
                session.status = "failed"
                if not session.failure_pending_completion:
                    session.error = {"message": f"Codex App Server stopped: {detail}"}
                session.interrupt_requested = False
                session.turn_start_ambiguous = False
                session.turn_completed = True
                session.failure_pending_completion = False
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
    def _notification_turn_id(params: dict[str, Any]) -> str | None:
        """通知本文に含まれるturn IDを取得する。"""
        turn_id = _turn_id_from(params)
        if turn_id is not None:
            return turn_id
        turn = params.get("turn")
        if isinstance(turn, dict):
            turn_id = turn.get("id")
            if isinstance(turn_id, str) and turn_id:
                return turn_id
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
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id:
                    item_id = "__current__"
                session.progress_items[item_id] = text
                session.set_progress(text)
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


def _public_turn_status(status: Any) -> str:
    if status == "inProgress":
        return "running"
    if status in TERMINAL_STATUSES:
        return status
    return "failed"
