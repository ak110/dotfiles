"""Codex接続先の切替と状態互換性。"""

from __future__ import annotations

import asyncio
import logging
import typing
import uuid
from typing import Any

from agent_toolkit._agents_server import (
    api_credits,
    codex_providers,
    codex_resume_state,
    resume_waits,
    shared_roots,
    turn_state,
)
from agent_toolkit._agents_server import state as shared_state
from agent_toolkit._agents_server.codex_instructions import (
    _current_service_tier,
    _developer_instructions,
)
from agent_toolkit._agents_server.codex_protocol import DEFAULT_WAIT_TIMEOUT, AppServerError, JsonRpcResponseError
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.launch_prompts import LIGHTWEIGHT_LAUNCH_KINDS, SUBAGENT_BASE_INSTRUCTIONS
from agent_toolkit._agents_server.state import SessionState, begin_reply
from agent_toolkit._atk import codex_connection
from agent_toolkit._atk.usage_limit_policy import should_wait

if typing.TYPE_CHECKING:
    from agent_toolkit._agents_server.codex_manager_contract import CodexManager

_LOG = logging.getLogger("agent-toolkit.agents-server.codex")


class CodexConnectionsMixin:
    """Codex接続先の切替と状態互換性。

    AppServerManagerへ合成して使うため、各メソッドでは合成後の共有状態と他の責務を参照する。
    """

    async def _switch_provider(self: CodexManager, session: SessionState, client: Any) -> bool:
        """新しいturnを開始する前に、同じthreadを独立API認証の次候補へcold resumeする。"""
        if session.turn_start_ambiguous or session.turn_start_sent and not session.turn_completed:
            return False
        selection = await codex_providers.select(
            client.request, session.cwd, primary=session.codex_model_provider, role=codex_connection.role_of(session.model_type)
        )
        if session.codex_model_provider:
            session.codex_attempted_provider_ids.add(session.codex_model_provider)
        if session.codex_subscription_provider:
            session.codex_attempted_provider_ids.add(session.codex_subscription_provider)
        original = session.codex_model_provider
        for candidate in selection.candidates:
            if candidate in session.codex_attempted_provider_ids:
                continue
            session.codex_attempted_provider_ids.add(candidate)
            if session.interrupt_requested:
                return False
            try:
                await self._close_for_provider_switch(session, client)
                if session.interrupt_requested:
                    return False
                session.codex_model_provider = candidate
                await self._resume_thread(session, client, self._writer_session_ids.get(session.session_id))
            except JsonRpcResponseError as exc:
                session.codex_model_provider = original
                _LOG.info(
                    "Codex接続先の再開が拒否されました: session_id=%s provider=%s %s",
                    session.session_id,
                    candidate,
                    exc.diagnostic()[1],
                )
                continue
            except AppServerError, TimeoutError, OSError:
                session.codex_model_provider = original
                _LOG.info("Codex接続先の再開を確定できません: session_id=%s provider=%s", session.session_id, candidate)
                return False
            except BaseException:
                session.codex_model_provider = original
                raise
            _LOG.info("Codex接続先を確定しました: session_id=%s provider=%s", session.session_id, candidate)
            return True
        return False

    async def _close_for_provider_switch(self: CodexManager, session: SessionState, client: Any) -> None:
        """Loaded threadの接続先が再利用されないよう、unsubscribeの閉鎖完了を待つ。"""
        async with asyncio.timeout(DEFAULT_WAIT_TIMEOUT):
            await self._close_thread(session.session_id, client, session=session)

    async def _close_thread(self: CodexManager, thread_id: str, client: Any, *, session: SessionState | None = None) -> None:
        """解除応答と閉鎖通知を対応付け、対象threadの資源解放を確認する。"""
        closed = asyncio.Event()
        self._resume_close_events[thread_id] = closed
        try:
            if getattr(client, "closed", False) or getattr(client, "reader_failure", None) is not None:
                raise AppServerError("Codex App Server connection lost before thread closure")
            response = await client.request("thread/unsubscribe", {"threadId": thread_id})
            if response.get("status") == "notLoaded":
                return
            if response.get("status") not in {"unsubscribed", "notSubscribed"}:
                raise AppServerError("thread/unsubscribe returned an unexpected status")
            async with self._condition:
                await self._condition.wait_for(
                    lambda: (
                        closed.is_set()
                        or session is not None
                        and session.interrupt_requested
                        or getattr(client, "closed", False)
                        or getattr(client, "reader_failure", None) is not None
                    )
                )
            if not closed.is_set() and not (session is not None and session.interrupt_requested):
                raise AppServerError("thread/unsubscribe did not complete the connection closure")
        finally:
            self._resume_close_events.pop(thread_id, None)

    async def _resume_thread(
        self: CodexManager,
        session: SessionState,
        client: Any,
        writer_session_id: str | None = None,
    ) -> str | None:
        """保存済みCodex threadを現在の実行条件で再開する。"""
        await self._apply_connection_order(session, client)
        try:
            target = await self._compatible_resume_provider(session, client)
        except (AppServerError, TimeoutError, OSError) as error:
            raise codex_resume_state.IncompatibleResumeError(
                "Codexの再開条件を取得できず、再開先との状態互換性を確認できない"
            ) from error
        resume_params: dict[str, Any] = {
            "threadId": session.session_id,
            "cwd": session.cwd,
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
            "serviceTier": await run_file_operation(_current_service_tier),
        }
        if session.model is not None:
            resume_params["model"] = session.model
        resume_params["modelProvider"] = target
        config = self._base_thread_config(lightweight=session.launch_kind in LIGHTWEIGHT_LAUNCH_KINDS)
        owner_session_id = self._root_session_id
        if owner_session_id is not None:
            writer_session_id = writer_session_id or uuid.uuid4().hex
            config.update(self._agents_server_config(owner_session_id, writer_session_id, client))
        if session.launch_kind == "explore":
            config = await run_file_operation(self._explore_config, config, client)
        resume_params["config"] = config
        resume_params["developerInstructions"] = await run_file_operation(_developer_instructions, session.launch_kind)
        if owner_session_id is not None and writer_session_id is not None:
            # thread/resume中に子MCPが初期化されるため、直接親を復旧候補の選定より先に公開する。
            await run_file_operation(shared_roots.write_host_alias, owner_session_id, writer_session_id, session.session_id)
        resume_response = await self._request_thread_resume(client, resume_params)
        resumed_thread = resume_response.get("thread")
        if not isinstance(resumed_thread, dict) or resumed_thread.get("id") != session.session_id:
            raise AppServerError("thread/resume returned an unexpected thread.id")
        if (session.codex_model_provider is not None or "modelProvider" in resume_response) and resume_response.get(
            "modelProvider"
        ) != target:
            raise AppServerError("thread/resume did not confirm the requested connection")
        session.codex_state_provider = target
        return writer_session_id

    @staticmethod
    async def _compatible_resume_provider(session: SessionState, client: Any) -> str:
        """通常設定も含めて今回の再開先を確定し、同threadの状態を再利用できることを確かめる。"""
        # 状態の生成先の観測根拠は`docs/development/audit-records.md`の
        # 「agent-toolkit/agent_toolkit/_agents_server/codex_resume_state.py：会話状態の生成先：2026年10月23日」にある。
        target = session.codex_model_provider
        if target is None:
            effective = await client.request("config/read", {"cwd": session.cwd, "includeLayers": False})
            configuration = effective.get("config")
            if not isinstance(configuration, dict):
                raise codex_resume_state.IncompatibleResumeError("Codexの再開先設定を取得できず、状態互換性を確認できない")
            target = configuration.get("model_provider") or "openai"
        if not isinstance(target, str):
            raise codex_resume_state.IncompatibleResumeError("Codexの再開先設定が不正で、状態互換性を確認できない")
        if session.codex_state_provider != target:
            record = await client.request("thread/read", {"threadId": session.session_id, "includeTurns": False})
            thread = record.get("thread")
            path = thread.get("path") if isinstance(thread, dict) else None
            await run_file_operation(
                codex_resume_state.validate_resume,
                session.session_id,
                target,
                session.codex_state_provider,
                path if isinstance(path, str) else None,
            )
        return target

    async def _mark_incompatible_resume(
        self: CodexManager,
        session: SessionState,
        error: codex_resume_state.IncompatibleResumeError,
        *,
        new_reply: bool = False,
    ) -> None:
        """元の会話・進捗を保持し、同threadの再送を終えて引き継げる終了を公開する。"""
        if session.pending_result is not None:
            resume_waits.finalize_pending_result(session, touch=False)
        if new_reply:
            progress = (session.agent_message, session.plan, session.commentary, session.diff_changed, session.last_action)
            # 拒否したreplyにも独立した終了を割り当て、回収済みの前turnと同一視させない。
            begin_reply(session, preserve_waits=True)
            session.agent_message, session.plan, session.commentary, session.diff_changed, session.last_action = progress
        session.status = "failed"
        session.error = {"message": str(error), "codexErrorInfo": codex_resume_state.INCOMPATIBLE_STATE}
        session.record_turn_completed()
        session.touch()
        await self._notify_waiters()

    async def _request_thread_resume(self: CodexManager, client: Any, params: dict[str, Any]) -> dict[str, Any]:
        """unsubscribe後の閉鎖と再開が競合した場合だけ、閉鎖通知を待って再送する。"""
        params = {**params, "baseInstructions": SUBAGENT_BASE_INSTRUCTIONS}
        thread_id = params["threadId"]
        closed = asyncio.Event()
        self._resume_close_events[thread_id] = closed
        try:
            async with asyncio.timeout(shared_state.SESSION_INITIALIZATION_TIMEOUT):
                try:
                    return await client.request("thread/resume", params)
                except JsonRpcResponseError as exc:
                    if str(exc) != (
                        f"thread/resume: thread {thread_id} is closing; retry thread/resume after the thread is closed"
                    ):
                        raise
                    async with self._condition:
                        await self._condition.wait_for(
                            lambda: (
                                closed.is_set()
                                or getattr(client, "closed", False)
                                or getattr(client, "reader_failure", None) is not None
                            )
                        )
                    if not closed.is_set():
                        raise
                return await client.request("thread/resume", params)
        except JsonRpcResponseError as exc:
            if exc.method == "thread/resume" and thread_id in str(exc) and "already has an active writer" in str(exc):
                raise codex_resume_state.ThreadWriterConflictError(str(exc)) from exc
            raise
        finally:
            self._resume_close_events.pop(thread_id, None)

    def _hold_provider_failure(self: CodexManager, session: SessionState, error: Any = None) -> bool:
        """候補切替の間は失敗を公開せず、既存の監視から継続を一度だけ配送する。"""
        if (
            session.interrupt_requested
            or not self._provider_failure(session, error)
            or not codex_providers.has_provider_configuration()
        ):
            return False
        session.hold_result(
            turn_state.CodexProviderWait(
                {"status": session.status, "agent_message": session.agent_message, "error": session.error}
            )
        )
        # App Serverは残高不足を区別した値を返さないため、クレジットの候補の不受理を残高不足として扱う。
        kind = codex_connection.provider_kind(
            session.codex_model_provider, subscription_provider=session.codex_subscription_provider
        )
        if kind == "credits":
            api_credits.record_shortage("codex")
        session.status = "running"
        return True

    @staticmethod
    def _provider_failure(session: SessionState, error: Any = None) -> bool:
        """利用上限で終了した失敗と、移行済みAPIが受け付けなかった失敗だけを、代替の接続先での試行の対象と判定する。

        統括担当は会話の中で接続を切り替えないため対象にしない。
        """
        if not session.codex_model_provider or not session.turn_completed or session.turn_start_ambiguous:
            return False
        if codex_connection.role_of(session.model_type) == "orchestrator":
            return False
        result = session.pending_result
        status = result["status"] if result is not None else session.status
        if error is None:
            error = result["error"] if result is not None else session.error
        if isinstance(error, dict) and error.get("codexErrorInfo") == codex_resume_state.WRITER_CONFLICT:
            return False
        limit = error.get("usageLimit") if isinstance(error, dict) else None
        if isinstance(limit, dict) and should_wait("codex", limit.get("type")):
            return False
        if session.codex_provider_resume_pending:
            return True
        return status == "failed" and codex_providers.fallback_failure(
            error, subscription=session.codex_model_provider == session.codex_subscription_provider
        )

    @staticmethod
    async def _apply_connection_order(session: SessionState, client: Any) -> None:
        """保持したproviderの種別を使用順から外した会話を、使用順の先頭の利用可能な候補へ移す。

        移した後の種別は使用順に含まれるため、使用順から外れた種別へは戻らない。
        """
        role = codex_connection.role_of(session.model_type)
        if not codex_connection.outside_order(
            session.codex_model_provider, role, subscription_provider=session.codex_subscription_provider
        ):
            return
        selection = await codex_providers.select(client.request, session.cwd, role=role)
        _LOG.info("Codex接続先を使用順の先頭へ移しました: session_id=%s provider=%s", session.session_id, selection.primary)
        session.codex_model_provider = selection.primary
        session.codex_subscription_provider = selection.primary if selection.subscription else None
