"""Codex App Serverと共有session状態を管理する公開入口。"""

from __future__ import annotations

import asyncio
import typing
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import (
    child_environment,
    codex_clients,
    codex_connections,
    codex_instructions,
    codex_notifications,
    codex_providers,
    codex_replies,
    codex_reset,
    codex_start,
    codex_turns,
    engine_availability,
    lifetime,
    session_identity,
    wait_compaction,
)
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._agents_server import state as shared_state
from agent_toolkit._agents_server.codex_instructions import (
    _developer_instructions,
)
from agent_toolkit._agents_server.codex_protocol import (
    AppServerError,
    JsonRpcResponseError,
    StartCandidateUnavailable,
    _WaitCompactionTurn,
)
from agent_toolkit._agents_server.codex_rpc import APP_SERVER_STREAM_LIMIT_BYTES, APP_SERVER_WORKING_DIRECTORY, JsonRpcProcess
from agent_toolkit._agents_server.launch_prompts import SUBAGENT_BASE_INSTRUCTIONS, python_runtime_instructions
from agent_toolkit._agents_server.state import LaunchKind, SessionState
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import codex_usage_limit, mcp_environment, process_tree, windows_job

__all__ = [
    "APP_SERVER_STREAM_LIMIT_BYTES",
    "APP_SERVER_WORKING_DIRECTORY",
    "AppServerError",
    "AppServerManager",
    "JsonRpcProcess",
    "JsonRpcResponseError",
    "LaunchKind",
    "SUBAGENT_BASE_INSTRUCTIONS",
    "StartCandidateUnavailable",
    "_atk_config",
    "_developer_instructions",
    "child_environment",
    "codex_providers",
    "codex_usage_limit",
    "lifetime",
    "mcp_environment",
    "plugin_roots",
    "process_tree",
    "python_runtime_instructions",
    "shared_state",
    "windows_job",
]


class AppServerManager(
    codex_instructions.CodexInstructionsMixin,
    codex_clients.CodexClientsMixin,
    codex_connections.CodexConnectionsMixin,
    codex_turns.CodexTurnsMixin,
    codex_reset.CodexResetMixin,
    codex_start.CodexStartMixin,
    codex_replies.CodexRepliesMixin,
    codex_notifications.CodexNotificationsMixin,
):  # pylint: disable=too-many-ancestors  # 責務別のmixinへ分割したため。
    """Codex App Serverと共有session状態を管理する。

    App Serverは委譲sessionごとに起動し、そのsessionの開始・継続・再開・中断を所有する接続へ送る。
    App Serverはthreadごとのstdio MCPサーバーを子孫として起動し、購読解除の後も猶予の間は生存させる。
    このためsessionの資源解放は、所有するApp Serverと子孫の終了を確認して完了とする。
    """

    START_FAILURE_EXCLUDES_CANDIDATE: typing.ClassVar[bool] = False
    """起動の例外は候補を変えても結果が変わらないため、次の候補へ進まずに委譲元へ返す。"""
    INTERRUPT_REQUIRES_TURN_ID: typing.ClassVar[bool] = True
    """`turn/interrupt`はturnの識別子を要するため、中断の要求はturnの開始を待ってから送る。"""
    ORPHAN_TAKEOVER: typing.ClassVar[bool] = True
    """所有者のいない`running`の登録簿の記録を、`thread/read`の結果から終了として引き継げる。"""

    async def __aenter__(self) -> typing.Self:
        return self

    async def __aexit__(self, *_exception: object) -> None:
        await self.close()

    @staticmethod
    def unavailable_reason(session: SessionState) -> str | None:
        """終了したsessionがengineの可用性を理由に失敗した場合、その除外理由を返す。

        接続先がモデルIDを受け付けなかった失敗も、別の候補なら結果が変わるため除外理由にする。
        """

        def model_rejected(error: Mapping[str, Any]) -> str | None:
            if engine_availability.rejected_parameter(error) == "model":
                return engine_availability.ENGINE_MODEL_REJECTED_REASON
            return None

        return engine_availability.unavailable_reason(session, other_reason=model_rejected)

    @staticmethod
    def excludes_with_recorded_reason(reason: str) -> bool:
        """sandboxの旧記録と、API設定済みの利用上限の旧記録は現在の利用可否で判定し直す。"""
        if reason.partition(":")[0] in {
            "exploreSandboxNamespaceDenied",
            "exploreSandboxRpcError",
            "exploreSandboxTimeout",
            "exploreSandboxInitializationFailed",
            "exploreSandboxProbeFailed",
            "exploreSandboxInvalidResponse",
            "exploreSandboxCommandFailed",
        }:
            return False
        return reason != "usageLimitExceeded" or not codex_providers.has_provider_configuration()

    def __init__(
        self,
        sessions: dict[str, SessionState] | None = None,
        condition: asyncio.Condition | None = None,
        publish_registry: bool = False,
        *,
        root_session_id: str | None = None,
        validate_session_id: session_identity.IdentityValidator | None = None,
    ) -> None:
        self._clients: dict[str, JsonRpcProcess] = {}
        """委譲session（親thread）の識別子から、そのsessionが所有するApp Server接続への対応。"""
        self._unassigned_clients: dict[int, JsonRpcProcess] = {}
        """sessionへの所有移譲前と、短命な照会中の接続。解放失敗後も再試行できるよう保持する。"""
        self.sessions = sessions if sessions is not None else {}
        self._condition = condition if condition is not None else asyncio.Condition()
        self._publish_registry = publish_registry
        self._root_session_id = root_session_id
        self._validate_session_id = validate_session_id or session_identity.identity_validator(self.sessions, root_session_id)
        self._lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._writer_session_ids: dict[str, str] = {}
        self._resume_close_events: dict[str, asyncio.Event] = {}
        self._wait_compactors: dict[str, wait_compaction.WaitCompaction] = {}
        self._wait_compaction_turns: dict[str, _WaitCompactionTurn] = {}
        self._auto_reset_monitors: dict[str, asyncio.Task[None]] = {}
        self._auto_reset_checks: dict[str, asyncio.Task[None]] = {}
