"""agents_serverのバックエンドが共有するsession状態と検証を定義する。"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import pathlib
from collections.abc import Callable, Coroutine
from typing import Any

RESULT_RETENTION_SECONDS = 1800.0
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})
# 委譲先の実行主体は、両backendの既定の指示ではユーザーと直接対話する主体として起動される。
# 起動経路の別を実行主体が観測できないため、規範が主体別に定める条文を適用できる状態を明示の指示で成立させる。
# Codexの`developerInstructions`はdeveloper roleメッセージとして注入され、既定の指示を置換しない。
DELEGATE_NOTICE = """あなたは別のコーディングエージェントから起動された委譲先である。
この会話の入力はユーザーの発話ではなく、呼び出し元エージェントが渡したタスクである。
あなたの応答はユーザーの画面へ表示されず、呼び出し元エージェントへ返る。"""
DELEGATE_SYSTEM_PROMPT = f"""{DELEGATE_NOTICE}
規範が委譲先又はサブエージェントへ課す条文を自身へ適用し、メインエージェント又は最上位セッションへ限定した条文を適用しない。
ユーザーへの確認は回答を得られないため発行せず、確認を要する事項は完了報告へ含めて呼び出し元へ差し戻す。"""
EXPLORE_SYSTEM_PROMPT = f"""{DELEGATE_NOTICE}
あなたは調査専用の担当である。依頼された対象を読み取り、結論と根拠だけを日本語で返す。
ファイルを作成、変更又は削除しない。コマンドは対象を変更しない読み取り操作に限る。
所在、該当箇所及び観測した事実を、後続の判断に足りる粒度で列挙する。"""
ModelCandidate = tuple[str, str, str]


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
    explore: bool = False
    excluded_candidates: frozenset[ModelCandidate] = dataclasses.field(default_factory=frozenset)
    turn_id: str = ""
    status: str = "running"
    plan: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    current_item: dict[str, Any] | None = None
    commentary: str = ""
    diff_changed: bool = False
    error: Any = None
    agent_message: str = ""
    protocol_warnings: list[str] = dataclasses.field(default_factory=list)
    reply_attempted: bool = False
    reply_turn_started: bool = False
    reply_retryable: bool = False
    turn_start_sent: bool = False
    turn_start_ambiguous: bool = False
    interrupt_requested: bool = False
    turn_completed: bool = False
    failure_pending_completion: bool = False
    updated_at: str = dataclasses.field(default_factory=_utc_now)
    retention_deadline: float | None = None
    turn_control_lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock, repr=False)
    _progress_text: str = dataclasses.field(default="", repr=False)
    progress_items: dict[str, str] = dataclasses.field(default_factory=dict, repr=False)

    @property
    def terminal(self) -> bool:
        """最新turnが終端状態であるかを返す。"""
        return self.status in TERMINAL_STATUSES

    @property
    def result_available(self) -> bool:
        """終端結果を返せる状態であるかを返す。"""
        return self.terminal and self.turn_completed and not self.turn_start_ambiguous

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
        """状態の更新時刻を現在時刻へ更新する。"""
        self.updated_at = _utc_now()
        if self.result_available:
            if self.retention_deadline is None:
                self.retention_deadline = asyncio.get_running_loop().time() + RESULT_RETENTION_SECONDS
        else:
            self.retention_deadline = None

    def public_status(self, *, include_result: bool = False) -> dict[str, Any]:
        """公開契約へ状態を射影する。内部のturn識別子は含めない。"""
        result: dict[str, Any] = {
            "session_id": self.session_id,
            "engine": self.engine,
            "status": self.status,
            "progress": self.progress,
        }
        if self.model_type is not None:
            result["model_type"] = self.model_type
        if include_result and self.result_available:
            result["agent_message"] = self.agent_message
            if _nonempty_error(self.error):
                result["error"] = self.error
        return result

    def previous_result(self) -> dict[str, Any]:
        """継続入力の応答へ退避する直前turnの結果を返す。"""
        result: dict[str, Any] = {
            "session_id": self.session_id,
            "engine": self.engine,
            "status": self.status,
            "agent_message": self.agent_message,
        }
        if _nonempty_error(self.error):
            result["error"] = self.error
        return result


@dataclasses.dataclass(frozen=True)
class SessionResumeState:
    """結果本文の回収後も同じ会話を再開するために保持する最小状態。"""

    session_id: str
    cwd: str
    model: str | None
    effort: str | None
    engine: str
    model_type: str | None = None
    explore: bool = False
    excluded_candidates: frozenset[ModelCandidate] = dataclasses.field(default_factory=frozenset)

    @classmethod
    def from_session(cls, session: SessionState) -> SessionResumeState:
        """終端sessionから再開に必要な入力だけを退避する。"""
        return cls(
            session_id=session.session_id,
            cwd=session.cwd,
            model_type=session.model_type,
            explore=session.explore,
            excluded_candidates=session.excluded_candidates,
            model=session.model,
            effort=session.effort,
            engine=session.engine,
        )


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
    session.protocol_warnings = []
    session.reply_retryable = False
    session.turn_start_sent = False
    session.turn_start_ambiguous = False
    session.interrupt_requested = False
    session.turn_completed = False
    session.failure_pending_completion = False
    session.retention_deadline = None
    if reset_progress:
        session.reset_progress()
    session.touch()


def _begin_reply(session: SessionState) -> None:
    """終端済みsessionの新しいturnを開始する準備をする。"""
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


def _validate_model_effort(model: str | None, effort: str | None) -> None:
    if (model is None) != (effort is None):
        raise ValueError("model and effort must be provided together")
    if model is not None and (not model.strip() or not effort or not effort.strip()):
        raise ValueError("model and effort must be non-empty strings")
