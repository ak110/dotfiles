"""Claudeのturn結果と継続コマンドの処理。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
import logging
import pathlib
import re
from collections.abc import Awaitable, Callable
from typing import Any

from agent_toolkit._agents_server import (
    claude_mcp,
    configured_model,
    result_contract,
    resume_waits,
    turn_state,
    wait_compaction,
    wait_output_tracking,
)
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._agents_server.claude_channels import _Command, _CommandChannel
from agent_toolkit._agents_server.claude_diagnostics import _InitializationDiagnostic
from agent_toolkit._agents_server.claude_plugins import ClaudePluginReloader
from agent_toolkit._agents_server.claude_runtime import ClaudeRuntime
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.launch_prompts import plugin_resource_instructions
from agent_toolkit._agents_server.state import SessionState, begin_reply

_LOG = logging.getLogger("agent-toolkit.agents-server.claude")


class ClaudeTurns(ClaudeRuntime):
    """受信結果の公開と所有キューのコマンドを処理する。"""

    @staticmethod
    async def _watch_plugin_changes(reloader: ClaudePluginReloader, channel: _CommandChannel) -> None:
        while True:
            await reloader.wait_for_change()
            reloader.request_reload()
            await channel.send("plugin_reload", "")

    @staticmethod
    async def _query_with_resources(client: Any, options: Any, reloader: ClaudePluginReloader, cwd: str, body: str) -> None:
        # serverの保持版とreloadする子の入力版は寿命が異なるため、最新の参照先を次の入力へ添える。
        root = pathlib.Path(options.plugins[0]["path"])
        if root != plugin_roots.SERVER_PLUGIN_ROOT or reloader.updated:
            resources = await run_file_operation(plugin_resource_instructions, root, "claude", cwd)
            body, replaced = re.subn(
                r'\A(?P<delivery><atk-auto source="agents-server" kind="delivery">\n)?'
                r'<atk-auto source="agent-toolkit" kind="plugin-resources">\n.*?\n</atk-auto>',
                lambda match: (match["delivery"] or "") + resources,
                body,
                count=1,
                flags=re.DOTALL,
            )
            if not replaced:
                body += "\n\n" + resources
        await client.query(body)

    @staticmethod
    async def _recover_mcp_connections(client: Any, initialization: dict[str, Any], session: SessionState) -> None:
        def report_pending(warnings: tuple[str, ...]) -> None:
            session.claude_mcp_diagnostics = warnings

        session.claude_mcp_diagnostics = await claude_mcp.recover_connections(
            client, initialization, session.session_id, report_pending=report_pending
        )

    async def _consume_result_message(self, session: SessionState, message: Any) -> None:
        """未回収対象の寿命に従ってturnの結果を保留または確定する。"""
        await wait_output_tracking.consume_agents_wait_background_outputs_async(session)
        result = self._result_values(session, message)
        if session.interrupt_requested:
            # killの所有者がCLIの終了を確認してから公開する。後着の失敗結果を回復の契機へ戻さない。
            session.hold_result(turn_state.InterruptedWait({**result, "status": "interrupted"}))
            return
        if (
            result["status"] == "failed"
            and session.usage_limit is not None
            and session.usage_limit.status == "rejected"
            and isinstance(result["error"], dict)
            and result["error"].get("apiErrorStatus") == 429
            and (changed := await configured_model.changed_model_error(session)) is not None
        ):
            self._finalize_turn(session, {**result, "error": changed})
            return
        if session.claude_api_resume_pending:
            if result["status"] == "completed":
                session.api_error = None
            return
        if session.usage_limit_resume_at is not None:
            if result["status"] != "completed":
                return
            resume_waits.clear_usage_limit_wait(session)
            session.api_error = None
            session.usage_limit = None
        # 対象の利用上限では結果を保留し、MCP層の常駐監視がAPI移行または解除後の継続を進める。
        if session.effort_resume_turn != session.turn_seq and result_contract.hold_invalid_result(session, result):
            return
        if resume_waits.begin_usage_limit_wait(session, result):
            return
        # 自動再開したturnもバックグラウンドタスクを残して待機を表明し得るため、`origin`によらず保留を判定する。
        # 完了通知は`ResultMessage`より前にも届く。Stop hookの実行中にバックグラウンドタスクが終わると、
        # その通知で追跡集合は空になるが、CLIは`ResultMessage`の後に通知の再開turnを開始する。
        # 次のturnの有無はCLIの`idle`の報告だけが確定できるため、報告が`idle`になるまで保留し、
        # `session_state_changed`の分岐で確定する。この判定はMCP層の再開済みsessionにも適用する。
        # 根拠とした版と順序は`docs/development/audit-records.md`の次の節にある。
        # 「agent-toolkit/agent_toolkit/_agents_server/claude.py：結果の保留とturn状態の報告：2026年10月4日」
        if resume_waits.has_pending_auto_resume_targets(session):
            self._log_turn_state_fallback(session)
            resume_waits.begin_auto_resume_wait(session, result)
            session.touch()
        else:
            self._log_turn_state_fallback(session)
            self._finalize_turn(session, result)

    async def _handle_command(
        self,
        client: Any,
        session: SessionState | None,
        command: _Command,
        iterator: Any,
        *,
        query: Callable[[str], Awaitable[None]] | None = None,
        reloader: ClaudePluginReloader | None = None,
    ) -> Any:
        command_kind, prompt, future = command
        if command_kind == "plugin_reload":
            if not future.done():
                future.set_result(("plugins_pending", None))
            return iterator
        if session is None:
            if not future.done():
                future.set_exception(ValueError("Claude session initialization is incomplete"))
            return iterator
        if command_kind == "interrupt":
            if session.terminal:
                if not future.done():
                    future.set_result(("interrupt_accepted", None))
                return iterator
            try:
                await client.interrupt()
            except Exception as exc:
                session.interrupt_requested = False
                session.touch()
                if not future.done():
                    future.set_exception(exc)
            else:
                if session.awaiting_auto_resume:
                    # モデルのturnは終わっており、Claude Code CLIはturnの外で受けた中断要求へ`ResultMessage`を返さない。
                    # 以後の再開turnは読まず、killの所有者が資源を回収してから停止結果を確定する。
                    iterator = None
                if not future.done():
                    future.set_result(("interrupt_accepted", None))
            await self._notify_waiters()
            return iterator
        if future.cancelled():
            return iterator
        if command_kind == "effort":
            try:
                # Python SDK 0.2.179に公開setterが無いため、CLIの既存制御プロトコルを所有キューから送る。
                # 起動時の--effortを重ねるだけでは、CLIが自発的に始める次turnへ反映できない。
                await client._query._send_control_request(  # pylint: disable=protected-access
                    {"subtype": "apply_flag_settings", "settings": {"effortLevel": prompt}}
                )
            except Exception as error:  # pylint: disable=broad-exception-caught
                if not future.done():
                    future.set_exception(error)
            else:
                session.claude_applied_effort = prompt
                session.touch()
                if not future.done():
                    future.set_result(("effort_applied", None))
            return iterator
        if reloader is not None:
            await reloader.reload_if_idle(client, session)
        if command_kind == "compact":
            # SDKではターン間の$.session.compact()を使えない。公開/compact入力を所有キューで直列化する。
            # 予約後の完了通知や追送を先に消費し、待機が変わった場合は新しいturnを起こさない。
            if wait_compaction.wait_key(session) != prompt:
                future.set_result(("compact_skipped", None))
                return iterator
            prompt = "/compact"
        if session.awaiting_auto_resume and not resume_waits.is_observation_preserving_reply(session):
            (await self._finalize_pending_result(session))
        held = resume_waits.HeldTurn.capture(session)
        kind = "reply" if session.terminal or held is not None else "steer"
        previous_result = session.previous_result() if kind == "reply" and held is None else None
        try:
            if kind == "reply":
                begin_reply(session, preserve_waits=held is not None)
            await (query or client.query)(prompt)
            session.record_turn_sent()
        except Exception as exc:
            if held is not None:
                held.restore(session)
                if iterator is None:
                    iterator = aiter(client.receive_messages())
                if not future.done():
                    future.set_result(("reply_failed", None))
            elif kind == "reply":
                self._record_failure(session, exc)
                if not future.done():
                    future.set_result(("reply_failed", previous_result))
            elif not future.done():
                future.set_exception(exc)
            await self._notify_waiters()
            return iterator
        if kind == "reply":
            if not future.done():
                future.set_result(("reply_started", previous_result))
        elif not future.done():
            future.set_result(("steered", None))
        session.retention_deadline = None
        session.touch()
        await self._notify_waiters()
        # 保留中は同じ受信を続ける。追送中に到着した完了通知を読取taskの取消で失わない。
        return aiter(client.receive_messages()) if kind == "reply" and iterator is None else iterator

    @staticmethod
    def _result_values(session: SessionState, message: Any) -> dict[str, Any]:
        terminal_reason = getattr(message, "terminal_reason", None)
        if terminal_reason in {"aborted_streaming", "aborted_tools"}:
            status = "interrupted"
        else:
            status = "failed" if bool(getattr(message, "is_error", False)) else "completed"
        result = getattr(message, "result", None)
        agent_message = result if isinstance(result, str) and result.strip() else session.agent_message
        error: dict[str, Any] | None = None
        errors = getattr(message, "errors", None)
        if status == "completed" and not agent_message.strip():
            status = "failed"
            error = {"message": "Claude Agent SDK returned no assistant output"}
        if status == "failed":
            if error is not None:
                pass
            elif isinstance(errors, list) and errors:
                error = {"message": "; ".join(str(item) for item in errors)}
            elif isinstance(result, str) and result:
                error = {"message": result}
            else:
                error = {"message": "Claude Agent SDK returned an error"}
            # `api_error_status`は失敗したAPI呼び出しのHTTPステータスを示す。
            # MCP層がengineの可用性を判定するために保持する。
            api_error_status = getattr(message, "api_error_status", None)
            if isinstance(api_error_status, int):
                error["apiErrorStatus"] = api_error_status
        return {"status": status, "agent_message": agent_message, "error": error}

    @staticmethod
    def _finalize_turn(session: SessionState, result: dict[str, Any]) -> None:
        session.clear_hold()
        session.status = result["status"]
        session.agent_message = result["agent_message"]
        session.error = result["error"]
        session.record_turn_completed()
        session.touch()

    @staticmethod
    def _log_turn_state_fallback(session: SessionState) -> None:
        """CLIのturn状態の報告を受けないまま結果を判定したことを、sessionごとに1回だけ警告する。

        報告が無い場合は追跡集合だけで判定する従来の方法へ戻り、`ResultMessage`より前に終わったバックグラウンドタスクの
        再開turnを判別できない。CLIが報告をやめて判定の精度が下がったことを検出できるようにログへ残す。
        """
        if session.cli_turn_state is not None or session.cli_turn_state_fallback_logged:
            return
        session.cli_turn_state_fallback_logged = True
        _LOG.warning(
            "claude_turn_state_unreported session_id=%s: CLIのturn状態の報告が無いため、結果の保留を追跡集合だけで判定する",
            session.session_id,
        )

    @classmethod
    async def _finalize_pending_result(cls, session: SessionState) -> None:
        """保留した終了時の結果を確定する。

        Claude backendはタスク完了通知と孫sessionの終了によって同一sessionを再開できるため終了時の結果を保留する。
        Codex backendは孫sessionの終了による再開だけを持ち、同じ保留をMCP層の監視が確定する。
        確定の時点で残るバックグラウンドタスクと孫sessionの`error`への記録は、確定の契機によらず共通処理が行う。
        """
        await wait_output_tracking.consume_agents_wait_background_outputs_async(session)
        resume_waits.finalize_pending_result(session)

    @classmethod
    def _record_failure(
        cls,
        session: SessionState,
        error: BaseException,
        *,
        diagnostic: _InitializationDiagnostic | None = None,
    ) -> None:
        """失敗を終了時の結果へ確定し、初期化の完了後に生じた失敗へは原因の特定に要する診断を添える。"""
        if isinstance(error, configured_model.ConfiguredModelChanged):
            cls._finalize_turn(session, {"status": "failed", "agent_message": session.agent_message, "error": error.error})
            return
        failure: dict[str, Any] = {"message": str(error) or error.__class__.__name__}
        if diagnostic is not None:
            stderr = diagnostic.stderr.strip()
            failure.update(
                engine=session.engine or "claude",
                model=session.model,
                stderr=stderr,
                childPid=diagnostic.child_pid,
                debugFile=diagnostic.debug_file,
            )
            _LOG.error(
                "claude_session_failure session_id=%s engine=%s model=%s child_pid=%s debug_file=%s stderr=%s",
                session.session_id,
                session.engine or "claude",
                session.model,
                diagnostic.child_pid,
                diagnostic.debug_file,
                stderr,
            )
        cls._finalize_turn(
            session,
            {"status": "failed", "agent_message": session.agent_message, "error": failure},
        )

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()
