"""Codex managerの責務間で共有する静的な型契約。実行時の継承と状態はAppServerManagerが持つ。"""

from __future__ import annotations

import asyncio
import contextlib
import typing
from collections.abc import Coroutine
from typing import Any

from agent_toolkit._agents_server import (
    codex_auto_reset,
    codex_providers,
    codex_resume_state,
    session_identity,
    wait_compaction,
)
from agent_toolkit._agents_server.codex_protocol import JsonRpcResponseError, _WaitCompactionTurn
from agent_toolkit._agents_server.codex_rpc import JsonRpcProcess
from agent_toolkit._agents_server.state import LaunchKind, ModelCandidate, ResumePrompt, SessionState
from agent_toolkit._common import codex_usage_limit


class CodexManager(typing.Protocol):
    """Codexの責務別mixinが参照する合成先の共有状態とメソッド。"""

    _clients: dict[str, JsonRpcProcess]
    _unassigned_clients: dict[int, JsonRpcProcess]
    _background_tasks: set[asyncio.Task[None]]
    _writer_session_ids: dict[str, str]
    _resume_close_events: dict[str, asyncio.Event]
    _wait_compactors: dict[str, wait_compaction.WaitCompaction]
    _wait_compaction_turns: dict[str, _WaitCompactionTurn]
    _auto_reset_monitors: dict[str, asyncio.Task[None]]
    _auto_reset_checks: dict[str, asyncio.Task[None]]
    sessions: dict[str, SessionState]
    _condition: asyncio.Condition
    _publish_registry: bool
    _root_session_id: str | None
    _validate_session_id: session_identity.IdentityValidator
    _lock: asyncio.Lock
    _close_task: asyncio.Task[None] | None

    def _schedule(self, awaitable: Coroutine[Any, Any, None]) -> None: ...

    async def _ensure_client(self, launch_kind: LaunchKind | None = None, handoff_prompt: str = "") -> JsonRpcProcess: ...

    async def _discard_client(self, client: Any) -> None: ...

    def _owned_client(self, session: SessionState) -> Any: ...

    async def _acquire_owned_client(
        self, session_id: str, launch_kind: LaunchKind, handoff_prompt: str = ""
    ) -> JsonRpcProcess: ...

    async def _release_owned_client(self, session_id: str) -> None: ...

    async def read_thread_turns(self, session_id: str) -> list[tuple[str, str]]: ...

    async def list_models(self) -> list[dict[str, Any]]: ...

    async def close(self) -> None: ...

    async def _close(self) -> None: ...

    async def _switch_provider(self, session: SessionState, client: Any) -> bool: ...

    async def _close_for_provider_switch(self, session: SessionState, client: Any) -> None: ...

    async def _close_thread(self, thread_id: str, client: Any, *, session: SessionState | None = None) -> None: ...

    async def _resume_thread(self, session: SessionState, client: Any, writer_session_id: str | None = None) -> str | None: ...

    @staticmethod
    async def _compatible_resume_provider(session: SessionState, client: Any) -> str: ...

    async def _mark_incompatible_resume(
        self, session: SessionState, error: codex_resume_state.IncompatibleResumeError, *, new_reply: bool = False
    ) -> None: ...

    async def _request_thread_resume(self, client: Any, params: dict[str, Any]) -> dict[str, Any]: ...

    def _hold_provider_failure(self, session: SessionState, error: Any = None) -> bool: ...

    def _thread_config(
        self, session_id: str | None = None, *, lightweight: bool, client: Any = None
    ) -> tuple[dict[str, Any], str | None, str | None]: ...

    async def _handle_notification(self, message: dict[str, Any]) -> None: ...

    async def _handle_server_request(self, message: dict[str, Any], client: Any) -> None: ...

    async def _fail_for_request(self, params: Any, method: str, client: Any) -> None: ...

    async def _interrupt(self, session_id: str, turn_id: str, client: Any) -> None: ...

    async def _handle_interrupt_response_error(self, session_id: str, turn_id: str, error: JsonRpcResponseError) -> None: ...

    async def _handle_client_failure(self, error: BaseException, client: Any) -> None: ...

    def _codex_sessions(self) -> list[SessionState]: ...

    def _client_sessions(self, client: Any) -> list[SessionState]: ...

    def _find_thread_owner(self, thread_id: Any) -> SessionState | None: ...

    def _find_session(self, params: Any) -> SessionState | None: ...

    async def send_message(self, session: SessionState, prompt: str) -> dict[str, Any]: ...

    def _input_turn_control(self, session: SessionState) -> contextlib.AbstractAsyncContextManager[None]: ...

    async def release_session(self, session: SessionState) -> None: ...

    async def interrupt(self, session: SessionState) -> None: ...

    async def _start_reply_locked(
        self, session: SessionState, prompt: str
    ) -> tuple[str, dict[str, Any], BaseException | None]: ...

    async def _wait_after_steer_rejection(self, session: SessionState, expected_turn_id: str, client: Any) -> str: ...

    def _hold_usage_limit_failure(self, session: SessionState, limit: codex_usage_limit.UsageLimitState | None) -> bool: ...

    def _auto_reset_eligible(self, session: SessionState, *, before_hold: bool = False) -> bool: ...

    def _start_auto_reset_monitor(self, session: SessionState, client: JsonRpcProcess) -> None: ...

    async def _monitor_auto_reset(self, session: SessionState, client: JsonRpcProcess) -> None: ...

    def _start_auto_reset_check(self, session: SessionState) -> None: ...

    async def _check_auto_reset(self, session: SessionState, client: JsonRpcProcess, turn_seq: int) -> None: ...

    async def _cancel_auto_reset_check(self, session_id: str) -> None: ...

    async def _stop_auto_reset(self, session_id: str) -> None: ...

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
    ) -> SessionState: ...

    async def _start_thread(
        self, client: JsonRpcProcess, cwd: str, model: str | None, launch_kind: LaunchKind, *, role: str = "delegate"
    ) -> tuple[dict[str, Any], codex_providers.ProviderSelection | None, set[str], str | None, str | None]: ...

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
        model_provider: str | None = None,
        subscription_provider: str | None = None,
        state_provider: str | None = None,
        handoff_prompt: str = "",
    ) -> SessionState: ...

    async def _cancel_resume(self, session: SessionState) -> None: ...

    async def _mark_failed(self, session: SessionState, error: BaseException) -> None: ...

    async def _mark_turn_start_ambiguous(self, session: SessionState, error: BaseException) -> None: ...

    async def _start_turn(self, session: SessionState, prompt: str, client: JsonRpcProcess | None = None) -> None: ...

    async def _notify_waiters(self) -> None: ...

    async def _compact_wait(self, session: SessionState, key: str) -> None: ...

    @staticmethod
    def _agents_server_config(owner_session_id: str, writer_session_id: str, client: Any = None) -> dict[str, Any]: ...

    @staticmethod
    def _base_thread_config(*, lightweight: bool) -> dict[str, Any]: ...

    @staticmethod
    def _explore_config(config: dict[str, Any], client: Any) -> dict[str, Any]: ...

    @staticmethod
    def _capture_result(session: SessionState) -> dict[str, Any]: ...

    @staticmethod
    def _initialize_turn(session: SessionState) -> None: ...

    @staticmethod
    def _begin_reply(session: SessionState) -> None: ...

    @staticmethod
    def _provider_failure(session: SessionState, error: Any = None) -> bool: ...

    @staticmethod
    async def _apply_connection_order(session: SessionState, client: Any) -> None: ...

    @staticmethod
    def _turn_start_response_is_ambiguous(client: Any, error: BaseException) -> bool: ...

    @staticmethod
    def _mark_turn_start_sent(session: SessionState, *, fast_mode: bool) -> None: ...

    @staticmethod
    async def _read_usage_limit_failure(session: SessionState, error: Any) -> codex_usage_limit.UsageLimitState | None: ...

    @staticmethod
    def _auto_reset_check_done(task: asyncio.Task[None]) -> None: ...

    @staticmethod
    def _apply_auto_reset_result(session: SessionState, result: codex_auto_reset.AutoResetResult | None) -> None: ...

    @staticmethod
    def _track_subagent_thread(
        session: SessionState, item: Any, *, source_thread_id: str | None = None, source_turn_id: str | None = None
    ) -> None: ...

    @staticmethod
    def _notification_turn_id(params: dict[str, Any]) -> str | None: ...

    @staticmethod
    async def _consume_items(session: SessionState, items: Any) -> None: ...

    @staticmethod
    async def _consume_item(session: SessionState, item: dict[str, Any]) -> None: ...
