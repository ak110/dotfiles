"""Codex通知の状態反映と子thread追跡。"""

from __future__ import annotations

import typing
from typing import Any

from agent_toolkit._agents_server import compaction_metrics, context_usage, result_contract, resume_waits, wait_output_tracking
from agent_toolkit._agents_server.codex_protocol import (
    JsonRpcResponseError,
    _public_turn_status,
    _thread_id_from,
    _turn_id_from,
)
from agent_toolkit._agents_server.state import TERMINAL_STATUSES, SessionState, append_bounded
from agent_toolkit._agents_server.wait_output_tracking import consume_agents_server_tool_result
from agent_toolkit._common.shell_segments import extract_execution_segments
from agent_toolkit._common.shell_tokens import is_agents_wait_command

if typing.TYPE_CHECKING:
    from agent_toolkit._agents_server.codex_manager_contract import CodexManager

# `item/started`のうちモデル出力に数えないitem種別。
# `userMessage`はモデル呼び出しより前に届く入力の記録で、利用上限の失敗はその後に起こり得る。
# codex-cli 0.157.0の通常のturnでは`userMessage`の後に`reasoning`が届いた（2026年9月26日の実行で確認）。
_NON_MODEL_OUTPUT_ITEM_TYPES = frozenset({"userMessage", "hookPrompt", "contextCompaction"})


# Codex CLI 0.148.0のServerRequest schemaで確認した全server-initiated request:
# item/commandExecution/requestApproval・item/fileChange/requestApproval・
# item/tool/requestUserInput・mcpServer/elicitation/request・
# item/permissions/requestApproval・item/tool/call・
# account/chatgptAuthTokens/refresh・attestation/generate・
# applyPatchApproval・execCommandApproval。
# 承認用の公開MCP toolは設けず、readerで必ず応答して非対話要求をfailedへ記録する。


class CodexNotificationsMixin:
    """Codex通知の状態反映と子thread追跡。

    AppServerManagerへ合成して使うため、各メソッドでは合成後の共有状態と他の責務を参照する。
    """

    async def _handle_notification(self: CodexManager, message: dict[str, Any]) -> None:
        """App Server通知をsession状態へ反映する。

        Codex CLI 0.153.4では``contextCompaction``の``item/started``と
        ``item/completed``がそれぞれミリ秒単位の必須時刻を持つ。
        監査記録は``docs/development/audit-records.md``の
        「agent-toolkit/agent_toolkit/_agents_server/codex.py：コンパクション計測通知：2026年9月10日」に置く。
        """
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return
        thread_id = _thread_id_from(params)
        compacting = self._wait_compaction_turns.get(thread_id or "")
        notification_turn_id = self._notification_turn_id(params)
        if compacting is not None and notification_turn_id is not None and notification_turn_id != compacting.original_turn_id:
            # 圧縮は独立したturnを発生する。担当のturn ID・結果・待機対象を変更しない。
            session = self.sessions.get(thread_id or "")
            if session is not None:
                await compaction_metrics.observe_compaction_item(session, method, params)
            if method == "turn/completed":
                turn = params.get("turn")
                if not isinstance(turn, dict) or turn.get("status") != "completed":
                    compacting.error = "待機中の会話圧縮が正常に完了しなかった"
                self._wait_compaction_turns.pop(thread_id or "", None)
                compacting.done.set()
                await self._notify_waiters()
            elif method == "thread/tokenUsage/updated":
                session = self.sessions.get(thread_id or "")
                usage = params.get("tokenUsage")
                last = usage.get("last") if isinstance(usage, dict) else None
                if session is not None and isinstance(usage, dict) and isinstance(last, dict):
                    context_usage.record_context_usage(session, last.get("totalTokens"), usage.get("modelContextWindow"))
            return
        if method == "thread/tokenUsage/updated":
            thread_id = params.get("threadId")
            session = self.sessions.get(thread_id) if isinstance(thread_id, str) else None
            if session is None or session.engine != "codex" or not session.turn_id or params.get("turnId") != session.turn_id:
                return
            usage = params.get("tokenUsage")
            if not isinstance(usage, dict):
                return
            last = usage.get("last")
            if isinstance(last, dict):
                context_usage.record_context_usage(session, last.get("totalTokens"), usage.get("modelContextWindow"))
            return
        if method == "thread/closed":
            close_event = self._resume_close_events.get(_thread_id_from(params) or "")
            if close_event is not None:
                close_event.set()
                await self._notify_waiters()
            return
        if method == "thread/started":
            thread = params.get("thread")
            if isinstance(thread, dict):
                owner = self._find_thread_owner(thread.get("parentThreadId"))
                if owner is not None and isinstance(thread.get("id"), str):
                    owner.codex_subagent_thread_ids.add(thread["id"])
        session = self._find_session(params)
        if session is None:
            # 子孫threadの通知は子孫の追跡だけに使い、親のstatusや進捗へ混ぜない。
            owner = self._find_thread_owner(_thread_id_from(params))
            if owner is not None:
                child_thread_id = _thread_id_from(params)
                if method == "turn/completed" and isinstance(child_thread_id, str):
                    owner.codex_live_subagent_thread_ids.discard(child_thread_id)
                    owner.codex_terminal_subagent_thread_ids.add(child_thread_id)
                self._track_subagent_thread(
                    owner,
                    params.get("item"),
                    source_thread_id=child_thread_id,
                    source_turn_id=self._notification_turn_id(params),
                )
                turn = params.get("turn")
                if isinstance(turn, dict) and isinstance(turn.get("items"), list):
                    for item in turn["items"]:
                        self._track_subagent_thread(
                            owner,
                            item,
                            source_thread_id=child_thread_id,
                            source_turn_id=self._notification_turn_id(params),
                        )
            return
        notification_turn_id = self._notification_turn_id(params)
        if notification_turn_id is not None and session.turn_id and notification_turn_id != session.turn_id:
            return
        if method == "error":
            if params.get("willRetry") is True:
                error = params.get("error")
                info = error.get("codexErrorInfo") if isinstance(error, dict) else None
                error_type = next(iter(info), "api_error") if isinstance(info, dict) else str(info or "api_error")
                detail = info.get(error_type) if isinstance(info, dict) else None
                status = detail.get("httpStatusCode") if isinstance(detail, dict) else None
                session.record_api_error(
                    error_type, status if isinstance(status, int) and not isinstance(status, bool) else None
                )
                await self._notify_waiters()
            return
        item = params.get("item")
        if (
            method
            in {
                "turn/completed",
                "item/agentMessage/delta",
                "item/reasoning/textDelta",
                "item/reasoning/summaryTextDelta",
            }
            or (
                method == "item/started"
                and isinstance(item, dict)
                and item.get("type") not in _NON_MODEL_OUTPUT_ITEM_TYPES
                and item.get("type") != "subAgentActivity"
            )
            or (method == "item/completed" and isinstance(item, dict) and item.get("type") in {"agentMessage", "reasoning"})
        ):
            session.api_error = None
        turn = params.get("turn")
        if method == "turn/started":
            session.record_turn_sent()
            if isinstance(turn, dict):
                turn_id = turn.get("id")
                if isinstance(turn_id, str):
                    session.record_turn_started(turn_id)
            if not session.failure_pending_completion:
                session.status = "running"
        elif method == "turn/completed":
            # 結果を公開可能にする前に保存出力の読取を完了し、終了状態と確定時刻を同じloop区間で反映する。
            if isinstance(turn, dict):
                await self._consume_items(session, turn.get("items"))
            await wait_output_tracking.consume_agents_wait_background_outputs_async(session)
            failure_pending_completion = session.failure_pending_completion
            status = session.status
            error = session.error
            if isinstance(turn, dict):
                turn_id = turn.get("id")
                if isinstance(turn_id, str):
                    session.turn_id = turn_id
                if not failure_pending_completion:
                    status = _public_turn_status(turn.get("status"))
                turn_error = turn.get("error")
                if turn_error is not None or not failure_pending_completion:
                    error = turn_error
            else:
                status = "failed"
                error = "turn/completed did not contain turn"
            if status not in TERMINAL_STATUSES:
                status = "failed"
            # 状態を終了へ進めた後にawaitすると、waitや状態保存が保留判定前の結果を回収できてしまう。
            limit = await self._read_usage_limit_failure(session, error) if status == "failed" else None
            session.status = status
            session.error = error
            session.record_turn_completed()
            if session.status == "completed":
                session.codex_attempted_provider_ids = {session.codex_model_provider} if session.codex_model_provider else set()
            result = {"status": session.status, "agent_message": session.agent_message, "error": session.error}
            held_by_result_contract = result_contract.hold_invalid_result(session, result)
            if not held_by_result_contract:
                session.status = result["status"]
                session.error = result["error"]
            if held_by_result_contract:
                pass
            elif resume_waits.has_pending_auto_resume_targets(session) or (
                session.codex_self_message_observed and not session.codex_self_message_repair_consumed
            ):
                # 未観測の孫sessionが残るturnは、待機表明を完了報告として公開せずに結果を保留する。
                # 監査記録は`docs/development/audit-records.md`の
                # 「agent-toolkit/agent_toolkit/_agents_server/codex.py：孫sessionの待機表明と自動再開：2026年10月1日」にある。
                # MCP層の常駐監視（`_monitor_auto_resume`）が孫の終了を観測し、同じsessionを一度だけ再開する。
                # 期限到来・追跡先の喪失・再開失敗の確定と未観測識別子の記録も、同じ監視が既存の診断で行う。
                resume_waits.begin_auto_resume_wait(
                    session, {"status": session.status, "agent_message": session.agent_message, "error": session.error}
                )
                session.status = "running"
            elif self._hold_usage_limit_failure(session, limit) or self._hold_provider_failure(session):
                pass
            elif not resume_waits.is_overload_failure(session):
                resume_waits.clear_overload_resume(session)
            elif (session.availability_checked or session.turn_seq > 1) and resume_waits.begin_overload_resume_wait(
                session, {"status": session.status, "agent_message": session.agent_message, "error": session.error}
            ):
                # 起動の可用性確認を過ぎた後の過負荷は時間の経過で解け得るため、結果を保留して待機後に同じsessionで続ける。
                # 継続の送信はMCP層の常駐監視（`_monitor_auto_resume`）が行う。確認を終える前の過負荷は起動時の候補切替が扱う。
                session.status = "running"
            session.compaction_started_at_ms.clear()
        elif method == "turn/plan/updated":
            plan = params.get("plan")
            if isinstance(plan, list):
                session.plan = [item for item in plan if isinstance(item, dict)]
        elif method == "turn/diff/updated":
            diff = params.get("diff")
            if isinstance(diff, str) and diff:
                session.diff_changed = True
        elif method == "item/started":
            item = params.get("item")
            self._track_subagent_thread(session, item)
            session.record_current_item_start(item if isinstance(item, dict) else None)
            if isinstance(item, dict):
                if item.get("type") not in _NON_MODEL_OUTPUT_ITEM_TYPES:
                    session.model_output_observed = True
                if item.get("type") == "fileChange":
                    session.diff_changed = True
                await compaction_metrics.observe_compaction_item(session, method, params)
        elif method == "item/completed":
            item = params.get("item")
            if isinstance(item, dict):
                session.record_current_item_start(None)
                (await self._consume_item(session, item))
                await compaction_metrics.observe_compaction_item(session, method, params)
        elif method == "item/agentMessage/delta":
            delta = params.get("delta")
            if isinstance(delta, str):
                item_id = params.get("itemId")
                if not isinstance(item_id, str) or not item_id:
                    current = session.current_item
                    item_id = current.get("id") if isinstance(current, dict) else None
                item_id = item_id if isinstance(item_id, str) and item_id else "__current__"
                session.progress_items[item_id] = append_bounded(session.progress_items.get(item_id, ""), delta)
                session.commentary = session.progress_items[item_id]
                session.set_progress(session.commentary)
        elif method in {"item/fileChange/outputDelta", "item/fileChange/patchUpdated"}:
            session.diff_changed = True
        session.touch()
        await self._notify_waiters()

    async def _handle_server_request(self: CodexManager, message: dict[str, Any], client: Any) -> None:
        """要求を受信した接続（`client`）へ応答し、その接続が所有するsessionだけへ失敗を反映する。"""
        method = message.get("method")
        params = message.get("params")
        request_id = message.get("id")
        if not isinstance(method, str):
            return
        if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
            # IDなしの異常なserver requestへはJSON-RPC応答を返せないため、
            # 接続上のactive turnをfailedへ遷移させ、turn完了通知を待つ。
            await self._fail_for_request(params, method, client)
            return
        if method == "mcpServer/elicitation/request":
            response: dict[str, Any] = {"action": "cancel", "content": None, "_meta": None}
            await client.send({"id": request_id, "result": response})
            session = self._find_session(params)
            if session is not None:
                session.protocol_warnings.append("mcpServer/elicitation/request was cancelled")
                session.touch()
                await self._notify_waiters()
            return
        await self._fail_for_request(params, method, client)
        await client.send(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Unsupported non-interactive server request: {method}",
                },
            }
        )

    async def _fail_for_request(self: CodexManager, params: Any, method: str, client: Any) -> None:
        session = self._find_session(params)
        if session is not None:
            sessions = [session] if not session.terminal else []
        else:
            sessions = [item for item in self._client_sessions(client) if not item.terminal]
        interrupt_targets: list[tuple[str, str]] = []
        for active in sessions:
            has_active_turn = bool(active.turn_id)
            active.status = "failed"
            active.error = {"message": f"Codex requested interactive server input: {method}"}
            active.protocol_warnings.append(f"unsupported server request: {method}")
            if has_active_turn:
                active.record_turn_started()
                active.record_failure_pending_completion()
            else:
                active.record_turn_completed()
            if has_active_turn and not active.interrupt_requested:
                interrupt_targets.append((active.session_id, active.turn_id))
            active.interrupt_requested = has_active_turn
            active.touch()
        await self._notify_waiters()
        # reader task cannot await a request response for turn/interrupt: that would
        # deadlock the same reader. Schedule it after publishing failed and waking waiters.
        for session_id, turn_id in interrupt_targets:
            self._schedule(self._interrupt(session_id, turn_id, client))

    async def _interrupt(self: CodexManager, session_id: str, turn_id: str, client: Any) -> None:
        if getattr(client, "closed", False):
            return
        try:
            await client.request("turn/interrupt", {"threadId": session_id, "turnId": turn_id})
        except JsonRpcResponseError as exc:
            await self._handle_interrupt_response_error(session_id, turn_id, exc)
        except Exception as exc:
            await self._handle_client_failure(exc, client)

    async def _handle_interrupt_response_error(
        self: CodexManager, session_id: str, turn_id: str, error: JsonRpcResponseError
    ) -> None:
        """turn/interruptのJSON-RPC errorを対象turnだけへ記録する。"""
        session = self._find_session({"threadId": session_id})
        if session is None or session.turn_completed or session.turn_id != turn_id:
            return
        session.error = {"message": str(error) or error.__class__.__name__}
        session.protocol_warnings.append(f"turn/interrupt failed: {error}")
        session.touch()
        await self._notify_waiters()

    async def _handle_client_failure(self: CodexManager, error: BaseException, client: Any) -> None:
        """reader異常時に、その接続が所有するsessionのactive turnをfailedへ遷移させて待機者を起こす。"""
        detail = str(error) or error.__class__.__name__
        changed = False
        for session in self._client_sessions(client):
            compacting = self._wait_compaction_turns.pop(session.session_id, None)
            if compacting is not None:
                compacting.error = detail
                compacting.done.set()
            if not session.terminal or session.failure_pending_completion:
                if session.awaiting_auto_resume and session.pending_result is not None:
                    resume_waits.finalize_pending_result(session, touch=False)
                session.status = "failed"
                if not session.failure_pending_completion:
                    session.error = {
                        **(session.error if isinstance(session.error, dict) else {}),
                        "message": f"Codex App Server stopped: {detail}",
                    }
                session.api_error = None
                session.interrupt_requested = False
                session.record_turn_completed()
                session.touch()
                changed = True
        if changed or self._resume_close_events:
            await self._notify_waiters()

    def _codex_sessions(self: CodexManager) -> list[SessionState]:
        """managerと共有する一覧から、このbackendが状態を更新できるsessionだけを返す。"""
        return [session for session in self.sessions.values() if session.engine == "codex"]

    def _client_sessions(self: CodexManager, client: Any) -> list[SessionState]:
        """共有一覧のCodex sessionのうち、`client`の接続を所有するsessionだけを返す。"""
        return [session for session in self._codex_sessions() if self._clients.get(session.session_id) is client]

    def _find_thread_owner(self: CodexManager, thread_id: Any) -> SessionState | None:
        """直接開始したthreadか、その子孫を所有するCodex sessionを返す。"""
        if not isinstance(thread_id, str):
            return None
        return next(
            (
                session
                for session in self._codex_sessions()
                if thread_id == session.session_id or thread_id in session.codex_subagent_thread_ids
            ),
            None,
        )

    def _find_session(self: CodexManager, params: Any) -> SessionState | None:
        thread_id = _thread_id_from(params)
        sessions = self._codex_sessions()
        if thread_id is not None:
            session = next((item for item in sessions if item.session_id == thread_id), None)
            if session is not None:
                return session
        turn_id = _turn_id_from(params)
        if turn_id is not None:
            return next((item for item in sessions if item.turn_id == turn_id), None)
        return None

    @staticmethod
    def _track_subagent_thread(
        session: SessionState,
        item: Any,
        *,
        source_thread_id: str | None = None,
        source_turn_id: str | None = None,
    ) -> None:
        """開始・再操作で観測したsubagentのthreadを記録し、そのthreadの通知を所有sessionへ対応付ける。"""
        if not isinstance(item, dict) or item.get("type") != "subAgentActivity":
            return
        thread_id = item.get("agentThreadId")
        if not isinstance(thread_id, str) or not thread_id:
            return
        activity_id = item.get("id")
        kind = item.get("kind")
        if isinstance(activity_id, str):
            activity_key = (
                source_thread_id or session.session_id,
                source_turn_id or session.turn_id,
                activity_id,
                str(kind),
            )
            if activity_key in session.codex_subagent_activity_ids:
                return
            session.codex_subagent_activity_ids.add(activity_key)
        if thread_id == session.session_id:
            if kind == "interacted" and source_thread_id in {None, session.session_id}:
                session.codex_self_message_observed = True
            return
        session.codex_subagent_thread_ids.add(thread_id)
        if kind == "started":
            session.codex_live_subagent_thread_ids.add(thread_id)
            session.codex_terminal_subagent_thread_ids.discard(thread_id)
        elif kind in {"completed", "interrupted"}:
            session.codex_live_subagent_thread_ids.discard(thread_id)
            session.codex_terminal_subagent_thread_ids.add(thread_id)

    @staticmethod
    def _notification_turn_id(params: dict[str, Any]) -> str | None:
        """通知本文に含まれるturn IDを取得する。"""
        turn_id = _turn_id_from(params)
        if turn_id is not None:
            return turn_id
        turn = params.get("turn")
        if isinstance(turn, dict):
            turn_id = turn.get("id")
            if isinstance(turn_id, str) and turn_id:
                return turn_id
        return None

    @staticmethod
    async def _consume_items(session: SessionState, items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, dict):
                (await CodexNotificationsMixin._consume_item(session, item))

    @staticmethod
    async def _consume_item(session: SessionState, item: dict[str, Any]) -> None:
        CodexNotificationsMixin._track_subagent_thread(session, item)
        item_type = item.get("type")
        if item_type == "agentMessage":
            text = item.get("text")
            if isinstance(text, str):
                session.agent_message = text
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id:
                    item_id = "__current__"
                session.progress_items[item_id] = text
                session.set_progress(text)
        elif item_type == "plan":
            text = item.get("text")
            if isinstance(text, str):
                session.plan = [{"text": text, "status": "completed"}]
        elif item_type == "fileChange":
            session.diff_changed = True
        elif item_type == "commandExecution":
            command = item.get("command")
            output = item.get("aggregatedOutput")
            # `timeout`などの実行前置語と`bash -c`の展開は、Claudeの待機回収と同じ区間解析で扱う。
            if (
                item.get("exitCode") == 0
                and isinstance(command, str)
                and isinstance(output, str)
                and any(
                    segment.resolved and is_agents_wait_command(segment.tokens)
                    for segment in extract_execution_segments(command)
                )
            ):
                await wait_output_tracking.consume_agents_wait_output_async(session, output)
        elif item_type == "mcpToolCall" and item.get("server") == "agents_server":
            arguments = item.get("arguments")
            result = item.get("result")
            structured = result.get("structuredContent") if isinstance(result, dict) else None
            if isinstance(arguments, dict) and isinstance(structured, dict):
                consume_agents_server_tool_result(session, str(item.get("tool", "")), arguments, structured)
