"""agents_serverのバックエンドが共有するsession状態と検証を定義する。"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import logging
import typing
from collections.abc import Callable, Coroutine
from typing import Any

from agent_toolkit._agents_server import session_registry, task_documents
from agent_toolkit._agents_server.input_validation import validate_prompt
from agent_toolkit._agents_server.result_projection import (
    action_detail,
    nonempty_error,
    progress_excerpt,
    with_result_next_action,
)
from agent_toolkit._common import claude_usage_limit

_LOG = logging.getLogger("agent-toolkit.agents-server.state")

RESULT_RETENTION_SECONDS = 1800.0
# 委譲先の最終活動時刻からの経過が本値を超えた待機の応答へ、停滞の可能性を示す項目を加える。
# 値はユーザーの提案に基づく300秒とする。長時間のコマンドの実行待ちでも超過し得るため、
# 超過は停滞の確定ではなく委譲元が状況を調べる契機として扱う。
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
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})


ModelCandidate = tuple[str, str, str]
LaunchKind = task_documents.LaunchKind
_TOUCH_LISTENERS: set[Callable[[], None]] = set()
_TERMINAL_LISTENERS: set[Callable[[SessionState], None]] = set()
_LIFECYCLE_LISTENERS: set[Callable[[SessionState, str], None]] = set()


def add_lifecycle_listener(listener: Callable[[SessionState, str], None]) -> None:
    """稼働の開始と結果確定の通知先を登録する。"""
    _LIFECYCLE_LISTENERS.add(listener)


def remove_lifecycle_listener(listener: Callable[[SessionState, str], None]) -> None:
    """稼働の開始と結果確定の通知先を解除する。"""
    _LIFECYCLE_LISTENERS.discard(listener)


def add_touch_listener(listener: Callable[[], None]) -> None:
    """session状態の更新通知先を登録する。"""
    _TOUCH_LISTENERS.add(listener)


def remove_touch_listener(listener: Callable[[], None]) -> None:
    """session状態の更新通知先を解除する。"""
    _TOUCH_LISTENERS.discard(listener)


def add_terminal_listener(listener: Callable[[SessionState], None]) -> None:
    """turnの終端結果が確定したsessionの通知先を登録する。"""
    _TERMINAL_LISTENERS.add(listener)


def notify_touch_listeners() -> None:
    """登録された全ての通知先へ、session状態の更新を通知する。"""
    for listener in tuple(_TOUCH_LISTENERS):
        listener()


def remove_terminal_listener(listener: Callable[[SessionState], None]) -> None:
    """turnの終端結果が確定したsessionの通知先を解除する。"""
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
            delivery_task = asyncio.create_task(sender(prompt))
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


@dataclasses.dataclass
class SessionState:
    """MCPから観測できる1つの委譲先sessionと最新turnの共有状態。"""

    session_id: str
    cwd: str
    model: str | None = None
    effort: str | None = None
    engine: str = "codex"
    fast_mode: bool | None = None
    model_type: str | None = None
    launch_kind: LaunchKind = "delegate"
    label: str = ""
    prompt: str = ""
    announced: bool = False
    # sessionを最初に開始した時刻。turnごとに更新する`started_at`とは別に保持し、再開後も引き継ぐ。
    # レーン稼働時間のようにsession全体の経過を測る呼び出し元が起点として読む。
    created_at: str = dataclasses.field(default_factory=utc_now)
    started_at: str = dataclasses.field(default_factory=utc_now)
    excluded_candidates: frozenset[ModelCandidate] = dataclasses.field(default_factory=frozenset)
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
    reply_attempted: bool = False
    reply_turn_started: bool = False
    reply_retryable: bool = False
    turn_start_sent: bool = False
    turn_start_ambiguous: bool = False
    interrupt_requested: bool = False
    turn_completed: bool = False
    failure_pending_completion: bool = False
    # モデル由来の出力（テキスト、思考、ツール呼び出し）を1回以上受信したか。
    # Claudeでは、APIの応答開始（`message_start`）の受信で真とする。
    # engineの可用性失敗は最初のモデル出力より前に生じるため、`start`はこの値で起動直後の終端待ちを打ち切る。
    # `start`は新しいsessionだけを待つため、turnごとに初期化しない。
    model_output_observed: bool = False
    # Claude backendのバックグラウンドタスクだけを表し、Codex backendでは値を持たない。キーはtask識別子とする。
    live_tasks: dict[str, LiveTask] = dataclasses.field(default_factory=dict)
    live_child_session_ids: set[str] = dataclasses.field(default_factory=set)
    terminal_child_session_ids: set[str] = dataclasses.field(default_factory=set)
    # 背景実行へ移った`atk agents wait`の出力ファイルの絶対パス。結果本文がツール結果に現れないため、
    # 孫sessionの終端判定と未観測の記録の直前にこのファイルを読み、回収済みの孫sessionを追跡から外す。
    agents_wait_background_outputs: set[str] = dataclasses.field(default_factory=set)
    child_tool_uses: dict[str, tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=dict, repr=False)
    # 未完了のツール呼び出し。キーは`tool_use_id`、値はツール名、そのブロックを受信した時刻および入力の1行要約の組とする。
    # `child_tool_uses`は`agents_server`のツール呼び出しの引数を孫session追跡のために保持する別の責務を持つため統合しない。
    pending_tool_uses: dict[str, tuple[str, str, str]] = dataclasses.field(default_factory=dict, repr=False)
    # 最後に観測した行動。assistantのテキスト出力ではその抜粋、ツール呼び出しではツール名またはitem種別と入力の1行要約を持つ。
    # statuslineが、テキスト出力の無い区間でも稼働を表示するための射影元とする。
    last_action: str = ""
    awaiting_auto_resume: bool = False
    # Claude Code CLIが`session_state_changed`で報告した最新のturn状態（`running`・`idle`・`requires_action`）。
    # 一度も報告を受けていないsession（Codex backendと、報告を発行しないCLI）では`None`とし、
    # `has_pending_auto_resume_targets`は従来どおり追跡集合だけで判定する。
    # turnをまたいで最新の報告を保持し、turnの開始時に初期化しない（次のturnの開始はCLIが`running`で報告する）。
    cli_turn_state: str | None = None
    # 状態の報告を受け取らずに`ResultMessage`を受け取り、従来の判定へ戻ったことを警告ログへ記録済みか。
    # sessionごとに1回だけ記録する。
    cli_turn_state_fallback_logged: bool = False
    # `auto_resume_consumed`を真にするのは、Claude backendのタスク完了通知による再開と、
    # MCP層が孫sessionの終端を検出して発行する再開の2つの処理だけである。
    # Codex backendは孫sessionが残るturnの結果を保留し、後者の再開だけに到達する。
    auto_resume_consumed: bool = False
    auto_resume_deadline: float | None = None
    # `start`の可用性確認を終えたか。確認を終える前の過負荷は起動時の候補切替が扱う。
    availability_checked: bool = False
    # 過負荷による自動継続。連鎖の中で行った継続の回数、次の継続を送る時刻（イベントループの時計）と、
    # 連鎖の最初の過負荷の時刻を持つ。過負荷以外の終端、委譲元の`kill`・`send_message`および上限到達で初期化する。
    overload_resume_count: int = 0
    overload_resume_at: float | None = None
    overload_first_at: str | None = None
    # Claudeが最後に報告した利用枠の状態。待機の対象かの判定と解除予定時刻に使う。
    usage_limit: claude_usage_limit.UsageLimitState | None = None
    # 利用上限の解除待ち。次に同じsessionへ継続を送る時刻（イベントループの時計）、待機の回数と最初の拒否の時刻を持つ。
    # 待機対象以外の終端と委譲元の`kill`・`send_message`で初期化し、回数では打ち切らない。
    usage_limit_resume_at: float | None = None
    usage_limit_wait_count: int = 0
    usage_limit_first_at: str | None = None
    pending_result: dict[str, Any] | None = None
    finalized_at: str | None = None
    updated_at: str = dataclasses.field(default_factory=utc_now)
    output_updated_at: str | None = None
    api_error: dict[str, Any] | None = None
    # backend資源の解放期限。未回収の終端結果はこの期限を過ぎても保持する。
    retention_deadline: float | None = None
    turn_control_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock, repr=False)
    _progress_text: str = dataclasses.field(default="", repr=False)
    progress_items: dict[str, str] = dataclasses.field(default_factory=dict, repr=False)
    compaction_started_at_ms: dict[str, int] = dataclasses.field(default_factory=dict, repr=False)
    # Codex接続が購読した子孫thread。thread解放だけに使い、状態ファイルや再開記録へ射影しない。
    codex_subagent_thread_ids: set[str] = dataclasses.field(default_factory=set, repr=False)
    publish_registry: bool = dataclasses.field(default=False, repr=False)
    # sessionを作成した時点の委譲元sessionの識別子。登録簿へ公開し、`atk serve`の一覧が親子付けに使う。
    launcher_session_id: str | None = dataclasses.field(default=None, repr=False)
    _published_registry_launcher: str | None = dataclasses.field(default=None, repr=False)
    _published_registry_terminal: bool | None = dataclasses.field(default=None, repr=False)
    _published_registry_turn_seq: int | None = dataclasses.field(default=None, repr=False)
    _published_registry_status: str | None = dataclasses.field(default=None, repr=False)
    _published_registry_fast_mode: bool | None = dataclasses.field(default=None, repr=False)
    _published_registry_launch_info: session_registry.LaunchInfo | None = dataclasses.field(default=None, repr=False)
    _terminal_notified: bool = dataclasses.field(default=False, repr=False)
    _lifecycle_running: bool = dataclasses.field(default=False, repr=False)

    @property
    def terminal(self) -> bool:
        """最新turnが終端状態であるかを返す。"""
        return self.status in TERMINAL_STATUSES

    @property
    def result_available(self) -> bool:
        """終端結果を返せる状態であるかを返す。"""
        return self.terminal and self.turn_completed and not self.turn_start_ambiguous and not self.awaiting_auto_resume

    @property
    def progress(self) -> str:
        """最新テキスト出力の公開用抜粋を返す。"""
        return progress_excerpt(self._progress_text)

    def set_progress(self, text: str) -> None:
        """最新テキスト出力を更新する。"""
        self._progress_text = text
        if text.strip():
            self.output_updated_at = utc_now()
            self.last_action = progress_excerpt(text)
        self.touch()

    def record_tool_use_start(self, tool_use_id: str, tool_name: str, tool_input: Any = None) -> None:
        """未完了のツール呼び出しを記録し、最後の行動をツール名と入力の要約で更新する。"""
        detail = action_detail(tool_input)
        self.pending_tool_uses[tool_use_id] = (tool_name, utc_now(), detail)
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
        self.current_item_started_at = utc_now()
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

    def reset_progress(self) -> None:
        """現在turnの進捗を初期化する。"""
        self._progress_text = ""
        self.progress_items.clear()
        self.output_updated_at = None
        self.api_error = None

    def record_api_error(self, error_type: str, http_status: int | None) -> None:
        """連続するAPI失敗を記録し、活動時刻を変えずに状態の読者へ通知する。"""
        if self.api_error is None:
            self.api_error = {"type": error_type, "http_status": http_status, "first_at": utc_now(), "count": 1}
        else:
            self.api_error["type"] = error_type
            self.api_error["http_status"] = http_status
            self.api_error["count"] += 1
        for listener in tuple(_TOUCH_LISTENERS):
            listener()

    def touch(self) -> None:
        """状態の更新時刻を現在時刻へ更新する。

        turnの終端結果が確定した時点で、登録済みの終端通知先へこのsessionを1回だけ渡す。
        turnを再開した後の終端では、同じ通知を改めて1回行う。
        """
        self.updated_at = utc_now()
        if self.result_available:
            if self.finalized_at is None:
                self.finalized_at = utc_now()
            if self.retention_deadline is None:
                self.retention_deadline = asyncio.get_running_loop().time() + RESULT_RETENTION_SECONDS
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
            for lifecycle_listener in tuple(_LIFECYCLE_LISTENERS):
                lifecycle_listener(self, event)
        # 再開ではbackendが再生成したsessionを公開した後に起動情報を写すため、起動情報の変化でも公開し直す。
        launch_info = session_registry.LaunchInfo.of(self)
        if (
            self.publish_registry
            and not self.result_delivered
            and (
                registry_terminal != self._published_registry_terminal
                or self.turn_seq != self._published_registry_turn_seq
                or self.status != self._published_registry_status
                or self.launcher_session_id != self._published_registry_launcher
                or self.fast_mode != self._published_registry_fast_mode
                or launch_info != self._published_registry_launch_info
            )
        ):
            session_registry.publish(
                self.session_id,
                terminal=registry_terminal,
                engine=self.engine,
                cwd=self.cwd,
                model=self.model,
                effort=self.effort,
                fast_mode=self.fast_mode,
                model_type=self.model_type,
                launch_kind=self.launch_kind,
                turn_seq=self.turn_seq,
                launch_info=launch_info,
                started_at=self.started_at,
                session_updated_at=self.updated_at,
                turn_id=self.turn_id or None,
                status=typing.cast(typing.Literal["starting", "running", "completed", "failed", "interrupted"], self.status),
                launcher_session_id=self.launcher_session_id,
            )
            self._published_registry_launcher = self.launcher_session_id
            self._published_registry_terminal = registry_terminal
            self._published_registry_turn_seq = self.turn_seq
            self._published_registry_status = self.status
            self._published_registry_fast_mode = self.fast_mode
            self._published_registry_launch_info = launch_info
        if not registry_terminal:
            self._terminal_notified = False
        elif not self._terminal_notified:
            self._terminal_notified = True
            _LOG.info(
                "session_transition event=terminal session_id=%s writer=state status=%s turn_seq=%d",
                self.session_id,
                self.status,
                self.turn_seq,
            )
            for terminal_listener in tuple(_TERMINAL_LISTENERS):
                terminal_listener(self)
        for listener in tuple(_TOUCH_LISTENERS):
            listener()

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
        if self.result_delivered:
            return {}
        result: dict[str, Any] = {
            "status": self.status,
            "agent_message": self.agent_message,
        }
        if nonempty_error(self.error):
            result["error"] = self.error
        return with_result_next_action(result, self.label)


@dataclasses.dataclass(frozen=True)
class SessionResumeState:
    """同じ会話の再開と未回収の終端結果の返却に必要な状態を保持する。"""

    session_id: str
    cwd: str
    model: str | None
    effort: str | None
    engine: str
    fast_mode: bool | None = None
    model_type: str | None = None
    launch_kind: LaunchKind = "delegate"
    # `label`・`prompt`・`created_at`は`session_registry.LaunchInfo`の項目であり、再開時はその定義から写す。
    label: str = ""
    prompt: str = ""
    # 登録簿から復元した旧形式のsessionでは開始時刻が不明なため`None`とする。
    created_at: str | None = None
    started_at: str | None = dataclasses.field(default_factory=utc_now)
    updated_at: str | None = dataclasses.field(default_factory=utc_now)
    output_updated_at: str | None = None
    turn_seq: int = 0
    excluded_candidates: frozenset[ModelCandidate] = dataclasses.field(default_factory=frozenset)
    status: str = ""
    agent_message: str = ""
    error: Any = None
    finalized_at: str | None = None
    result_delivered: bool = False
    retention_deadline: float | None = None

    @classmethod
    def from_session(cls, session: SessionState) -> SessionResumeState:
        """終端sessionから再開に必要な入力だけを退避する。"""
        return cls(
            session_id=session.session_id,
            cwd=session.cwd,
            model_type=session.model_type,
            launch_kind=session.launch_kind,
            **session_registry.LaunchInfo.of(session).as_kwargs(),
            started_at=session.started_at,
            updated_at=session.updated_at,
            output_updated_at=session.output_updated_at,
            turn_seq=session.turn_seq,
            excluded_candidates=session.excluded_candidates,
            model=session.model,
            effort=session.effort,
            engine=session.engine,
            fast_mode=session.fast_mode,
            status=session.status,
            agent_message=session.agent_message,
            error=session.error,
            finalized_at=session.finalized_at,
            result_delivered=session.result_delivered,
            retention_deadline=session.retention_deadline,
        )


def fast_mode_fields(engine: str | None, fast_mode: object) -> dict[str, bool]:
    """Codexの既知の速度だけを公開し、旧形式と他engineの表示を保持する。"""
    return {"fast_mode": fast_mode} if engine == "codex" and isinstance(fast_mode, bool) else {}


def selected_candidate(session: SessionState | SessionResumeState) -> ModelCandidate | None:
    """sessionの起動時に確定した候補を返す。"""
    if session.model is None or session.effort is None:
        return None
    return session.engine, session.model, session.effort


def has_uncollected_result(session: SessionState | SessionResumeState, result_consumed: bool | None) -> bool:
    """終端結果が未回収かを返す。

    結果ファイルを扱える消費側は回収状態を渡し、結果ファイルを扱えない処理だけは`None`を渡す。
    """
    if session.finalized_at is None or session.result_delivered:
        return False
    return result_consumed is None or not result_consumed


def terminal_result_payload(session: SessionState | SessionResumeState) -> dict[str, Any]:
    """終端結果ファイルへ保存する結果と復旧用の項目を返す。

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
        result["error"] = session.error
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
    session.reply_retryable = False
    session.turn_start_sent = False
    session.turn_start_ambiguous = False
    session.interrupt_requested = False
    session.turn_completed = False
    session.failure_pending_completion = False
    if not preserve_waits:
        session.live_child_session_ids.clear()
        session.terminal_child_session_ids.clear()
        session.agents_wait_background_outputs.clear()
        session.child_tool_uses.clear()
        session.auto_resume_consumed = False
    session.pending_tool_uses.clear()
    session.last_action = ""
    session.awaiting_auto_resume = False
    session.auto_resume_deadline = None
    session.pending_result = None
    session.finalized_at = None
    session.retention_deadline = None
    session.started_at = utc_now()
    if reset_progress:
        session.reset_progress()
    session.touch()


def begin_reply(session: SessionState, *, preserve_waits: bool = False) -> None:
    """終端済みsessionの新しいturnを開始する準備をする。"""
    session.turn_seq += 1
    initialize_turn(session, reset_progress=False, preserve_waits=preserve_waits)
    session.reply_attempted = True
    session.reply_turn_started = False
    session.touch()
