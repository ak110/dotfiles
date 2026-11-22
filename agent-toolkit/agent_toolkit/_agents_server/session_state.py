"""sessionの実行状態、登録と結果の公開。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any

from agent_toolkit._agents_server import state as contracts
from agent_toolkit._agents_server import turn_state
from agent_toolkit._agents_server.file_operations import finish_task, run_file_operation
from agent_toolkit._agents_server.logging_config import redact_diagnostic_values
from agent_toolkit._agents_server.result_projection import (
    action_detail,
    nonempty_error,
    progress_excerpt,
)
from agent_toolkit._atk import usage_refresh
from agent_toolkit._common import claude_usage_limit, session_registry, startup_features


@dataclasses.dataclass
class SessionState:
    """MCPから観測できる1つの委譲先sessionと最新turnの共有状態。"""

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
    # 利用上限でサブスクリプションからAPIへ移した委譲先を、サブスクリプションへ戻してよい時刻（epoch秒）。
    # 所有MCPサーバーのメモリーだけに置き、解放後の再開は保存した接続を使う。
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
    effort_resume_turn: int | None = None
    effort_resuming: bool = False
    effort_request_results: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict, repr=False)
    claude_applied_effort: str | None = None
    announced: bool = False
    # sessionを最初に開始した時刻。turnごとに更新する`started_at`とは別に保持し、再開後も引き継ぐ。
    # レーン稼働時間のようにsession全体の経過を測る呼び出し元が起点として読む。
    created_at: str = dataclasses.field(default_factory=contracts.utc_now)
    started_at: str = dataclasses.field(default_factory=contracts.utc_now)
    excluded_candidates: frozenset[contracts.ModelCandidate] = dataclasses.field(default_factory=frozenset)
    turn_seq: int = 0
    turn_id: str = ""
    status: str = "running"
    plan: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    current_item: dict[str, Any] | None = None
    # Codex backendの進行中itemを受信した時刻。`current_item`と対で保持する。
    current_item_started_at: str | None = None
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
    # 背景実行へ移った`atk agents wait`の出力ファイルの絶対パス。結果本文がツール結果に現れないため、
    # 孫sessionの終了判定と未観測の記録の直前にこのファイルを読み、回収済みの孫sessionを追跡から外す。
    agents_wait_background_outputs: set[str] = dataclasses.field(default_factory=set)
    child_tool_uses: dict[str, tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=dict, repr=False)
    # 未完了のツール呼び出し。キーは`tool_use_id`、値はツール名、そのブロックを受信した時刻および入力の1行要約の組とする。
    # `child_tool_uses`は`agents_server`のツール呼び出しの引数を孫session追跡のために保持する別の責務を持つため統合しない。
    pending_tool_uses: dict[str, tuple[str, str, str]] = dataclasses.field(default_factory=dict, repr=False)
    # 最後に観測した行動。assistantのテキスト出力ではその抜粋、ツール呼び出しではツール名またはitem種別と入力の1行要約を持つ。
    # statuslineが、テキスト出力の無い区間でも稼働を表示するための射影元とする。
    last_action: str = ""
    hold: turn_state.ResultHold | None = None
    # Claude Code CLIが`session_state_changed`で報告した最新のturn状態（`running`・`idle`・`requires_action`）。
    # 一度も報告を受けていないsession（Codex backendと、報告を発行しないCLI）では`None`とし、
    # `has_pending_auto_resume_targets`は従来どおり追跡集合だけで判定する。
    # turnをまたいで最新の報告を保持し、turnの開始時に初期化しない（次のturnの開始はCLIが`running`で報告する）。
    cli_turn_state: str | None = None
    # 状態の報告を受け取らずに`ResultMessage`を受け取り、従来の判定へ戻ったことを警告ログへ記録済みか。
    # sessionごとに1回だけ記録する。
    cli_turn_state_fallback_logged: bool = False
    result_contract_recovery_consumed: bool = False
    # `start`の可用性確認を終えたか。確認を終える前の過負荷は起動時の候補切替が扱う。
    availability_checked: bool = False
    # 過負荷による自動継続。連鎖の中で行った継続の回数、次の継続を送る時刻（イベントループの時計）と、
    # 連鎖の最初の過負荷の時刻を持つ。過負荷以外の終了、委譲元の`kill`・`send_message`および上限到達で初期化する。
    overload_resume_count: int = 0
    overload_first_at: str | None = None
    # Claudeが最後に報告した利用枠の状態。待機の対象かの判定と解除予定時刻に使う。
    usage_limit: claude_usage_limit.UsageLimitState | None = None
    # 利用上限の解除待ち。次に同じsessionへ継続を送る時刻（イベントループの時計）、待機の回数と最初の拒否の時刻を持つ。
    # 待機対象以外の終了と委譲元の`kill`・`send_message`で初期化し、回数では打ち切らない。
    usage_limit_wait_count: int = 0
    usage_limit_first_at: str | None = None
    finalized_at: str | None = None
    updated_at: str = dataclasses.field(default_factory=contracts.utc_now)
    output_updated_at: str | None = None
    api_error: dict[str, Any] | None = None
    # 最新モデル文脈の観測値。未知は0で補完しない。
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
    # 親turnの結果を保留する組み込みの子と、再開時に結果の回収を求める終了済みの子。
    codex_live_subagent_thread_ids: set[str] = dataclasses.field(default_factory=set, repr=False)
    codex_terminal_subagent_thread_ids: set[str] = dataclasses.field(default_factory=set, repr=False)
    # item/started・item/completed・turn/completedでの同じ活動の再掲を一度だけ反映する。
    codex_subagent_activity_ids: set[tuple[str, str, str, str]] = dataclasses.field(default_factory=set, repr=False)
    codex_self_message_observed: bool = False
    codex_self_message_repair_consumed: bool = False
    publish_registry: bool = dataclasses.field(default=False, repr=False)
    # sessionを作成した時点の委譲元sessionの識別子。登録簿へ公開し、`atk serve`の一覧が親子付けに使う。
    launcher_session_id: str | None = dataclasses.field(default=None, repr=False)
    # turnを実行する委譲先CLIのプロセスの識別。所有するagents_serverの識別とともに登録簿へ公開し、
    # 所有側が終了を公開せずに終了した記録を、照会側が全プロセスの不在から終了と判定する入力にする。
    executor_process: session_registry.ProcessIdentity | None = dataclasses.field(default=None, repr=False)
    # 所有するMCPサーバーの状態ディレクトリのルート。登録簿へ公開し、所有サーバーを失った実行中の案内に使う。
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
        """結果と理由を不可分に保留する。外部の追跡集合と再開履歴は維持する。"""
        self.hold = hold

    def clear_hold(self) -> None:
        """保留を解除する。継続の連鎖と回復消費はそれぞれの所有者が更新する。"""
        self.hold = None

    def set_background_deadline(self, deadline: float | None) -> None:
        """背景完了待ちの期限を更新し、異なる理由の保留を変更しない。"""
        if isinstance(self.hold, turn_state.BackgroundWait):
            self.hold = dataclasses.replace(self.hold, deadline=deadline)

    @property
    def awaiting_auto_resume(self) -> bool:
        """再開または中断の終了確認まで結果を保留しているかを返す。"""
        return self.hold is not None

    @property
    def pending_result(self) -> dict[str, Any] | None:
        """保留理由と一緒に保持された結果を返す。"""
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
        """利用上限の再開予定を返す。"""
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
        """返却形式の回復入力待ちかを返す。"""
        return isinstance(self.hold, turn_state.ResultContractWait)

    @property
    def result_contract_recovery_reason(self) -> str | None:
        """保留中の返却形式の診断を返す。"""
        return self.hold.reason if isinstance(self.hold, turn_state.ResultContractWait) else None

    @property
    def turn_start_sent(self) -> bool:
        """開始要求が未送信・拒否の段階を過ぎたかを返す。"""
        return self.turn_start in {turn_state.TurnStart.SENT, turn_state.TurnStart.STARTED, turn_state.TurnStart.AMBIGUOUS}

    @property
    def turn_start_ambiguous(self) -> bool:
        """開始要求の実行有無が未確定かを返す。"""
        return self.turn_start is turn_state.TurnStart.AMBIGUOUS

    def record_turn_sent(self) -> None:
        """開始要求の送信を記録する。先着した開始通知は保持する。"""
        if self.turn_start is turn_state.TurnStart.UNSENT:
            self.turn_start = turn_state.TurnStart.SENT

    def record_turn_started(self, turn_id: str | None = None) -> None:
        """応答または通知で観測した開始を記録する。"""
        if turn_id is not None:
            self.turn_id = turn_id
        self.turn_start = turn_state.TurnStart.STARTED

    def record_turn_ambiguous(self) -> None:
        """送信済み要求の応答喪失を記録し、既知のturn識別子を保持する。"""
        if self.turn_start is not turn_state.TurnStart.STARTED:
            self.turn_start = turn_state.TurnStart.AMBIGUOUS

    def record_turn_rejected(self) -> None:
        """開始要求が受理されなかったことを記録する。"""
        self.turn_start = turn_state.TurnStart.REJECTED

    def record_turn_completed(self) -> None:
        """終了を記録し、応答喪失中だった開始を確定する。"""
        self.turn_end = turn_state.TurnEnd.COMPLETED
        if self.turn_start is turn_state.TurnStart.AMBIGUOUS:
            self.turn_start = turn_state.TurnStart.STARTED

    def record_turn_running(self) -> None:
        """実行中の終了段階を記録し、開始受理段階を保持する。"""
        self.turn_end = turn_state.TurnEnd.IN_PROGRESS

    def record_failure_pending_completion(self) -> None:
        """失敗を観測し、実行中turnの終了を待つ段階を記録する。"""
        self.turn_end = turn_state.TurnEnd.FAILURE_PENDING

    @property
    def turn_completed(self) -> bool:
        """最新turnの終了を観測済みかを返す。"""
        return self.turn_end is turn_state.TurnEnd.COMPLETED

    @property
    def failure_pending_completion(self) -> bool:
        """失敗を観測した実行中turnの終了待ちかを返す。"""
        return self.turn_end is turn_state.TurnEnd.FAILURE_PENDING

    @property
    def terminal(self) -> bool:
        """最新turnが終了状態であるかを返す。"""
        return self.status in contracts.TERMINAL_STATUSES

    @property
    def result_available(self) -> bool:
        """終了時の結果を返せる状態であるかを返す。"""
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
        """最新テキスト出力の公開用抜粋を返す。"""
        return progress_excerpt(self._progress_text)

    def set_progress(self, text: str) -> None:
        """最新テキスト出力を更新する。"""
        self._progress_text = text
        if text.strip():
            self.output_updated_at = contracts.utc_now()
            self.last_action = progress_excerpt(text)
        self.touch()

    def record_tool_use_start(self, tool_use_id: str, tool_name: str, tool_input: Any = None) -> None:
        """未完了のツール呼び出しを記録し、最後の行動をツール名と入力の要約で更新する。"""
        detail = action_detail(tool_input)
        self.pending_tool_uses[tool_use_id] = (tool_name, contracts.utc_now(), detail)
        self.last_action = f"{tool_name}: {detail}" if detail else tool_name

    def record_tool_use_end(self, tool_use_id: str) -> None:
        """完了したツール呼び出しを未完了の記録から除く。"""
        self.pending_tool_uses.pop(tool_use_id, None)

    def record_current_item_start(self, item: dict[str, Any] | None) -> None:
        """Codex backendの進行中itemと受信時刻を記録し、最後の行動をitem種別と入力の要約で更新する。"""
        self.current_item = item
        if item is None:
            self.current_item_started_at = None
            return
        self.current_item_started_at = contracts.utc_now()
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
        if self.current_item is not None and self.current_item_started_at is not None:
            entry: dict[str, str] = {"started_at": self.current_item_started_at}
            for key in ("type",):
                value = self.current_item.get(key)
                if isinstance(value, str) and value:
                    entry[key] = value
            detail = action_detail(self.current_item, exclude=("type", "id"))
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

    def attach_executor(self, pid: int | None) -> None:
        """turnを実行する委譲先CLIのプロセスを記録する。プロセスを起動し直した場合は次の`touch`で登録簿を更新する。"""
        if pid is None or (self.executor_process is not None and self.executor_process.pid == pid):
            return
        self.executor_process = session_registry.ProcessIdentity.of(pid)

    async def attach_executor_async(self, pid: int | None) -> None:
        """プロセスの起動時刻の照会をworkerで待ち、loop上で登録情報へ反映する。"""
        if pid is None or (self.executor_process is not None and self.executor_process.pid == pid):
            return
        self.executor_process = await run_file_operation(session_registry.ProcessIdentity.of, pid)

    def reset_progress(self) -> None:
        """現在turnの進捗を初期化する。"""
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

    def touch(self) -> None:
        """状態の更新時刻を現在時刻へ更新する。

        turnの終了時の結果が確定した時点で、登録済みの終了通知先へこのsessionを1回だけ渡す。
        turnを再開した後の終了では、同じ通知を改めて1回行う。
        """
        self.updated_at = contracts.utc_now()
        usage_refresh.track_session(self)
        message = (
            self.agent_message
            if self.result_available
            else (self.pending_result.get("agent_message") if self.pending_result is not None else None)
        )
        if isinstance(message, str):
            responses = dict(self.final_responses)
            responses[self.turn_seq] = message
            self.final_responses = tuple(responses.items())
        if self.result_available:
            self.terminal_error = (
                json.dumps(redact_diagnostic_values(self.error), ensure_ascii=False) if self.error is not None else None
            )
            if self.finalized_at is None:
                self.finalized_at = contracts.utc_now()
            if self.retention_deadline is None:
                self.retention_deadline = asyncio.get_running_loop().time() + contracts.RESULT_RETENTION_SECONDS
        else:
            self.retention_deadline = None
        registry_terminal = self.result_available
        event = None
        if self.status == "running" and not self._lifecycle_running:
            self._lifecycle_running = True
            event = "start"
        elif registry_terminal and self._lifecycle_running:
            self._lifecycle_running = False
            event = "terminal"
        if event is not None:
            for lifecycle_listener in tuple(contracts._LIFECYCLE_LISTENERS):
                lifecycle_listener(self, event)
        if self.publish_registry:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                self.publish_registration()
            else:
                if self._registration_task is None or self._registration_task.done():
                    self._registration_task = loop.create_task(self._publish_registration_until_current())
        if not registry_terminal:
            self._terminal_notified = False
        elif not self._terminal_notified:
            self._terminal_notified = True
            contracts._LOG.info(
                "session_transition event=terminal session_id=%s writer=state status=%s turn_seq=%d",
                self.session_id,
                self.status,
                self.turn_seq,
            )
            for terminal_listener in tuple(contracts._TERMINAL_LISTENERS):
                terminal_listener(self)
        contracts.notify_touch_listeners(self)

    async def _publish_registration_until_current(self) -> str | None:
        if not session_registry.owner_process_is_cached():
            await run_file_operation(session_registry.owner_process)
        while self._registration_parameters() is not None:
            error = await self.publish_registration_async()
            if error is not None:
                return error
        return None

    async def drain_registration(self) -> None:
        """通知から開始した登録公開を完了させる。"""
        task = self._registration_task
        if task is not None:
            await finish_task(task)

    @property
    def registration_pending(self) -> bool:
        """通知からの登録公開が進行中かを返す。"""
        return self._registration_task is not None and not self._registration_task.done()

    async def publish_registration_async(self) -> str | None:
        """確定した登録情報だけをworkerへ渡し、成功したsnapshotをloopで覚える。"""
        async with self._registration_lock:
            return await self._publish_registration_async()

    async def _publish_registration_async(self) -> str | None:
        if not session_registry.owner_process_is_cached():
            await run_file_operation(session_registry.owner_process)
        parameters = self._registration_parameters()
        if parameters is None:
            return None
        executor = self.executor_process

        def remember(_result: None) -> None:
            self._remember_registration(parameters, executor)

        try:
            await run_file_operation(session_registry.publish, self.session_id, on_success=remember, **parameters)
        except OSError as error:
            contracts._LOG.exception("session登録の保存に失敗しました: session_id=%s cwd=%s", self.session_id, self.cwd)
            return str(error)
        return None

    def publish_registration(self) -> str | None:
        """登録簿を更新し、保存不成立でも開始済みのsessionと通知を失わせない。

        成功した値だけを公開済みとして覚えるため、次の状態更新・flushでも保存を再試行できる。
        この操作だけでは活動時刻を進めない。
        """
        parameters = self._registration_parameters()
        if parameters is None:
            return None
        try:
            session_registry.publish(self.session_id, **parameters)
        except OSError as error:
            contracts._LOG.exception("session登録の保存に失敗しました: session_id=%s cwd=%s", self.session_id, self.cwd)
            return str(error)
        self._remember_registration(parameters, self.executor_process)
        return None

    def _registration_parameters(self) -> dict[str, Any] | None:
        if not self.publish_registry or self.result_delivered:
            return None
        registry_state = session_registry.PublicationState.from_status(self.status, result_available=self.result_available)
        # 再開ではbackendの初回公開の後に起動情報を写すため、その変化でも更新する。
        launch_info = session_registry.LaunchInfo.of(self)
        if not (
            registry_state != self._published_registry_state
            or self.turn_seq != self._published_registry_turn_seq
            or self.launcher_session_id != self._published_registry_launcher
            or self.fast_mode != self._published_registry_fast_mode
            or self.result_contract_recovery_consumed != self._published_registry_result_contract_recovery_consumed
            or launch_info != self._published_registry_launch_info
            or self.effort != self._published_registry_effort
            or self.executor_process != self._published_registry_executor
            or self.owner_root_session_id != self._published_registry_owner_root
        ):
            return None
        owner = session_registry.owner_process()
        return {
            "state": registry_state,
            "engine": self.engine,
            "cwd": self.cwd,
            "model": self.model,
            "effort": self.effort,
            "fast_mode": self.fast_mode,
            "codex_model_provider": self.codex_model_provider,
            "codex_subscription_provider": self.codex_subscription_provider,
            "codex_state_provider": self.codex_state_provider,
            "claude_api_env_file": self.claude_api_env_file,
            "claude_connection": self.claude_connection,
            "claude_role": self.claude_role,
            "model_type": self.model_type,
            "launch_kind": self.launch_kind,
            "startup_profile": self.startup_profile,
            "turn_seq": self.turn_seq,
            "launch_info": launch_info,
            "result_contract_recovery_consumed": self.result_contract_recovery_consumed,
            "started_at": self.started_at,
            "session_updated_at": self.updated_at,
            "turn_id": self.turn_id or None,
            # 先頭を所有サーバーとする順序契約を保つため、所有サーバーの識別を得られない場合は委譲先CLIも載せない。
            "processes": ()
            if owner is None
            else tuple(process for process in (owner, self.executor_process) if process is not None),
            "owner_root_session_id": self.owner_root_session_id,
            "launcher_session_id": self.launcher_session_id,
        }

    def _remember_registration(self, parameters: dict[str, Any], executor: session_registry.ProcessIdentity | None) -> None:
        self._published_registry_effort = parameters["effort"]
        self._published_registry_launcher = parameters["launcher_session_id"]
        self._published_registry_state = parameters["state"]
        self._published_registry_turn_seq = parameters["turn_seq"]
        self._published_registry_fast_mode = parameters["fast_mode"]
        self._published_registry_result_contract_recovery_consumed = parameters["result_contract_recovery_consumed"]
        self._published_registry_launch_info = parameters["launch_info"]
        self._published_registry_executor = executor
        self._published_registry_owner_root = parameters["owner_root_session_id"]

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
        """継続入力の応答へ退避する直前turnの結果を返す。"""
        return contracts.previous_result_payload(self)
