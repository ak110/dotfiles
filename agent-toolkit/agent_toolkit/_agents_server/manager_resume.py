from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from agent_toolkit._agents_server import (
    admission,
    backends,
    claude,
    codex_providers,
    launch_prompts,
    launch_requests,
    manager_registry,
    result_contract,
    retained_results,
    service_io,
    service_turns,
    shared_layout,
    wait_run,
)
from agent_toolkit._agents_server.cli_turn import CliTurnManager
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.launch_requests import (
    wrap_delivery_body,
)
from agent_toolkit._agents_server.manager_base import PendingResume
from agent_toolkit._agents_server.plugin_root import SERVER_PLUGIN_ROOT
from agent_toolkit._agents_server.responses import (
    turn_elapsed_seconds,
)
from agent_toolkit._agents_server.resume_waits import (
    auto_resume_wait_deadline,
    clear_overload_resume,
    clear_usage_limit_wait,
    cli_turn_may_continue,
    finalize_pending_result,
    has_pending_auto_resume_targets,
    is_background_completion_wait,
    is_final_response_with_unobserved_children,
)
from agent_toolkit._agents_server.state import PreviousResult, ResumePrompt, SessionResumeState, SessionState
from agent_toolkit._agents_server.wait_output_tracking import consume_agents_wait_background_outputs_async
from agent_toolkit._common import session_registry, startup_features, task_documents
from agent_toolkit._common.delegated_session import FINAL_RESPONSE_NOTICE
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


def admission_wait_fields(manager: object) -> dict[str, dict[str, object]]:
    return vars(manager).setdefault("_admission_wait_fields", {})


def note_admission_wait(session: SessionState, ticket: admission.Admission) -> None:
    fields = ticket.response_fields()
    if fields:
        session.last_action = (
            f"マシン全体の同時稼働数の上限で{fields['admission_wait_seconds']}秒待って再開した（{fields['admission_limit']}）"
        )


class ResumeDeferred(ActionableError):
    """別の操作との競合でbackendの再開には着手していない。"""


class ManagerResume(manager_registry.ManagerRegistry):
    """孫sessionの終了と待機の解除を観測して自動で継続し、保存済みsessionを再開する。"""

    async def _effort_catalog(self, engine: str) -> list[dict[str, Any]]:
        """所有backendが公開するモデル一覧で、Codexの実受理段を確認する。

        本人・親の明示したeffort要求の監視が段の取得を使う。
        """
        return await cast(backends.ModelCatalog, self._backend(engine)).list_models()

    async def _apply_effort(self, session: SessionState, value: str) -> None:
        """Claudeの所有キューへ設定を渡し、他engineは次turn引数の更新だけを使う。"""
        if session.engine == "claude":
            await cast(Any, self._backend("claude")).set_effort(session, value)

    async def _send_with_resources(
        self, session: SessionState, prompt: str, *, result_recovery: bool = False
    ) -> dict[str, Any]:
        backend = self._backend(session.engine)
        if result_recovery and isinstance(backend, CliTurnManager):
            cli_recovery = True
            send = backend.recover_result_contract
        else:
            cli_recovery = False
            send = backend.send_message
        resources = await run_file_operation(
            launch_prompts.plugin_resource_instructions, SERVER_PLUGIN_ROOT, session.engine, session.cwd
        )

        ticket = (
            await admission.acquire(await run_file_operation(self.launcher_session_id), state_root=self._state_root())
            if cli_recovery or session.status != "running"
            else None
        )
        try:
            async with service_io.next_input(session):
                result = await send(session, resources + "\n\n" + prompt)
        finally:
            if ticket is not None:
                ticket.release()

        if ticket is not None:
            note_admission_wait(session, ticket)
        return result

    async def _child_result_is_terminal(self, session_id: str) -> bool:
        if self._status_writer is None or not shared_layout.valid_session_id(session_id):
            return False
        return (await run_file_operation(self._status_writer.read_result, session_id)) is not None

    async def _advance_child_session_wait(self, session: SessionState) -> None:
        """保留中の結果を、孫sessionの終了・過負荷と利用上限の待機の経過または保持期限に応じて進める。"""
        if not session.awaiting_auto_resume or session.pending_result is None:
            return
        if session.interrupt_requested:
            return
        expiry = session.usage_limit_resume_at
        if (
            session.engine == "codex"
            and session.usage_limit_wait_count == 0
            and expiry is not None
            and asyncio.get_running_loop().time() >= expiry
        ):
            finalize_pending_result(session)
            return
        if session.codex_auto_reset_pending:
            return
        if session.result_contract_recovery_pending:
            if session.result_contract_recovery_consumed:
                return
            pending = session.pending_result
            assert pending is not None
            turn_seq = session.turn_seq

            def owns_recovery() -> bool:
                return session.turn_seq == turn_seq and session.pending_result is pending

            reason = session.result_contract_recovery_reason or "返却形式を検査できません"
            session.result_contract_recovery_consumed = True
            try:
                delivery = await self._send_with_resources(
                    session,
                    wrap_delivery_body(
                        (
                            "直前の不足申告の前提を一度再確認する。形式違反と判定したものではない。"
                            if result_contract.invalid_result_reason(session, pending) is None
                            else "直前の返却は返却形式の契約に適合しない。"
                        )
                        + f"診断: {reason}\n"
                        + "成果と残作業を確認し、担当内で補える作業を完遂した上で所定の返却形式を返せ。"
                        + "取得可能な未読・保存出力・残工程を補い、補えない不足だけを試した手段と再開条件付きで返せ。"
                        "権限拒否・認可不足の回避は求めない。実行済みの非冪等操作と保存成果を保持し、残工程だけを進めよ。"
                        + (
                            "未読・未走査の範囲を担当内で完了させ、正常返却は先頭を状態: completedとする"
                            "（既存の末尾状態行も受理する）。前提不足が残る単位は、同じインデントで"
                            "続行できない区分: <有効な区分>と続行できない理由: <不足と再開条件>を連続させる。"
                            "読了範囲の成果と追加報告を保持し、続行不能を含む返却には全体成功行を付けない。"
                            if session.launch_kind == "explore"
                            and task_documents.launched_task_document(session.prompt) is None
                            else ""
                        )
                        + f"\n{FINAL_RESPONSE_NOTICE}"
                    ),
                    result_recovery=True,
                )
                if delivery.get("delivery") == "reply_failed":
                    raise RuntimeError("返却形式の回復入力を配送できませんでした")
            except asyncio.CancelledError:
                if owns_recovery():
                    session.result_contract_recovery_consumed = False
                raise
            except Exception as exc:
                if not owns_recovery():
                    return
                session.clear_hold()
                session.status = "failed"
                session.agent_message = pending["agent_message"]
                recovery_error: dict[str, Any] = {
                    "message": f"{type(exc).__name__}: {exc}",
                    "resultContract": reason,
                    "nextAction": result_contract.RECOVERY_NEXT_ACTION,
                }
                session.error = recovery_error
                session.record_turn_completed()
                session.touch()
            return
        if session.claude_api_resume_pending:
            await self._resume_claude_wait(
                session,
                wrap_delivery_body(
                    "直前のturnはClaudeの接続の失敗で止まったため、接続先を切り替えて同じ会話を再開した。"
                    "この会話に残っている操作と成果物が実際の進み具合である。それを確かめて残る作業を続け、所定の返却形式を返してください。"
                    "この会話に操作と成果物が残っていなければ、依頼を最初から実行してください。"
                    "接続の切替と最初からの実行は想定外事象として報告しないでください。"
                ),
            )
            return
        if session.codex_provider_resume_pending:
            pending = session.pending_result
            try:
                await self._send_with_resources(session, wrap_delivery_body(codex_providers.CONTINUE_PROMPT))
            except Exception:
                session.clear_hold()
                session.status = pending["status"]
                session.agent_message = pending["agent_message"]
                session.error = pending["error"]
                session.record_turn_completed()
                session.touch()
            return
        if session.overload_resume_at is not None:
            if asyncio.get_running_loop().time() >= session.overload_resume_at:
                await self._resume_after_overload(session)
            return
        if session.usage_limit_resume_at is not None:
            if asyncio.get_running_loop().time() >= session.usage_limit_resume_at:
                await self._resume_after_usage_limit(session)
            return

        await consume_agents_wait_background_outputs_async(session)

        own_pending = session.pending_result
        resolutions = {
            session_id: (await run_file_operation(session_registry.resolve, session_id))
            for session_id in tuple(session.live_child_session_ids)
        }
        writer = self._status_writer
        tracked_turns = {
            child_id: entry.recorded_turn_seq
            for child_id, entry in resolutions.items()
            if entry.recorded_turn_seq is not None
            and not entry.resume_pending
            and entry.state in {session_registry.Resolution.TERMINAL, session_registry.Resolution.RELEASED}
        }
        delivered: set[str] = set()
        if writer is not None and tracked_turns and shared_layout.valid_session_id(session.session_id):
            directory = (
                shared_layout.status_directory(writer.root_session_id, self._state_root())
                / "wait-results"
                / f"{session.session_id}.json"
            )
            delivered = await run_file_operation(wait_run.delivered_children, directory, tracked_turns)
            for child_id in sorted(delivered):
                current = await run_file_operation(session_registry.resolve, child_id)
                if session.pending_result is not own_pending:
                    return
                if current.resume_pending or current.recorded_turn_seq != tracked_turns[child_id]:
                    delivered.remove(child_id)
                    continue
                session.live_child_session_ids.discard(child_id)
                session.terminal_child_session_ids.discard(child_id)
        if session.pending_result is not own_pending:
            return

        if not cli_turn_may_continue(session) and is_final_response_with_unobserved_children(session):
            finalize_pending_result(session)
            return
        if (
            delivered
            and own_pending is not None
            and is_background_completion_wait(session)
            and not has_pending_auto_resume_targets(session)
            and not session.terminal_child_session_ids
            and not session.codex_terminal_subagent_thread_ids
            and not session.codex_self_message_observed
            and not result_contract.is_wait_statement(own_pending.get("agent_message"))
        ):
            finalize_pending_result(session)
            return
        terminal = {
            session_id
            for session_id, resolution in resolutions.items()
            if resolution.state is session_registry.Resolution.TERMINAL and session_id not in delivered
        }
        unobserved: set[str] = set()
        for session_id, resolution in resolutions.items():
            if session_id in delivered or resolution.state not in {
                session_registry.Resolution.MISSING,
                session_registry.Resolution.RELEASED,
                session_registry.Resolution.UNREADABLE,
            }:
                continue

            if await self._child_result_is_terminal(session_id):
                terminal.add(session_id)
                continue
            unobserved.add(session_id)
        # 照会のawait中にCLIが回収した子は、照会前の写しから追跡・再配送へ戻さない。
        terminal.intersection_update(session.live_child_session_ids)
        unobserved.intersection_update(session.live_child_session_ids)
        for session_id in terminal:
            session.live_child_session_ids.discard(session_id)
            session.terminal_child_session_ids.add(session_id)

        if not has_pending_auto_resume_targets(session) and (
            session.terminal_child_session_ids
            or session.codex_terminal_subagent_thread_ids
            or session.codex_self_message_observed
            and not session.codex_self_message_repair_consumed
        ):
            identifiers = sorted(session.terminal_child_session_ids | session.codex_terminal_subagent_thread_ids)
            repair_message = session.codex_self_message_observed and not session.codex_self_message_repair_consumed
            prompt = wrap_delivery_body(
                (
                    "あなたが起動した子は終了した。\n"
                    f"終了したsession/thread: {', '.join(identifiers)}\n"
                    "各子の結果を受け取り、残る工程を続けて所定の返却形式を返してください。"
                    if identifiers
                    else "直前のturnの返却本文を確認してください。"
                )
                + (
                    "\n`send_message`で自分宛てに送った本文は委譲元へ届いていない。"
                    "送ろうとした内容の全文を最終応答へ書いて返してください。"
                    if repair_message
                    else ""
                )
                + f"\n{FINAL_RESPONSE_NOTICE}",
            )
            if repair_message:
                session.codex_self_message_repair_consumed = True
            pending_result = session.pending_result
            assert pending_result is not None
            try:
                delivery = await self._send_with_resources(session, prompt)
                if delivery.get("delivery") == "reply_failed":
                    if repair_message:
                        session.codex_self_message_repair_consumed = False
                    return
            except Exception as exc:
                session.clear_hold()
                session.live_child_session_ids.clear()
                session.status = "failed"
                session.agent_message = pending_result["agent_message"]
                session.error = {
                    "message": f"{type(exc).__name__}: {exc}",
                    "unobservedSessions": sorted(set(identifiers) | unobserved),
                }
                session.record_turn_completed()
                session.touch()
                return

            session.terminal_child_session_ids.difference_update(identifiers)
            session.codex_terminal_subagent_thread_ids.difference_update(identifiers)
            await self._discard_automatic_reply(session, delivery)
            return

        # 保留対象が背景taskの終了だけで消えた場合は確定しない。backendはその終了に続く再開turnの結果で
        # 保留中の結果を差し替えるため、ここで確定すると再開turnの結果より先に待機表明の結果を公開してしまう。
        # この場合の確定は再開turnの結果か保留期限の経過に委ねる。
        deadline = auto_resume_wait_deadline(session)
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            finalize_pending_result(session, unobserved_sessions=unobserved)

    async def _resume_claude_wait(self, session: SessionState, body: str) -> None:
        """CLI内の再試行を所有単位ごと回収し、killと同じ境界で同じ会話を再開する。"""
        async with session.turn_control_lock:
            if session.interrupt_requested or not session.awaiting_auto_resume or session.pending_result is None:
                return
            pending = session.pending_result
            prompt = ResumePrompt(body)
            try:
                prompt.context = await run_file_operation(
                    launch_prompts.plugin_resource_instructions, SERVER_PLUGIN_ROOT, session.engine, session.cwd
                )
                await cast(claude.ClaudeServerManager, self._backend(session.engine)).resume(
                    session.session_id,
                    prompt,
                    session.cwd,
                    session.model,
                    session.effort,
                    model_type=session.model_type,
                    launch_kind=session.launch_kind,
                    excluded_candidates=session.excluded_candidates,
                    turn_seq=session.turn_seq,
                    api_env_file=session.claude_api_env_file,
                    existing_session=session,
                )
            except Exception as exc:
                session.clear_hold()
                session.status = pending["status"]
                session.agent_message = pending["agent_message"]
                session.error = {"message": f"{type(exc).__name__}: {exc}", "cause": pending["error"]}
                session.record_turn_completed()
                session.touch()
            finally:
                prompt.close()

    async def _resume_after_overload(self, session: SessionState) -> None:
        """過負荷の待機を終えたsessionへ、同じ作業を続ける指示を新しいturnとして送る。"""
        pending_result = session.pending_result
        assert pending_result is not None
        error = pending_result["error"]
        message = error.get("message") if isinstance(error, dict) else None
        prompt = wrap_delivery_body(
            "直前のturnはモデルの過負荷（serverOverloaded）で中断した。\n"
            f"失敗の内容: {message or 'serverOverloaded'}\n"
            "中断前の作業を続け、所定の返却形式を返せ。",
        )
        finalize_pending_result(session, touch=False, keep_resume_chain=True)
        try:
            delivery = await self._send_with_resources(session, prompt)
        except Exception as exc:
            clear_overload_resume(session)
            session.status = pending_result["status"]
            session.agent_message = pending_result["agent_message"]
            session.error = {**error, "autoResumeError": f"{type(exc).__name__}: {exc}"} if isinstance(error, dict) else error
            session.record_turn_completed()
            session.touch()
            return
        session.api_error = None
        await self._discard_automatic_reply(session, delivery)

    async def _resume_after_usage_limit(self, session: SessionState) -> None:
        """利用上限の解除予定時刻を迎えたsessionへ、同じ作業を続ける指示を新しいturnとして送る。

        再び拒否された場合はbackendが同じ待機へ戻すため、回数と総時間では打ち切らない。
        """
        pending_result = session.pending_result
        assert pending_result is not None
        error = pending_result["error"]
        usage_limit = error.get("usageLimit") if isinstance(error, dict) else None
        limit_type = usage_limit.get("type") if isinstance(usage_limit, dict) else None
        prompt = wrap_delivery_body(
            f"直前のturnは{session.engine}の利用上限（{limit_type or '不明'}）で中断し、解除予定時刻まで待った。\n"
            "中断前の作業を続け、所定の返却形式を返せ。",
        )
        if session.engine == "claude":
            await self._resume_claude_wait(session, prompt)
            return
        finalize_pending_result(session, touch=False, keep_resume_chain=True)
        try:
            delivery = await self._send_with_resources(session, prompt)
        except Exception as exc:
            clear_usage_limit_wait(session)
            session.status = pending_result["status"]
            session.agent_message = pending_result["agent_message"]
            session.error = {**error, "autoResumeError": f"{type(exc).__name__}: {exc}"} if isinstance(error, dict) else error
            session.record_turn_completed()
            session.touch()
            return
        await self._discard_automatic_reply(session, delivery)

    async def _discard_automatic_reply(self, session: SessionState, delivery: dict[str, Any]) -> None:
        previous = delivery.get("previous_result")
        if self._status_writer is not None and isinstance(previous, PreviousResult) and (end := previous.end) is not None:
            await self._status_writer.delete_result_async(session.session_id, collector="auto-resume", expected_end=end)

    async def _pending_resume_status(self, pending: PendingResume) -> dict[str, Any]:
        session = self.sessions.get(pending.state.session_id)
        if session is not None:
            return await self._result_response_async(session, include_result=False)
        response: dict[str, Any] = {"status": "running", "progress": ""}
        elapsed_seconds = turn_elapsed_seconds(pending.state.started_at)
        if elapsed_seconds is not None:
            response["elapsed_seconds"] = elapsed_seconds
        return response

    async def _run_resume(
        self,
        resume_state: SessionResumeState,
        prompt: ResumePrompt,
        on_failure: Callable[[str, Exception], Awaitable[None]] | None = None,
        *,
        supersede_interruption: bool = False,
    ) -> SessionState:
        """backendの再開を完了し、失敗時だけ再試行用状態を復元する。"""
        session_id = resume_state.session_id
        claimed = False
        try:
            claimed = await run_file_operation(
                session_registry.claim_resume, session_id, resume_state.turn_seq, state_root=self._state_root()
            )
            if not claimed:
                raise ResumeDeferred(
                    f"同じsessionの再開が別の処理で進行しています: {session_id}",
                    next_action="同じ`session_id`を`show`で確認し、`atk agents wait`で終了を観測する",
                )
            backend = self._backend(resume_state.engine)
            prompt.context = await run_file_operation(
                launch_prompts.plugin_resource_instructions, SERVER_PLUGIN_ROOT, resume_state.engine, resume_state.cwd
            )
            await launch_requests.check_plugin_commands(resume_state.cwd)
            provider_args: dict[str, Any] = {}
            if resume_state.engine == "claude" and resume_state.claude_api_env_file is not None:
                provider_args["api_env_file"] = resume_state.claude_api_env_file
            if resume_state.engine == "claude" and resume_state.claude_connection is not None:
                provider_args["connection_kind"] = resume_state.claude_connection
            if resume_state.codex_model_provider is not None:
                provider_args = {
                    "model_provider": resume_state.codex_model_provider,
                    "subscription_provider": resume_state.codex_subscription_provider,
                }
            if resume_state.engine == "codex" and resume_state.codex_state_provider is not None:
                provider_args["state_provider"] = resume_state.codex_state_provider
            if resume_state.engine in {"claude", "codex"}:
                provider_args["handoff_prompt"] = resume_state.prompt
            if supersede_interruption and self._is_registry_resume_state(resume_state):
                try:
                    await self._take_stopped_result(session_id, resume_state, collector="auto-resume")
                except retained_results.ResultBeingCollected as busy:
                    if self._status_writer is not None and session_id in self._recovery_targets:
                        self._status_writer.show_recovery_wait(resume_state)
                    raise ResumeDeferred(busy.reason, next_action=busy.next_action) from busy
            # 止まっているsessionを再び動かす追送も、マシン全体の同時稼働数の上限に空きが出るまで待つ。
            ticket = await admission.acquire(await run_file_operation(self.launcher_session_id), state_root=self._state_root())
            try:
                with startup_features.selection(resume_state.startup_profile):
                    session = await backend.resume(
                        session_id,
                        prompt,
                        resume_state.cwd,
                        resume_state.model,
                        resume_state.effort,
                        model_type=resume_state.model_type,
                        launch_kind=resume_state.launch_kind,
                        excluded_candidates=resume_state.excluded_candidates,
                        turn_seq=resume_state.turn_seq,
                        fast_mode=resume_state.fast_mode,
                        request_prompt=resume_state.prompt,
                        result_contract_recovery_consumed=resume_state.result_contract_recovery_consumed,
                        **provider_args,
                    )
            finally:
                ticket.release()

            admission_wait_fields(self)[session_id] = ticket.response_fields()
            note_admission_wait(session, ticket)
            session_registry.LaunchInfo.of(resume_state).apply_to(session)
            session.startup_profile = resume_state.startup_profile
            session.result_contract_recovery_consumed = resume_state.result_contract_recovery_consumed
            if session.status == "starting":
                session.status = "running"
            session.announced = True
            session.touch()
            await session.drain_registration()
            _LOG.info(
                "session_transition event=resume session_id=%s writer=mcp-manager status=%s turn_seq=%d",
                session.session_id,
                session.status,
                session.turn_seq,
            )
            return session
        except BaseException as error:
            if supersede_interruption and isinstance(error, ResumeDeferred):
                raise
            if session_id not in self.sessions:
                self.expired_sessions[session_id] = resume_state
            if on_failure is not None and isinstance(error, Exception):
                # 待機側が旧turnを回収済みとして対象から外す前に、失敗通知を確定する。
                await on_failure(session_id, error)
            raise
        finally:
            try:
                if claimed:
                    await run_file_operation(
                        session_registry.release_resume_claim, session_id, resume_state.turn_seq, state_root=self._state_root()
                    )
            finally:
                prompt.close()
                pending = self._pending_resumes.get(session_id)
                if pending is not None and pending.task is asyncio.current_task():
                    self._pending_resumes.pop(session_id, None)

    @staticmethod
    def _consume_resume_exception(task: asyncio.Task[SessionState]) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            task.exception()

    def _start_resume(
        self,
        resume_state: SessionResumeState,
        prompt: str,
        previous_result: dict[str, Any] | None,
        *,
        on_failure: Callable[[str, Exception], Awaitable[None]] | None = None,
        supersede_interruption: bool = False,
    ) -> tuple[PendingResume, int]:
        """期限切れ状態を一意な進行中再開操作へ原子的に移す。"""
        session_id = resume_state.session_id
        self.expired_sessions.pop(session_id, None)
        self.stopped_sessions.pop(session_id, None)
        resume_prompt = ResumePrompt(prompt)
        gate = service_turns.current()

        async def owned_resume() -> SessionState:
            entered = False
            try:
                assert gate is not None
                async with gate.admit(new_turn=True, session_id=session_id):
                    await asyncio.sleep(0)
                    entered = True
                    return await self._run_resume(
                        resume_state, resume_prompt, on_failure, supersede_interruption=supersede_interruption
                    )
            finally:
                if not entered:
                    self.expired_sessions[session_id] = resume_state
                    resume_prompt.close()
                    pending = self._pending_resumes.get(session_id)
                    if pending is not None and pending.task is asyncio.current_task():
                        self._pending_resumes.pop(session_id, None)

        task = (
            asyncio.create_task(owned_resume(), eager_start=True)
            if gate is not None
            else asyncio.create_task(
                self._run_resume(resume_state, resume_prompt, on_failure, supersede_interruption=supersede_interruption)
            )
        )
        if task.done() and not task.cancelled() and task.exception() is not None:
            task.result()
        pending = PendingResume(
            state=resume_state,
            task=task,
            prompt=resume_prompt,
            previous_result=previous_result,
        )
        if not task.done():
            self._pending_resumes[session_id] = pending
        task.add_done_callback(self._consume_resume_exception)
        return pending, resume_prompt.initial_ticket

    async def _cancel_pending_resume(
        self,
        pending: PendingResume,
        timeout: float,
    ) -> tuple[SessionState, bool]:
        pending.prompt.close()
        cancellation_requested = not pending.task.done()
        if cancellation_requested:
            pending.task.cancel()
        outcomes = await asyncio.wait_for(
            asyncio.gather(pending.task, return_exceptions=True),
            timeout=timeout,
        )
        outcome = outcomes[0]
        if not isinstance(outcome, asyncio.CancelledError):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome, outcome.interrupt_requested

        resume_state = pending.state
        session = self.sessions.get(resume_state.session_id)
        if session is None or session.turn_seq <= resume_state.turn_seq:
            identifier = resume_state.session_id
            if session is None:
                self.expired_sessions.pop(identifier, None)
                self.stopped_sessions[identifier] = resume_state
            if self._pending_resumes.get(identifier) is pending:
                self._pending_resumes.pop(identifier, None)
            await self._notify_waiters()
            raise ActionableError(
                "再開待ちを取り消した。新しいturnが成立しておらず、中断要求は未配送です",
                next_action="showで元のturnを確認し、未回収の結果はwaitで回収する",
            )
        pending.discard_previous_result()
        self.expired_sessions.pop(session.session_id, None)
        if self._pending_resumes.get(session.session_id) is pending:
            self._pending_resumes.pop(session.session_id, None)
        session.status = "interrupted"
        session.record_turn_completed()
        session.interrupt_requested = False
        session.touch()
        await self._notify_waiters()
        return session, False

    async def _resume_and_reply(
        self,
        resume_state: SessionResumeState,
        prompt: str,
        previous_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """保存済みの最小状態から同じ会話を再開し、公開契約の応答を返す。

        再開は結果保持期限の経過と所有主体の終了の双方を契機とする。
        結果本文を保持したまま所有主体だけが終了した場合は、直前結果を応答へ含める。
        """
        await self._wait_for_resource_release(resume_state.session_id)
        await launch_requests.reject_stale_role_reply(resume_state)
        if previous_result and self._status_writer is not None and not self._is_registry_resume_state(resume_state):
            await self._status_writer.retain_result_async(resume_state)
        pending, ticket = self._start_resume(
            resume_state,
            prompt,
            previous_result,
        )
        return await self._resume_response(pending, ticket)
