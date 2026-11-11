"""Codex会話の開始と再開。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import typing
from typing import Any

from agent_toolkit._agents_server import codex_providers, codex_resume_state, session_identity, shared_roots
from agent_toolkit._agents_server import state as shared_state
from agent_toolkit._agents_server.codex_instructions import _current_service_tier, _developer_instructions
from agent_toolkit._agents_server.codex_protocol import (
    AppServerError,
    JsonRpcResponseError,
    StartCandidateUnavailable,
    _client_pid,
)
from agent_toolkit._agents_server.codex_rpc import JsonRpcProcess
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.input_validation import validate_cwd, validate_model_effort, validate_prompt
from agent_toolkit._agents_server.launch_prompts import LIGHTWEIGHT_LAUNCH_KINDS, SUBAGENT_BASE_INSTRUCTIONS
from agent_toolkit._agents_server.session_errors import SessionInitializationTimeoutError
from agent_toolkit._agents_server.state import LaunchKind, ModelCandidate, ResumePrompt, SessionState, initialize_turn
from agent_toolkit._atk import codex_connection
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk.usage_limit_policy import should_wait
from agent_toolkit._common import codex_usage_limit

if typing.TYPE_CHECKING:
    from agent_toolkit._agents_server.codex_manager_contract import CodexManager

_LOG = logging.getLogger("agent-toolkit.agents-server.codex")


class CodexStartMixin:
    """Codex会話の開始と再開。

    AppServerManagerへ合成して使うため、各メソッドでは合成後の共有状態と他の責務を参照する。
    """

    async def start(
        self: CodexManager,
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
        """新しいthreadとturnを開始し、直ちにsession状態を返す。"""
        validate_prompt(prompt)
        validate_cwd(cwd)
        validate_model_effort(model, effort)
        async with contextlib.AsyncExitStack() as resources:
            client = await self._ensure_client(launch_kind, prompt)
            resources.push_async_callback(self._discard_client, client)
            thread_response, selection, attempted, owner_session_id, writer_session_id = await self._start_thread(
                client, cwd, model, launch_kind, role=codex_connection.role_of(model_type)
            )
            session_id = thread_response["thread"]["id"]
            await session_identity.validate_identity(self._validate_session_id, session_id, None)
            if owner_session_id is not None and writer_session_id is not None:
                await run_file_operation(shared_roots.write_host_alias, owner_session_id, writer_session_id, session_id)
            self._clients[session_id] = client
            self._unassigned_clients.pop(id(client), None)
            resources.pop_all()
        if writer_session_id is not None:
            self._writer_session_ids[session_id] = writer_session_id
        session = SessionState(
            session_id=session_id,
            cwd=cwd,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            model=model,
            effort=effort,
            engine="codex",
            turn_seq=1,
            publish_registry=self._publish_registry,
            prompt=request_prompt if request_prompt is not None else prompt,
            result_contract_recovery_consumed=result_contract_recovery_consumed,
            codex_model_provider=(thread_response.get("modelProvider") or selection.primary) if selection else None,
            codex_subscription_provider=selection.primary if selection is not None and selection.subscription else None,
            codex_state_provider=thread_response.get("modelProvider") or (selection.primary if selection else None),
            codex_attempted_provider_ids=attempted,
        )
        self.sessions[session_id] = session
        await session.attach_executor_async(_client_pid(client))
        initialize_turn(session)
        session.status = "starting"
        session.touch()
        try:
            await self._start_turn(session, prompt, client)
        except Exception as exc:
            if self._turn_start_response_is_ambiguous(client, exc):
                await self._mark_turn_start_ambiguous(session, exc)
            else:
                await self._mark_failed(session, exc)
            await session.drain_registration()
            return session
        await session.drain_registration()
        return session

    async def _start_thread(
        self: CodexManager,
        client: JsonRpcProcess,
        cwd: str,
        model: str | None,
        launch_kind: LaunchKind,
        *,
        role: str = "delegate",
    ) -> tuple[dict[str, Any], codex_providers.ProviderSelection | None, set[str], str | None, str | None]:
        """新しい接続でthreadを開始し、応答・接続先の選択・試行した接続先・内側MCPの所有者と書込主体を返す。"""
        selection = None
        if codex_providers.has_provider_configuration():
            selection = await codex_providers.select(client.request, cwd, role=role)
            if not codex_providers.has_provider_configuration():
                selection = None
        params: dict[str, Any] = {
            "cwd": cwd,
            "baseInstructions": SUBAGENT_BASE_INSTRUCTIONS,
            # Codexが承認を要すると判定するコマンドは拒否され、PermissionRequest hookは呼ばれない。
            # 委譲先の標準手順は承認を要しない操作で構成し、ホストの権限制約を維持する。
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
            "serviceTier": await run_file_operation(_current_service_tier),
        }
        if model is not None:
            params["model"] = model
        config, owner_session_id, writer_session_id = self._thread_config(
            lightweight=launch_kind in LIGHTWEIGHT_LAUNCH_KINDS, client=client
        )
        if launch_kind == "explore":
            config = await run_file_operation(self._explore_config, config, client)
        params["config"] = config
        params["developerInstructions"] = await run_file_operation(_developer_instructions, launch_kind)
        attempted: set[str] = set()
        try:
            async with asyncio.timeout(shared_state.SESSION_INITIALIZATION_TIMEOUT):
                thread_response = None
                if selection is not None and (selection.primary or selection.candidates):
                    order = (selection.primary, *selection.candidates)
                    failures: list[str] = []
                    if (
                        selection.subscription
                        and selection.ordinary_usage_allowed is False
                        and not should_wait("codex", selection.usage_limit_type)
                    ):
                        order = selection.candidates
                        resets = codex_usage_limit.UsageLimitState(selection.usage_limit_resets_at).resets_at_iso() or "不明"
                        skipped = (
                            "サブスクリプションが利用上限で通常利用できない"
                            f"（ordinaryUsageAllowed=false、上限種別={selection.usage_limit_type or '不明'}、"
                            f"解除予定={resets}）。"
                            f"`codex_usage_limit_wait`={_atk_config.resolve_mutable_setting('codex_usage_limit_wait')}"
                            "はこの上限の解除を待たない"
                        )
                        if not order:
                            key = _atk_config.connection_order_key("codex", role)
                            # 利用上限は時間の経過で解除され、接続先の設定や認証の欠陥を示さない。
                            # 候補の不成立として返し、同じ起動要求の次の候補へ進める。
                            raise StartCandidateUnavailable(
                                f"Codexの接続を開始していない（provider={selection.primary}）: {skipped}。"
                                f"使用順`{key}`に他の利用可能な接続先が無い",
                                next_action=(
                                    "理由に示した解除予定まで待つか、`model_type`へ別のengineの候補を指定して起動し直す。"
                                    f"上限中もCodexを使う場合は`atk config set {key} <種別列>`でAPI課金の接続先を使用順へ加える"
                                ),
                            )
                        failures.append(f"{selection.primary}: 試していない。{skipped}")
                    for candidate in order:
                        attempted.add(candidate)
                        try:
                            thread_response = await client.request("thread/start", {**params, "modelProvider": candidate})
                        except JsonRpcResponseError as exc:
                            detail, logged = exc.diagnostic()
                            _LOG.info("Codex接続先の開始が拒否されました: provider=%s %s", candidate, logged)
                            # サブスクリプションの主候補が一般的なRPC拒否を返した場合は、API課金の候補へ切り替えない。
                            if candidate == selection.primary and selection.subscription:
                                raise
                            failures.append(f"{candidate}: {detail}")
                            continue
                        if thread_response.get("modelProvider") != candidate:
                            raise AppServerError("thread/start did not confirm the requested connection")
                        break
                    if thread_response is None:
                        # 利用上限による未試行と区別し、RPC拒否の理由を委譲元へ返す。
                        raise AppServerError("Codex connection candidates were rejected: " + "; ".join(failures))
                else:
                    thread_response = await client.request("thread/start", params)
        except TimeoutError as exc:
            diagnostic_method = getattr(client, "initialization_diagnostic", None)
            diagnostic = (
                diagnostic_method()
                if callable(diagnostic_method)
                else {"stage": "thread_start_response_wait", "pid": "unavailable"}
            )
            _LOG.error("Codex thread/start初期化timeout: diagnostic=%s", diagnostic)
            raise SessionInitializationTimeoutError(
                f"Codex thread/start did not return within {shared_state.SESSION_INITIALIZATION_TIMEOUT:.0f}s: "
                f"cwd={cwd}, launch_kind={launch_kind}; diagnostic={diagnostic}"
            ) from exc
        thread = thread_response.get("thread")
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or not thread["id"]:
            raise AppServerError("thread/start returned no thread.id")
        return thread_response, selection, attempted, owner_session_id, writer_session_id

    async def resume(
        self: CodexManager,
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
    ) -> SessionState:
        """保存済みthreadを再開して新しいturnを開始する。"""
        validate_cwd(cwd)
        validate_model_effort(model, effort)
        await self._stop_auto_reset(session_id)
        client = await self._acquire_owned_client(session_id, launch_kind, handoff_prompt)
        session = SessionState(
            session_id=session_id,
            cwd=cwd,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            model=model,
            effort=effort,
            engine="codex",
            turn_seq=turn_seq + 1,
            fast_mode=fast_mode,
            publish_registry=self._publish_registry,
            prompt=request_prompt if request_prompt is not None else prompt if isinstance(prompt, str) else handoff_prompt,
            result_contract_recovery_consumed=result_contract_recovery_consumed,
            codex_model_provider=model_provider,
            codex_subscription_provider=subscription_provider,
            codex_state_provider=state_provider,
        )
        self.sessions[session_id] = session
        await session.attach_executor_async(_client_pid(client))
        initialize_turn(session)
        try:
            writer_session_id = await self._resume_thread(
                session,
                client,
                self._writer_session_ids.get(session.session_id),
            )
            if writer_session_id is not None:
                self._writer_session_ids[session.session_id] = writer_session_id
            await prompt.deliver(lambda value: self._start_turn(session, value, client))
        except asyncio.CancelledError:
            await self._cancel_resume(session)
        except codex_resume_state.IncompatibleResumeError as exc:
            prompt.record_delivery("reply_failed")
            await self._mark_incompatible_resume(session, exc)
        except Exception as exc:
            if self._turn_start_response_is_ambiguous(client, exc):
                prompt.record_delivery("reply_ambiguous")
                await self._mark_turn_start_ambiguous(session, exc)
            else:
                prompt.record_delivery("reply_failed")
                await self._mark_failed(session, exc)
        await session.drain_registration()
        return session

    async def _cancel_resume(self: CodexManager, session: SessionState) -> None:
        """再開取消時に配送済みturnを中断し、実作業の終了まで待つ。"""
        if not session.turn_start_sent:
            session.status = "interrupted"
            session.record_turn_completed()
            session.interrupt_requested = False
            session.touch()
            await self._notify_waiters()
            return

        if not session.turn_id and not session.turn_completed:
            async with self._condition:
                await self._condition.wait_for(lambda: bool(session.turn_id) or session.turn_completed)
        if session.turn_completed:
            return

        session.interrupt_requested = True
        session.touch()
        await self._notify_waiters()
        await self.interrupt(session)
        async with self._condition:
            await self._condition.wait_for(lambda: session.turn_completed)
