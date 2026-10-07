"""agents_serverのバックエンドが共有するsession状態と検証を定義する。"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import json
import logging
import pathlib
import time
import typing
from collections.abc import Callable, Coroutine, Iterable, Mapping
from typing import Any

from agent_toolkit._agents_server import session_registry, task_documents, tool_names
from agent_toolkit._common import background_output, claude_usage_limit, message_format
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger("agent-toolkit.agents-server.state")

RESULT_RETENTION_SECONDS = 1800.0
# Claude CodeのBashツールが背景実行へ許す実行時間の上限（ミリ秒を秒へ換算）。Claude Code 2.1.291の
# Bashツールの入力スキーマは背景実行の`timeout`を「default 1800000, max 7200000」と説明し、上限に達した
# バックグラウンドタスクを止めて完了通知を送る。本体の実装は環境変数による上限の引き上げも受け付けるため、
# この値は環境変数を設定しない場合の上限である。
CLAUDE_BACKGROUND_BASH_MAX_SECONDS = 7200.0
# 待機表明の保留を、完了通知が届かない場合に打ち切る期限。保留が待つバックグラウンドタスクの完了通知は背景実行の上限以内に
# 届くため、上限より短い期限は稼働中のバックグラウンドタスクを待つ保留を打ち切ってしまう。完了通知の配送と再開turnの開始に
# 要する時間の余裕を上限へ加える。結果保持期限とは目的が異なり、値も共有しない。
AUTO_RESUME_DEADLINE_SECONDS = CLAUDE_BACKGROUND_BASH_MAX_SECONDS + 600.0
UNFINISHED_BACKGROUND_TASKS_KEY = "unfinishedBackgroundTasks"
"""保留した結果を確定した時点で稼働中だったバックグラウンドタスクの識別子を`error`へ記録する項目名。"""
UNOBSERVED_SESSIONS_KEY = "unobservedSessions"
"""終端を観測していなかった孫sessionの識別子を`error`へ記録する項目名。

保留の確定に加え、自動再開を消費した後のturnの終端など保留と無関係な処理でも記録する。
"""
HELD_RESULT_FINALIZED_KEY = "heldResultFinalized"
"""待機表明の保留を、待機対象が残ったまま確定したことを`error`へ示す項目名。

`unobservedSessions`は保留と無関係な処理でも記録されるため、確定した結果が再開したturnの結果ではないことは
この項目だけで判別する。
"""
# 起動の可用性確認を過ぎた後にCodexのturnがモデルの過負荷で終端した場合に、同じsessionへ継続を送るまでの待機秒数。
# 要素数が1回の失敗の連鎖で行う自動継続の上限回数となる。値はユーザー指示（15秒・30秒・60秒）による。
# 直後の再送では過負荷が解けなかった観測があるため待機を置き、間隔を広げる。上限に達しても解けない場合は
# 最後の失敗を公開し、その候補を次回の起動の除外対象として記録して、委譲元の起動し直しで別のモデルへ移す。
OVERLOAD_RESUME_DELAYS_SECONDS = (15.0, 30.0, 60.0)
OVERLOAD_ERROR_INFO = "serverOverloaded"
# Claude CodeのWeekly limitと5時間の利用上限の解除待ちを`api_error`の`type`で示す値。
# 解除待ちは回数と総時間の上限を持たない（解除まで待ち、別の候補へ切り替えない。ユーザー指示）。
USAGE_LIMIT_ERROR_TYPE = "usage_limit"
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
# （agents_server_mcp.pyのSTART_AVAILABILITY_TIMEOUT）を直列に加えた和が
# HOST_BACKGROUND_THRESHOLD_SECONDSを下回るように選ぶ。
# この関係が成立しなくなると、初期化の失敗が確定する前にホストがツール呼び出しを背景へ移し、
# 委譲元は`start`の失敗を受け取らないまま待機へ進む。
# 監査記録は`docs/development/audit-records.md`の
# 「agent-toolkit/agent_toolkit/_agents_server/state.py：session初期化の待機上限：2026年9月11日」にある。
SESSION_INITIALIZATION_TIMEOUT = 50.0
SESSION_INITIALIZATION_ATTEMPTS = 2
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})
TASK_MODEL_TYPES = {
    "add-wi.subagent.md": "high_tier",
    "bulk-replace-review.subagent.md": "low_tier",
    "copilot-review-audit.subagent.md": "high_tier",
    "defect-investigation.subagent.md": "high_tier",
    "exec-review.subagent.md": "medium_tier",
    "exec.subagent.md": "high_tier",
    "external-write-review.subagent.md": "low_tier",
    "lane-integration.subagent.md": "high_tier",
    "pick-wi-explain.subagent.md": "low_tier",
    "pick-wi.subagent.md": "medium_tier",
    "reader-fit-review.subagent.md": "low_tier",
    "session-termination.subagent.md": "high_tier",
    "usability-review.subagent.md": "medium_tier",
}
"""`<役割名>.subagent.md`の役割名と工程別モデル設定の対応。"""
SHARE_DIR = pathlib.Path(__file__).resolve().parents[2] / "share"


def _read_share(name: str) -> str:
    """共有プロンプトまたは規範を末尾改行なしで読む。"""
    return (SHARE_DIR / name).read_text(encoding="utf-8").rstrip("\n")


def _read_prompt(name: str) -> str:
    """Markdownの先頭見出しを除いた固定プロンプトを読む。"""
    return _read_share(name).split("\n\n", maxsplit=1)[1]


NORMATIVE_ELEMENT = message_format.AUTO_INSERTED_ELEMENT
NORMATIVE_SOURCE = "agent-toolkit"


def _normative(body: str, *, kind: str) -> str:
    """System promptへ渡す本文へ、生成主体と種別を示す境界を付ける。

    委譲先のsystem promptは、ホストが用意する指示と同じ仕組みで実行主体へ届く。
    本リポジトリが生成した範囲を委譲先が判別できるよう、他の自動注入と同じ形式で囲む。
    """
    return message_format.auto_message(body, source=NORMATIVE_SOURCE, kind=kind)


# 通常委譲へ追加する規範は、起動フックと共有するrules-subagent.mdが定める。
SUBAGENT_RULES = _read_share("rules-subagent.md")
CLAUDE_CODE_SUBAGENT_RULES = _read_share("rules-subagent.claude-code.md")
# 委譲先の実行主体は、両backendが用意する指示ではユーザーと直接対話する主体として起動される。
# 起動方法の違いを実行主体が観測できないため、規範が主体別に定める条文を適用できる状態を明示の指示で成立させる。
# Codexの`developerInstructions`はdeveloper roleメッセージとして注入され、ホストが用意する指示を置換しない。
DELEGATE_NOTICE = _read_prompt("agents-server-delegate-notice.md")
_DELEGATE_ROLE = _normative(f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-delegate.md')}", kind="delegate")
DELEGATE_SYSTEM_PROMPT = f"{_DELEGATE_ROLE}\n\n{_normative(SUBAGENT_RULES, kind='rules-subagent')}"
CLAUDE_DELEGATE_SYSTEM_PROMPT = (
    f"{_DELEGATE_ROLE}\n\n{_normative(f'{SUBAGENT_RULES}\n\n{CLAUDE_CODE_SUBAGENT_RULES}', kind='rules-subagent')}"
)
EXPLORE_SYSTEM_PROMPT = _normative(f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-explore.md')}", kind="explore")
SHELL_SYSTEM_PROMPT = _normative(f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-shell.md')}", kind="shell")
WRITE_SYSTEM_PROMPT = _normative(f"{DELEGATE_NOTICE}\n{_read_prompt('agents-server-write.md')}", kind="write")
ModelCandidate = tuple[str, str, str]
LaunchKind = task_documents.LaunchKind
# 起動条件の種別ごとのシステム指示。Claude backendの通常委譲だけは、preset指示へ追記する形で渡す。
LAUNCH_SYSTEM_PROMPTS: dict[LaunchKind, str] = {
    "delegate": DELEGATE_SYSTEM_PROMPT,
    "explore": EXPLORE_SYSTEM_PROMPT,
    "shell": SHELL_SYSTEM_PROMPT,
    "write": WRITE_SYSTEM_PROMPT,
}
# 同じsessionの自動再開を実際に行うbackend（ClaudeとCodex）だけが起動時の指示へ加える。
# Antigravity backendは自動再開を実機で確かめていないため、この能力を伝えない。
AUTO_RESUME_NOTICE = _normative(_read_prompt("agents-server-auto-resume.md"), kind="auto-resume")
# プロジェクト規範と設定の読込を省く軽量な起動条件を共有する種別。
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


class DelegateBackendError(RuntimeError):
    """委譲先CLIの起動、または委譲先CLIとの通信の形式が想定と異なることを示す。

    MCPのツール処理の共通層は本例外を受け取ると、CLIの導入と認証の確認と別engineでの再起動を次の操作として返す。
    """


RESEND_AFTER_WAIT_NEXT_ACTION = "`atk agents wait`で終端を観測してから`send_message`を再送する"
"""turnが中断中または未終端のため継続要求を受け付けない場合の次の操作。MCP層と各backendが共有する。"""

# レビューを目的とするsessionのlabelの末尾。`start`のlabelの凡例と、`<役割名>.subagent.md`から生成するlabelがこの末尾を持つ。
REVIEW_LABEL_SUFFIX = "-review"

# レビューを目的とするsessionの完了結果を受け取った主体へ示す次の操作。
REVIEW_RESULT_NEXT_ACTION = (
    "レビューの指摘を受領した。採否を確定する前に`agent-toolkit:review-standards`を起動し、"
    "同スキルの`references/reviewee.md`に従って採否と修正を確定する。"
    "ユーザーの合意を見送りの根拠にする場合は、合意を示すユーザー発話を特定してから根拠にする"
)


# 結果はメインと委譲先の双方が受け取るため、両者が実行できる操作を受け取った主体の役割ごとに示す。
IMPROVEMENT_RESULT_NEXT_ACTION = (
    "`agent_message`の`気付いた改善点:`で始まる全行を上流へ渡す。"
    "メインエージェントは次のユーザーへの発話へ転記し、委譲先は自身の返却の末尾へ逐語で引き継ぐ。"
    "転記する各行は字下げを除く行頭に`気付いた改善点:`を原文のまま置き、標識の前と、標識とコロンの間へ報告元などの語を入れない。"
    "報告元と確かめた範囲は標識行の原文の後ろか別の行に添える。"
    "未確認の主張の扱いなど残りの細則は`agent-toolkit:delegation`の`references/receiving.md`「受領後の扱い」に従う"
)


def append_result_next_action(result: dict[str, Any], next_action: str) -> dict[str, Any]:
    """既存の案内を保持して次の操作を併記し、複数の返却処理で同じ案内を重ねない。"""
    result = dict(result)
    existing = result.get("next_action")
    if isinstance(existing, str) and existing:
        if next_action not in existing:
            result["next_action"] = f"{existing}\n{next_action}"
    else:
        result["next_action"] = next_action
    return result


def with_result_next_action(result: dict[str, Any], label: str | None) -> dict[str, Any]:
    """受領時に必要なレビューの採否確定と改善点の転記を、既存の次の操作に併記する。"""
    if result.get("status") == "completed" and isinstance(label, str) and label.endswith(REVIEW_LABEL_SUFFIX):
        result = append_result_next_action(result, REVIEW_RESULT_NEXT_ACTION)
    message = result.get("agent_message")
    if isinstance(message, str) and any(line.lstrip().startswith("気付いた改善点:") for line in message.splitlines()):
        result = append_result_next_action(result, IMPROVEMENT_RESULT_NEXT_ACTION)
    return result


class ActionableRuntimeError(ActionableError, RuntimeError):
    """次の操作を持つ`RuntimeError`。既存の`except RuntimeError`節が捕捉する範囲を保つために使う。"""


class ActionableTimeoutError(ActionableError, TimeoutError):
    """次の操作を持つ`TimeoutError`。既存の`except TimeoutError`節が捕捉する範囲を保つために使う。"""


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


# ツール呼び出しの入力を1行へ要約するときの上限文字数。
# statuslineは受け取った説明を表示幅で切り詰めるため、上限は`show`の応答が
# 停滞の原因を判別できる長さとして定める。
_ACTION_DETAIL_LIMIT = 200


def _action_detail(payload: Any, exclude: tuple[str, ...] = ()) -> str:
    """ツール呼び出しの入力を、keyの受信順を保った1行の要約へ変換する。

    各項目を`<key>=<値>`の形で並べ、文字列以外の値は区切りに空白を含めないJSONへ直列化する。
    `exclude`には、呼び出し元が別の項目として既に公開しているkeyを渡す。
    引数、コマンド文字列およびパッチ内容を含めるのは、同じツール名を繰り返す区間では
    ツール名だけの表示が変化せず、稼働中と停止中を区別できないためである。
    """
    if not isinstance(payload, Mapping):
        return ""
    parts: list[str] = []
    for key, value in payload.items():
        if key in exclude:
            continue
        rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        parts.append(f"{key}={rendered}")
    normalized = " ".join(" ".join(parts).split())
    if len(normalized) <= _ACTION_DETAIL_LIMIT:
        return normalized
    return f"{normalized[:_ACTION_DETAIL_LIMIT]}…"


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


# `api_error`の公開項目。利用上限の解除待ちだけが後半の2項目を持つ。
API_ERROR_USAGE_LIMIT_KEYS = ("limit_type", "resets_at")
API_ERROR_PUBLIC_KEYS = ("type", "http_status", "elapsed_seconds", *API_ERROR_USAGE_LIMIT_KEYS)


def activity_projection(
    *,
    updated_at: str | None,
    output_updated_at: str | None,
    started_at: str | None,
    api_error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """最後の活動からの経過とAPI失敗を公開項目へ射影する。

    停滞の判定入力は活動時刻とする。テキスト出力の時刻を判定入力にすると、
    ツール呼び出しだけを長時間続ける正常なsessionを停滞と判定し、
    委譲元が不要な催促と巻き取りへ進む。
    `show`・`list`・`atk agents wait`・`atk agents list`は本関数を共有する。
    呼び出し手段ごとに判定入力が分かれると、同じsessionへ異なる停滞の印が返る。
    """
    del output_updated_at
    seconds_since_activity = elapsed_seconds(updated_at or started_at)
    if seconds_since_activity is None:
        return {}
    projection: dict[str, Any] = {"seconds_since_activity": seconds_since_activity}
    if api_error is not None:
        first_at = api_error.get("first_at")
        elapsed = elapsed_seconds(first_at) if isinstance(first_at, str) else None
        if elapsed is not None:
            projection["api_error"] = {
                "type": api_error.get("type"),
                "http_status": api_error.get("http_status"),
                "elapsed_seconds": elapsed,
            }
            # 利用上限の解除待ちでは、種類と解除予定時刻を加えてAPI再試行と区別できるようにする。
            for key in API_ERROR_USAGE_LIMIT_KEYS:
                if api_error.get(key) is not None:
                    projection["api_error"][key] = api_error[key]
    return projection


def nonempty_error(error: Any) -> bool:
    """`error`が公開応答へ含める内容を持つかを返す（`None`、空文字列、空の辞書は持たない）。"""
    return error is not None and error != "" and error != {}


def public_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    """内部の結果保存項目を含めず、回収と継続に使う結果だけを返す。"""
    result = {
        key: payload[key]
        for key in (
            "session_id",
            "status",
            "label",
            "agent_message",
            "next_action",
            "recovery",
            "engine",
            "model",
            "effort",
            "model_type",
        )
        if key in payload
    }
    if nonempty_error(payload.get("error")):
        result["error"] = payload["error"]
    return result


def public_notice(payload: Mapping[str, str]) -> dict[str, str]:
    """整列後の通知本文を返す。送信時刻は保存と整列に残す。"""
    return {"body": payload["body"]}


def _append_bounded(existing: str, delta: str, limit: int = 4000) -> str:
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
    created_at: str = dataclasses.field(default_factory=_utc_now)
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
    updated_at: str = dataclasses.field(default_factory=_utc_now)
    output_updated_at: str | None = None
    api_error: dict[str, Any] | None = None
    # backend資源の解放期限。未回収の終端結果はこの期限を過ぎても保持する。
    retention_deadline: float | None = None
    turn_control_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock, repr=False)
    _progress_text: str = dataclasses.field(default="", repr=False)
    progress_items: dict[str, str] = dataclasses.field(default_factory=dict, repr=False)
    compaction_started_at_ms: dict[str, int] = dataclasses.field(default_factory=dict, repr=False)
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

    def record_tool_use_start(self, tool_use_id: str, tool_name: str, tool_input: Any = None) -> None:
        """未完了のツール呼び出しを記録し、最後の行動をツール名と入力の要約で更新する。"""
        detail = _action_detail(tool_input)
        self.pending_tool_uses[tool_use_id] = (tool_name, _utc_now(), detail)
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
        self.current_item_started_at = _utc_now()
        item_type = item.get("type")
        if isinstance(item_type, str) and item_type:
            detail = _action_detail(item, exclude=("type", "id"))
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
            detail = _action_detail(self.current_item, exclude=("type", "id"))
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
            self.api_error = {"type": error_type, "http_status": http_status, "first_at": _utc_now(), "count": 1}
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
        self.updated_at = _utc_now()
        if self.result_available:
            if self.finalized_at is None:
                self.finalized_at = _utc_now()
            if self.retention_deadline is None:
                self.retention_deadline = asyncio.get_running_loop().time() + RESULT_RETENTION_SECONDS
        else:
            self.retention_deadline = None
        registry_terminal = self.result_available
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
    started_at: str | None = dataclasses.field(default_factory=_utc_now)
    updated_at: str | None = dataclasses.field(default_factory=_utc_now)
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


def cli_turn_may_continue(session: SessionState) -> bool:
    """Claude Code CLIが次のturnを開始し得る状態と報告しているかを返す。

    CLIは`session_state_changed`の`idle`を、保留した結果の送出と背景エージェントの待機を終えて
    次のturnが発生しないと確定した時点で発行する（Claude Code 2.1.289のスキーマ記述
    「authoritative turn-over signal」）。turnの終了前にキューへ入った完了通知は、`ResultMessage`の
    後に`idle`を送らず次のturnを開始させるため、`ResultMessage`の受信時点の最新の報告は`running`のままとなる。
    報告を一度も受けていないsessionでは判定に使わない。
    """
    return session.cli_turn_state is not None and session.cli_turn_state != "idle"


def has_pending_auto_resume_targets(session: SessionState) -> bool:
    """自動再開が追跡する子session、Claude taskまたはCLIの次のturnが残るかを返す。

    Claude・Codex backendとMCP層は、開始、解除、再開の全条件で本述語だけを使う。
    追跡集合（`live_tasks`・`live_child_session_ids`）だけでは、Stop hookの実行中などturnの最終応答から
    `ResultMessage`までの間にバックグラウンドタスクが終わった場合を判別できない。その完了通知で集合は空になるが、
    CLIは`ResultMessage`の後に完了通知の再開turnを開始する。このためCLIのturn状態の報告も判定へ加える。
    一方、シェルのバックグラウンドタスクが動いている間もCLIは`idle`を報告するため、追跡集合も残す。
    観測した版と順序は`docs/development/audit-records.md`
    「agent-toolkit/agent_toolkit/_agents_server/claude.py：結果の保留とturn状態の報告：2026年10月4日」にある。
    """
    return bool(session.live_tasks or session.live_child_session_ids) or cli_turn_may_continue(session)


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
    session.agents_wait_background_outputs.clear()
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
    記録の対象を`agents_server`のツールへ限ると、委譲元は委譲先が何で止まっているかを
    `show`の応答から判定できない。
    """
    for block in _content_blocks(message):
        tool_use_id = _block_value(block, "id")
        tool_name = _block_value(block, "name")
        tool_input = _block_value(block, "input")
        if isinstance(tool_use_id, str) and isinstance(tool_name, str):
            session.record_tool_use_start(tool_use_id, tool_name, tool_input)
            normalized = _agents_server_tool_name(tool_name)
            if normalized is not None:
                arguments = dict(tool_input) if isinstance(tool_input, Mapping) else {}
                session.child_tool_uses[tool_use_id] = (normalized, arguments)
            elif tool_name == "Bash" and _is_agents_wait_command(tool_input):
                session.child_tool_uses[tool_use_id] = (_AGENTS_WAIT_TOOL_USE, {})
            continue

        tool_use_id = _block_value(block, "tool_use_id")
        if not isinstance(tool_use_id, str):
            continue
        session.record_tool_use_end(tool_use_id)
        tool_use = session.child_tool_uses.pop(tool_use_id, None)
        if tool_use is None:
            continue
        if tool_use[0] == _AGENTS_WAIT_TOOL_USE:
            consume_agents_wait_output(session, _tool_result_text(_block_value(block, "content")))
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
    if normalized in tool_names.RECORDED_START_OPERATIONS:
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
        # このレコードは孫sessionを所有する別プロセスが持つため、観測側は削除しない。
        session.live_child_session_ids.discard(session_id)
        session.terminal_child_session_ids.add(session_id)


# 委譲先がBashで実行した`atk agents wait`の呼び出しを`child_tool_uses`で識別する名前。
# agents_serverのツール名と衝突しない値とする。
_AGENTS_WAIT_TOOL_USE = "atk agents wait"
# `atk agents wait`がエージェント環境の自動保存時に標準出力へ書く保存先の行。
_AGENTS_WAIT_SAVED_PREFIX = "保存先: "
WAIT_BODY_START_PREFIX = "本文開始: session_id="
"""`atk agents wait`の要約が結果本文と通知本文の直前に置く行の接頭辞。後ろへsession識別子を続ける。"""
WAIT_BODY_END_PREFIX = "本文終了: session_id="
"""`atk agents wait`の要約が結果本文と通知本文の直後に置く行の接頭辞。後ろへsession識別子を続ける。"""


def _is_agents_wait_command(tool_input: Any) -> bool:
    command = tool_input.get("command") if isinstance(tool_input, Mapping) else None
    return isinstance(command, str) and "atk agents wait" in command


def _tool_result_text(value: Any) -> str:
    """ツール結果の本文を文字列として連結する。"""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        text = value.get("text")
        if isinstance(text, str):
            return text
        return _tool_result_text(value.get("content"))
    if isinstance(value, list | tuple):
        return "\n".join(_tool_result_text(item) for item in value)
    return ""


def _collected_session_ids(text: str) -> set[str]:
    """`atk agents wait`のJSON Lines出力から、終端結果を回収したsession識別子を返す。"""
    collected: set[str] = set()
    for line in text.splitlines():
        try:
            payload: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("session_id"), str)
            and payload.get("status") in TERMINAL_STATUSES
        ):
            collected.add(payload["session_id"])
    return collected


def consume_agents_wait_output(session: SessionState, text: str) -> None:
    """委譲先が`atk agents wait`で終端結果を回収した孫sessionを自動再開の追跡から外す。

    回収済みの結果は再配送されないため、そのsessionの終端を理由に委譲先を再開させると、
    委譲先は受け取り済みの結果について同じ報告を返し直すだけのturnを費やす。
    回収の根拠は待機コマンドが返したJSON Linesとし、エージェント環境の自動保存時は標準出力が示す保存先を読む。
    結果ファイルの不在は公開前の状態と区別できないため、回収の根拠に用いない。
    ホストが待機を背景実行へ移した場合は結果本文が出力ファイルへ書かれるため、そのパスを記録し、
    `consume_agents_wait_background_outputs`が判定の直前に読む。
    """
    collected = _collected_from_wait_output(text)
    session.agents_wait_background_outputs.update(background_output.output_paths(text))
    _discard_collected(session, collected)


def consume_agents_wait_background_outputs(session: SessionState) -> None:
    """背景実行の`atk agents wait`が出力ファイルへ書いた終端結果の孫sessionを追跡から外す。

    孫sessionの終端判定と未観測の記録の直前に呼ぶ。出力ファイルが無い、読めない、終端statusの行が無い
    （待機が未完了、または`status: running`だけ）場合は追跡に残す。
    """
    collected: set[str] = set()
    for path in session.agents_wait_background_outputs:
        with contextlib.suppress(OSError, UnicodeError):
            collected |= _collected_from_wait_output(pathlib.Path(path).read_text(encoding="utf-8"))
    _discard_collected(session, collected)


def _collected_from_wait_output(text: str) -> set[str]:
    """`atk agents wait`の標準出力から、終端結果を回収したsession識別子を返す。

    標準出力はJSON Linesか、エージェント環境の自動保存時に保存先の行と要約を持つ。
    ツール結果の本文と背景実行の出力ファイルはどちらも標準出力そのものであるため、同じ規則で読む。
    要約が表示する委譲先の本文は待機の出力を引用して終端行や保存先の行と同じ形の行を含み得るため、
    本文の範囲の行は回収の根拠から外す。
    """
    lines = _lines_outside_wait_bodies(text)
    collected = _collected_session_ids("\n".join(lines))
    for line in lines:
        if not line.startswith(_AGENTS_WAIT_SAVED_PREFIX):
            continue
        with contextlib.suppress(OSError, UnicodeError):
            collected |= _collected_session_ids(
                pathlib.Path(line.removeprefix(_AGENTS_WAIT_SAVED_PREFIX).strip()).read_text(encoding="utf-8")
            )
    return collected


def _lines_outside_wait_bodies(text: str) -> list[str]:
    """`本文開始:`の行から同じsession識別子の`本文終了:`の行までを除いた行を返す。

    終了の行が無い範囲は末尾まで本文として扱い、本文の行を回収の根拠へ混ぜない。
    """
    lines: list[str] = []
    end_line: str | None = None
    for line in text.splitlines():
        if end_line is not None:
            if line == end_line:
                end_line = None
            continue
        if line.startswith(WAIT_BODY_START_PREFIX):
            end_line = WAIT_BODY_END_PREFIX + line.removeprefix(WAIT_BODY_START_PREFIX)
            continue
        lines.append(line)
    return lines


def _discard_collected(session: SessionState, collected: set[str]) -> None:
    session.live_child_session_ids.difference_update(collected)
    session.terminal_child_session_ids.difference_update(collected)


def finalize_pending_result(
    session: SessionState,
    *,
    touch: bool = True,
    keep_resume_chain: bool = False,
    unobserved_sessions: Iterable[str] = (),
) -> None:
    """保留したturn結果を公開可能な終端状態へ移す。

    過負荷の自動継続と利用上限の解除待ちの継続を送る処理だけが`keep_resume_chain`を真にし、連鎖の回数と開始時刻を引き継ぐ。
    それ以外（`kill`、委譲元の`send_message`、期限到来、ストリーム終端）は連鎖を終える。
    連鎖を終える確定では、確定の時点で稼働中のバックグラウンドタスクと終端を観測していない孫sessionを`error`へ記録し、
    待機対象が残ったまま確定したことを`heldResultFinalized`で示す。
    保留した結果は待機表明であり、待機対象が残るまま公開する結果は再開turnの結果ではないことを委譲元へ示すためである。
    追跡集合から既に外した未観測の孫sessionは`unobserved_sessions`で渡す。既存の`error`の内容は保つ。
    """
    result = session.pending_result
    if result is None:
        raise RuntimeError("auto-resume wait has no pending result")
    unfinished_tasks = set(session.live_tasks)
    unobserved = set(session.live_child_session_ids) | set(unobserved_sessions)
    session.overload_resume_at = None
    session.usage_limit_resume_at = None
    if not keep_resume_chain:
        clear_overload_resume(session)
        clear_usage_limit_wait(session)
    session.awaiting_auto_resume = False
    session.auto_resume_deadline = None
    session.pending_result = None
    session.live_child_session_ids.clear()
    session.status = result["status"]
    session.agent_message = result["agent_message"]
    session.error = result["error"]
    session.turn_completed = True
    session.turn_start_ambiguous = False
    if not keep_resume_chain and (unfinished_tasks or unobserved):
        _merge_error_identifiers(session, UNFINISHED_BACKGROUND_TASKS_KEY, unfinished_tasks)
        _merge_error_identifiers(session, UNOBSERVED_SESSIONS_KEY, unobserved)
        assert isinstance(session.error, dict)
        session.error[HELD_RESULT_FINALIZED_KEY] = True
    if touch:
        session.touch()


def is_overload_failure(session: SessionState) -> bool:
    """turnがCodexのモデルの過負荷で失敗したかを返す。"""
    return (
        session.status == "failed"
        and isinstance(session.error, dict)
        and session.error.get("codexErrorInfo") == OVERLOAD_ERROR_INFO
    )


def clear_overload_resume(session: SessionState) -> None:
    """過負荷による自動継続の連鎖を終える。"""
    session.overload_resume_count = 0
    session.overload_resume_at = None
    session.overload_first_at = None


def begin_overload_resume_wait(session: SessionState, result: dict[str, Any]) -> bool:
    """過負荷で終端したturnの結果を保留し、待機後の継続を予定する。

    上限に達していれば保留せずに連鎖を終えて偽を返し、呼び出し元は結果をそのまま公開する。
    待機中は既存の`api_error`へ種別`serverOverloaded`とHTTP状態の不明を記録して状態の読者へ公開する。
    """
    count = session.overload_resume_count
    if count >= len(OVERLOAD_RESUME_DELAYS_SECONDS):
        clear_overload_resume(session)
        return False
    session.pending_result = result
    session.awaiting_auto_resume = True
    session.auto_resume_deadline = None
    session.overload_resume_at = asyncio.get_running_loop().time() + OVERLOAD_RESUME_DELAYS_SECONDS[count]
    session.overload_resume_count = count + 1
    if session.overload_first_at is None:
        session.overload_first_at = _utc_now()
    session.api_error = {
        "type": OVERLOAD_ERROR_INFO,
        "http_status": None,
        "first_at": session.overload_first_at,
        "count": session.overload_resume_count,
    }
    for listener in tuple(_TOUCH_LISTENERS):
        listener()
    return True


def clear_usage_limit_wait(session: SessionState) -> None:
    """利用上限の解除待ちの連鎖を終える。"""
    session.usage_limit_wait_count = 0
    session.usage_limit_resume_at = None
    session.usage_limit_first_at = None


def begin_usage_limit_wait(session: SessionState, result: dict[str, Any]) -> bool:
    """Weekly limitか5時間の利用上限で失敗したturnの結果を保留し、解除後の継続を予定する。

    最後に報告された利用枠が待機の対象でなければ保留せずに連鎖を終えて偽を返し、呼び出し元は従来どおり扱う。
    保留した結果の`error.usageLimit`と`api_error`へ種類と解除予定時刻を記録し、状態の読者へ解除待ちを公開する。
    待機は`auto_resume_deadline`と`retention_deadline`の対象にせず、回数でも打ち切らない。
    """
    limit = session.usage_limit
    if result.get("status") != "failed" or limit is None or not limit.is_wait_target:
        clear_usage_limit_wait(session)
        return False
    original_error = result.get("error")
    error: dict[str, Any] = (
        dict(original_error)
        if isinstance(original_error, dict)
        else {"message": str(original_error or "Claude usage limit reached")}
    )
    resets_at = limit.resets_at_iso()
    error["usageLimit"] = {"type": limit.limit_type, "resetsAt": resets_at}
    result = {**result, "error": error}
    session.pending_result = result
    session.awaiting_auto_resume = True
    session.auto_resume_deadline = None
    session.usage_limit_resume_at = asyncio.get_running_loop().time() + limit.delay_seconds(time.time())
    session.usage_limit_wait_count += 1
    if session.usage_limit_first_at is None:
        session.usage_limit_first_at = _utc_now()
    http_status = error.get("apiErrorStatus")
    session.api_error = {
        "type": USAGE_LIMIT_ERROR_TYPE,
        "http_status": http_status if isinstance(http_status, int) else None,
        "first_at": session.usage_limit_first_at,
        "count": session.usage_limit_wait_count,
        "limit_type": limit.limit_type,
        "resets_at": resets_at,
    }
    for listener in tuple(_TOUCH_LISTENERS):
        listener()
    return True


def begin_auto_resume_wait(session: SessionState, result: dict[str, Any]) -> float:
    """終端結果を保留し、自動再開の待機期限を返す。"""
    session.pending_result = result
    session.awaiting_auto_resume = True
    deadline = asyncio.get_running_loop().time() + AUTO_RESUME_DEADLINE_SECONDS
    session.auto_resume_deadline = deadline
    return deadline


def record_unobserved_sessions(session: SessionState, session_ids: set[str]) -> None:
    """未観測の孫session識別子を既存のerror項目へ併合する。"""
    _merge_error_identifiers(session, UNOBSERVED_SESSIONS_KEY, session_ids)
    session.touch()


def _merge_error_identifiers(session: SessionState, key: str, identifiers: set[str]) -> None:
    """識別子の集合を既存のerrorの`key`項目へ併合する。集合が空ならerrorを変えない。"""
    if not identifiers:
        return
    merged = set(identifiers)
    current = session.error
    error: dict[str, Any]
    if isinstance(current, dict):
        error = dict(current)
    elif nonempty_error(current):
        error = {"message": str(current)}
    else:
        error = {}
    existing_identifiers = error.get(key)
    if isinstance(existing_identifiers, list):
        merged.update(item for item in existing_identifiers if isinstance(item, str))
    error[key] = sorted(merged)
    session.error = error


def _agents_server_tool_name(tool_name: str) -> str | None:
    for prefix in tool_names.MCP_NAMESPACES:
        if tool_name.startswith(prefix):
            return tool_name.removeprefix(prefix)
    if tool_name in tool_names.RECORDED_START_OPERATIONS or tool_name == "kill":
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
        raise ActionableError("prompt must be a non-empty string", next_action="委譲先へ渡す空でない本文を`prompt`へ指定する")


_CWD_NEXT_ACTION = "既存ディレクトリの絶対パスを`cwd`へ指定する"


def _validate_cwd(cwd: str) -> None:
    if not isinstance(cwd, str) or not cwd or not pathlib.PurePath(cwd).is_absolute():
        raise ActionableError("cwd must be a non-empty absolute path", next_action=_CWD_NEXT_ACTION)
    if not pathlib.Path(cwd).is_dir():
        raise ActionableError(f"cwd is not an existing directory: {cwd}", next_action=_CWD_NEXT_ACTION)


def _validate_shell_request(command: str, summary_policy: str) -> None:
    if not isinstance(command, str) or not command.strip():
        raise ActionableError("command must be a non-empty string", next_action="実行する空でないコマンドを`command`へ指定する")
    if not isinstance(summary_policy, str) or not summary_policy.strip():
        raise ActionableError(
            "summary_policy must be a non-empty string",
            next_action="報告へ含める値と粒度を書いた空でない要約方針を`summary_policy`へ指定する",
        )


_MODEL_EFFORT_NEXT_ACTION = "`model_type`の候補を`<engine>:<model>/<effort>`の形で書き、modelとeffortの両方を指定する"


def _validate_model_effort(model: str | None, effort: str | None) -> None:
    if (model is None) != (effort is None):
        raise ActionableError("model and effort must be provided together", next_action=_MODEL_EFFORT_NEXT_ACTION)
    if model is not None and (not model.strip() or not effort or not effort.strip()):
        raise ActionableError("model and effort must be non-empty strings", next_action=_MODEL_EFFORT_NEXT_ACTION)
