"""agents_serverのバックエンドが共有するsession状態と検証を定義する。"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import json
import logging
import pathlib
import typing
from collections.abc import Callable, Coroutine, Mapping
from typing import Any, Literal

from agent_toolkit._agents_server import session_registry, tool_names

_LOG = logging.getLogger("agent-toolkit.agents-server.state")

RESULT_RETENTION_SECONDS = 1800.0
# 自動再開の待機上限は終端結果の保持期限とは目的が異なる。本計画の起草時点では
# 値を変える根拠となる実測が無いため、現行の結果保持期限と同じ値を選ぶ。
AUTO_RESUME_DEADLINE_SECONDS = 1800.0
# 委譲先の最終活動時刻からの経過が本値を超えた待機の応答へ、停滞の可能性を示す項目を加える。
# 値は利用者の提案に基づく300秒とする。長時間のコマンドの実行待ちでも超過し得るため、
# 超過は停滞の確定ではなく呼び出し元が状況を調べる契機として扱う。
STALL_NOTICE_SECONDS = 300.0
# ホストが応答しないMCPツール呼び出しを背景タスクへ移すまでの秒数。
# 以降の上限はこの閾値を制約として導出する。単独の値として決めない。
HOST_BACKGROUND_THRESHOLD_SECONDS = 120.0
# backendがsessionの初期化を完了するまで起動側が待つ上限秒数と、同じ候補で試みる回数。
# Claude Codeの記録では、start系ツールの呼び出しから起動された子sessionの記録の先頭エントリまでの
# 経過が233件中232件で47.65秒以内に収まり、残る1件が604.22秒だった。
# 同じ母集団のうち7件は初期化が到達せず、ホストがMCPツール呼び出しを1800.5秒で打ち切っていた。
# 1回の上限は観測の上位側の47.65秒を含む値とし、回数との積へ起動直後の可用性失敗を待つ上限
# （agents_server_mcp.pyのSTART_AVAILABILITY_TIMEOUT）を直列に加えた和が
# HOST_BACKGROUND_THRESHOLD_SECONDSを下回るように選ぶ。
# この関係が崩れると、初期化の失敗が確定する前にホストがツール呼び出しを背景へ移し、
# 呼び出し元は`start`の失敗を受け取らないまま待機へ進む。
# 監査記録は`docs/development/audit-records.md`の
# 「agent-toolkit/agent_toolkit/_agents_server/state.py：session初期化の待機上限：2026年9月11日」にある。
SESSION_INITIALIZATION_TIMEOUT = 50.0
SESSION_INITIALIZATION_ATTEMPTS = 2
# `atk agents wait`が待機対象を1件以上取得した後に用いる上限秒数。
WAIT_TIMEOUT_SECONDS = 3600.0
# `atk agents wait`が待機対象を1件も取得できない状態を続けられる上限秒数。
# 起動に失敗した委譲先はsessionを登録しないため、対象が空のまま待ち続けると呼び出し元が失敗を観測できない。
# 対象は待機中にも追加されるため空であることを即時の終了条件にはできず、
# SESSION_INITIALIZATION_TIMEOUTとSESSION_INITIALIZATION_ATTEMPTSの積へ
# START_AVAILABILITY_TIMEOUTを加えた和を上回る値を選ぶ。
# この関係が崩れると、初期化中の委譲先を待つ正常な待機を打ち切る。
EMPTY_WAIT_TIMEOUT_SECONDS = 150.0
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})
TASK_MODEL_TYPES = {
    "add-wi.subagent.md": "execute",
    "exec-review.subagent.md": "execute_review",
    "exec.subagent.md": "execute",
    "lane-integration.subagent.md": "execute",
    "pick-wi.subagent.md": "pick_wi",
    "session-review-delegate.subagent.md": "session_review",
    "session-termination.subagent.md": "execute",
    "upstream-submission.subagent.md": "execute",
}
"""専用タスク文書名と工程別モデル設定の対応。"""
SHARE_DIR = pathlib.Path(__file__).resolve().parents[2] / "share"


def _read_share(name: str) -> str:
    """共有プロンプト又は規範を末尾改行なしで読む。"""
    return (SHARE_DIR / name).read_text(encoding="utf-8").rstrip("\n")


def _read_prompt(name: str) -> str:
    """Markdownの先頭見出しを除いた固定プロンプトを読む。"""
    return _read_share(name).split("\n\n", maxsplit=1)[1]


# 通常委譲へ追加する規範の正本は、起動フックと共有するrules-subagent.mdとする。
SUBAGENT_RULES = _read_share("rules-subagent.md")
CLAUDE_CODE_SUBAGENT_RULES = _read_share("rules-subagent.claude-code.md")
# 委譲先の実行主体は、両backendの既定の指示ではユーザーと直接対話する主体として起動される。
# 起動経路の別を実行主体が観測できないため、規範が主体別に定める条文を適用できる状態を明示の指示で成立させる。
# Codexの`developerInstructions`はdeveloper roleメッセージとして注入され、既定の指示を置換しない。
DELEGATE_NOTICE = _read_prompt("agents-server-delegate-notice.md")
DELEGATE_SYSTEM_PROMPT = f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-delegate.md')}\n\n{SUBAGENT_RULES}"
CLAUDE_DELEGATE_SYSTEM_PROMPT = f"{DELEGATE_SYSTEM_PROMPT}\n\n{CLAUDE_CODE_SUBAGENT_RULES}"
EXPLORE_SYSTEM_PROMPT = f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-explore.md')}"
SHELL_SYSTEM_PROMPT = f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-shell.md')}"
WRITE_SYSTEM_PROMPT = f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-write.md')}"
ModelCandidate = tuple[str, str, str]
LaunchKind = Literal["delegate", "explore", "shell", "write"]
# 起動条件の種別ごとのシステム指示。Claude backendの通常委譲だけは、preset指示へ追記する形で渡す。
LAUNCH_SYSTEM_PROMPTS: dict[LaunchKind, str] = {
    "delegate": DELEGATE_SYSTEM_PROMPT,
    "explore": EXPLORE_SYSTEM_PROMPT,
    "shell": SHELL_SYSTEM_PROMPT,
    "write": WRITE_SYSTEM_PROMPT,
}
AUTO_RESUME_NOTICE = _read_prompt("agents-server-auto-resume.md")
# プロジェクト指示と設定の読込を省く軽量な起動条件を共有する種別。
LIGHTWEIGHT_LAUNCH_KINDS = frozenset({"explore", "shell", "write"})
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


class SessionInitializationTimeoutError(RuntimeError):
    """backendがsessionの初期化を上限内に完了せず、起動を打ち切ったことを示す。

    起動を要求した主体は無制限に待たされる代わりに本例外を受領し、対象と原因を報告できる。
    """


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


def elapsed_seconds(value: str | None) -> int | None:
    """ISO 8601のタイムゾーン付き時刻から現在までの経過秒を返す。

    解釈できない値とタイムゾーンを持たない値では`None`を返す。
    """
    if not isinstance(value, str):
        return None
    try:
        timestamp = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if timestamp.utcoffset() is None:
        return None
    return max(0, int((datetime.datetime.now(datetime.UTC) - timestamp).total_seconds()))


def activity_projection(
    *,
    updated_at: str | None,
    output_updated_at: str | None,
    started_at: str | None,
) -> dict[str, Any]:
    """活動とテキスト出力の各時刻からの経過、及び停滞の印を公開項目へ射影する。

    停滞の判定入力は活動時刻とする。テキスト出力の時刻を判定入力にすると、
    ツール呼び出しだけを長時間続ける正常なsessionを停滞と判定し、
    呼び出し元が不要な催促と巻き取りへ進む。
    テキスト出力の停止と活動の停止を呼び出し元が1回の照会で切り分けられるよう、
    両者の時刻と経過を同じ応答へ並べる。
    `show`・`list`・`atk agents wait`・`atk agents list`の4経路は本関数を共有する。
    経路ごとに判定入力が分かれると、同じsessionへ異なる停滞の印が返る。
    """
    seconds_since_activity = elapsed_seconds(updated_at or started_at)
    if seconds_since_activity is None:
        return {}
    projection: dict[str, Any] = {
        "updated_at": updated_at,
        "seconds_since_activity": seconds_since_activity,
        "output_updated_at": output_updated_at,
        "seconds_since_output": elapsed_seconds(output_updated_at or started_at),
    }
    if seconds_since_activity >= STALL_NOTICE_SECONDS:
        projection["stalled"] = True
    return projection


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
    prompt: str = ""
    announced: bool = False
    started_at: str = dataclasses.field(default_factory=_utc_now)
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
    # Claude backendの背景タスクだけを表し、Codex backendでは値を持たない。
    live_task_ids: set[str] = dataclasses.field(default_factory=set)
    live_child_session_ids: set[str] = dataclasses.field(default_factory=set)
    terminal_child_session_ids: set[str] = dataclasses.field(default_factory=set)
    child_tool_uses: dict[str, tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=dict, repr=False)
    # 未完了のツール呼び出し。キーは`tool_use_id`、値はツール名と当該ブロックを受信した時刻の対とする。
    # `child_tool_uses`は`agents_server`のツール呼び出しの引数を孫session追跡のために保持する別の責務を持つため統合しない。
    pending_tool_uses: dict[str, tuple[str, str]] = dataclasses.field(default_factory=dict, repr=False)
    # 最後に観測した行動。assistantのテキスト出力ではその抜粋、ツール呼び出しではツール名又はitem種別を持つ。
    # statuslineが、テキスト出力の無い区間でも稼働を表示するための射影元とする。
    last_action: str = ""
    awaiting_auto_resume: bool = False
    # `auto_resume_consumed`は、Claude backendのタスク完了通知による再開と、
    # MCP層が孫sessionの終端を検出して発行する再開の2経路だけが真にする。
    # Codex backendは終端結果を保留しないため、当該経路へ到達しない。
    auto_resume_consumed: bool = False
    auto_resume_deadline: float | None = None
    pending_result: dict[str, Any] | None = None
    finalized_at: str | None = None
    updated_at: str = dataclasses.field(default_factory=_utc_now)
    output_updated_at: str | None = None
    # backend資源の解放期限。未回収の終端結果はこの期限を過ぎても保持する。
    retention_deadline: float | None = None
    turn_control_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock, repr=False)
    _progress_text: str = dataclasses.field(default="", repr=False)
    progress_items: dict[str, str] = dataclasses.field(default_factory=dict, repr=False)
    compaction_started_at_ms: dict[str, int] = dataclasses.field(default_factory=dict, repr=False)
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
        if text.strip():
            self.output_updated_at = _utc_now()
            self.last_action = _progress_excerpt(text)
        self.touch()

    def record_tool_use_start(self, tool_use_id: str, tool_name: str) -> None:
        """未完了のツール呼び出しを記録し、最後の行動をツール名で更新する。"""
        self.pending_tool_uses[tool_use_id] = (tool_name, _utc_now())
        self.last_action = tool_name

    def record_tool_use_end(self, tool_use_id: str) -> None:
        """完了したツール呼び出しを未完了の記録から除く。"""
        self.pending_tool_uses.pop(tool_use_id, None)

    def record_current_item_start(self, item: dict[str, Any] | None) -> None:
        """Codex backendの進行中itemと受信時刻を記録し、最後の行動をitem種別で更新する。"""
        self.current_item = item
        if item is None:
            self.current_item_started_at = None
            return
        self.current_item_started_at = _utc_now()
        item_type = item.get("type")
        if isinstance(item_type, str) and item_type:
            self.last_action = item_type

    def active_tool_uses(self) -> list[dict[str, str]]:
        """未完了のツール呼び出しを、開始時刻の昇順で公開項目へ射影する。

        呼び出し元が停滞を疑った時点で、長時間のコマンドの実行中か活動そのものの停止かを
        1回の照会で切り分けられるようにする。
        Claude backendは`tool_use`ブロックの記録から、Codex backendは進行中itemから射影する。
        1つのsessionはいずれか一方のbackendだけを使うため、両者を同じ項目で返す。
        引数、コマンド文字列、パッチ内容その他の本文は載せない。
        当該本文には秘匿値が含まれ得るため、呼び出し元の応答へ渡さない。
        """
        if self.current_item is not None and self.current_item_started_at is not None:
            entry: dict[str, str] = {"started_at": self.current_item_started_at}
            for key in ("type", "id"):
                value = self.current_item.get(key)
                if isinstance(value, str) and value:
                    entry[key] = value
            return [entry]
        ordered = sorted(self.pending_tool_uses.items(), key=lambda item: (item[1][1], item[0]))
        return [{"name": tool_name, "started_at": started_at} for _, (tool_name, started_at) in ordered]

    def reset_progress(self) -> None:
        """現在turnの進捗を初期化する。"""
        self._progress_text = ""
        self.progress_items.clear()
        self.output_updated_at = None

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
    prompt: str = ""
    started_at: str = dataclasses.field(default_factory=_utc_now)
    updated_at: str = dataclasses.field(default_factory=_utc_now)
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
            label=session.label,
            prompt=session.prompt,
            started_at=session.started_at,
            updated_at=session.updated_at,
            output_updated_at=session.output_updated_at,
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
    session.live_child_session_ids.clear()
    session.terminal_child_session_ids.clear()
    session.child_tool_uses.clear()
    session.pending_tool_uses.clear()
    session.last_action = ""
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
    """Claude SDKのツール利用と結果から、孫sessionと未完了のツール呼び出しの状態を更新する。

    未完了のツール呼び出しは`agents_server`のツールに限らず記録する。
    記録の対象を`agents_server`のツールへ限ると、呼び出し元は委譲先が何で止まっているかを
    `show`の応答から判定できない。
    """
    for block in _content_blocks(message):
        tool_use_id = _block_value(block, "id")
        tool_name = _block_value(block, "name")
        tool_input = _block_value(block, "input")
        if isinstance(tool_use_id, str) and isinstance(tool_name, str):
            session.record_tool_use_start(tool_use_id, tool_name)
            normalized = _agents_server_tool_name(tool_name)
            if normalized is not None:
                arguments = dict(tool_input) if isinstance(tool_input, Mapping) else {}
                session.child_tool_uses[tool_use_id] = (normalized, arguments)
            continue

        tool_use_id = _block_value(block, "tool_use_id")
        if not isinstance(tool_use_id, str):
            continue
        session.record_tool_use_end(tool_use_id)
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
    if normalized in {"start", "start_explore", "start_shell", "start_write"}:
        session_id = result.get("session_id")
        if isinstance(session_id, str) and session_id:
            session.live_child_session_ids.add(session_id)
        return
    if normalized != "kill":
        return
    session_id = arguments.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    if result.get("status") in TERMINAL_STATUSES | {"expired"}:
        # 当該レコードは孫sessionを所有する別プロセスが持つため、観測側は削除しない。
        session.live_child_session_ids.discard(session_id)
        session.terminal_child_session_ids.add(session_id)


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
    for prefix in tool_names.MCP_NAMESPACES:
        if tool_name.startswith(prefix):
            return tool_name.removeprefix(prefix)
    if tool_name in {"start", "start_explore", "start_shell", "start_write", "kill"}:
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
