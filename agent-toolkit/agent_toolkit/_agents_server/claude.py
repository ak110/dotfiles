"""Claude Agent SDKを1セッション1長命タスクで所有するバックエンド。

`ClaudeSDKClient`の接続、メッセージ消費、継続入力、切断は同じセッションタスクが担当する。
MCP層はキューへ入力を渡し、クライアントへ直接アクセスしない。
"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
import asyncio
import logging
import pathlib
import shutil
import typing
from typing import Any

from agent_toolkit._agents_server import (
    api_credits,
    child_environment,
    claude_settings,
    engine_availability,
    result_projection,
    resume_waits,
    session_errors,
    session_identity,
)
from agent_toolkit._agents_server import state as shared_state
from agent_toolkit._agents_server.claude_channels import _CommandChannel
from agent_toolkit._agents_server.claude_diagnostics import (
    _DEBUG_LOG_DIR_NAME,
    _DEBUG_LOG_RETENTION,
    _InitializationDiagnostic,
    _owned_runtime_processes,
    _prepare_debug_file,
    rename_debug_file_for_session,
)
from agent_toolkit._agents_server.claude_messages import _connection_kind, _returns_to_subscription, consume_assistant_message
from agent_toolkit._agents_server.claude_options import (
    _LAUNCH_ALLOWED_TOOLS,
    _build_options,
    _settings_from_cmdline,
    plugin_roots,
)
from agent_toolkit._agents_server.claude_stream import ClaudeStream
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.session_errors import SessionInitializationTimeoutError, SessionOwnerGoneError
from agent_toolkit._agents_server.state import LaunchKind, ModelCandidate, ResumePrompt, SessionState, begin_reply
from agent_toolkit._atk import claude_connection
from agent_toolkit._common import claude_connection as connection_settings
from agent_toolkit._common.next_action import ActionableError

__all__ = [
    "ClaudeServerManager",
    "LaunchKind",
    "SessionOwnerGoneError",
    "_CommandChannel",
    "_DEBUG_LOG_DIR_NAME",
    "_DEBUG_LOG_RETENTION",
    "_InitializationDiagnostic",
    "_LAUNCH_ALLOWED_TOOLS",
    "_build_options",
    "_owned_runtime_processes",
    "_prepare_debug_file",
    "_settings_from_cmdline",
    "check_dependencies",
    "consume_assistant_message",
    "plugin_roots",
    "rename_debug_file_for_session",
    "shutil",
]

_LOG = logging.getLogger("agent-toolkit.agents-server.claude")


def check_dependencies() -> None:
    """Claude Agent SDKの依存を読み込み、optionsを構築する。"""
    options = _build_options(str(pathlib.Path.cwd()), None, None)
    claude_settings.cleanup_settings(options)


class ClaudeServerManager(ClaudeStream):
    """Claudeセッションの起動と継続入力を管理する。"""

    START_FAILURE_EXCLUDES_CANDIDATE: typing.ClassVar[bool] = False

    """起動状態や帰属が未確定の一般例外は返す。確認済みの起動前不成立だけを専用型で次候補へ渡す。"""

    INTERRUPT_REQUIRES_TURN_ID: typing.ClassVar[bool] = False

    """中断の要求にturnの識別子を使わない。"""

    ORPHAN_TAKEOVER: typing.ClassVar[bool] = False

    """所有者のいない登録簿の記録を、委譲先CLIの記録から終了として引き継がない。"""

    async def __aenter__(self) -> ClaudeServerManager:
        return self

    async def __aexit__(self, *_exception: object) -> None:
        await self.close()

    @staticmethod
    def unavailable_reason(session: SessionState) -> str | None:
        """終了したsessionがengineの可用性を理由に失敗した場合、その除外理由を返す。

        429は利用枠の種類を付けて記録し、旧版が記録した`429`と区別する。
        """

        def api_status_reason(status: Any) -> str:
            if status != 429:
                return str(status)
            limit_type = session.usage_limit.limit_type if session.usage_limit is not None else None
            return f"429:{limit_type or 'rate_limit'}"

        return engine_availability.unavailable_reason(session, api_status_reason=api_status_reason)

    @staticmethod
    def excludes_with_recorded_reason(reason: str) -> bool:
        """記録済みの除外理由が、候補を除外する根拠になるかを返す。

        旧版が429へ記録した`429`はWeekly limitと5時間の利用上限による拒否も含むため、根拠にせず、
        その候補で起動して利用枠の報告から判定し直す。
        """
        return reason != engine_availability.LEGACY_CLAUDE_RATE_LIMIT_REASON

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
        role = "orchestrator" if model_type == "orchestrate" else "delegate"
        choices = claude_connection.candidates(role, env=child_environment.inherited())
        failures = list(choices.diagnostics)
        connections = choices.connections
        if role == "delegate":
            # 同じプロセスで直前に残高不足を観測した場合は、クレジットで失敗して切り替える往復を省く。
            connections = api_credits.order(connections, _connection_kind, engine="claude")
            if connections != choices.connections:
                _LOG.info("claude_connection_reordered reason=api_credits order=%s", ",".join(c.kind for c in connections))
        for connection in connections:
            _LOG.info("claude_connection_attempt connection=%s role=%s resumed=false", connection.kind, role)
            try:
                session = await self._start_owned_task(
                    prompt,
                    cwd,
                    model,
                    effort,
                    session_id=None,
                    model_type=model_type,
                    launch_kind=launch_kind,
                    excluded_candidates=excluded_candidates,
                    turn_seq=1,
                    request_prompt=request_prompt,
                    result_contract_recovery_consumed=result_contract_recovery_consumed,
                    api_env_file=connection.api_env_file,
                    connection_kind=connection.kind,
                    isolation_env_file=connection.isolation_env_file,
                )
                session.claude_connection_diagnostics = choices.warnings
                _LOG.info("claude_connection_selected session_id=%s connection=%s", session.session_id, connection.kind)
                return session
            except (SessionInitializationTimeoutError, session_identity.DuplicateSessionIdentityError) as error:
                _LOG.info(
                    "claude_connection_failed connection=%s phase=initialization error_type=%s",
                    connection.kind,
                    type(error).__name__,
                )
                raise
            except (OSError, ValueError, RuntimeError) as error:
                _LOG.info(
                    "claude_connection_failed connection=%s phase=initialization error_type=%s",
                    connection.kind,
                    type(error).__name__,
                )
                if connection.kind == "inherit":
                    raise
                failures.append(f"{connection.kind}: {type(error).__name__}: {error}。認証と利用枠を確認する")
        raise RuntimeError("Claude接続候補が全て不成立: " + "; ".join(failures))

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
        api_env_file: str | None = None,
        connection_kind: str = "inherit",
        request_prompt: str | None = None,
        result_contract_recovery_consumed: bool = False,
        existing_session: SessionState | None = None,
        isolation_env_file: str | None = None,
        handoff_prompt: str = "",
    ) -> SessionState:
        """保存済みClaude sessionを新しい所有タスクで再開する。

        `fast_mode`はCodexの速度の指定であり、本backendは受け取って使わない。
        """
        del fast_mode
        selected_connection = claude_connection.apply_current_settings(
            connection_settings.Connection(
                existing_session.claude_connection or "inherit" if existing_session else connection_kind,
                api_env_file or (existing_session.claude_api_env_file if existing_session else None),
                isolation_env_file,
            ),
            role=(existing_session.claude_role if existing_session else None)
            or ("orchestrator" if model_type == "orchestrate" else "delegate"),
            env=child_environment.inherited(),
        )
        connection_kind = selected_connection.kind
        api_env_file = selected_connection.api_env_file
        isolation_env_file = selected_connection.isolation_env_file
        await self._stop_owned_task(session_id)
        if existing_session is not None:
            begin_reply(existing_session, preserve_waits=True)
        selected = connection_kind
        role = "orchestrator" if model_type == "orchestrate" else "delegate"
        _LOG.info("claude_connection_attempt connection=%s role=%s resumed=true", selected, role)
        try:
            session = await self._start_owned_task(
                prompt,
                cwd,
                model,
                effort,
                session_id=session_id,
                model_type=model_type,
                launch_kind=launch_kind,
                excluded_candidates=excluded_candidates,
                turn_seq=turn_seq + 1,
                request_prompt=request_prompt,
                result_contract_recovery_consumed=result_contract_recovery_consumed,
                api_env_file=api_env_file,
                connection_kind=selected,
                existing_session=existing_session,
                isolation_env_file=isolation_env_file,
                handoff_prompt=handoff_prompt,
            )
        except (OSError, ValueError, RuntimeError, SessionInitializationTimeoutError) as error:
            _LOG.info(
                "claude_connection_failed connection=%s phase=initialization error_type=%s",
                selected,
                type(error).__name__,
            )
            raise
        _LOG.info("claude_connection_selected session_id=%s connection=%s", session.session_id, selected)
        return session

    async def _start_owned_task(
        self,
        prompt: str | ResumePrompt,
        cwd: str,
        model: str | None,
        effort: str | None,
        *,
        session_id: str | None,
        model_type: str | None,
        launch_kind: LaunchKind,
        excluded_candidates: frozenset[ModelCandidate],
        turn_seq: int,
        api_env_file: str | None = None,
        connection_kind: str = "inherit",
        request_prompt: str | None = None,
        result_contract_recovery_consumed: bool = False,
        existing_session: SessionState | None = None,
        isolation_env_file: str | None = None,
        handoff_prompt: str = "",
    ) -> SessionState:
        """新規または保存済みsessionを所有する長命タスクを開始する。"""
        diagnostic = _InitializationDiagnostic()
        if connection_kind == "subscription" and isolation_env_file is None:
            isolation_env_file = claude_connection.apply_current_settings(
                connection_settings.Connection(connection_kind),
                role="orchestrator" if model_type == "orchestrate" else "delegate",
                env=child_environment.inherited(),
            ).isolation_env_file
        debug_file = await run_file_operation(_prepare_debug_file, launch_kind)
        diagnostic.debug_file = str(debug_file)
        options = await run_file_operation(
            _build_options,
            cwd,
            model,
            effort,
            session_id,
            launch_kind=launch_kind,
            stderr=diagnostic.capture_stderr,
            debug_file=debug_file,
            root_session_id=self._root_session_id,
            api_env_file=api_env_file,
            connection_kind=connection_kind,
            isolation_env_file=isolation_env_file,
            handoff_prompt=existing_session.prompt
            if existing_session
            else prompt
            if isinstance(prompt, str)
            else handoff_prompt,
        )
        loop = asyncio.get_running_loop()
        initialized: asyncio.Future[SessionState] = loop.create_future()
        task: asyncio.Task[Any] = asyncio.create_task(
            self._run(
                prompt,
                cwd,
                model,
                effort,
                options,
                initialized,
                expected_session_id=session_id,
                model_type=model_type,
                launch_kind=launch_kind,
                excluded_candidates=excluded_candidates,
                turn_seq=turn_seq,
                diagnostic=diagnostic,
                api_env_file=api_env_file,
                connection_kind=connection_kind,
                existing_session=existing_session,
                request_prompt=request_prompt
                if request_prompt is not None
                else prompt
                if isinstance(prompt, str)
                else handoff_prompt,
                result_contract_recovery_consumed=result_contract_recovery_consumed,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._forget_task)
        try:
            session = await asyncio.wait_for(initialized, timeout=shared_state.SESSION_INITIALIZATION_TIMEOUT)
            if diagnostic.mcp_recovery_task is not None:
                # 起動の全試行と可用性確認を予約し、残予算を回復待ちと応答確定へ半分ずつ割り当てる。
                recovery_budget = (
                    max(
                        0.0,
                        shared_state.HOST_BACKGROUND_THRESHOLD_SECONDS
                        - shared_state.SESSION_INITIALIZATION_TIMEOUT * shared_state.SESSION_INITIALIZATION_ATTEMPTS
                        - shared_state.START_AVAILABILITY_TIMEOUT,
                    )
                    / 2
                )
                completed, _ = await asyncio.wait({diagnostic.mcp_recovery_task}, timeout=recovery_budget)
                if completed:
                    await diagnostic.mcp_recovery_task
        except TimeoutError as exc:
            # SDKがinitを届けないまま接続を保つ場合、所有タスクが失敗してもこの待機は解消しない。
            await self._release_unstarted_task(task)
            diagnostic.record_exception(exc)
            details = await diagnostic.public_async()
            _LOG.error("Claude session初期化timeout: diagnostic=%s", details)
            raise SessionInitializationTimeoutError(
                f"Claude session did not reach init within {shared_state.SESSION_INITIALIZATION_TIMEOUT:.0f}s: "
                f"cwd={cwd}, launch_kind={launch_kind}, model={model}; diagnostic={details}"
            ) from exc
        except BaseException as exc:
            await self._release_unstarted_task(task)
            diagnostic.record_exception(exc)
            _LOG.error("Claude session初期化失敗: diagnostic=%s", await diagnostic.public_async())
            raise
        diagnostic.debug_file = str(
            await run_file_operation(rename_debug_file_for_session, debug_file, session.session_id, launch_kind)
        )
        await session.drain_registration()
        return session

    async def send_message(self, session: SessionState, prompt: str) -> dict[str, Any]:
        channel = self._channels.get(session.session_id)
        if channel is None:
            raise SessionOwnerGoneError("the Claude session owner task has ended")
        # 通常の追送と、孫sessionの終了・利用上限・過負荷の後の自動再開は、いずれもここで次のturnを始める。
        # 進行中のturnと、CLIが自ら次のturnを始め得る待機（バックグラウンドタスクなど）では所有タスクを止めない。
        held_without_pending = session.awaiting_auto_resume and not resume_waits.has_pending_auto_resume_targets(session)
        role = session.claude_role or ("orchestrator" if session.model_type == "orchestrate" else "delegate")
        # 保持した接続の種別を使用順から外した会話は、次のturnで使用順の先頭の利用可能な候補へ移す。
        if (session.terminal or held_without_pending) and claude_connection.outside_order(session.claude_connection, role):
            previous = session.previous_result() if session.terminal and not session.awaiting_auto_resume else None
            await self._resume_next_turn(session, prompt)
            return {"delivery": "reply_started", "previous_result": previous}
        if (session.terminal or held_without_pending) and _returns_to_subscription(session):
            return await self._return_to_subscription(session, prompt)
        async with session.turn_control_lock:
            if not session.terminal and session.interrupt_requested:
                raise ActionableError(
                    "the active Claude turn is being interrupted", next_action=result_projection.RESEND_AFTER_WAIT_NEXT_ACTION
                )
            future = channel.send("prompt", prompt)
            actual_delivery, previous_result = await future
        result: dict[str, Any] = {"delivery": actual_delivery}
        if actual_delivery in {"reply_started", "reply_failed", "reply_ambiguous"}:
            result["previous_result"] = previous_result
        return result

    async def _return_to_subscription(self, session: SessionState, prompt: str) -> dict[str, Any]:
        """APIへ移した会話を、次のturnの指示とともにサブスクリプションの新しい所有タスクで再開する。

        接続は所有タスクの起動時に決まるため、上限でAPIへ移した場合と同じ再開の処理で同じsession IDを続ける。
        再開に失敗した場合はAPIの接続と戻す時刻を元へ戻し、呼び出し元へ失敗を伝える。
        """
        previous_result = session.previous_result() if session.terminal and not session.awaiting_auto_resume else None
        saved = (session.claude_connection, session.claude_api_env_file, session.claude_subscription_retry_at)
        session.claude_connection = "subscription"
        session.claude_api_env_file = None
        session.claude_subscription_retry_at = None
        session.claude_connection_failures.pop("subscription", None)
        _LOG.info("claude_subscription_return session_id=%s", session.session_id)
        try:
            await self._resume_next_turn(session, prompt)
        except BaseException:
            session.claude_connection, session.claude_api_env_file, session.claude_subscription_retry_at = saved
            raise
        return {"delivery": "reply_started", "previous_result": previous_result}

    async def _resume_next_turn(self, session: SessionState, prompt: str) -> None:
        """同じ会話IDの所有タスクへ、次のturnの入力を渡す。"""
        resume_prompt = ResumePrompt(prompt)
        try:
            await self.resume(
                session.session_id,
                resume_prompt,
                session.cwd,
                session.model,
                session.effort,
                model_type=session.model_type,
                launch_kind=session.launch_kind,
                excluded_candidates=session.excluded_candidates,
                turn_seq=session.turn_seq,
                existing_session=session,
            )
        finally:
            resume_prompt.close()

    async def interrupt(self, session: SessionState) -> None:
        """公開killから所有タスクへ中断要求を送り、受理を待つ。"""
        channel = self._channels.get(session.session_id)
        if channel is None and session.session_id not in self._residual_processes:
            raise SessionOwnerGoneError("the Claude session owner task has ended")
        if session.terminal:
            return
        session.interrupt_requested = True
        session.touch()
        try:
            if channel is None:
                await self._reclaim_runtime(session.session_id)
                delivery = "interrupt_accepted"
            else:
                future = channel.send("interrupt", "")
                delivery, _ = await future
        except Exception:
            session.interrupt_requested = False
            session.touch()
            await self._notify_waiters()
            raise
        if delivery != "interrupt_accepted":
            session.interrupt_requested = False
            session.touch()
            await self._notify_waiters()
            raise session_errors.DelegateBackendError(f"unexpected Claude interrupt delivery: {delivery}")
        # control要求の受理とCLIの終了は別の事象である。所有タスクの後始末でCLIと子孫を回収してから停止を公開する。
        await self._stop_owned_task(session.session_id)
        if session.pending_result is not None:
            resume_waits.finalize_pending_result(session, touch=False)
        resume_waits.clear_usage_limit_wait(session)
        session.api_error = None
        self._finalize_turn(session, {"status": "interrupted", "agent_message": session.agent_message, "error": session.error})
        await self._notify_waiters()
