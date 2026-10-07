"""孫sessionの終端、過負荷と利用上限の解除を待った後の自動再開と、`send_message`による保存済みsessionの再開。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from agent_toolkit._agents_server import (
    launch_requests,
    manager_registry,
    session_registry,
    shared_layout,
)
from agent_toolkit._agents_server.launch_requests import (
    wrap_delivery_body,
)
from agent_toolkit._agents_server.manager_base import PendingResume
from agent_toolkit._agents_server.responses import (
    turn_elapsed_seconds,
)
from agent_toolkit._agents_server.resume_waits import (
    clear_overload_resume,
    clear_usage_limit_wait,
    finalize_pending_result,
    has_pending_auto_resume_targets,
)
from agent_toolkit._agents_server.state import ResumePrompt, SessionResumeState, SessionState
from agent_toolkit._agents_server.wait_output_tracking import consume_agents_wait_background_outputs

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


class ManagerResume(manager_registry.ManagerRegistry):
    """孫sessionの終端と待機の解除を観測して自動で継続し、保存済みsessionを再開する。"""

    def _child_result_is_terminal(self, session_id: str) -> bool:
        """孫sessionの共有された終端結果ファイルが終端を示すかを返す。

        このファイルは同じルートsessionの`results`配下を全ての書込主体が共有するため、
        別プロセスが起動した孫sessionの終端も同じ処理で判定できる。
        """
        if self._status_writer is None or not shared_layout.valid_session_id(session_id):
            return False
        return self._status_writer.read_result(session_id) is not None

    async def _advance_child_session_wait(self, session: SessionState) -> None:
        """保留中の結果を、孫sessionの終端・過負荷と利用上限の待機の経過または保持期限に応じて進める。"""
        if not session.awaiting_auto_resume or session.pending_result is None:
            return
        if session.overload_resume_at is not None:
            if asyncio.get_running_loop().time() >= session.overload_resume_at:
                await self._resume_after_overload(session)
            return
        if session.usage_limit_resume_at is not None:
            if asyncio.get_running_loop().time() >= session.usage_limit_resume_at:
                await self._resume_after_usage_limit(session)
            return
        # 背景実行の`atk agents wait`で回収済みの孫sessionは、登録簿と終端結果ファイルが消えた後も未観測にしない。
        consume_agents_wait_background_outputs(session)
        resolutions = {session_id: session_registry.resolve(session_id) for session_id in session.live_child_session_ids}
        terminal = {
            session_id
            for session_id, resolution in resolutions.items()
            if resolution.state is session_registry.Resolution.TERMINAL
        }
        unobserved: set[str] = set()
        for session_id, resolution in resolutions.items():
            if resolution.state not in {
                session_registry.Resolution.MISSING,
                session_registry.Resolution.RELEASED,
                session_registry.Resolution.UNREADABLE,
            }:
                continue
            # レコードの不在と解放済みは、所有側の解放と7日超の回収からも生じるため、終端結果ファイルを終端の第2の根拠とする。
            if self._child_result_is_terminal(session_id):
                terminal.add(session_id)
                continue
            unobserved.add(session_id)
        pending_unobserved = self._pending_unobserved_child_sessions.get(session.session_id)
        if pending_unobserved is not None:
            unobserved.update(pending_unobserved[1])
        for session_id in terminal:
            session.live_child_session_ids.discard(session_id)
            session.terminal_child_session_ids.add(session_id)
        session.live_child_session_ids.difference_update(unobserved)

        if not has_pending_auto_resume_targets(session) and session.terminal_child_session_ids:
            identifiers = sorted(session.terminal_child_session_ids)
            prompt = wrap_delivery_body(
                "あなたが`agents_server`で起動した次のsessionは終端した。\n"
                f"終端したsession: {', '.join(identifiers)}\n"
                "各sessionの結果を確認し、所定の返却形式を返せ。",
            )
            session.auto_resume_consumed = True
            pending_result = session.pending_result
            assert pending_result is not None
            session.status = pending_result["status"]
            session.agent_message = pending_result["agent_message"]
            session.error = pending_result["error"]
            if unobserved:
                self._pending_unobserved_child_sessions[session.session_id] = (session.turn_seq + 1, unobserved)
            try:
                await self._backend(session.engine).send_message(session, prompt)
            except Exception as exc:
                self._pending_unobserved_child_sessions.pop(session.session_id, None)
                session.awaiting_auto_resume = False
                session.auto_resume_deadline = None
                session.pending_result = None
                session.live_child_session_ids.clear()
                session.status = "failed"
                session.agent_message = pending_result["agent_message"]
                session.error = {
                    "message": f"{type(exc).__name__}: {exc}",
                    "unobservedSessions": sorted(set(identifiers) | unobserved),
                }
                session.turn_completed = True
                session.turn_start_ambiguous = False
                session.touch()
                return
            if session.pending_result is not None:
                finalize_pending_result(session, touch=False)
            if self._status_writer is not None:
                self._status_writer.delete_result(session.session_id, collector="auto-resume")
            return

        # 保留対象が背景taskの終端だけで消えた場合は確定しない。backendはその終端に続く再開turnの結果で
        # 保留中の結果を差し替えるため、ここで確定すると再開turnの結果より先に待機表明の結果を公開してしまう。
        # この場合の確定は再開turnの結果か保留期限の経過に委ねる。
        if not has_pending_auto_resume_targets(session) and unobserved:
            finalize_pending_result(session, unobserved_sessions=unobserved)
            return

        deadline = session.auto_resume_deadline
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            finalize_pending_result(session, unobserved_sessions=unobserved)

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
            await self._backend(session.engine).send_message(session, prompt)
        except Exception as exc:
            clear_overload_resume(session)
            session.status = pending_result["status"]
            session.agent_message = pending_result["agent_message"]
            session.error = {**error, "autoResumeError": f"{type(exc).__name__}: {exc}"} if isinstance(error, dict) else error
            session.turn_completed = True
            session.turn_start_ambiguous = False
            session.touch()
            return
        session.api_error = None
        if self._status_writer is not None:
            self._status_writer.delete_result(session.session_id, collector="auto-resume")

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
            f"直前のturnはClaude Codeの利用上限（{limit_type or '不明'}）で中断し、解除予定時刻まで待った。\n"
            "中断前の作業を続け、所定の返却形式を返せ。",
        )
        finalize_pending_result(session, touch=False, keep_resume_chain=True)
        try:
            await self._backend(session.engine).send_message(session, prompt)
        except Exception as exc:
            clear_usage_limit_wait(session)
            session.status = pending_result["status"]
            session.agent_message = pending_result["agent_message"]
            session.error = {**error, "autoResumeError": f"{type(exc).__name__}: {exc}"} if isinstance(error, dict) else error
            session.turn_completed = True
            session.turn_start_ambiguous = False
            session.touch()
            return
        if self._status_writer is not None:
            self._status_writer.delete_result(session.session_id, collector="auto-resume")

    def _pending_resume_status(self, pending: PendingResume) -> dict[str, Any]:
        """進行中の再開操作を通常のrunning状態として射影する。"""
        session = self.sessions.get(pending.state.session_id)
        if session is not None:
            return self._result_response(session)
        response: dict[str, Any] = {"status": "running", "progress": ""}
        elapsed_seconds = turn_elapsed_seconds(pending.state.started_at)
        if elapsed_seconds is not None:
            response["elapsed_seconds"] = elapsed_seconds
        return response

    async def _run_resume(self, resume_state: SessionResumeState, prompt: ResumePrompt) -> SessionState:
        """backendの再開を完了し、失敗時だけ再試行用状態を復元する。"""
        session_id = resume_state.session_id
        backend = self._backend(resume_state.engine)
        try:
            await launch_requests.check_plugin_commands(resume_state.cwd)
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
            )
            session_registry.LaunchInfo.of(resume_state).apply_to(session)
            if self._status_writer is not None:
                self._status_writer.delete_result(session_id, collector="send-message")
            if session.status == "starting":
                session.status = "running"
            session.announced = True
            session.touch()
            _LOG.info(
                "session_transition event=resume session_id=%s writer=mcp-manager status=%s turn_seq=%d",
                session.session_id,
                session.status,
                session.turn_seq,
            )
            return session
        except BaseException:
            if session_id not in self.sessions:
                self.expired_sessions[session_id] = resume_state
            raise
        finally:
            prompt.close()
            pending = self._pending_resumes.get(session_id)
            if pending is not None and pending.task is asyncio.current_task():
                self._pending_resumes.pop(session_id, None)

    @staticmethod
    def _consume_resume_exception(task: asyncio.Task[SessionState]) -> None:
        """timeout後に完了した管理対象taskの例外を回収する。"""
        with contextlib.suppress(asyncio.CancelledError):
            task.exception()

    def _start_resume(
        self,
        resume_state: SessionResumeState,
        prompt: str,
        previous_result: dict[str, Any] | None,
    ) -> tuple[PendingResume, int]:
        """期限切れ状態を一意な進行中再開操作へ原子的に移す。"""
        session_id = resume_state.session_id
        self.expired_sessions.pop(session_id, None)
        self.stopped_sessions.pop(session_id, None)
        resume_prompt = ResumePrompt(prompt)
        task = asyncio.create_task(self._run_resume(resume_state, resume_prompt))
        pending = PendingResume(
            state=resume_state,
            task=task,
            prompt=resume_prompt,
            previous_result=previous_result,
        )
        self._pending_resumes[session_id] = pending
        task.add_done_callback(self._consume_resume_exception)
        return pending, resume_prompt.initial_ticket

    async def _cancel_pending_resume(
        self,
        pending: PendingResume,
        timeout: float,
    ) -> tuple[SessionState, bool]:
        """保留中の再開と配下作業を回収し、中断要求の受理有無を返す。"""
        pending.discard_previous_result()
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
        if session is None:
            session = SessionState(
                session_id=resume_state.session_id,
                cwd=resume_state.cwd,
                model=resume_state.model,
                effort=resume_state.effort,
                engine=resume_state.engine,
                model_type=resume_state.model_type,
                launch_kind=resume_state.launch_kind,
                excluded_candidates=resume_state.excluded_candidates,
                announced=True,
                turn_seq=resume_state.turn_seq + 1,
            )
            session_registry.LaunchInfo.of(resume_state).apply_to(session)
            self.sessions[session.session_id] = session
        self.expired_sessions.pop(session.session_id, None)
        if self._pending_resumes.get(session.session_id) is pending:
            self._pending_resumes.pop(session.session_id, None)
        session.status = "interrupted"
        session.turn_completed = True
        session.turn_start_ambiguous = False
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
        pending, ticket = self._start_resume(
            resume_state,
            prompt,
            previous_result,
        )
        return await self._resume_response(pending, ticket)
