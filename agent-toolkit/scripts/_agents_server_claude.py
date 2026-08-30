"""Claude Agent SDKを1セッション1長命タスクで所有するバックエンド。

`ClaudeSDKClient`の接続、メッセージ消費、継続入力、切断は同じセッションタスクが担当する。
MCP層はキューへ入力を渡し、クライアントへ直接アクセスしない。
"""

# Claude Agent SDKはCodexだけを使う経路で読み込まない。
# pylint: disable=import-outside-toplevel

from __future__ import annotations

import asyncio
import contextlib
import logging
import pathlib
from collections.abc import Callable
from typing import Any, Literal, cast

from _agents_server_state import (
    ResumePrompt,
    SessionOwnerGoneError,
    SessionState,
    _begin_reply,
)

_LOG = logging.getLogger("agent-toolkit.agents-server.claude")
_EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]
_DeliveryResult = tuple[str, dict[str, Any] | None]
_Command = tuple[Literal["prompt", "interrupt"], str, asyncio.Future[_DeliveryResult]]


class _CommandChannel:
    """1つのClaude sessionへの継続要求と中断要求を所有タスクへ渡す。

    受理した要求の応答futureは、所有タスクによる処理か`close`のいずれかで必ず解決する。
    所有タスクの終了経路が増えても、`close`の1箇所で受理済みの要求を解決できる。
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[_Command] = asyncio.Queue()
        self._closed = False

    def send(self, kind: Literal["prompt", "interrupt"], prompt: str) -> asyncio.Future[_DeliveryResult]:
        """要求を受理して応答futureを返す。閉鎖後は受理せず例外を送出する。"""
        if self._closed:
            raise SessionOwnerGoneError("the Claude session owner task has ended")
        future: asyncio.Future[_DeliveryResult] = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((kind, prompt, future))
        return future

    async def get(self) -> _Command:
        """所有タスクが次の要求を取り出す。"""
        return await self._queue.get()

    def close(self) -> None:
        """以降の受理を止め、未処理の要求を所有タスクの終了として解決する。"""
        self._closed = True
        while True:
            try:
                _kind, _prompt, future = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not future.done():
                future.set_exception(SessionOwnerGoneError("the Claude session owner task has ended"))


def _build_options(cwd: str, model: str | None, effort: str | None, session_id: str | None = None) -> Any:
    """Claude Code既定のシステム指示を有効にしたSDKオプションを組む。"""
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        cwd=cwd,
        model=model,
        effort=cast(_EffortLevel, effort),
        resume=session_id,
        permission_mode="bypassPermissions",
        setting_sources=["user", "project"],
        system_prompt={"type": "preset", "preset": "claude_code"},
    )


def check_dependencies() -> None:
    """Claude Agent SDKの依存を読み込み、optionsを構築する。"""
    _build_options(str(pathlib.Path.cwd()), None, None)


def _message_name(message: Any) -> str:
    return message.__class__.__name__


def _assistant_text(message: Any) -> str:
    blocks = getattr(message, "content", ())
    return "\n".join(text for block in blocks if (text := getattr(block, "text", None)) is not None and isinstance(text, str))


class ClaudeServerManager:
    """Claudeセッションの所有タスクと結果メタデータを管理する。"""

    def __init__(
        self,
        sessions: dict[str, SessionState] | None = None,
        condition: asyncio.Condition | None = None,
        client_factory: Callable[[Any], Any] | None = None,
        expire_session: Callable[[str], None] | None = None,
    ) -> None:
        self.sessions = sessions if sessions is not None else {}
        self._condition = condition if condition is not None else asyncio.Condition()
        self._client_factory = client_factory or self._default_client_factory
        self._expire_session = expire_session or self._expire_local_session
        self._tasks: set[asyncio.Task[Any]] = set()
        self._task_sessions: dict[asyncio.Task[Any], str] = {}
        self._channels: dict[str, _CommandChannel] = {}

    def _expire_local_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    @staticmethod
    def _default_client_factory(options: Any) -> Any:
        from claude_agent_sdk import ClaudeSDKClient

        return ClaudeSDKClient(options)

    async def start(
        self,
        prompt: str,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
    ) -> SessionState:
        return await self._start_owned_task(prompt, cwd, model, effort, session_id=None)

    async def resume(
        self,
        session_id: str,
        prompt: ResumePrompt,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
    ) -> SessionState:
        """保存済みClaude sessionを新しい所有タスクで再開する。"""
        await self._stop_owned_task(session_id)
        return await self._start_owned_task(prompt, cwd, model, effort, session_id=session_id)

    async def _stop_owned_task(self, session_id: str) -> None:
        """同じsession IDを所有する旧タスクを終了し、再開時のキュー競合を防ぐ。"""
        task = next((item for item in self._tasks if self._task_sessions.get(item) == session_id), None)
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _start_owned_task(
        self,
        prompt: str | ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
        *,
        session_id: str | None,
    ) -> SessionState:
        """新規又は保存済みsessionを所有する長命タスクを開始する。"""
        options = _build_options(cwd, model, effort, session_id)
        loop = asyncio.get_running_loop()
        initialized: asyncio.Future[SessionState] = loop.create_future()
        task: asyncio.Task[Any] = asyncio.create_task(
            self._run(prompt, cwd, model, effort, options, initialized, expected_session_id=session_id)
        )
        self._tasks.add(task)
        task.add_done_callback(self._forget_task)
        try:
            return await initialized
        except Exception:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def send_message(self, session: SessionState, prompt: str) -> dict[str, Any]:
        channel = self._channels.get(session.session_id)
        if channel is None:
            raise SessionOwnerGoneError("the Claude session owner task has ended")
        async with session.turn_control_lock:
            if not session.terminal and session.interrupt_requested:
                raise ValueError("the active Claude turn is being interrupted")
            future = channel.send("prompt", prompt)
            actual_delivery, previous_result = await future
        result: dict[str, Any] = {"delivery": actual_delivery}
        if actual_delivery in {"reply_started", "reply_failed", "reply_ambiguous"}:
            result["previous_result"] = previous_result
        return result

    async def interrupt(self, session: SessionState) -> None:
        """公開killから所有タスクへ中断要求を送り、受理を待つ。"""
        channel = self._channels.get(session.session_id)
        if channel is None:
            raise SessionOwnerGoneError("the Claude session owner task has ended")
        if session.terminal:
            return
        session.interrupt_requested = True
        session.touch()
        try:
            future = channel.send("interrupt", "")
            delivery, _ = await future
        except Exception:
            session.interrupt_requested = False
            session.touch()
            await self._notify_waiters()
            raise
        if delivery != "interrupt_accepted":
            session.interrupt_requested = False
            session.touch()
            await self._notify_waiters()
            raise RuntimeError(f"unexpected Claude interrupt delivery: {delivery}")
        await self._notify_waiters()

    def _forget_task(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        self._task_sessions.pop(task, None)

    async def _run(
        self,
        prompt: str | ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
        options: Any,
        initialized: asyncio.Future[SessionState],
        *,
        expected_session_id: str | None,
    ) -> None:
        client: Any = None
        session: SessionState | None = None
        channel: _CommandChannel | None = None
        iterator: Any = None
        message_task: asyncio.Task[Any] | None = None
        command_task: asyncio.Task[Any] | None = None
        retrieved: _Command | None = None
        active_future: asyncio.Future[_DeliveryResult] | None = None
        try:
            client = self._client_factory(options)
            await client.connect()
            if isinstance(prompt, ResumePrompt):
                await prompt.deliver(client.query)
            else:
                await client.query(prompt)
            iterator = aiter(client.receive_messages())
            while True:
                timeout: float | None = None
                if session is not None and session.retention_deadline is not None:
                    timeout = session.retention_deadline - asyncio.get_running_loop().time()
                    if timeout <= 0:
                        self._expire_session(session.session_id)
                        break

                if iterator is not None and message_task is None:
                    message_task = asyncio.create_task(anext(iterator))
                if channel is not None and command_task is None:
                    command_task = asyncio.create_task(channel.get())
                pending = {task for task in (message_task, command_task) if task is not None}
                if not pending:
                    raise RuntimeError("Claude session has no message stream or command queue")
                done, _ = await asyncio.wait(pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    if session is not None:
                        self._expire_session(session.session_id)
                    break

                if command_task is not None and command_task in done:
                    retrieved = command_task.result()
                    command_task = None

                if message_task in done:
                    completed_message_task = message_task
                    message_task = None
                    try:
                        message = completed_message_task.result()
                    except StopAsyncIteration:
                        if session is not None and session.result_available:
                            iterator = None
                        else:
                            raise RuntimeError("Claude Agent SDK message stream ended before ResultMessage") from None
                    else:
                        name = _message_name(message)
                        if name == "SystemMessage" and getattr(message, "subtype", None) == "init":
                            data = getattr(message, "data", {})
                            session_id = data.get("session_id") if isinstance(data, dict) else None
                            if not isinstance(session_id, str) or not session_id:
                                raise RuntimeError("Claude init message did not contain session_id")
                            if expected_session_id is not None and session_id != expected_session_id:
                                raise RuntimeError("Claude resume returned an unexpected session_id")
                            session = SessionState(
                                session_id=session_id,
                                cwd=cwd,
                                model=model,
                                effort=effort,
                                engine="claude",
                            )
                            self.sessions[session_id] = session
                            channel = _CommandChannel()
                            self._channels[session_id] = channel
                            current_task = asyncio.current_task()
                            if current_task is not None:
                                self._task_sessions[current_task] = session_id
                            if not initialized.done():
                                initialized.set_result(session)
                        elif name == "AssistantMessage" and session is not None:
                            text = _assistant_text(message)
                            session.agent_message = text
                            session.set_progress(text)
                            await self._notify_waiters()
                        elif name == "ResultMessage" and session is not None:
                            self._record_result(session, message)
                            iterator = None
                            await self._notify_waiters()

                if retrieved is not None:
                    command = retrieved
                    retrieved = None
                    active_future = command[2]
                    iterator = await self._handle_command(client, session, command, iterator)
                    active_future = None
        except Exception as exc:
            if session is None:
                if not initialized.done():
                    initialized.set_exception(exc)
            else:
                self._record_failure(session, exc)
                await self._notify_waiters()
        finally:
            for task in (message_task, command_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in (message_task, command_task) if task is not None), return_exceptions=True)
            if command_task is not None and not command_task.cancelled() and command_task.exception() is None:
                retrieved = command_task.result()
            if retrieved is not None and not retrieved[2].done():
                retrieved[2].set_exception(SessionOwnerGoneError("the Claude session owner task has ended"))
            if active_future is not None and not active_future.done():
                active_future.set_exception(SessionOwnerGoneError("the Claude session owner task has ended"))
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.disconnect()
            if session is not None:
                self._channels.pop(session.session_id, None)
            if channel is not None:
                channel.close()

    async def _handle_command(
        self,
        client: Any,
        session: SessionState | None,
        command: _Command,
        iterator: Any,
    ) -> Any:
        command_kind, prompt, future = command
        if session is None:
            if not future.done():
                future.set_exception(ValueError("Claude session initialization is incomplete"))
            return iterator
        if command_kind == "interrupt":
            if session.terminal:
                if not future.done():
                    future.set_result(("interrupt_accepted", None))
                return iterator
            try:
                await client.interrupt()
            except Exception as exc:
                session.interrupt_requested = False
                session.touch()
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(("interrupt_accepted", None))
            await self._notify_waiters()
            return iterator
        if future.cancelled():
            return iterator
        kind = "reply" if session.terminal else "steer"
        previous_result = session.previous_result() if kind == "reply" else None
        try:
            if kind == "reply":
                _begin_reply(session)
            await client.query(prompt)
        except Exception as exc:
            if kind == "reply":
                self._record_failure(session, exc)
                if not future.done():
                    future.set_result(("reply_failed", previous_result))
            elif not future.done():
                future.set_exception(exc)
            await self._notify_waiters()
            return None if session.result_available else iterator
        if kind == "reply":
            if not future.done():
                future.set_result(("reply_started", previous_result))
        elif not future.done():
            future.set_result(("steered", None))
        session.retention_deadline = None
        session.touch()
        await self._notify_waiters()
        return aiter(client.receive_messages()) if kind == "reply" else iterator

    @staticmethod
    def _record_result(session: SessionState, message: Any) -> None:
        terminal_reason = getattr(message, "terminal_reason", None)
        if terminal_reason in {"aborted_streaming", "aborted_tools"}:
            session.status = "interrupted"
        else:
            session.status = "failed" if bool(getattr(message, "is_error", False)) else "completed"
        result = getattr(message, "result", None)
        session.agent_message = result if isinstance(result, str) else session.agent_message
        errors = getattr(message, "errors", None)
        if session.status == "failed":
            if isinstance(errors, list) and errors:
                session.error = {"message": "; ".join(str(item) for item in errors)}
            elif isinstance(result, str) and result:
                session.error = {"message": result}
            else:
                session.error = {"message": "Claude Agent SDK returned an error"}
        session.turn_completed = True
        session.turn_start_ambiguous = False
        session.touch()

    @staticmethod
    def _record_failure(session: SessionState, error: BaseException) -> None:
        session.status = "failed"
        session.error = {"message": str(error) or error.__class__.__name__}
        session.turn_completed = True
        session.turn_start_ambiguous = False
        session.touch()

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
