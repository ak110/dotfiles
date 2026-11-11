"""Codexの利用上限待機と自動reset。"""

from __future__ import annotations

import asyncio
import logging
import time
import typing
from typing import Any

from agent_toolkit._agents_server import codex_auto_reset, resume_waits, turn_state
from agent_toolkit._agents_server.codex_protocol import AppServerError
from agent_toolkit._agents_server.codex_rpc import JsonRpcProcess
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.state import SessionState, utc_now
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk.usage_limit_policy import should_wait
from agent_toolkit._common import codex_usage_limit, state_paths

if typing.TYPE_CHECKING:
    from agent_toolkit._agents_server.codex_manager_contract import CodexManager

_LOG = logging.getLogger("agent-toolkit.agents-server.codex")


AUTO_RESET_CHECK_INTERVAL = 30.0


class CodexResetMixin:
    """Codexの利用上限待機と自動reset。

    AppServerManagerへ合成して使うため、各メソッドでは合成後の共有状態と他の責務を参照する。
    """

    def _hold_usage_limit_failure(
        self: CodexManager, session: SessionState, limit: codex_usage_limit.UsageLimitState | None
    ) -> bool:
        """読取済みの利用枠を使い、終了反映と同じloop区間で保留を確定する。"""
        if session.status != "failed" or limit is None:
            return False
        error = {
            **(session.error if isinstance(session.error, dict) else {}),
            "codexErrorInfo": "usageLimitExceeded",
            "usageLimit": {"type": limit.limit_type, "resetsAt": limit.resets_at_iso()},
        }
        session.error = error
        auto_reset = (
            self._auto_reset_eligible(session, before_hold=True)
            and _atk_config.resolve_mutable_setting("codex_auto_reset") == "true"
        )
        if session.interrupt_requested or (not auto_reset and not should_wait("codex", limit.limit_type)):
            return False
        session.hold_result(
            turn_state.UsageLimitWait(
                {"status": session.status, "agent_message": session.agent_message, "error": error},
                asyncio.get_running_loop().time() + limit.delay_seconds(time.time()),
            )
        )
        session.usage_limit_wait_count += 1
        if session.usage_limit_first_at is None:
            session.usage_limit_first_at = utc_now()
        session.api_error = {
            "type": "usage_limit",
            "limit_type": limit.limit_type,
            "resets_at": limit.resets_at_iso(),
            "first_at": session.usage_limit_first_at,
            "last_at": utc_now(),
            "count": session.usage_limit_wait_count,
        }
        session.status = "running"
        if auto_reset:
            self._start_auto_reset_check(session)
        return True

    def _auto_reset_eligible(self: CodexManager, session: SessionState, *, before_hold: bool = False) -> bool:
        return (
            self.sessions.get(session.session_id) is session
            and not session.interrupt_requested
            and (before_hold or not session.result_available)
            and self._owned_client(session) is not None
            and (session.codex_model_provider or session.codex_state_provider)
            in {None, session.codex_subscription_provider or "openai"}
        )

    def _start_auto_reset_monitor(self: CodexManager, session: SessionState, client: JsonRpcProcess) -> None:
        task = self._auto_reset_monitors.get(session.session_id)
        if task is None or task.done():
            task = asyncio.create_task(self._monitor_auto_reset(session, client))
            self._auto_reset_monitors[session.session_id] = task
            task.add_done_callback(self._auto_reset_check_done)

    async def _monitor_auto_reset(self: CodexManager, session: SessionState, client: JsonRpcProcess) -> None:
        while self.sessions.get(session.session_id) is session and self._owned_client(session) is client:
            await asyncio.sleep(AUTO_RESET_CHECK_INTERVAL)
            if session.result_available or session.interrupt_requested:
                return
            try:
                enabled = await run_file_operation(_atk_config.resolve_mutable_setting, "codex_auto_reset")
            except ValueError:
                _LOG.exception(
                    "Codex自動reset設定を解決できません: session_id=%s。"
                    "atk config get codex_auto_resetとAGENT_TOOLKIT_CONFIG_CODEX_AUTO_RESETを確認してください",
                    session.session_id,
                )
                continue
            if enabled == "true" and not session.turn_control_lock.locked() and self._auto_reset_eligible(session):
                self._start_auto_reset_check(session)

    def _start_auto_reset_check(self: CodexManager, session: SessionState) -> None:
        task = self._auto_reset_checks.get(session.session_id)
        if task is not None and not task.done():
            return
        client = self._owned_client(session)
        if client is None:
            return
        # 通知readerへ戻る前に保留を標示し、結果公開と起動可用性の確定を防ぐ。
        session.codex_auto_reset_pending = True
        task = asyncio.create_task(self._check_auto_reset(session, client, session.turn_seq))
        self._auto_reset_checks[session.session_id] = task
        task.add_done_callback(self._auto_reset_check_done)

    async def _check_auto_reset(self: CodexManager, session: SessionState, client: JsonRpcProcess, turn_seq: int) -> None:
        result = None

        async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
            enabled = await run_file_operation(_atk_config.resolve_mutable_setting, "codex_auto_reset")
            if enabled != "true":
                raise AppServerError("確認中にcodex_auto_resetが無効になった")
            if (
                session.turn_seq != turn_seq
                or self._owned_client(session) is not client
                or not self._auto_reset_eligible(session)
            ):
                raise asyncio.CancelledError
            return await client.request(method, params)

        try:
            try:
                root = await run_file_operation(state_paths.state_dir)
                result = await codex_auto_reset.check_auto_reset(request, state_dir=root)
                if result.diagnostic:
                    _LOG.info("Codex自動reset: session_id=%s diagnostic=%s", session.session_id, result.diagnostic)
            except AppServerError, OSError, TimeoutError, ValueError:
                _LOG.exception(
                    "Codex自動resetを確認できません: session_id=%s。"
                    "この例外ログの接続・応答・保存状態を確認し、"
                    "停止した会話は利用枠回復後にsend_messageで継続してください",
                    session.session_id,
                )
            async with session.turn_control_lock:
                if (
                    session.turn_seq == turn_seq
                    and self._owned_client(session) is client
                    and self._auto_reset_eligible(session)
                ):
                    self._apply_auto_reset_result(session, result)
        finally:
            session.codex_auto_reset_pending = False
            await self._notify_waiters()

    async def _cancel_auto_reset_check(self: CodexManager, session_id: str) -> None:
        task = self._auto_reset_checks.get(session_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self._auto_reset_checks.get(session_id) is task:
                del self._auto_reset_checks[session_id]
        session = self.sessions.get(session_id)
        if session is not None:
            session.codex_auto_reset_pending = False

    async def _stop_auto_reset(self: CodexManager, session_id: str) -> None:
        monitor = self._auto_reset_monitors.pop(session_id, None)
        if monitor is not None:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        await self._cancel_auto_reset_check(session_id)

    @staticmethod
    async def _read_usage_limit_failure(session: SessionState, error: Any) -> codex_usage_limit.UsageLimitState | None:
        """終了を公開する前に、保留判定に必要な利用枠だけを読み取る。"""
        if not isinstance(error, dict) or error.get("codexErrorInfo") != "usageLimitExceeded":
            return None
        limit = await run_file_operation(codex_usage_limit.from_thread, session.session_id)
        return limit or codex_usage_limit.UsageLimitState(None)

    @staticmethod
    def _auto_reset_check_done(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOG.error(
                "Codex自動resetの監視・確認taskが失敗しました。"
                "この例外ログとatk config get codex_auto_resetを確認し、"
                "停止した会話は利用枠回復後にsend_messageで継続してください",
                exc_info=error,
            )

    @staticmethod
    def _apply_auto_reset_result(session: SessionState, result: codex_auto_reset.AutoResetResult | None) -> None:
        pending = session.pending_result
        if not session.awaiting_auto_resume or pending is None or session.usage_limit_resume_at is None:
            return
        raw = result.rate_limits if result is not None else None
        by_id = raw.get("rateLimitsByLimitId") if raw is not None else None
        bucket = by_id.get("codex") if isinstance(by_id, dict) else raw.get("rateLimits") if raw is not None else None
        windows = [bucket.get(name) for name in ("primary", "secondary")] if isinstance(bucket, dict) else []
        windows = [window for window in windows if isinstance(window, dict)]
        # 自動resetは消費判断に使う週次windowだけを検証するため、他のwindowの使用率は未検証の生値である。
        # 使用率を確認できないwindowが1つでもあれば、空きを確認できたとせず既存の上限待機へ進む。
        used = [codex_usage_limit.used_percent(window) for window in windows]
        if used and all(value is not None and value < 100 for value in used):
            session.hold_result(turn_state.UsageLimitWait(pending, asyncio.get_running_loop().time()))
            session.touch()
            return
        if result is not None and result.reset_confirmed and raw is None:
            session.touch()
            return
        reset_credits = raw.get("rateLimitResetCredits") if raw is not None else None
        limit = codex_usage_limit.from_rate_limits(bucket)
        if (
            result is not None
            and result.outcome == "nothingToReset"
            and isinstance(reset_credits, dict)
            and isinstance(count := reset_credits.get("availableCount"), int)
            and not isinstance(count, bool)
            and count > 0
            and limit.limit_type == "seven_day"
        ):
            return
        error = pending.get("error")
        previous_limit = error.get("usageLimit") if isinstance(error, dict) else None
        kind = limit.limit_type or (previous_limit.get("type") if isinstance(previous_limit, dict) else None)
        if should_wait("codex", kind):
            if limit.resets_at is not None:
                session.hold_result(
                    turn_state.UsageLimitWait(pending, asyncio.get_running_loop().time() + limit.delay_seconds(time.time()))
                )
            session.touch()
            return
        resume_waits.finalize_pending_result(session)
