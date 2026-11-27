"""Claude所有タスクへの要求とコンテキスト使用量の観測。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
import asyncio
import dataclasses
import logging
from typing import Any, Literal

from agent_toolkit._agents_server import context_usage, resume_waits, service_turns, turn_state
from agent_toolkit._agents_server.result_projection import CLAUDE_CHILD_COMPLETION_DELIVERY_ERROR
from agent_toolkit._agents_server.session_errors import SessionOwnerGoneError
from agent_toolkit._agents_server.state import ResumePrompt, SessionState
from agent_toolkit._common.diagnostic_redaction import redact_secrets

_LOG = logging.getLogger("agent-toolkit.agents-server.claude")

_DeliveryResult = tuple[str, dict[str, Any] | None]


type ChildWriteOutcome = Literal["not_sent", "unconfirmed", "sent"]
type ChildWriteResult = tuple[ChildWriteOutcome, str | None]


def child_delivery_error(error: BaseException) -> str:
    return redact_secrets(f"{type(error).__name__}: {error}")


@dataclasses.dataclass
class ChildCompletionDelivery:
    held: resume_waits.HeldTurn
    children: dict[str, str | None]
    accepted: bool = False
    revoked: bool = False
    reply_key: tuple[int, str] | None = None
    outcome: ChildWriteOutcome = "not_sent"
    cause: str | None = None
    settled: bool = False

    def available(self, session: SessionState) -> bool:
        return (
            not self.revoked
            and not session.interrupt_requested
            and session.turn_seq == self.held.turn_seq
            and session.pending_result is self.held.hold.result
            and all(self.matches(session, child) for child in self.children)
        )

    def matches(self, session: SessionState, child: str) -> bool:
        return (
            child in session.terminal_child_session_ids
            and session.claude_child_completion_tokens.get(child) == self.children[child]
        )

    def accept(self, session: SessionState) -> bool:
        if not self.available(session):
            return False
        self.accepted = True
        return True

    def owns_reply(self, session: SessionState) -> bool:
        return self.reply_key == (session.turn_seq, session.started_at) and session.pending_result is None

    def consume(self, session: SessionState) -> bool:
        self.settled = True
        if not (self.owns_reply(session) or self.available(session)):
            return False
        session.discard_child_completions(child for child in self.children if self.matches(session, child))
        session.claude_child_resume_retry_turn = None
        return True

    def fail(self, session: SessionState) -> None:
        unconfirmed = self.outcome == "unconfirmed"
        reply_progressed = self.owns_reply(session) and (
            session.turn_start is turn_state.TurnStart.STARTED or session.turn_completed or session.terminal
        )
        if not self.consume(session) or (unconfirmed and reply_progressed):
            return
        session.clear_hold()
        session.status = "failed"
        session.agent_message = self.held.hold.result["agent_message"]
        if unconfirmed:
            session.record_turn_ambiguous()
        session.error = {
            "type": CLAUDE_CHILD_COMPLETION_DELIVERY_ERROR,
            "message": (
                "子の完了通知の送達を確認できません" if unconfirmed else "子の完了通知は未送信です。自動で再開できません"
            ),
            "delivery": "reply_ambiguous" if unconfirmed else "reply_failed",
            "unobservedSessions": sorted(self.children),
            "nextAction": (
                f"`atk agents show {session.session_id}`で委譲先の現在のターンを照会し、"
                f"`atk agents logs {session.session_id} --verbose`で子の完了通知とその後の応答を確認する。"
                "再開済みならその結果を回収する。未再開と確認できた場合に限り残作業を続ける。"
                "記録がないか不十分で判別できなければ同じ通知を再送しない"
                if unconfirmed
                else "子の結果を回収し、残作業を続ける"
            ),
        }
        if self.cause is not None:
            session.error["cause"] = self.cause
        session.record_turn_completed()
        session.touch()

    def retry_or_fail(self, session: SessionState) -> None:
        if session.claude_child_resume_retry_turn == self.held.turn_seq:
            self.fail(session)
        else:
            session.claude_child_resume_retry_turn = self.held.turn_seq
            self.settled = True
            session.touch()


class ChildCompletionPrompt(ResumePrompt):
    def __init__(self, prompt: str, delivery: ChildCompletionDelivery) -> None:
        super().__init__(prompt)
        self.child_delivery = delivery


_CommandKind = Literal["prompt", "interrupt", "plugin_reload", "effort"]
_Command = tuple[_CommandKind, str, asyncio.Future[_DeliveryResult], ChildCompletionDelivery | None]


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
        self, kind: _CommandKind, prompt: str, *, child_delivery: ChildCompletionDelivery | None = None
    ) -> asyncio.Future[_DeliveryResult]:
        if self._closed:
            raise SessionOwnerGoneError("the Claude session owner task has ended")
        future: asyncio.Future[_DeliveryResult] = asyncio.get_running_loop().create_future()
        gate = service_turns.current()
        self.tickets[future] = gate.capture() if gate else None
        future.add_done_callback(lambda done: self.tickets.pop(done, None))
        self._queue.put_nowait((kind, prompt, future, child_delivery))
        return future

    async def get(self) -> _Command:
        return await self._queue.get()

    def close(self, error: BaseException | None = None) -> None:
        self._closed = True
        while True:
            try:
                _kind, _prompt, future, _delivery = self._queue.get_nowait()
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
