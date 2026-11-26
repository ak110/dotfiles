"""Codexターンの開始。"""

from __future__ import annotations

import datetime
import logging
import typing
from typing import Any

from agent_toolkit._agents_server import codex_providers, codex_resume_state, configured_model
from agent_toolkit._agents_server.codex_instructions import _current_service_tier
from agent_toolkit._agents_server.codex_protocol import (
    JsonRpcResponseError,
    StartCandidateUnavailable,
    TurnStartResponseError,
    _client_pid,
    redact_diagnostic,
)
from agent_toolkit._agents_server.codex_rpc import JsonRpcProcess
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._atk import codex_connection

if typing.TYPE_CHECKING:
    from agent_toolkit._agents_server.codex_manager_contract import CodexManager

_LOG = logging.getLogger("agent-toolkit.agents-server.codex")


CONNECTION_REJECTED_MESSAGE = "Codex connection rejected the request"


class CodexTurnsMixin:
    """Codexターンの開始。

    AppServerManagerへ合成して使うため、各メソッドでは合成後の共有状態と他の責務を参照する。
    """

    async def _mark_failed(
        self: CodexManager,
        session: SessionState,
        error: BaseException,
    ) -> None:
        """要求開始の失敗を終了状態へ反映し、待機者を起こす。"""
        public_error = {"message": str(error) or error.__class__.__name__}
        writer_conflict = isinstance(error, codex_resume_state.ThreadWriterConflictError)
        if writer_conflict:
            public_error["codexErrorInfo"] = codex_resume_state.WRITER_CONFLICT
        failure = public_error.copy()
        if isinstance(error, JsonRpcResponseError) and isinstance(error.data, dict):
            failure.update(error.data)
        changed = (
            await configured_model.changed_model_error(session)
            if failure.get("codexErrorInfo") == "usageLimitExceeded"
            else None
        )

        session.interrupt_requested = False
        limit = (
            None
            if changed is not None or isinstance(error, StartCandidateUnavailable) or writer_conflict
            else await self._read_usage_limit_failure(session, failure)
        )
        auto_reset = limit is not None and await self._auto_reset_configured(session)
        session.turn_id = ""
        session.status = "failed"
        session.plan = []
        session.record_current_item_start(None)
        session.commentary = ""
        session.diff_changed = False
        session.error = changed or public_error
        if (
            session.codex_model_provider
            and session.codex_model_provider != session.codex_subscription_provider
            and not isinstance(error, StartCandidateUnavailable)
            and not writer_conflict
            and changed is None
        ):
            logged = (
                error.diagnostic()[1]
                if isinstance(error, JsonRpcResponseError)
                else redact_diagnostic(f"{type(error).__name__}: {error}")
            )
            _LOG.info(
                "Codex turn開始の拒否を汎用文言へ置き換えました: session_id=%s provider=%s %s",
                session.session_id,
                session.codex_model_provider,
                logged,
            )
            session.error = {
                "message": (
                    f"{CONNECTION_REJECTED_MESSAGE}。接続先と元のエラーはagents-server.logのsession_id={session.session_id}の行にある"
                )
            }
        session.agent_message = ""
        session.protocol_warnings = []
        session.record_turn_rejected()
        session.record_turn_completed()
        if (
            not isinstance(error, StartCandidateUnavailable)
            and not writer_conflict
            and changed is None
            and not self._hold_usage_limit_failure(session, limit, auto_reset=auto_reset)
        ):
            self._hold_provider_failure(session, failure)
        session.touch()
        await self._notify_waiters()

    async def _mark_turn_start_ambiguous(self: CodexManager, session: SessionState, error: BaseException) -> None:
        if session.terminal:
            return
        session.status = "running"
        session.error = {"message": str(error) or error.__class__.__name__}
        session.record_turn_ambiguous()
        session.interrupt_requested = False
        session.record_turn_running()
        session.touch()
        await self._notify_waiters()

    async def _start_turn(
        self: CodexManager,
        session: SessionState,
        prompt: str,
        client: JsonRpcProcess | None = None,
        *,
        auth: dict[str, Any] | None = None,
        quota_workspace: str | None = None,
        auth_observed_at: datetime.datetime | None = None,
    ) -> None:
        if client is None:
            client = await self._acquire_owned_client(session.session_id, session.launch_kind)
        self._start_auto_reset_monitor(session, client)

        await session.attach_executor_async(_client_pid(client))
        provider = session.codex_model_provider or session.codex_state_provider
        subscription: bool | None = await run_file_operation(codex_connection.subscription_provider_for, provider) is not None
        if not codex_providers.has_provider_configuration() and provider in (None, "openai"):
            subscription = provider == "openai" if provider is not None else None
        params: dict[str, Any] = {
            "threadId": session.session_id,
            "input": [{"type": "text", "text": prompt}],
            "cwd": session.cwd,
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "dangerFullAccess"},
            "serviceTier": await _current_service_tier(
                client.request,
                session.cwd,
                subscription=subscription,
                provider=provider,
                auth=auth,
                quota_workspace=quota_workspace,
                auth_observed_at=auth_observed_at,
            ),
        }
        if session.model is not None:
            params["model"] = session.model
        if session.effort is not None:
            params["effort"] = session.effort
        response = await client.request(
            "turn/start",
            params,
            on_sent=lambda: self._mark_turn_start_sent(session, fast_mode=params["serviceTier"] == "priority"),
        )
        turn = response.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise TurnStartResponseError("turn/start returned no turn.id")
        session.record_turn_started(turn_id)
        session.touch()
        await self._notify_waiters()

    async def _notify_waiters(self: CodexManager) -> None:
        async with self._condition:
            self._condition.notify_all()

    @staticmethod
    def _turn_start_response_is_ambiguous(client: Any, error: BaseException) -> bool:
        """turn/startの失敗が実行状態を判定できない応答喪失であるかを返す。"""
        if isinstance(error, (JsonRpcResponseError, StartCandidateUnavailable, codex_resume_state.ThreadWriterConflictError)):
            return False
        if bool(getattr(client, "closed", False)) or getattr(client, "reader_failure", None) is not None:
            return False
        process = getattr(client, "process", None)
        return process is None or getattr(process, "returncode", None) is None

    @staticmethod
    def _mark_turn_start_sent(session: SessionState, *, fast_mode: bool) -> None:
        session.record_turn_sent()
        session.fast_mode = fast_mode
        session.touch()
