"""失敗・保留・過負荷を再現する共有backend。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
from typing import Any

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import resume_waits, session_errors, state
from agent_toolkit._testing.agents_server_support_common import FakeBackend, _complete, _set_wait_timeout


class UnavailableStartBackend(FakeBackend):
    """起動応答の前にengineの可用性で終了する偽バックエンド。"""

    def __init__(
        self,
        sessions: dict[str, state.SessionState],
        engine: str,
        error: Any = None,
    ) -> None:
        super().__init__(sessions, engine)
        self.error = error if error is not None else {"message": "usage limit", "codexErrorInfo": "usageLimitExceeded"}

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        _complete(session, message="", error=self.error)
        return session


class DelayedUnavailableBackend(FakeBackend):
    """起動応答を返した後にengineの可用性で終了する偽バックエンド。"""

    def __init__(
        self,
        sessions: dict[str, state.SessionState],
        engine: str,
        condition: asyncio.Condition,
        error: Any = None,
        failure_release: asyncio.Event | None = None,
    ) -> None:
        super().__init__(sessions, engine)
        self._condition = condition
        self._failure_release = failure_release
        self.error = error if error is not None else {"message": "usage limit", "codexErrorInfo": "usageLimitExceeded"}
        self.pending: list[asyncio.Task[None]] = []

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        self.pending.append(asyncio.create_task(self._fail_after_response(session)))
        return session

    async def _fail_after_response(self, session: state.SessionState) -> None:
        if self._failure_release is None:
            await asyncio.sleep(0)
        else:
            await self._failure_release.wait()
        _complete(session, message="", error=self.error)
        async with self._condition:
            self._condition.notify_all()


class OutputObservedBackend(FakeBackend):
    """起動応答を返した後にモデル出力を受信し、終了しないまま実行を続ける偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], engine: str, condition: asyncio.Condition) -> None:
        super().__init__(sessions, engine)
        self._condition = condition
        self.pending: list[asyncio.Task[None]] = []

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        self.pending.append(asyncio.create_task(self._observe_output(session)))
        return session

    async def _observe_output(self, session: state.SessionState) -> None:
        await asyncio.sleep(0)
        session.model_output_observed = True
        async with self._condition:
            self._condition.notify_all()


class BlockingInterruptBackend(FakeBackend):
    """中断要求の配送を解除イベントまで停止する偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], engine: str) -> None:
        super().__init__(sessions, engine)
        self.interrupt_started = asyncio.Event()
        self.release_interrupt = asyncio.Event()

    async def interrupt(self, session: state.SessionState) -> None:
        del session
        self.interrupt_calls += 1
        self.interrupt_started.set()
        await self.release_interrupt.wait()


class BlockingResumeBackend(FakeBackend):
    """保存済みsessionの再開を解除イベントまで停止する偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], engine: str) -> None:
        super().__init__(sessions, engine)
        self.resume_started = asyncio.Event()
        self.resume_finished = asyncio.Event()
        self.release_resume = asyncio.Event()

    async def resume(
        self,
        session_id: str,
        prompt: state.ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
        **kwargs: Any,
    ) -> state.SessionState:
        self.resume_started.set()
        try:
            await self.release_resume.wait()
            return await super().resume(session_id, prompt, cwd, model, effort, **kwargs)
        finally:
            self.resume_finished.set()


class FailedResumeBackend(FakeBackend):
    """再開turnを結果本文付きの失敗として確定する偽バックエンド。"""

    async def resume(
        self,
        session_id: str,
        prompt: state.ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
        **kwargs: Any,
    ) -> state.SessionState:
        session = await super().resume(session_id, prompt, cwd, model, effort, **kwargs)
        _complete(session, message="reply失敗結果", error={"message": "reply failed"})
        return session


class ConcurrentOwnerGoneBackend(FakeBackend):
    """2件の継続要求の両方が配送を始めた時点で、両方へ所有主体の終了（`SessionOwnerGoneError`）を送出する偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], engine: str) -> None:
        super().__init__(sessions, engine)
        self.owner_gone_session: state.SessionState | None = None
        self.send_started = 0
        self.both_started = asyncio.Event()

    async def send_message(self, session: state.SessionState, prompt: str) -> dict[str, Any]:
        if session is self.owner_gone_session:
            self.send_started += 1
            if self.send_started == 2:
                self.both_started.set()
            await self.both_started.wait()
            raise session_errors.SessionOwnerGoneError("owner gone")
        return await super().send_message(session, prompt)


class _RegistryBackend(FakeBackend):
    """作成したsessionを登録簿へ公開するバックエンド。委譲元の記録を登録簿で観測するテストが使う。"""

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.publish_registry = True
        return session


_LAUNCHER_ENVIRONMENT_NAMES = (
    "CLAUDE_CODE_SESSION_ID",
    "AGENT_TOOLKIT_OWNER_SESSION",
    "AGENT_TOOLKIT_DELEGATED_SESSION",
    "AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND",
    "AGENT_TOOLKIT_STATUS_HOST_SESSION",
    "CODEX_THREAD_ID",
)


class ThreadReadBackend(FakeBackend):
    """Codex App Serverの`thread/read`へ固定のturn一覧を返すか、照会の失敗を返すバックエンド。"""

    def __init__(
        self, sessions: dict[str, state.SessionState], turns: list[tuple[str, str]] | None, error: Exception | None = None
    ) -> None:
        super().__init__(sessions, "codex")
        self.turns = turns
        self.error = error
        self.read_calls: list[str] = []

    async def read_thread_turns(self, session_id: str) -> list[tuple[str, str]]:
        self.read_calls.append(session_id)
        if self.error is not None:
            raise self.error
        assert self.turns is not None
        return self.turns


_OVERLOAD_ERROR = {
    "message": "Selected model is at capacity. Please try a different model.",
    "codexErrorInfo": "serverOverloaded",
}


async def _wait_until_terminal(manager: server_manager.AgentsServerManager, attempts: int = 50) -> dict[str, Any]:
    """委譲元と同じく、`running`が返る間は`wait`を再発行して終了時の結果を受け取る。"""
    _set_wait_timeout(manager, 0.2)
    for _ in range(attempts):
        response = await manager.wait()
        if response.get("status") in state.TERMINAL_STATUSES:
            return response
    raise AssertionError("終了時の結果を受け取れない")


class OverloadingBackend(FakeBackend):
    """起動応答の後のturnをCodexの過負荷で終了させる偽バックエンド。

    Codex backendの`turn/completed`の処理（可用性確認後の過負荷の保留）を、共有状態の同じ関数で再現する。
    `overloads`回まで過負荷で終え、その後の継続turnは`final_message`で正常に終える。
    """

    def __init__(
        self,
        sessions: dict[str, state.SessionState],
        condition: asyncio.Condition,
        overloads: int,
        final_message: str = "継続後の結果",
    ) -> None:
        super().__init__(sessions, "codex")
        self._condition = condition
        self._remaining = overloads
        self._final_message = final_message
        self.pending: list[asyncio.Task[None]] = []
        self.finish_release = asyncio.Event()

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.model = session.model or "gpt-6.1-sol"
        session.effort = session.effort or "high"
        self.pending.append(asyncio.create_task(self._finish_turn(session, initial=True)))
        return session

    async def send_message(self, session: state.SessionState, prompt: str) -> dict[str, Any]:
        result = await super().send_message(session, prompt)
        self.pending.append(asyncio.create_task(self._finish_turn(session, initial=False)))
        return result

    async def _finish_turn(self, session: state.SessionState, *, initial: bool) -> None:
        if initial:
            await asyncio.wait_for(self.finish_release.wait(), timeout=5)
        else:
            await asyncio.sleep(0)
        if self._remaining > 0:
            self._remaining -= 1
            session.status = "failed"
            session.error = dict(_OVERLOAD_ERROR)
            session.record_turn_completed()
            if (session.availability_checked or session.turn_seq > 1) and resume_waits.begin_overload_resume_wait(
                session, {"status": "failed", "agent_message": "", "error": session.error}
            ):
                session.status = "running"
            else:
                session.touch()
        else:
            _complete(session, message=self._final_message)
        async with self._condition:
            self._condition.notify_all()
