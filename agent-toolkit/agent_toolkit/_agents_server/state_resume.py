from __future__ import annotations

import asyncio
import dataclasses
import datetime
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, Literal

from agent_toolkit._agents_server import turn_state
from agent_toolkit._agents_server.input_validation import validate_prompt
from agent_toolkit._agents_server.logging_config import redact_diagnostic_values
from agent_toolkit._agents_server.result_projection import nonempty_error, with_result_next_action
from agent_toolkit._common import session_registry, startup_features

if TYPE_CHECKING:
    from agent_toolkit._agents_server.state import LaunchKind, ModelCandidate, SessionState

ResumeDelivery = Literal["reply_started", "reply_failed", "reply_ambiguous"]


def utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


class ResumePrompt:
    def __init__(self, prompt: str) -> None:
        validate_prompt(prompt)
        self._next_ticket = 1
        self._current: tuple[int, str, asyncio.Event] | None = (
            self._next_ticket,
            prompt,
            asyncio.Event(),
        )
        self._changed = asyncio.Event()
        self._closed = False
        self._delivery: ResumeDelivery | None = None
        self.context = ""

    @property
    def delivery(self) -> ResumeDelivery:
        """要求ごとに確定した配送結果を返す。未配送で終了した要求は開始失敗とする。"""
        return self._delivery or "reply_failed"

    def record_delivery(self, delivery: ResumeDelivery) -> None:
        if self._delivery is not None and self._delivery != delivery:
            raise RuntimeError("再開要求の配送結果は確定済みです")
        self._delivery = delivery

    @property
    def initial_ticket(self) -> int:
        return 1

    def submit_or_observe(self, prompt: str) -> tuple[int | None, asyncio.Event | None]:
        validate_prompt(prompt)
        if self._closed:
            return None, None
        if self._current is not None:
            return None, self._changed
        self._next_ticket += 1
        ticket = self._next_ticket
        self._current = (ticket, prompt, asyncio.Event())
        self._signal_change()
        return ticket, None

    def cancel(self, ticket: int) -> None:
        current = self._current
        if current is None or current[0] != ticket:
            return
        self._current = None
        current[2].set()
        self._signal_change()

    def close(self) -> None:
        self._closed = True
        current = self._current
        self._current = None
        if current is not None:
            current[2].set()
        self._signal_change()

    async def deliver(self, sender: Callable[[str], Coroutine[Any, Any, None]]) -> None:
        while True:
            current = self._current
            if current is None:
                if self._closed:
                    raise asyncio.CancelledError
                changed = self._changed
                await changed.wait()
                continue
            ticket, prompt, cancelled = current
            delivery_task = asyncio.create_task(sender(self.context + "\n\n" + prompt if self.context else prompt))
            cancellation_task = asyncio.create_task(cancelled.wait())
            try:
                done, _ = await asyncio.wait(
                    {delivery_task, cancellation_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except BaseException:
                delivery_task.cancel()
                cancellation_task.cancel()
                await asyncio.gather(delivery_task, cancellation_task, return_exceptions=True)
                raise
            if delivery_task in done:
                cancellation_task.cancel()
                await asyncio.gather(cancellation_task, return_exceptions=True)
                await delivery_task
                self.record_delivery("reply_started")
                if self._current is not None and self._current[0] == ticket:
                    self._current = None
                self._closed = True
                self._signal_change()
                return
            delivery_task.cancel()
            await asyncio.gather(delivery_task, return_exceptions=True)

    def _signal_change(self) -> None:
        changed = self._changed
        self._changed = asyncio.Event()
        changed.set()


class PreviousResult(dict[str, Any]):
    def __init__(self, payload: dict[str, Any], origin: SessionResumeState) -> None:
        super().__init__(payload)
        self.origin = origin

    @property
    def end(self) -> tuple[int, str] | None:
        return (self.origin.turn_seq, self.origin.finalized_at) if self.origin.finalized_at is not None else None


@dataclasses.dataclass(frozen=True)
class SessionResumeState:
    session_id: str
    cwd: str
    model: str | None
    effort: str | None
    engine: str
    fast_mode: bool | None = None
    codex_model_provider: str | None = None
    codex_subscription_provider: str | None = None
    codex_state_provider: str | None = None
    claude_api_env_file: str | None = None
    claude_connection: str | None = None
    claude_role: str | None = None
    model_type: str | None = None
    launch_kind: LaunchKind = "delegate"
    startup_profile: startup_features.StartupProfile | None = None

    label: str = ""
    prompt: str = ""
    followup_prompts: tuple[str, ...] = ()
    final_responses: tuple[tuple[int, str], ...] = ()
    terminal_error: str | None = None
    effort_history: tuple[tuple[str, str, str, str], ...] = ()
    effort_levels: tuple[str, ...] = ()

    created_at: str | None = None
    started_at: str | None = dataclasses.field(default_factory=utc_now)
    updated_at: str | None = dataclasses.field(default_factory=utc_now)
    output_updated_at: str | None = None
    context_tokens: int | float | None = None
    context_window: int | float | None = None
    turn_seq: int = 0
    excluded_candidates: frozenset[ModelCandidate] = dataclasses.field(default_factory=frozenset)
    status: str = ""
    agent_message: str = ""
    last_action: str = ""
    error: Any = None
    finalized_at: str | None = None
    result_delivered: bool = False
    retention_deadline: float | None = None
    result_contract_recovery_consumed: bool = False

    @classmethod
    def from_session(cls, session: SessionState) -> SessionResumeState:
        return cls(
            session_id=session.session_id,
            cwd=session.cwd,
            model_type=session.model_type,
            launch_kind=session.launch_kind,
            startup_profile=session.startup_profile,
            **session_registry.LaunchInfo.of(session).as_kwargs(),
            started_at=session.started_at,
            updated_at=session.updated_at,
            output_updated_at=session.output_updated_at,
            context_tokens=session.context_tokens,
            context_window=session.context_window,
            turn_seq=session.turn_seq,
            excluded_candidates=session.excluded_candidates,
            model=session.model,
            effort=session.effort,
            engine=session.engine,
            fast_mode=session.fast_mode,
            codex_model_provider=session.codex_model_provider,
            codex_subscription_provider=session.codex_subscription_provider,
            codex_state_provider=session.codex_state_provider,
            claude_api_env_file=session.claude_api_env_file,
            claude_connection=session.claude_connection,
            claude_role=session.claude_role,
            status=session.status,
            agent_message=session.agent_message,
            last_action=session.last_action,
            error=session.error,
            finalized_at=session.finalized_at,
            result_delivered=session.result_delivered,
            retention_deadline=session.retention_deadline,
            result_contract_recovery_consumed=session.result_contract_recovery_consumed,
        )


@dataclasses.dataclass(frozen=True)
class RegistryResumeState(SessionResumeState):
    """別プロセスの共有登録から復元し、照会時に鮮度確認を要する再開状態。

    世代移行の`move_context`で停止済みsessionとして往復するため、
    `service_state`の状態codecから循環importなしに参照できるここへ置く。
    """

    result_root_session_id: str | None = None
    result_owner_status_file: str = "root.json"


def fast_mode_fields(engine: str | None, fast_mode: object) -> dict[str, bool]:
    """Codexの既知の速度だけを公開し、旧形式と他engineの表示を保持する。"""
    return {"fast_mode": fast_mode} if engine == "codex" and isinstance(fast_mode, bool) else {}


def selected_candidate(session: SessionState | SessionResumeState) -> ModelCandidate | None:
    if session.model is None or session.effort is None:
        return None
    return session.engine, session.model, session.effort


def has_uncollected_result(session: SessionState | SessionResumeState, result_consumed: bool | None) -> bool:
    if session.finalized_at is None or session.result_delivered:
        return False
    return result_consumed is None or not result_consumed


def previous_result_payload(session: SessionState) -> dict[str, Any]:
    if session.result_delivered:
        return {}
    result: dict[str, Any] = {"status": session.status, "agent_message": session.agent_message}
    if nonempty_error(session.error):
        result["error"] = session.error
    return PreviousResult(with_result_next_action(result, session.label), SessionResumeState.from_session(session))


def terminal_result_payload(session: SessionState | SessionResumeState) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": session.status,
        "agent_message": session.agent_message,
        "turn_seq": session.turn_seq,
        "finalized_at": session.finalized_at,
        "engine": session.engine,
        "model": session.model,
        "effort": session.effort,
        "model_type": session.model_type,
    }
    if nonempty_error(session.error):
        result["error"] = redact_diagnostic_values(session.error)
    return result


def initialize_turn(session: SessionState, *, reset_progress: bool = True, preserve_waits: bool = False) -> None:
    session.turn_id = ""
    session.status = "running"
    session.plan = []
    session.record_current_item_start(None)
    session.cancel_pending_effort_resume()
    session.commentary = ""
    session.diff_changed = False
    session.error = None
    session.agent_message = ""
    session.result_delivered = False
    session.protocol_warnings = []
    session.turn_start = turn_state.TurnStart.UNSENT
    session.interrupt_requested = False
    session.record_turn_running()
    if not preserve_waits:
        session.live_child_session_ids.clear()
        session.terminal_child_session_ids.clear()
        session.codex_terminal_subagent_thread_ids.clear()
        session.agents_wait_background_outputs.clear()
        session.child_tool_uses.clear()
    session.codex_self_message_observed = False
    session.pending_tool_uses.clear()
    session.last_action = ""
    session.clear_hold()
    session.finalized_at = None
    session.retention_deadline = None
    session.started_at = utc_now()
    if reset_progress:
        session.reset_progress()
    session.touch()


def begin_reply(session: SessionState, *, preserve_waits: bool = False) -> None:
    session.turn_seq += 1
    initialize_turn(session, reset_progress=False, preserve_waits=preserve_waits)
    session.touch()
