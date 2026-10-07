"""engine別のbackendが満たす操作の型（`Backend`）と、engine名からbackendを生成する関数。

engineごとの性質（起動の例外の扱い、可用性の失敗の判定、中断に要する識別子、所有者のいない記録の引き継ぎ）は
各backendのクラスが属性と静的メソッドで持つ。`manager`系のモジュールはengine名を比べずにbackendへ問い合わせ、
engine名とbackendのクラスの対応と、engineごとに異なる生成時の引数は本モジュールだけが持つ。
"""

from __future__ import annotations

import asyncio
import pathlib
import typing
from collections.abc import Callable
from typing import Any

from agent_toolkit._agents_server import antigravity as antigravity_backend
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server.launch_requests import MODEL_TYPE_NEXT_ACTION
from agent_toolkit._agents_server.state import LaunchKind, ModelCandidate, ResumePrompt, SessionState
from agent_toolkit._common.next_action import ActionableError

CODEX_ENGINE = "codex"
"""Codexのengine名。モデルの一覧と`thread/read`はCodexのbackendだけが持つ。"""


class Backend(typing.Protocol):
    """engine別のbackendが満たす操作と性質。"""

    START_FAILURE_EXCLUDES_CANDIDATE: typing.ClassVar[bool]
    """起動の例外をその候補の可用性の失敗として扱い、次の候補へ進むか。"""
    INTERRUPT_REQUIRES_TURN_ID: typing.ClassVar[bool]
    """中断の要求にturnの識別子を要するか。"""
    ORPHAN_TAKEOVER: typing.ClassVar[bool]
    """所有者のいない登録簿の記録を、委譲先CLIの記録から終端として引き継げるか。"""

    @staticmethod
    def unavailable_reason(session: SessionState) -> str | None:
        """終端したsessionがengineの可用性を理由に失敗した場合、その除外理由を返す。"""
        ...

    @staticmethod
    def excludes_with_recorded_reason(reason: str) -> bool:
        """記録済みの除外理由が、候補を除外する根拠になるかを返す。"""
        ...

    async def start(
        self,
        prompt: str,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
        *,
        model_type: str | None = None,
        launch_kind: LaunchKind = "delegate",
        excluded_candidates: frozenset[ModelCandidate] = frozenset(),
    ) -> SessionState:
        """新しいsessionの最初のturnを開始する。"""
        ...

    async def resume(
        self,
        session_id: str,
        prompt: ResumePrompt,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
        *,
        model_type: str | None = None,
        launch_kind: LaunchKind = "delegate",
        excluded_candidates: frozenset[ModelCandidate] = frozenset(),
        turn_seq: int = 0,
        fast_mode: bool | None = None,
    ) -> SessionState:
        """保存済みのsessionへ新しいturnを開始する。"""
        ...

    async def send_message(self, session: SessionState, prompt: str) -> dict[str, Any]:
        """実行中のturnへ追加の指示を配送する。"""
        ...

    async def interrupt(self, session: SessionState) -> None:
        """実行中のturnへ中断を要求する。"""
        ...

    async def release_session(self, session_id: str) -> None:
        """sessionが保持する委譲先の資源を解放する。"""
        ...

    async def close(self) -> None:
        """backendが保持する全ての資源を解放する。"""
        ...


class TurnHistory(typing.Protocol):
    """委譲先CLIの記録からturnの一覧を読めるbackend。`ORPHAN_TAKEOVER`が真のbackendが満たす。"""

    async def read_thread_turns(self, session_id: str) -> list[tuple[str, str]]:
        """sessionのturnの識別子と状態を古い順に返す。"""
        ...


class ModelCatalog(typing.Protocol):
    """接続先が受け付けるモデルの一覧を返せるbackend。Codexのbackendが満たす。"""

    async def list_models(self) -> list[dict[str, Any]]:
        """接続先が受け付けるモデルの一覧を返す。"""
        ...


_BACKEND_CLASSES: dict[str, type[Backend]] = {
    "claude": claude_backend.ClaudeServerManager,
    CODEX_ENGINE: codex_backend.AppServerManager,
    "agy": antigravity_backend.AntigravityManager,
}

SUPPORTED_ENGINES = frozenset(_BACKEND_CLASSES)
"""backendを持つengine名の集合。"""


def backend_class(engine: str) -> type[Backend] | None:
    """engine名に対応するbackendのクラスを返す。対応するbackendが無いengine名は`None`を返す。"""
    return _BACKEND_CLASSES.get(engine)


def create_backend(
    engine: str,
    sessions: dict[str, SessionState],
    condition: asyncio.Condition,
    *,
    expire_session: Callable[[str], None],
    root_session_id: str | None,
    log_directory: pathlib.Path | None,
) -> Backend:
    """engine名に対応するbackendを生成する。

    `expire_session`はClaudeのbackendだけが、`log_directory`はAntigravityのbackendだけが使う。
    """
    if engine == CODEX_ENGINE:
        return codex_backend.AppServerManager(
            sessions,
            condition,
            publish_registry=True,
            root_session_id=root_session_id,
        )
    if engine == "claude":
        return claude_backend.ClaudeServerManager(
            sessions,
            condition,
            expire_session=expire_session,
            publish_registry=True,
            root_session_id=root_session_id,
        )
    if engine == "agy":
        return antigravity_backend.AntigravityManager(
            sessions,
            condition,
            publish_registry=True,
            log_directory=log_directory,
            root_session_id=root_session_id,
        )
    raise ActionableError(f"unsupported engine: {engine}", next_action=MODEL_TYPE_NEXT_ACTION)


def excludes_with_recorded_reason(engine: str, reason: str) -> bool:
    """記録済みの除外理由が、engineの候補を除外する根拠になるかを返す。backendを持たないengineは常に除外する。"""
    backend_type = backend_class(engine)
    return backend_type is None or backend_type.excludes_with_recorded_reason(reason)


def interrupt_requires_turn_id(engine: str) -> bool:
    """engineへの中断の要求にturnの識別子を要するかを返す。"""
    backend_type = backend_class(engine)
    return backend_type is not None and backend_type.INTERRUPT_REQUIRES_TURN_ID


def turn_history(backend: Backend) -> TurnHistory:
    """`ORPHAN_TAKEOVER`が真のengineのbackendを、turnの一覧を読めるbackendとして返す。"""
    return typing.cast(TurnHistory, backend)


def model_catalog(backend: Backend) -> ModelCatalog:
    """Codexのbackendを、モデルの一覧を返せるbackendとして返す。"""
    return typing.cast(ModelCatalog, backend)
