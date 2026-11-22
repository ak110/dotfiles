"""agents_serverのバックエンドが共有するsession状態と検証を定義する。"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import logging
import typing
from collections.abc import Callable, Coroutine
from typing import Any

from agent_toolkit._agents_server import turn_state
from agent_toolkit._agents_server.input_validation import validate_prompt
from agent_toolkit._agents_server.logging_config import redact_diagnostic_values
from agent_toolkit._agents_server.result_projection import (
    nonempty_error,
    with_result_next_action,
)
from agent_toolkit._common import session_registry, startup_features, task_documents
lazy from agent_toolkit._agents_server.session_state import SessionState as SessionState

_LOG = logging.getLogger("agent-toolkit.agents-server.state")

RESULT_RETENTION_SECONDS = 1800.0
# 委譲先の最終活動時刻からの経過が本値を超えた待機の応答へ、停滞の可能性を示す項目を加える。
# 値はユーザーの提案に基づく300秒とする。長時間のコマンドの実行待ちでも超過し得るため、
# 超過は委譲元が状況を調べる契機として扱う。
STALL_NOTICE_SECONDS = 300.0
# ホストが応答しないMCPツール呼び出しをバックグラウンドタスクへ移すまでの秒数。
# 以降の上限はこの閾値を制約として導出する。単独の値として決めない。
HOST_BACKGROUND_THRESHOLD_SECONDS = 120.0
# backendがsessionの初期化を完了するまでagents_serverが待つ上限秒数と、同じ候補で試みる回数。
# Claude Codeの記録では、`start`の呼び出しから起動された子sessionの記録の先頭エントリまでの
# 経過が233件中232件で47.65秒以内に収まり、残る1件が604.22秒だった。
# 同じ母集団のうち7件は初期化が到達せず、ホストがMCPツール呼び出しを1800.5秒で打ち切っていた。
# 1回の上限は観測の上位側の47.65秒を含む値とし、回数との積へ起動直後の可用性失敗を待つ上限
# （`_agents_server/manager.py`のSTART_AVAILABILITY_TIMEOUT）を直列に加えた和が
# HOST_BACKGROUND_THRESHOLD_SECONDSを下回るように選ぶ。
# この関係が成立しなくなると、初期化の失敗が確定する前にホストがツール呼び出しを背景へ移し、
# 委譲元は`start`の失敗を受け取らないまま待機へ進む。
# 監査記録は`docs/development/audit-records.md`の
# 「agent-toolkit/agent_toolkit/_agents_server/state.py：session初期化の待機上限：2026年9月11日」にある。
SESSION_INITIALIZATION_TIMEOUT = 50.0
SESSION_INITIALIZATION_ATTEMPTS = 2
# 起動直後の可用性失敗を確定するために`start`が終了を待つ上限秒数。
# Codex CLI 0.152.0で利用上限に達した状態のturnは、backendの起動応答から3.84〜4.27秒後に
# `turn/completed`で失敗した（2026-09-02、`explore_fast`候補`gpt-5.6-terra/medium`で3回測定）。
# 再検証は同じ失敗状態で`AppServerManager.start`を呼び、応答から終了までの経過を測る。
# 可用性失敗は最初のモデル出力より前に生じるため、正常起動ではモデル出力の観測で上限を待たずに打ち切る。
# Claudeでは、失敗した要求では始まらないAPIの応答開始（`message_start`）をモデル出力の観測として扱う。
START_AVAILABILITY_TIMEOUT = 15.0
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})


ModelCandidate = tuple[str, str, str]
LaunchKind = task_documents.LaunchKind
ResumeDelivery = typing.Literal["reply_started", "reply_failed", "reply_ambiguous"]
_TOUCH_LISTENERS: set[Callable[[], None]] = set()
_TOUCH_OWNERS: dict[Callable[[], None], dict[str, SessionState]] = {}
_TERMINAL_LISTENERS: set[Callable[[SessionState], None]] = set()
_LIFECYCLE_LISTENERS: set[Callable[[SessionState, str], None]] = set()


def add_lifecycle_listener(listener: Callable[[SessionState, str], None]) -> None:
    """稼働の開始と結果確定の通知先を登録する。"""
    _LIFECYCLE_LISTENERS.add(listener)


def remove_lifecycle_listener(listener: Callable[[SessionState, str], None]) -> None:
    """稼働の開始と結果確定の通知先を解除する。"""
    _LIFECYCLE_LISTENERS.discard(listener)


def add_touch_listener(listener: Callable[[], None], *, sessions: dict[str, SessionState] | None = None) -> None:
    """session状態の更新通知先を登録する。"""
    _TOUCH_LISTENERS.add(listener)
    if sessions is not None:
        _TOUCH_OWNERS[listener] = sessions


def remove_touch_listener(listener: Callable[[], None]) -> None:
    """session状態の更新通知先を解除する。"""
    _TOUCH_LISTENERS.discard(listener)
    _TOUCH_OWNERS.pop(listener, None)


def add_terminal_listener(listener: Callable[[SessionState], None]) -> None:
    """turnの終了時の結果が確定したsessionの通知先を登録する。"""
    _TERMINAL_LISTENERS.add(listener)


def notify_touch_listeners(session: SessionState | None = None) -> None:
    """登録された全ての通知先へ、session状態の更新を通知する。"""
    for listener in tuple(_TOUCH_LISTENERS):
        owner = _TOUCH_OWNERS.get(listener)
        if session is None or owner is None or owner.get(session.session_id) is session:
            listener()


def remove_terminal_listener(listener: Callable[[SessionState], None]) -> None:
    """turnの終了時の結果が確定したsessionの通知先を解除する。"""
    _TERMINAL_LISTENERS.discard(listener)


class ResumePrompt:
    """進行中のsession再開へ、無効化可能な継続入力を1件ずつ渡す。"""

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
        """backendが確定した配送結果を一度だけ保持する。"""
        if self._delivery is not None and self._delivery != delivery:
            raise RuntimeError("再開要求の配送結果は確定済みです")
        self._delivery = delivery

    @property
    def initial_ticket(self) -> int:
        """生成時に登録した継続入力の識別子を返す。"""
        return 1

    def submit_or_observe(self, prompt: str) -> tuple[int | None, asyncio.Event | None]:
        """空きがあれば継続入力を登録し、使用中なら状態変化イベントを返す。"""
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
        """指定した未確定の継続入力だけを無効化する。"""
        current = self._current
        if current is None or current[0] != ticket:
            return
        self._current = None
        current[2].set()
        self._signal_change()

    def close(self) -> None:
        """継続入力の受付と進行中の配送待ちを終了する。"""
        self._closed = True
        current = self._current
        self._current = None
        if current is not None:
            current[2].set()
        self._signal_change()

    async def deliver(self, sender: Callable[[str], Coroutine[Any, Any, None]]) -> None:
        """取り消されていない継続入力をsenderへ1件配送する。"""
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


def utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def append_bounded(existing: str, delta: str, limit: int = 4000) -> str:
    value = existing + delta
    return value if len(value) <= limit else value[-limit:]


@dataclasses.dataclass(frozen=True)
class LiveTask:
    """Claude backendが追跡する背景task（`TaskStartedMessage`の内容と受信時刻）。

    結果を保留している間の待機対象を`show`で委譲元へ示すために保持する。
    """

    task_type: str
    description: str
    started_at: str


class PreviousResult(dict[str, Any]):
    """backendが配送前に選んだ終了結果。公開する辞書の項目と回収用の識別を分ける。"""

    def __init__(self, payload: dict[str, Any], origin: SessionResumeState) -> None:
        super().__init__(payload)
        self.origin = origin

    @property
    def end(self) -> tuple[int, str] | None:
        return (self.origin.turn_seq, self.origin.finalized_at) if self.origin.finalized_at is not None else None


@dataclasses.dataclass(frozen=True)
class SessionResumeState:
    """同じ会話の再開と未回収の終了時の結果の返却に必要な状態を保持する。"""

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
    # `label`・`prompt`・`created_at`は`session_registry.LaunchInfo`の項目であり、再開時はその定義から写す。
    label: str = ""
    prompt: str = ""
    followup_prompts: tuple[str, ...] = ()
    final_responses: tuple[tuple[int, str], ...] = ()
    terminal_error: str | None = None
    effort_history: tuple[tuple[str, str, str, str], ...] = ()
    effort_levels: tuple[str, ...] = ()
    # 登録簿から復元した旧形式のsessionでは開始時刻が不明なため`None`とする。
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
    error: Any = None
    finalized_at: str | None = None
    result_delivered: bool = False
    retention_deadline: float | None = None
    result_contract_recovery_consumed: bool = False

    @classmethod
    def from_session(cls, session: SessionState) -> SessionResumeState:
        """終了sessionから再開に必要な入力だけを退避する。"""
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
    """sessionの起動時に確定した候補を返す。"""
    if session.model is None or session.effort is None:
        return None
    return session.engine, session.model, session.effort


def has_uncollected_result(session: SessionState | SessionResumeState, result_consumed: bool | None) -> bool:
    """終了時の結果が未回収かを返す。

    結果ファイルを扱える消費側は回収状態を渡し、結果ファイルを扱えない処理だけは`None`を渡す。
    """
    if session.finalized_at is None or session.result_delivered:
        return False
    return result_consumed is None or not result_consumed


def previous_result_payload(session: SessionState) -> dict[str, Any]:
    """未回収の旧turnだけを継続要求の応答へ渡す。"""
    if session.result_delivered:
        return {}
    result: dict[str, Any] = {"status": session.status, "agent_message": session.agent_message}
    if nonempty_error(session.error):
        result["error"] = session.error
    return PreviousResult(with_result_next_action(result, session.label), SessionResumeState.from_session(session))


def terminal_result_payload(session: SessionState | SessionResumeState) -> dict[str, Any]:
    """終了時の結果ファイルへ保存する結果と復旧用の項目を返す。

    保持中と退避済みのsessionが同じ結果本文を公開できるよう、必要な項目だけへ射影する。
    """
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
    """新しいturnの開始前に共有状態を初期化する。"""
    session.turn_id = ""
    session.status = "running"
    session.plan = []
    session.current_item = None
    session.current_item_started_at = None
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
    """終了済みsessionの新しいturnを開始する準備をする。"""
    session.turn_seq += 1
    initialize_turn(session, reset_progress=False, preserve_waits=preserve_waits)
    session.touch()


def finish_unowned_start(initialized: asyncio.Future[SessionState], turn_seq: int, error: BaseException) -> bool:
    """初期化待ちを中断して所有タスクを回収した後、そのturnだけを終了扱いにする。"""
    if not initialized.done() or initialized.cancelled() or initialized.exception() is not None:
        return False
    session = initialized.result()
    if session.turn_seq != turn_seq or session.turn_completed or session.terminal:
        return False
    session.clear_hold()
    session.status = "failed"
    session.error = {"message": f"初期化の受付確定前に所有タスクが終了した: {type(error).__name__}"}
    session.record_turn_completed()
    session.touch()
    return True
