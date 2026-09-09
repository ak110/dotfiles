"""agents_serverのバックエンドが共有するsession状態と検証を定義する。"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json
import pathlib
import typing
from collections.abc import Callable, Coroutine, Mapping
from typing import Any, Literal

from agent_toolkit._agents_server import session_registry

RESULT_RETENTION_SECONDS = 1800.0
# 自動再開の待機上限は終端結果の保持期限とは目的が異なる。本計画の起草時点では
# 値を変える根拠となる実測が無いため、現行の結果保持期限と同じ値を選ぶ。
AUTO_RESUME_DEADLINE_SECONDS = 1800.0
# 委譲先の最終活動時刻からの経過が本値を超えた待機の応答へ、停滞の可能性を示す項目を加える。
# 値は利用者の提案に基づく300秒とする。長時間のコマンドの実行待ちでも超過し得るため、
# 超過は停滞の確定ではなく呼び出し元が状況を調べる契機として扱う。
STALL_NOTICE_SECONDS = 300.0
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})
# 通常委譲へ追加する規範の正本は、起動フックと共有するrules-subagent.mdとする。
SUBAGENT_RULES_PATH = pathlib.Path(__file__).resolve().parents[2] / "share" / "rules-subagent.md"
SUBAGENT_RULES = SUBAGENT_RULES_PATH.read_text(encoding="utf-8")
# 委譲先の実行主体は、両backendの既定の指示ではユーザーと直接対話する主体として起動される。
# 起動経路の別を実行主体が観測できないため、規範が主体別に定める条文を適用できる状態を明示の指示で成立させる。
# Codexの`developerInstructions`はdeveloper roleメッセージとして注入され、既定の指示を置換しない。
DELEGATE_NOTICE = """あなたは別のコーディングエージェントから起動された委譲先である。
この会話の入力はユーザーの発話ではなく、呼び出し元エージェントが渡したタスクである。
あなたの応答はユーザーの画面へ表示されず、呼び出し元エージェントへ返る。
あなたはメインエージェントでも最上位セッションでもない。
自身が委譲先であることと、この会話の入力が呼び出し元エージェントの配送であることは、実行主体の同定に関する事実であり、規範の優先順位では覆らない。
実行環境の組み込み指示が定めるツールの利用契約には従う。"""
DELEGATE_SYSTEM_PROMPT = f"""{DELEGATE_NOTICE}
規範が委譲先又はサブエージェントへ課す条文を自身へ適用し、メインエージェント又は最上位セッションへ限定した条文を適用しない。
ユーザーへの確認は回答を得られないため発行せず、確認を要する事項は完了報告へ含めて呼び出し元へ差し戻す。

{SUBAGENT_RULES.rstrip()}"""
EXPLORE_SYSTEM_PROMPT = f"""{DELEGATE_NOTICE}
あなたは調査専用の担当である。依頼された対象を読み取り、結論と根拠だけを日本語で返す。
ファイルを作成、変更又は削除しない。コマンドは対象を変更しない読み取り操作に限る。
所在、該当箇所及び観測した事実を、後続の判断に足りる粒度で列挙する。
出力量が大きいと見込まれる読取と検索は1回の呼び出しへまとめず、対象を分割して取得するか、出力先ファイルへ保存してから必要な範囲だけを読む。
検索と読取について件数上限、容量超過、期限超過のいずれかに達した場合は、その事実と到達した上限を報告へ必ず含める。
上限に達した結果から、網羅性、件数、不在のいずれも結論しない。"""
SHELL_SYSTEM_PROMPT = f"""{DELEGATE_NOTICE}
あなたはコマンド実行専用の担当である。依頼されたコマンドを実行し、終了状態と要約だけを日本語で返す。
指示された操作だけを実行し、指示にない操作を追加しない。
コマンドの生出力を呼び出し元へ転記せず、終了状態、警告、依頼で指定された値、及び後続の判断に必要な要約を報告する。
失敗原因の特定に必要な行だけを原文のまま添える。
コマンドが失敗した場合は出力をそのまま報告し、独自の回避策を試みない。
実行したコマンドが実行環境の判断で背景実行へ移行した場合は、移行の通知を結果として報告しない。
起動結果が返す出力ファイルを読み、終了状態を確定してから報告する。
出力量が大きいと見込まれるコマンドは1回の実行へまとめず、対象を分割して実行するか、出力先ファイルへリダイレクトしてから必要な範囲だけを読む。
実行ツールが出力の切り詰め、容量超過、期限超過のいずれかを通知した場合は、その事実と切り詰められた範囲を要約へ必ず含める。
切り詰めを含む出力から、成功、網羅性、件数、終端のいずれも結論しない。"""
ModelCandidate = tuple[str, str, str]
LaunchKind = Literal["delegate", "explore", "shell"]
# 起動条件の種別ごとのシステム指示。Claude backendの通常委譲だけは、preset指示へ追記する形で渡す。
LAUNCH_SYSTEM_PROMPTS: dict[LaunchKind, str] = {
    "delegate": DELEGATE_SYSTEM_PROMPT,
    "explore": EXPLORE_SYSTEM_PROMPT,
    "shell": SHELL_SYSTEM_PROMPT,
}
AUTO_RESUME_NOTICE = (
    "この実行経路は、あなたが起動した委譲先（サブエージェント）の完了通知により、"
    "同じsessionを一度だけ自動的に再開する。\n"
    "`agents_server`で起動したsessionを待つ場合も、当該sessionの終端後に同じ再開が働き、"
    "当該ターンにつき一度だけ継続指示が届く。\n"
    "当該委譲先の完了を待つ場合は`待機中: <待機対象>`の1行だけを出力して当該ターンを終え、"
    "再開したターンで所定の返却形式を返す。\n"
    "背景ジョブはこの自動再開の対象ではない。背景ジョブの終了状態は同じターンの中で確定してから報告する。"
)
# プロジェクト指示と設定の読込を省く軽量な起動条件を共有する種別。
LIGHTWEIGHT_LAUNCH_KINDS = frozenset({"explore", "shell"})
_TOUCH_LISTENERS: set[Callable[[], None]] = set()
_TERMINAL_LISTENERS: set[Callable[[SessionState], None]] = set()


def add_touch_listener(listener: Callable[[], None]) -> None:
    """session状態の更新通知先を登録する。"""
    _TOUCH_LISTENERS.add(listener)


def remove_touch_listener(listener: Callable[[], None]) -> None:
    """session状態の更新通知先を解除する。"""
    _TOUCH_LISTENERS.discard(listener)


def add_terminal_listener(listener: Callable[[SessionState], None]) -> None:
    """turnの終端結果が確定したsessionの通知先を登録する。"""
    _TERMINAL_LISTENERS.add(listener)


def remove_terminal_listener(listener: Callable[[SessionState], None]) -> None:
    """turnの終端結果が確定したsessionの通知先を解除する。"""
    _TERMINAL_LISTENERS.discard(listener)


class SessionOwnerGoneError(RuntimeError):
    """session所有タスクが終了し、継続要求または中断要求を配送できないことを示す。

    MCP層はこの例外を受領して、保存済みの再開状態から同じ会話を再開する。
    """


class ResumePrompt:
    """進行中のsession再開へ、無効化可能な継続入力を1件ずつ渡す。"""

    def __init__(self, prompt: str) -> None:
        _validate_prompt(prompt)
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
        _validate_prompt(prompt)
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


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _progress_excerpt(text: str) -> str:
    """テキストを改行なしの末尾80文字へ正規化する。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    if len(normalized) <= 80:
        return normalized
    return f"…{normalized[-80:]}"


def _nonempty_error(error: Any) -> bool:
    return error is not None and error != "" and error != {}


def _append_bounded(existing: str, delta: str, limit: int = 4000) -> str:
    value = existing + delta
    return value if len(value) <= limit else value[-limit:]


@dataclasses.dataclass
class SessionState:
    """MCPから観測できる1つの委譲先sessionと最新turnの共有状態。"""

    session_id: str
    cwd: str
    model: str | None = None
    effort: str | None = None
    engine: str = "codex"
    model_type: str | None = None
    launch_kind: LaunchKind = "delegate"
    label: str = ""
    announced: bool = False
    started_at: str = dataclasses.field(default_factory=_utc_now)
    excluded_candidates: frozenset[ModelCandidate] = dataclasses.field(default_factory=frozenset)
    turn_seq: int = 0
    turn_id: str = ""
    status: str = "running"
    plan: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    current_item: dict[str, Any] | None = None
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
    # Claude backendの背景タスクだけを表し、Codex backendでは値を持たない。
    live_task_ids: set[str] = dataclasses.field(default_factory=set)
    live_child_session_ids: set[str] = dataclasses.field(default_factory=set)
    terminal_child_session_ids: set[str] = dataclasses.field(default_factory=set)
    child_tool_uses: dict[str, tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=dict, repr=False)
    awaiting_auto_resume: bool = False
    # `auto_resume_consumed`は、Claude backendのタスク完了通知による再開と、
    # MCP層が孫sessionの終端を検出して発行する再開の2経路だけが真にする。
    # Codex backendは終端結果を保留しないため、当該経路へ到達しない。
    auto_resume_consumed: bool = False
    auto_resume_deadline: float | None = None
    pending_result: dict[str, Any] | None = None
    finalized_at: str | None = None
    updated_at: str = dataclasses.field(default_factory=_utc_now)
    # backend資源の解放期限。未回収の終端結果はこの期限を過ぎても保持する。
    retention_deadline: float | None = None
    turn_control_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock, repr=False)
    _progress_text: str = dataclasses.field(default="", repr=False)
    progress_items: dict[str, str] = dataclasses.field(default_factory=dict, repr=False)
    publish_registry: bool = dataclasses.field(default=False, repr=False)
    _published_registry_terminal: bool | None = dataclasses.field(default=None, repr=False)
    _published_registry_turn_seq: int | None = dataclasses.field(default=None, repr=False)
    _published_registry_status: str | None = dataclasses.field(default=None, repr=False)
    _terminal_notified: bool = dataclasses.field(default=False, repr=False)

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
        return _progress_excerpt(self._progress_text)

    def set_progress(self, text: str) -> None:
        """最新テキスト出力を更新する。"""
        self._progress_text = text
        self.touch()

    def reset_progress(self) -> None:
        """現在turnの進捗を初期化する。"""
        self._progress_text = ""
        self.progress_items.clear()

    def touch(self) -> None:
        """状態の更新時刻を現在時刻へ更新する。

        turnの終端結果が確定した時点で、登録済みの終端通知先へ当該sessionを1回だけ渡す。
        turnを再開した後の終端では、同じ通知を改めて1回行う。
        """
        self.updated_at = _utc_now()
        if self.result_available:
            if self.finalized_at is None:
                self.finalized_at = _utc_now()
            if self.retention_deadline is None:
                self.retention_deadline = asyncio.get_running_loop().time() + RESULT_RETENTION_SECONDS
        else:
            self.retention_deadline = None
        registry_terminal = self.result_available
        if (
            self.publish_registry
            and not self.result_delivered
            and (
                registry_terminal != self._published_registry_terminal
                or self.turn_seq != self._published_registry_turn_seq
                or self.status != self._published_registry_status
            )
        ):
            session_registry.publish(
                self.session_id,
                terminal=registry_terminal,
                engine=self.engine,
                cwd=self.cwd,
                model=self.model,
                effort=self.effort,
                model_type=self.model_type,
                launch_kind=self.launch_kind,
                turn_seq=self.turn_seq,
                status=typing.cast(typing.Literal["running", "completed", "failed", "interrupted"], self.status),
            )
            self._published_registry_terminal = registry_terminal
            self._published_registry_turn_seq = self.turn_seq
            self._published_registry_status = self.status
        if not registry_terminal:
            self._terminal_notified = False
        elif not self._terminal_notified:
            self._terminal_notified = True
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
            if _nonempty_error(self.error):
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
        if _nonempty_error(self.error):
            result["error"] = self.error
        return result


@dataclasses.dataclass(frozen=True)
class SessionResumeState:
    """同じ会話の再開と未回収の終端結果の返却に必要な状態を保持する。"""

    session_id: str
    cwd: str
    model: str | None
    effort: str | None
    engine: str
    model_type: str | None = None
    launch_kind: LaunchKind = "delegate"
    label: str = ""
    started_at: str = dataclasses.field(default_factory=_utc_now)
    updated_at: str = dataclasses.field(default_factory=_utc_now)
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
            label=session.label,
            started_at=session.started_at,
            updated_at=session.updated_at,
            turn_seq=session.turn_seq,
            excluded_candidates=session.excluded_candidates,
            model=session.model,
            effort=session.effort,
            engine=session.engine,
            status=session.status,
            agent_message=session.agent_message,
            error=session.error,
            finalized_at=session.finalized_at,
            result_delivered=session.result_delivered,
            retention_deadline=session.retention_deadline,
        )


def selected_candidate(session: SessionState | SessionResumeState) -> ModelCandidate | None:
    """sessionの起動時に確定した候補を返す。"""
    if session.model is None or session.effort is None:
        return None
    return session.engine, session.model, session.effort


def has_pending_auto_resume_targets(session: SessionState) -> bool:
    """自動再開が追跡する子session又はClaude taskが残るかを返す。

    Claude・Codex backendとMCP層は、開始、解除、再開の全条件で本述語だけを使う。
    """
    return bool(session.live_task_ids or session.live_child_session_ids)


def has_uncollected_result(session: SessionState | SessionResumeState, result_consumed: bool | None) -> bool:
    """終端結果が未回収かを返す。

    結果ファイルを扱える消費側は回収状態を渡し、扱えない経路だけは`None`を渡す。
    """
    if session.finalized_at is None or session.result_delivered:
        return False
    return result_consumed is None or not result_consumed


def terminal_result_payload(session: SessionState | SessionResumeState) -> dict[str, Any]:
    """終端結果ファイルへ保存する公開結果を返す。

    保持中と退避済みのsessionが同じ結果本文を公開できるよう、必要な項目だけへ射影する。
    """
    result: dict[str, Any] = {
        "status": session.status,
        "agent_message": session.agent_message,
        "turn_seq": session.turn_seq,
        "finalized_at": session.finalized_at,
    }
    if _nonempty_error(session.error):
        result["error"] = session.error
    return result


def _initialize_turn(session: SessionState, *, reset_progress: bool = True) -> None:
    """新しいturnの開始前に共有状態を初期化する。"""
    session.turn_id = ""
    session.status = "running"
    session.plan = []
    session.current_item = None
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
    session.live_child_session_ids.clear()
    session.terminal_child_session_ids.clear()
    session.child_tool_uses.clear()
    session.awaiting_auto_resume = False
    session.auto_resume_consumed = False
    session.auto_resume_deadline = None
    session.pending_result = None
    session.finalized_at = None
    session.retention_deadline = None
    session.started_at = _utc_now()
    if reset_progress:
        session.reset_progress()
    session.touch()


def consume_claude_agents_server_message(session: SessionState, message: Any) -> None:
    """Claude SDKのツール利用と結果から孫sessionの状態を更新する。"""
    for block in _content_blocks(message):
        tool_use_id = _block_value(block, "id")
        tool_name = _block_value(block, "name")
        tool_input = _block_value(block, "input")
        if isinstance(tool_use_id, str) and isinstance(tool_name, str):
            normalized = _agents_server_tool_name(tool_name)
            if normalized is not None:
                arguments = dict(tool_input) if isinstance(tool_input, Mapping) else {}
                session.child_tool_uses[tool_use_id] = (normalized, arguments)
                continue

        tool_use_id = _block_value(block, "tool_use_id")
        if not isinstance(tool_use_id, str):
            continue
        tool_use = session.child_tool_uses.pop(tool_use_id, None)
        if tool_use is None:
            continue
        result = _structured_tool_result(_block_value(block, "content"))
        if result is not None:
            consume_agents_server_tool_result(session, tool_use[0], tool_use[1], result)


def consume_agents_server_tool_result(
    session: SessionState,
    tool_name: str,
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    """agents_serverツールの結果を孫session集合へ反映する。"""
    normalized = _agents_server_tool_name(tool_name)
    if normalized in {"start", "start_explore", "start_shell"}:
        session_id = result.get("session_id")
        if isinstance(session_id, str) and session_id:
            session.live_child_session_ids.add(session_id)
        return
    if normalized not in {"wait", "kill"}:
        return
    session_id = arguments.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    if result.get("status") in TERMINAL_STATUSES | {"expired"}:
        session.live_child_session_ids.discard(session_id)
        session.terminal_child_session_ids.add(session_id)
        session_registry.remove(session_id)


def finalize_pending_result(session: SessionState, *, touch: bool = True) -> None:
    """保留したturn結果を公開可能な終端状態へ移す。"""
    result = session.pending_result
    if result is None:
        raise RuntimeError("auto-resume wait has no pending result")
    session.awaiting_auto_resume = False
    session.auto_resume_deadline = None
    session.pending_result = None
    session.live_child_session_ids.clear()
    session.status = result["status"]
    session.agent_message = result["agent_message"]
    session.error = result["error"]
    session.turn_completed = True
    session.turn_start_ambiguous = False
    if touch:
        session.touch()


def begin_auto_resume_wait(session: SessionState, result: dict[str, Any]) -> float:
    """終端結果を保留し、自動再開の待機期限を返す。"""
    session.pending_result = result
    session.awaiting_auto_resume = True
    deadline = asyncio.get_running_loop().time() + AUTO_RESUME_DEADLINE_SECONDS
    session.auto_resume_deadline = deadline
    return deadline


def record_unobserved_sessions(session: SessionState, session_ids: set[str]) -> None:
    """未観測の孫session識別子を既存のerror項目へ併合する。"""
    identifiers = set(session_ids)
    current = session.error
    error: dict[str, Any]
    if isinstance(current, dict):
        error = dict(current)
    elif _nonempty_error(current):
        error = {"message": str(current)}
    else:
        error = {}
    existing_identifiers = error.get("unobservedSessions")
    if isinstance(existing_identifiers, list):
        identifiers.update(item for item in existing_identifiers if isinstance(item, str))
    error["unobservedSessions"] = sorted(identifiers)
    session.error = error
    session.touch()


def _agents_server_tool_name(tool_name: str) -> str | None:
    prefix = "mcp__agents_server__"
    if tool_name.startswith(prefix):
        return tool_name.removeprefix(prefix)
    if tool_name in {"start", "start_explore", "start_shell", "wait", "kill"}:
        return tool_name
    return None


def _content_blocks(message: Any) -> tuple[Any, ...]:
    content = _block_value(message, "content")
    return tuple(content) if isinstance(content, list | tuple) else ()


def _block_value(block: Any, name: str) -> Any:
    return block.get(name) if isinstance(block, Mapping) else getattr(block, name, None)


def _structured_tool_result(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        structured = value.get("structuredContent")
        if isinstance(structured, Mapping):
            return dict(structured)
        if isinstance(value.get("session_id"), str) or "status" in value:
            return dict(value)
        for key in ("content", "result"):
            nested = _structured_tool_result(value.get(key))
            if nested is not None:
                return nested
        text = value.get("text")
        if isinstance(text, str):
            return _structured_tool_result(text)
        return None
    if isinstance(value, list | tuple):
        for item in value:
            result = _structured_tool_result(item)
            if result is not None:
                return result
        return None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return _structured_tool_result(decoded)
    return None


def _begin_reply(session: SessionState) -> None:
    """終端済みsessionの新しいturnを開始する準備をする。"""
    session.turn_seq += 1
    _initialize_turn(session, reset_progress=False)
    session.reply_attempted = True
    session.reply_turn_started = False
    session.touch()


def _validate_prompt(prompt: str) -> None:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")


def _validate_cwd(cwd: str) -> None:
    if not isinstance(cwd, str) or not cwd or not pathlib.PurePath(cwd).is_absolute():
        raise ValueError("cwd must be a non-empty absolute path")
    if not pathlib.Path(cwd).is_dir():
        raise ValueError(f"cwd is not an existing directory: {cwd}")


def _validate_shell_request(command: str, summary_policy: str) -> None:
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    if not isinstance(summary_policy, str) or not summary_policy.strip():
        raise ValueError("summary_policy must be a non-empty string")


def _validate_model_effort(model: str | None, effort: str | None) -> None:
    if (model is None) != (effort is None):
        raise ValueError("model and effort must be provided together")
    if model is not None and (not model.strip() or not effort or not effort.strip()):
        raise ValueError("model and effort must be non-empty strings")
