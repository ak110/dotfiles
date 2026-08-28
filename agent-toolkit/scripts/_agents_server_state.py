"""agents_serverのバックエンドが共有するsession状態と検証を定義する。"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import pathlib
from typing import Any

RESULT_RETENTION_SECONDS = 1800.0
TERMINAL_STATUSES = frozenset({"completed", "failed", "interrupted"})


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
