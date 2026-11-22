"""engine別のbackendが満たす操作の型（`Backend`）と、engine名からbackendを生成する関数。

engineごとの性質（起動の例外の扱い、可用性の失敗の判定、中断に要する識別子、所有者のいない記録の引き継ぎ）は
各backendのクラスが属性と静的メソッドで持つ。`manager`系のモジュールはengine名を比べずにbackendへ問い合わせ、
engine名とbackendのクラスの対応と、engineごとに異なる生成時の引数は本モジュールだけが持つ。
"""

from __future__ import annotations

import asyncio
import configparser
import pathlib
import sys
import sysconfig
import typing
from collections.abc import Callable
from typing import Any

from agent_toolkit._agents_server import antigravity as antigravity_backend
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import opencode as opencode_backend
from agent_toolkit._agents_server.launch_requests import MODEL_TYPE_NEXT_ACTION
from agent_toolkit._agents_server.session_identity import IdentityValidator
from agent_toolkit._agents_server.state import LaunchKind, ModelCandidate, ResumePrompt, SessionState
from agent_toolkit._common.next_action import ActionableError

CODEX_ENGINE = "codex"
"""Codexのengine名。モデルの一覧と`thread/read`はCodexのbackendだけが持つ。"""


StartCandidateUnavailable = codex_backend.StartCandidateUnavailable


class Backend(typing.Protocol):
    START_FAILURE_EXCLUDES_CANDIDATE: typing.ClassVar[bool]
    """起動の例外をその候補の可用性の失敗として扱い、次の候補へ進むか。"""
    INTERRUPT_REQUIRES_TURN_ID: typing.ClassVar[bool]
    """中断の要求にturnの識別子を要するか。"""
    ORPHAN_TAKEOVER: typing.ClassVar[bool]
    """所有者のいない登録簿の記録を、委譲先CLIの記録から終了として引き継げるか。"""

    @staticmethod
    def unavailable_reason(session: SessionState) -> str | None:
        """終了したsessionがengineの可用性を理由に失敗した場合、その除外理由を返す。"""
        ...

    @staticmethod
    def excludes_with_recorded_reason(reason: str) -> bool: ...

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
        request_prompt: str | None = None,
        result_contract_recovery_consumed: bool = False,
    ) -> SessionState:
        """新しいsessionの最初のturnを開始する。

        request_promptは資源案内で装飾する前の起動本文。最初の結果が応答より早く到着しても、
        元の役割契約を検査できるよう、状態の生成時に設定する。
        """
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
        request_prompt: str | None = None,
        result_contract_recovery_consumed: bool = False,
        turn_seq: int = 0,
        fast_mode: bool | None = None,
    ) -> SessionState: ...

    async def send_message(self, session: SessionState, prompt: str) -> dict[str, Any]: ...

    async def interrupt(self, session: SessionState) -> None: ...

    async def release_session(self, session: SessionState) -> None:
        """保持期限到達時とstopでmanagerが呼び、終了後に残るsession単位の全資源を解放する。

        プロセス、接続、ロード済みthreadとその子孫を含む。共有一覧から除去した後も解放できるよう、
        解放対象のメモリ状態を渡す。保存済み会話の再開性と他sessionの資源は保つ。
        解放を完了できない場合は例外を送出し、managerは解放済みと記録せずに同じsessionへの再試行を受け付ける。
        """
        ...

    async def close(self) -> None: ...


class TurnHistory(typing.Protocol):
    """委譲先CLIの記録からturnの一覧を読めるbackend。`ORPHAN_TAKEOVER`が真のbackendが満たす。"""

    async def read_thread_turns(self, session_id: str) -> list[tuple[str, str]]: ...


class ModelCatalog(typing.Protocol):
    """接続先が受け付けるモデルの一覧を返せるbackend。Codexのbackendが満たす。"""

    async def list_models(self) -> list[dict[str, Any]]: ...


_BACKEND_CLASSES: dict[str, type[Backend]] = {
    "claude": claude_backend.ClaudeServerManager,
    CODEX_ENGINE: codex_backend.AppServerManager,
    "agy": antigravity_backend.AntigravityManager,
    "opencode": opencode_backend.OpenCodeManager,
}

SUPPORTED_ENGINES = frozenset(_BACKEND_CLASSES)
"""backendを持つengine名の集合。"""


def backend_class(engine: str) -> type[Backend] | None:
    return _BACKEND_CLASSES.get(engine)


def create_backend(
    engine: str,
    sessions: dict[str, SessionState],
    condition: asyncio.Condition,
    *,
    expire_session: Callable[[str], None],
    root_session_id: str | None,
    log_directory: pathlib.Path | None,
    validate_session_id: IdentityValidator | None = None,
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
            validate_session_id=validate_session_id,
        )
    if engine == "claude":
        return claude_backend.ClaudeServerManager(
            sessions,
            condition,
            expire_session=expire_session,
            publish_registry=True,
            root_session_id=root_session_id,
            validate_session_id=validate_session_id,
        )
    if engine == "agy":
        return antigravity_backend.AntigravityManager(
            sessions,
            condition,
            publish_registry=True,
            log_directory=log_directory,
            root_session_id=root_session_id,
            validate_session_id=validate_session_id,
        )
    if engine == "opencode":
        return opencode_backend.OpenCodeManager(
            sessions,
            condition,
            publish_registry=True,
            log_directory=log_directory / "opencode" if log_directory is not None else None,
            root_session_id=root_session_id,
            validate_session_id=validate_session_id,
        )
    raise ActionableError(f"unsupported engine: {engine}", next_action=MODEL_TYPE_NEXT_ACTION)


def excludes_with_recorded_reason(engine: str, reason: str) -> bool:
    backend_type = backend_class(engine)
    return backend_type is None or backend_type.excludes_with_recorded_reason(reason)


def start_dependency_failure(engine: str, error: ImportError) -> str | None:
    """Claudeの起動時の依存読込失敗を、実行環境の診断付きの候補不成立へ分類する。"""
    if engine != "claude":
        return None
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    configuration = pathlib.Path(sys.prefix) / "pyvenv.cfg"
    details = [f"実行中Python={running}"]
    try:
        if configuration.is_file():
            parser = configparser.ConfigParser()
            parser.read_string("[venv]\n" + configuration.read_text(encoding="utf-8"))
            section = parser["venv"]
            configured = section.get("version_info", section.get("version", ""))
            if configured and ".".join(configured.split(".")[:2]) != running:
                details.append(f"仮想環境のPython={configured}で実行系と一致しません")
        if not pathlib.Path(sysconfig.get_path("purelib")).is_dir():
            details.append("実行系が参照するsite-packagesがありません")
    except (OSError, UnicodeError, configparser.Error) as diagnostic_error:
        details.append(f"実行環境の版を確認できません: {diagnostic_error}")
    return (
        f"Claudeの依存を読み込めません: {error}（{'。'.join(details)}）。"
        "ホストを再読み込みするかセッションを再起動して、現在の仮想環境で再実行する"
    )


def interrupt_requires_turn_id(engine: str) -> bool:
    backend_type = backend_class(engine)
    return backend_type is not None and backend_type.INTERRUPT_REQUIRES_TURN_ID


def turn_history(backend: Backend) -> TurnHistory:
    """`ORPHAN_TAKEOVER`が真のengineのbackendを、turnの一覧を読めるbackendとして返す。"""
    return typing.cast(TurnHistory, backend)


def model_catalog(backend: Backend) -> ModelCatalog:
    """Codexのbackendを、モデルの一覧を返せるbackendとして返す。"""
    return typing.cast(ModelCatalog, backend)
