"""Claude所有タスクへの要求とコンテキスト使用量の観測。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
import asyncio
import logging
from typing import Any, Literal

from agent_toolkit._agents_server import context_usage, service_turns
from agent_toolkit._agents_server.session_errors import SessionOwnerGoneError
from agent_toolkit._agents_server.state import SessionState

_LOG = logging.getLogger("agent-toolkit.agents-server.claude")

_DeliveryResult = tuple[str, dict[str, Any] | None]


_Command = tuple[Literal["prompt", "interrupt", "compact", "plugin_reload", "effort"], str, asyncio.Future[_DeliveryResult]]


class _CommandChannel:
    """1つのClaude sessionへの継続要求と中断要求を所有タスクへ渡す。

    受理した要求の応答futureは、所有タスクによる処理か`close`のいずれかで必ず解決する。
    所有タスクを終了させる処理が増えても、`close`の1箇所で受理済みの要求を解決できる。
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[_Command] = asyncio.Queue()
        self._closed = False
        self.tickets: dict[asyncio.Future[_DeliveryResult], service_turns.Ticket | None] = {}

    def send(
        self, kind: Literal["prompt", "interrupt", "compact", "plugin_reload", "effort"], prompt: str
    ) -> asyncio.Future[_DeliveryResult]:
        if self._closed:
            raise SessionOwnerGoneError("the Claude session owner task has ended")
        future: asyncio.Future[_DeliveryResult] = asyncio.get_running_loop().create_future()
        gate = service_turns.current()
        self.tickets[future] = gate.capture() if gate else None
        future.add_done_callback(lambda done: self.tickets.pop(done, None))
        self._queue.put_nowait((kind, prompt, future))
        return future

    async def get(self) -> _Command:
        return await self._queue.get()

    def close(self, error: BaseException | None = None) -> None:
        self._closed = True
        while True:
            try:
                _kind, _prompt, future = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not future.done():
                future.set_exception(error or SessionOwnerGoneError("the Claude session owner task has ended"))


class _ContextUsageObserver:
    def __init__(self, client: Any) -> None:
        self._client = client
        self._revision = 0
        self._task: asyncio.Task[None] | None = None

    def request(self, session: SessionState) -> None:
        self._revision += 1
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._observe(session))

    async def _observe(self, session: SessionState) -> None:
        while True:
            revision = self._revision
            turn_seq = session.turn_seq
            try:
                usage = await self._client.get_context_usage()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # 旧CLIの未対応や照会失敗で、起動とモデル出力の処理を失敗させない。
                _LOG.debug("context_usage_unavailable session_id=%s error=%s", session.session_id, exc)
                usage = {}
            if revision != self._revision:
                continue
            if turn_seq == session.turn_seq:
                usage = usage if isinstance(usage, dict) else {}
                context_usage.record_context_usage(session, usage.get("totalTokens"), usage.get("rawMaxTokens"))
            return

    async def close(self) -> None:
        if self._task is not None:
            if not self._task.done():
                self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
