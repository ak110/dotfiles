from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from agent_toolkit._agents_server import state as contracts  # pylint: disable=cyclic-import
from agent_toolkit._agents_server import state_publication, turn_state
from agent_toolkit._agents_server.result_projection import action_detail, nonempty_error, progress_excerpt
from agent_toolkit._common import claude_usage_limit, session_registry, startup_features


@dataclasses.dataclass
class SessionState:
    session_id: str
    cwd: str
    model: str | None = None
    effort: str | None = None
    engine: str = "codex"
    fast_mode: bool | None = None
    # Codex接続先は内部の再開条件だけへ写し、公開状態へ射影しない。
    codex_model_provider: str | None = None
    codex_subscription_provider: str | None = None
    # 新規開始または状態互換性を確認して確定した生成先。旧版の選択先とは区別する。
    codex_state_provider: str | None = None
    codex_attempted_provider_ids: set[str] = dataclasses.field(default_factory=set, repr=False)
    codex_auto_reset_pending: bool = False
    claude_api_env_file: str | None = None
    claude_connection: str | None = None
    claude_role: str | None = None
    claude_connection_diagnostics: tuple[str, ...] = ()
    claude_mcp_diagnostics: tuple[str, ...] = ()
    claude_connection_failures: dict[str, str] = dataclasses.field(default_factory=dict, repr=False)

    claude_subscription_retry_at: float | None = None
    model_type: str | None = None
    launch_kind: contracts.LaunchKind = "delegate"
    startup_profile: startup_features.StartupProfile | None = dataclasses.field(default_factory=startup_features.requested)
    reply_start_pending: bool = False
    label: str = ""
    prompt: str = ""
    followup_prompts: tuple[str, ...] = ()
    final_responses: tuple[tuple[int, str], ...] = ()
    terminal_error: str | None = None
    effort_history: tuple[tuple[str, str, str, str], ...] = ()
    effort_levels: tuple[str, ...] = ()
    _effort_resume: turn_state.EffortResumePending | turn_state.EffortResumeRunning | None = None
    effort_request_results: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict, repr=False)
    claude_applied_effort: str | None = None
    announced: bool = False

    created_at: str = dataclasses.field(default_factory=contracts.utc_now)
    started_at: str = dataclasses.field(default_factory=contracts.utc_now)
    excluded_candidates: frozenset[contracts.ModelCandidate] = dataclasses.field(default_factory=frozenset)
    turn_seq: int = 0
    turn_id: str = ""
    status: str = "running"
    plan: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    _active_item: contracts.ActiveItem | None = None
    commentary: str = ""
    diff_changed: bool = False
    error: Any = None
    agent_message: str = ""
    result_delivered: bool = False
    protocol_warnings: list[str] = dataclasses.field(default_factory=list)
    turn_start: turn_state.TurnStart = turn_state.TurnStart.UNSENT
    interrupt_requested: bool = False
    turn_end: turn_state.TurnEnd = turn_state.TurnEnd.IN_PROGRESS
    # モデル由来の出力（テキスト、思考、ツール呼び出し）を1回以上受信したか。
    # Claudeでは、APIの応答開始（`message_start`）の受信で真とする。
    # engineの可用性失敗は最初のモデル出力より前に生じるため、`start`はこの値で起動直後の終了待ちを打ち切る。
    # `start`は新しいsessionだけを待つため、turnごとに初期化しない。
    model_output_observed: bool = False
    # Claude backendのバックグラウンドタスクだけを表し、Codex backendでは値を持たない。キーはtask識別子とする。
    live_tasks: dict[str, contracts.LiveTask] = dataclasses.field(default_factory=dict)
    live_child_session_ids: set[str] = dataclasses.field(default_factory=set)
    terminal_child_session_ids: set[str] = dataclasses.field(default_factory=set)

    agents_wait_background_outputs: set[str] = dataclasses.field(default_factory=set)
    child_tool_uses: dict[str, tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=dict, repr=False)
    # 未完了のツール呼び出し。キーは`tool_use_id`、値はツール名、そのブロックを受信した時刻および入力の1行要約の組とする。
    # `child_tool_uses`は`agents_server`のツール呼び出しの引数を孫session追跡のために保持する別の責務を持つため統合しない。
    pending_tool_uses: dict[str, tuple[str, str, str]] = dataclasses.field(default_factory=dict, repr=False)

    last_action: str = ""
    hold: turn_state.ResultHold | None = None
    # Claude Code CLIが`session_state_changed`で報告した最新のturn状態（`running`・`idle`・`requires_action`）。
    # 一度も報告を受けていないsession（Codex backendと、報告を発行しないCLI）では`None`とし、
    # `has_pending_auto_resume_targets`は従来どおり追跡集合だけで判定する。
    # turnをまたいで最新の報告を保持し、turnの開始時に初期化しない（次のturnの開始はCLIが`running`で報告する）。
    cli_turn_state: str | None = None

    cli_turn_state_fallback_logged: bool = False
    result_contract_recovery_consumed: bool = False

    availability_checked: bool = False

    _overload_retry: contracts.RetryChain | None = None
    # Claudeが最後に報告した利用枠の状態。待機の対象かの判定と解除予定時刻に使う。
    usage_limit: claude_usage_limit.UsageLimitState | None = None
    # 利用上限の解除待ち。次に同じsessionへ継続を送る時刻（イベントループの時計）、待機の回数と最初の拒否の時刻を持つ。
    # 待機対象以外の終了と委譲元の`kill`・`send_message`で初期化し、回数では打ち切らない。
    _usage_limit_retry: contracts.RetryChain | None = None
    finalized_at: str | None = None
    updated_at: str = dataclasses.field(default_factory=contracts.utc_now)
    output_updated_at: str | None = None
    api_error: dict[str, Any] | None = None

    context_tokens: int | float | None = None
    context_window: int | float | None = None
    # backend資源の解放期限。未回収の終了時の結果はこの期限を過ぎても保持する。
    retention_deadline: float | None = None
    turn_control_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock, repr=False)
    _progress_text: str = dataclasses.field(default="", repr=False)
    progress_items: dict[str, str] = dataclasses.field(default_factory=dict, repr=False)
    compaction_started_at_ms: dict[str, int] = dataclasses.field(default_factory=dict, repr=False)
    # Codex接続が購読した子孫thread。通知の振り分けと解放に使い、状態ファイルや再開記録へ射影しない。
    codex_subagent_thread_ids: set[str] = dataclasses.field(default_factory=set, repr=False)

    codex_live_subagent_thread_ids: set[str] = dataclasses.field(default_factory=set, repr=False)
    codex_terminal_subagent_thread_ids: set[str] = dataclasses.field(default_factory=set, repr=False)

    codex_subagent_activity_ids: set[tuple[str, str, str, str]] = dataclasses.field(default_factory=set, repr=False)
    codex_self_message_observed: bool = False
    codex_self_message_repair_consumed: bool = False
    publish_registry: bool = dataclasses.field(default=False, repr=False)

    launcher_session_id: str | None = dataclasses.field(default=None, repr=False)
    start_observation: str | None = dataclasses.field(default=None, repr=False)
    # turnを実行する委譲先CLIのプロセスの識別。所有するagents_serverの識別とともに登録簿へ公開し、
    # 所有側が終了を公開せずに終了した記録を、照会側が全プロセスの不在から終了と判定する入力にする。
    executor_process: session_registry.ProcessIdentity | None = dataclasses.field(default=None, repr=False)

    owner_root_session_id: str | None = dataclasses.field(default=None, repr=False)
    _published_registry_launcher: str | None = dataclasses.field(default=None, repr=False)
    _published_registry_state: session_registry.PublicationState | None = dataclasses.field(default=None, repr=False)
    _published_registry_turn_seq: int | None = dataclasses.field(default=None, repr=False)
    _published_registry_fast_mode: bool | None = dataclasses.field(default=None, repr=False)
    _published_registry_effort: str | None = dataclasses.field(default=None, repr=False)
    _published_registry_result_contract_recovery_consumed: bool = dataclasses.field(default=False, repr=False)
    _published_registry_launch_info: session_registry.LaunchInfo | None = dataclasses.field(default=None, repr=False)
    _published_registry_executor: session_registry.ProcessIdentity | None = dataclasses.field(default=None, repr=False)
    _published_registry_owner_root: str | None = dataclasses.field(default=None, repr=False)
    _terminal_notified: bool = dataclasses.field(default=False, repr=False)
    _lifecycle_running: bool = dataclasses.field(default=False, repr=False)
    _registration_task: asyncio.Task[str | None] | None = dataclasses.field(default=None, repr=False)
    _registration_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock, repr=False)

    def hold_result(self, hold: turn_state.ResultHold) -> None:
        self.hold = hold

    def clear_hold(self) -> None:
        self.hold = None

    def set_background_deadline(self, deadline: float | None) -> None:
        """背景完了待ちの期限を更新し、異なる理由の保留を変更しない。"""
        if isinstance(self.hold, turn_state.BackgroundWait):
            self.hold = dataclasses.replace(self.hold, deadline=deadline)

    @property
    def awaiting_auto_resume(self) -> bool:
        return self.hold is not None

    @property
    def pending_result(self) -> dict[str, Any] | None:
        return self.hold.result if self.hold is not None else None

    @property
    def auto_resume_deadline(self) -> float | None:
        """背景処理の完了通知にだけ適用する期限を返す。"""
        return self.hold.deadline if isinstance(self.hold, turn_state.BackgroundWait) else None

    @property
    def overload_resume_at(self) -> float | None:
        """過負荷の再試行予定を返す。"""
        return self.hold.resume_at if isinstance(self.hold, turn_state.OverloadWait) else None

    @property
    def usage_limit_resume_at(self) -> float | None:
        return self.hold.resume_at if isinstance(self.hold, turn_state.UsageLimitWait) else None

    @property
    def claude_api_resume_pending(self) -> bool:
        """Claudeの接続切替待ちかを返す。"""
        return isinstance(self.hold, turn_state.ClaudeConnectionWait)

    @property
    def codex_provider_resume_pending(self) -> bool:
        """Codexのprovider切替待ちかを返す。"""
        return isinstance(self.hold, turn_state.CodexProviderWait)

    @property
    def result_contract_recovery_pending(self) -> bool:
        return isinstance(self.hold, turn_state.ResultContractWait)

    @property
    def result_contract_recovery_reason(self) -> str | None:
        return self.hold.reason if isinstance(self.hold, turn_state.ResultContractWait) else None

    @property
    def turn_start_sent(self) -> bool:
        return self.turn_start in {turn_state.TurnStart.SENT, turn_state.TurnStart.STARTED, turn_state.TurnStart.AMBIGUOUS}

    @property
    def turn_start_ambiguous(self) -> bool:
        return self.turn_start is turn_state.TurnStart.AMBIGUOUS

    def record_turn_sent(self) -> None:
        if self.turn_start is turn_state.TurnStart.UNSENT:
            self.turn_start = turn_state.TurnStart.SENT

    def record_turn_started(self, turn_id: str | None = None) -> None:
        if turn_id is not None:
            self.turn_id = turn_id
        self.turn_start = turn_state.TurnStart.STARTED

    def record_turn_ambiguous(self) -> None:
        if self.turn_start is not turn_state.TurnStart.STARTED:
            self.turn_start = turn_state.TurnStart.AMBIGUOUS

    def record_turn_rejected(self) -> None:
        self.turn_start = turn_state.TurnStart.REJECTED

    def record_turn_completed(self) -> None:
        self.turn_end = turn_state.TurnEnd.COMPLETED
        if self.turn_start is turn_state.TurnStart.AMBIGUOUS:
            self.turn_start = turn_state.TurnStart.STARTED

    def record_turn_running(self) -> None:
        self.turn_end = turn_state.TurnEnd.IN_PROGRESS

    def record_failure_pending_completion(self) -> None:
        """失敗を観測し、実行中turnの終了を待つ段階を記録する。"""
        self.turn_end = turn_state.TurnEnd.FAILURE_PENDING

    @property
    def turn_completed(self) -> bool:
        return self.turn_end is turn_state.TurnEnd.COMPLETED

    @property
    def failure_pending_completion(self) -> bool:
        """失敗を観測した実行中turnの終了待ちかを返す。"""
        return self.turn_end is turn_state.TurnEnd.FAILURE_PENDING

    @property
    def terminal(self) -> bool:
        return self.status in contracts.TERMINAL_STATUSES

    @property
    def result_available(self) -> bool:
        return (
            self.terminal
            and self.turn_completed
            and not self.turn_start_ambiguous
            and not self.awaiting_auto_resume
            and self.effort_resume_turn != self.turn_seq
            and not self.effort_resuming
        )

    @property
    def progress(self) -> str:
        return progress_excerpt(self._progress_text)

    def set_progress(self, text: str) -> None:
        self._progress_text = text
        if text.strip():
            self.output_updated_at = contracts.utc_now()
            self.last_action = progress_excerpt(text)
        self.touch()

    @property
    def effort_resume_turn(self) -> int | None:
        return self._effort_resume.turn_seq if isinstance(self._effort_resume, turn_state.EffortResumePending) else None

    @property
    def effort_resuming(self) -> bool:
        return isinstance(self._effort_resume, turn_state.EffortResumeRunning)

    def reserve_effort_resume(self, turn_seq: int) -> None:
        self._effort_resume = turn_state.EffortResumePending(turn_seq)

    def cancel_pending_effort_resume(self) -> None:
        if isinstance(self._effort_resume, turn_state.EffortResumePending):
            self._effort_resume = None

    def begin_effort_resume(self) -> bool:
        requested_turn = self.effort_resume_turn
        if requested_turn is None:
            return False
        if requested_turn != self.turn_seq or self.interrupt_requested:
            self.cancel_pending_effort_resume()
            return False
        if not self.terminal or not self.turn_completed:
            return False
        self._effort_resume = turn_state.EffortResumeRunning()
        return True

    def finish_effort_resume(self) -> None:
        if self.effort_resuming:
            self._effort_resume = None

    @property
    def overload_resume_count(self) -> int:
        return self._overload_retry.count if self._overload_retry is not None else 0

    @property
    def overload_first_at(self) -> str | None:
        return self._overload_retry.first_at if self._overload_retry is not None else None

    def advance_overload_retry(self) -> None:
        self._overload_retry = contracts.RetryChain.advance(self._overload_retry)

    def reset_overload_retry(self) -> None:
        self._overload_retry = None

    @property
    def usage_limit_wait_count(self) -> int:
        return self._usage_limit_retry.count if self._usage_limit_retry is not None else 0

    @property
    def usage_limit_first_at(self) -> str | None:
        return self._usage_limit_retry.first_at if self._usage_limit_retry is not None else None

    def advance_usage_limit_retry(self) -> None:
        self._usage_limit_retry = contracts.RetryChain.advance(self._usage_limit_retry)

    def reset_usage_limit_retry(self) -> None:
        self._usage_limit_retry = None

    def record_tool_use_start(self, tool_use_id: str, tool_name: str, tool_input: Any = None) -> None:
        detail = action_detail(tool_input)
        self.pending_tool_uses[tool_use_id] = (tool_name, contracts.utc_now(), detail)
        self.last_action = f"{tool_name}: {detail}" if detail else tool_name

    def record_tool_use_end(self, tool_use_id: str) -> None:
        self.pending_tool_uses.pop(tool_use_id, None)

    @property
    def current_item(self) -> dict[str, Any] | None:
        return self._active_item.item if self._active_item is not None else None

    @property
    def current_item_started_at(self) -> str | None:
        return self._active_item.started_at if self._active_item is not None else None

    def record_current_item_start(self, item: dict[str, Any] | None) -> None:
        """Codex backendの進行中itemと受信時刻を記録し、最後の行動をitem種別と入力の要約で更新する。"""
        self._active_item = contracts.ActiveItem(item, contracts.utc_now()) if item is not None else None
        if item is None:
            return
        item_type = item.get("type")
        if isinstance(item_type, str) and item_type:
            detail = action_detail(item, exclude=("type", "id"))
            self.last_action = f"{item_type}: {detail}" if detail else item_type

    def active_tool_uses(self) -> list[dict[str, str]]:
        """未完了のツール呼び出しを、開始時刻の昇順で公開項目へ射影する。

        委譲元が停滞の可能性を確認する際、1回の照会で長時間のコマンドを実行しているのか
        活動そのものが停止したのかを判別できるようにする。
        Claude backendは`tool_use`ブロックの記録から、Codex backendは進行中itemから射影する。
        1つのsessionはいずれか一方のbackendだけを使うため、両者を同じ項目で返す。
        入力の1行要約は`detail`として載せ、要約が空の場合だけこのkeyを置かない。
        どのコマンドまたはどのファイルで止まっているかは、ツール名とitem種別だけでは判別できないためである。
        """
        if (active_item := self._active_item) is not None:
            entry: dict[str, str] = {"started_at": active_item.started_at}
            item_type = active_item.item.get("type")
            if isinstance(item_type, str) and item_type:
                entry["type"] = item_type
            detail = action_detail(active_item.item, exclude=("type", "id"))
            if detail:
                entry["detail"] = detail
            return [entry]
        ordered = sorted(self.pending_tool_uses.items(), key=lambda item: (item[1][1], item[0]))
        entries: list[dict[str, str]] = []
        for _, (tool_name, started_at, detail) in ordered:
            entry = {"name": tool_name, "started_at": started_at}
            if detail:
                entry["detail"] = detail
            entries.append(entry)
        return entries

    def reset_progress(self) -> None:
        self._progress_text = ""
        self.progress_items.clear()
        self.output_updated_at = None
        self.api_error = None

    def record_api_error(
        self, error_type: str, http_status: int | None, *, retry_at: str | None = None, attempt: int | None = None
    ) -> None:
        """連続するAPI失敗を記録し、活動時刻を変えずに状態の読者へ通知する。"""
        previous = self.api_error or {}
        self.api_error = {
            "type": error_type,
            "http_status": http_status,
            "first_at": previous.get("first_at", contracts.utc_now()),
            "count": attempt if attempt is not None else previous.get("count", 0) + 1,
        }
        if retry_at is not None:
            self.api_error["retry_at"] = retry_at
        contracts.notify_touch_listeners(self)

    def attach_executor(self, pid: int | None) -> None:
        state_publication.attach_executor(self, pid)

    async def attach_executor_async(self, pid: int | None) -> None:
        await state_publication.attach_executor_async(self, pid)

    def touch(self) -> None:
        state_publication.touch(self)

    async def drain_registration(self) -> None:
        await state_publication.drain_registration(self)

    async def publish_registration_async(self) -> str | None:
        return await state_publication.publish_registration_async(self)

    def publish_registration(self) -> str | None:
        return state_publication.publish_registration(self)

    @property
    def registration_pending(self) -> bool:
        return self._registration_task is not None and not self._registration_task.done()

    def public_status(self, *, include_result: bool = False) -> dict[str, Any]:
        """waitとkillが消費する状態を公開契約へ射影する。"""
        result: dict[str, Any] = {"status": self.status}
        if self.status == "running":
            result["progress"] = self.progress
        if include_result and self.result_available:
            result["agent_message"] = self.agent_message
            result.update(engine=self.engine, model=self.model, effort=self.effort, model_type=self.model_type)
            if nonempty_error(self.error):
                result["error"] = self.error
        return result

    def previous_result(self) -> dict[str, Any]:
        return contracts.previous_result_payload(self)
