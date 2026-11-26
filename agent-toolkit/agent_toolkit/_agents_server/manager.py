from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from typing import Any, Self

from agent_toolkit._agents_server import (
    backends,
    codex_resume_state,
    effort_control,
    launch_requests,
    lifetime,
    logging_config,
    manager_start,
    result_projection,
    resume_waits,
    retained_results,
    state,
    status_file,
    subagent_contract,
)
from agent_toolkit._agents_server.file_operations import finish_task, run_file_operation
from agent_toolkit._agents_server.input_validation import validate_prompt
from agent_toolkit._agents_server.launch_requests import wrap_delivery_body
from agent_toolkit._agents_server.session_errors import (
    KILL_NOT_DELIVERED_NEXT_ACTION,
    ActionableTimeoutError,
    SessionOwnerGoneError,
)
from agent_toolkit._agents_server.state import (
    SessionResumeState,
    SessionState,
    remove_lifecycle_listener,
    remove_terminal_listener,
    remove_touch_listener,
)
from agent_toolkit._common import process_tree, recorded_process, runtime_environment, session_registry, session_release
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


DEFAULT_KILL_TIMEOUT = 270.0


DEFAULT_SEND_MESSAGE_TIMEOUT = 270.0


REPLY_DELIVERIES = frozenset({"reply_started", "reply_failed", "reply_ambiguous"})


START_AVAILABILITY_TIMEOUT = state.START_AVAILABILITY_TIMEOUT


class AgentsServerManager(manager_start.ManagerStart):  # pylint: disable=too-many-ancestors  # 責務別のmixinへ分割したため。
    _close_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exception: object) -> None:
        await self.close()

    def activate(self) -> None:
        session_registry.declare_effort_receiver()
        self._auto_resume_task = asyncio.create_task(self._monitor_auto_resume())
        if self._status_writer is not None:
            self._status_writer.activate()
            self._heartbeat_task = asyncio.create_task(self._refresh_heartbeat())

    async def activate_async(self, *, shared_monitor: bool = False) -> None:
        session_registry.declare_effort_receiver()
        if self._status_writer is not None:
            await self._status_writer.activate_async()
            if not shared_monitor:
                self._heartbeat_task = asyncio.create_task(self._refresh_heartbeat())
        if not shared_monitor:
            self._auto_resume_task = asyncio.create_task(self._monitor_auto_resume())

    async def _monitor_auto_resume(self) -> None:
        """公開待機操作が呼ばれるかによらず、孫session終了後の自動再開を進める。"""
        while True:
            bound = runtime_environment.bind(self._caller_context) if self._caller_context else contextlib.nullcontext()
            with bound:
                awaiting = await self._process_auto_resume()
            await asyncio.sleep(0.1 if awaiting else 1.0)

    async def _process_auto_resume(self, *, process_controls: bool = True) -> bool:
        if process_controls:
            await effort_control.process_requests(
                self.sessions,
                self._effort_catalog,
                self._apply_effort,
                state_root=None if self._status_writer is None else self._status_writer.state_root,
            )
        ready_sessions = {key: value for key, value in self.sessions.items() if key not in self._service_paused}
        await effort_control.resume_changed(ready_sessions, self._send_with_resources)
        if process_controls:
            await session_release.process_requests(
                self.sessions,
                self.stop,
                state_root=None if self._status_writer is None else self._status_writer.state_root,
            )
        awaiting = [session for session in ready_sessions.values() if session.awaiting_auto_resume]
        for session in awaiting:
            await self._advance_child_session_wait(session)
        return bool(awaiting)

    async def _await_start_outcome(self, session: SessionState, timeout: float | None = None) -> None:
        """元のモジュールで外部から差し替えられる待機上限を、起動処理へ渡す。"""
        await super()._await_start_outcome(session, START_AVAILABILITY_TIMEOUT if timeout is None else timeout)

    async def send_message(
        self,
        session_id: str,
        prompt: str | None = None,
        timeout: float = DEFAULT_SEND_MESSAGE_TIMEOUT,
        *,
        instruction: str | None = None,
        extra_params: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ActionableError(
                "timeout must be positive", next_action="`timeout`を省略して270秒で待つか、正の秒数を指定する"
            )
        if session_id in self._force_stopping:
            raise ActionableError(
                "強制停止の処理中です。追送は未配送です",
                next_action="停止の結果を確認してから追送する",
            )
        task = asyncio.current_task()
        assert task is not None
        sends = self._active_sends.setdefault(session_id, set())
        sends.add(task)
        try:
            async with asyncio.timeout(float(timeout)):
                return await self._send_message_response(session_id, prompt, instruction, extra_params)
        except TimeoutError as exc:
            raise ActionableTimeoutError(
                f"send_message timed out: {session_id}; delivery is undetermined",
                next_action="`atk agents wait`で状態を確認し、指示が届いていないと判断した場合だけ`send_message`を再送する",
            ) from exc
        except asyncio.CancelledError:
            if session_id not in self._force_stopping:
                raise
            raise ActionableError(
                "強制停止により追送の処理を取り消しました",
                next_action="停止の結果を確認してから必要な指示を追送する",
            ) from None
        finally:
            sends.discard(task)
            if not sends:
                self._active_sends.pop(session_id, None)

    async def _send_message_response(
        self, session_id: str, prompt: str | None, instruction: str | None, extra_params: Mapping[str, str] | None
    ) -> dict[str, Any]:
        await self._drain_diagnostics()
        pending = self._pending_resumes.get(session_id)
        current = (
            pending.state if pending is not None else (self.sessions.get(session_id) or self.expired_sessions.get(session_id))
        )
        if current is None:
            current = await self._resolve_stopped_session(session_id)
        if current is None:
            await self.take_over_orphaned_session(session_id)
            await self._raise_if_owner_lost(session_id)
            current = await self._restore_registry_session(session_id)
        registry_resolved = self._is_registry_resume_state(current)
        prompt = await run_file_operation(
            launch_requests.continuation_prompt,
            current.prompt if current is not None else "",
            current.followup_prompts if current is not None else (),
            prompt=prompt,
            instruction=instruction,
            extra_params=extra_params,
        )
        response = await self._deliver_message(session_id, prompt)
        await run_file_operation(subagent_contract.append_followup, session_id, prompt, response["delivery"])
        session = self.sessions.get(session_id)
        if session is not None and response["delivery"] != "reply_failed":
            session.followup_prompts += (prompt,)
            session.touch()
            await session.drain_registration()
        if self._status_writer is not None:
            if session is not None:
                # 開始時と同じbest-effort公開で、配送結果と同じsessionの待機登録を保つ。
                await self._save_started_session(session)
            response["root_session_id"] = self._status_writer.root_session_id
        notices = await self._read_registry_notices(session_id) if registry_resolved else []
        previous: dict[str, Any] | None = response.pop("previous_result", None)
        if isinstance(previous, state.PreviousResult) and previous.end is not None:
            try:
                previous = await self._claim_resume_result(previous.origin, previous, collector="send-message")
            except retained_results.ResultBeingCollected:
                # 配送は既に完了した。先行CLIを待たず、原本を次のwaitに残す。
                previous = None
        if previous:
            response["previous_result"] = dict(previous)
        return self._deliver_notices(response, notices)

    async def _deliver_message(self, session_id: str, prompt: str) -> dict[str, Any]:
        validate_prompt(prompt)
        await self._wait_for_resource_release(session_id)
        if self._status_writer is not None:
            await self._status_writer.drain()
        await self.take_over_orphaned_session(session_id)
        prompt = wrap_delivery_body(prompt)
        while True:
            async with self._resume_lock:
                pending = self._pending_resumes.get(session_id)
                if pending is not None:
                    ticket, changed = pending.prompt.submit_or_observe(prompt)
                    if ticket is not None:
                        return await self._resume_response(pending, ticket)
                    if changed is not None:
                        await changed.wait()
                    else:
                        await asyncio.shield(pending.task)
                    continue
                retained = self.sessions.get(session_id)
                if (
                    retained is not None
                    and retained.retention_deadline is not None
                    and asyncio.get_running_loop().time() >= retained.retention_deadline
                ):
                    self._expire_session(session_id)
                stopped_state = await self._resolve_stopped_session(session_id)
                if stopped_state is None:
                    stopped_state = await self._restore_registry_session(session_id)
                resume_state = stopped_state or self.expired_sessions.get(session_id)
                if resume_state is not None:
                    previous_result = self._stopped_result_response(resume_state)
                    return await self._resume_and_reply(
                        resume_state,
                        prompt,
                        previous_result,
                    )
                session = await self._get_session(session_id)
            async with session.turn_control_lock:
                if session.interrupt_requested and not session.terminal:
                    raise ActionableError(
                        f"session is being interrupted: {session_id}",
                        next_action=result_projection.RESEND_AFTER_WAIT_NEXT_ACTION,
                    )
            if self._status_writer is not None and session.result_available and not session.result_delivered:
                # 配送の打ち切りで追送の応答が無くても、直前の結果はCLIに残す。
                await self._status_writer.retain_result_async(session)
            try:
                await launch_requests.reject_stale_role_reply(session)
                result = await self._send_with_resources(session, prompt)
            except SessionOwnerGoneError:
                async with self._resume_lock:
                    if self.sessions.get(session_id) is not session:
                        continue
                    self._expire_session(session_id)
                    resume_state = self.expired_sessions[session_id]
                    previous_result = self._stopped_result_response(resume_state)
                    return await self._resume_and_reply(
                        resume_state,
                        prompt,
                        previous_result,
                    )
            delivery = result["delivery"]
            await session.drain_registration()
            if delivery in {"reply_started", "reply_ambiguous"}:
                session.reset_progress()
            response: dict[str, Any] = {"delivery": delivery, "label": session.label}
            error = result.get("error")
            if (
                delivery == "reply_failed"
                and isinstance(error, dict)
                and error.get("codexErrorInfo") == codex_resume_state.WRITER_CONFLICT
            ):
                response["error"] = {"codexErrorInfo": codex_resume_state.WRITER_CONFLICT}
            if delivery in REPLY_DELIVERIES:
                response["previous_result"] = result["previous_result"]
            return response

    async def _kill_result_response_async(self, session: SessionState, *, kill_requested: bool, stop: bool) -> dict[str, Any]:
        notices = await self._read_notices(session.session_id)
        response: dict[str, Any] = await self._result_response_async(session, include_progress=False)
        if "agent_message" in response and session.finalized_at is not None:
            end = session.turn_seq, session.finalized_at

            claimed = not stop and not session.result_delivered
            if claimed and self._status_writer is not None:
                try:
                    claimed = (
                        await self._claim_resume_result(SessionResumeState.from_session(session), response, collector="kill")
                        is not None
                    )
                except retained_results.ResultBeingCollected:
                    return self._deliver_notices({"status": response["status"], "kill_requested": kill_requested}, notices)
            if not stop and not session.result_delivered and end == (session.turn_seq, session.finalized_at):
                session.result_delivered = True
                session.touch()
            if stop:
                response.pop("agent_message", None)
            elif not claimed:
                response = {"status": response["status"]}
        response["kill_requested"] = kill_requested
        await self._stop_after_terminal_response(session.session_id, response, stop)
        return self._deliver_notices(response, notices)

    async def _force_kill(self, session_id: str, timeout: float, stop: bool) -> dict[str, Any]:
        if session_id in self._force_stopping:
            raise ActionableError("強制停止の処理中です", next_action="先行した停止の結果を確認する")
        session = self.sessions.get(session_id)
        pending = self._pending_resumes.get(session_id)
        recovery = self._recovery_attempts.get(session_id)
        sends = set(self._active_sends.get(session_id, ()))
        if session is None and pending is None and recovery is None and not sends:
            orphaned = await self._force_finalize_orphaned_session(session_id)
            if orphaned is not None:
                await self._stop_after_terminal_response(session_id, orphaned, stop)
                return self._deliver_notices(orphaned, await self._read_registry_notices(session_id))
            return await self._kill_with_interrupt(session_id, timeout, stop)
        self._force_stopping.add(session_id)
        self._finish_recovery(session_id)
        operations = sends | {task for task in (pending.task if pending else None, recovery) if task is not None}
        if pending is not None:
            pending.prompt.close()
        for task in operations:
            task.cancel()
        try:
            requested = bool(operations) or session is not None and (not session.terminal or not session.turn_completed)
            if requested or session is not None:
                owned = session
                if owned is None and pending is not None:
                    owned = SessionState(session_id, pending.state.cwd, engine=pending.state.engine)
                if owned is not None:
                    owned.interrupt_requested = True
                    await self._release_force_target(owned)
                if operations:
                    await asyncio.gather(*operations, return_exceptions=True)
                session = self.sessions.get(session_id)
                if session is not None and session is not owned:
                    await self._release_force_target(session)
                if requested and session is not None:
                    session.clear_hold()
                    resume_waits.clear_usage_limit_wait(session)
                    session.status = "interrupted"
                    session.api_error = None
                    session.record_turn_completed()
                    session.touch()
            if session is None:
                if pending is not None and self._pending_resumes.get(session_id) is pending:
                    self._pending_resumes.pop(session_id, None)
                    self.expired_sessions.setdefault(session_id, pending.state)
                await self._cancel_owner_loss_recovery(session_id)
                response = {"status": "interrupted", "kill_requested": requested}
                await self._stop_after_terminal_response(session_id, response, stop)
                return self._deliver_notices(response, await self._read_registry_notices(session_id))
            await session.drain_registration()
            return await self._kill_result_response_async(session, kill_requested=requested, stop=stop)
        finally:
            if current := self.sessions.get(session_id):
                current.interrupt_requested = False
            if recovery is not None and recovery.done() and self._recovery_attempts.get(session_id) is recovery:
                del self._recovery_attempts[session_id]
            self._force_stopping.discard(session_id)
            await self._notify_waiters()

    async def _release_force_target(self, session: SessionState) -> None:
        executor = session.executor_process
        if session.engine != "codex" and executor is not None:
            failure = await run_file_operation(recorded_process.stop_recorded_executor, executor)
            if failure is not None:
                raise ActionableError(failure, next_action="停止できない理由を解消してforce付きkillを再試行する")
        await self._backend(session.engine).release_session(session)

    async def _force_finalize_orphaned_session(self, session_id: str) -> dict[str, Any] | None:
        resolution = await run_file_operation(session_registry.resolve, session_id)
        info = resolution.resume_info
        if (
            resolution.state is not session_registry.Resolution.RUNNING
            or resolution.resume_pending
            or not resolution.executor_processes
            or resolution.live_executors
            or info is None
            or await run_file_operation(status_file.live_writer_holds_session, session_id)
        ):
            return None

        if session_id in self.sessions or session_id in self._pending_resumes:
            return None
        await self._publish_registry_terminal(session_id, info, "interrupted", interruption=None, expected=resolution)
        current = await run_file_operation(session_registry.resolve, session_id)
        if (
            current.state is not session_registry.Resolution.TERMINAL
            or current.recorded_turn_seq != info.turn_seq
            or current.resume_info is None
            or current.resume_info.status != "interrupted"
        ):
            return None
        return {"status": "interrupted", "kill_requested": True}

    async def kill(
        self,
        session_id: str,
        timeout: float = DEFAULT_KILL_TIMEOUT,
        stop: bool = False,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            raise ActionableError(
                "timeout must be non-negative", next_action="`timeout`を省略して270秒で待つか、0以上の秒数を指定する"
            )
        if force:
            with process_tree.FORCE_STOP.set(True):
                return await self._force_kill(session_id, timeout, stop)
        return await self._kill_with_interrupt(session_id, timeout, stop)

    async def _kill_with_interrupt(self, session_id: str, timeout: float, stop: bool) -> dict[str, Any]:
        await self._drain_diagnostics()
        await self._settle_recovery_before_kill(session_id, timeout if timeout > 0 else DEFAULT_SEND_MESSAGE_TIMEOUT)
        observed_session = self.sessions.get(session_id)
        observed_turn = (
            None
            if observed_session is None
            else (observed_session.turn_seq, observed_session.terminal, observed_session.interrupt_requested)
        )
        await self._wait_for_resource_release(session_id)
        session = self.sessions.get(session_id)
        if session is not None:
            await session.drain_registration()
        if self._status_writer is not None:
            await self._status_writer.drain()
        stopped_state = await self._resolve_stopped_session(session_id)
        recovered_state: SessionResumeState | None = None
        if stopped_state is None and session_id not in self.sessions and session_id not in self._pending_resumes:
            await self.take_over_orphaned_session(session_id)
            owner_lost_response = await self._kill_owner_lost(session_id)
            if owner_lost_response is not None:
                notices = await self._read_registry_notices(session_id)
                await self._stop_after_terminal_response(session_id, owner_lost_response, stop)
                return self._deliver_notices(owner_lost_response, notices)
        if stopped_state is None:
            recovered_state = await self._restore_registry_session(session_id)
            stopped_state = recovered_state
        if recovered_state is not None:
            response = self._recovered_result_response(recovered_state)
            response["kill_requested"] = await self._cancel_owner_loss_recovery(session_id)
            return self._deliver_notices(response, await self._read_registry_notices(session_id))
        if stopped_state is not None:
            notices = await self._read_notices(session_id)
            try:
                stopped_response = await self._take_stopped_result(session_id, stopped_state, collector="kill")
            except retained_results.ResultBeingCollected:
                stopped_response = {"status": stopped_state.status}
            return self._deliver_notices({**(stopped_response or {"status": "expired"}), "kill_requested": False}, notices)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout) if timeout > 0 else None
        delivery_deadline = deadline if deadline is not None else loop.time() + DEFAULT_SEND_MESSAGE_TIMEOUT
        pending = self._pending_resumes.get(session_id)
        if pending is not None:
            try:
                session, interrupt_requested = await self._cancel_pending_resume(
                    pending,
                    max(0.0, delivery_deadline - loop.time()),
                )
            except TimeoutError as exc:
                raise ActionableTimeoutError(
                    f"kill timed out: {session_id}; the interrupt request was not delivered",
                    next_action=KILL_NOT_DELIVERED_NEXT_ACTION,
                ) from exc
            if interrupt_requested:
                await session.drain_registration()
                return await self._kill_result_response_async(session, kill_requested=True, stop=stop)
        else:
            expired_response = await self._expired_kill_response(session_id)
            if expired_response is not None:
                return await self._stop_after_terminal_response(session_id, expired_response, stop)
            session = await self._get_session(session_id)
        if observed_session is session and observed_turn is not None and observed_turn[0] == session.turn_seq:
            _, started_terminal, requested_before_call = observed_turn
        else:
            started_terminal = session.terminal
            requested_before_call = session.interrupt_requested
        if started_terminal:
            return await self._kill_result_response_async(session, kill_requested=False, stop=stop)

        requested = requested_before_call
        backend = self._backend(session.engine)
        try:
            await asyncio.wait_for(
                session.turn_control_lock.acquire(),
                timeout=max(0.0, delivery_deadline - loop.time()),
            )
        except TimeoutError as exc:
            raise ActionableTimeoutError(
                f"kill timed out: {session_id}; the interrupt request was not delivered",
                next_action=KILL_NOT_DELIVERED_NEXT_ACTION,
            ) from exc
        try:
            if session.terminal:
                requested = requested or requested_before_call or session.interrupt_requested
            elif session.interrupt_requested:
                requested = True
            else:
                if backends.interrupt_requires_turn_id(session.engine) and not session.turn_id:
                    if timeout == 0:
                        raise ActionableError(
                            "the active Codex turn has no turn_id",
                            next_action="`timeout`へ正の秒数を指定して`kill`を再発行するか、`atk agents wait`で終了を観測する",
                        )
                    assert deadline is not None
                    try:
                        async with self._condition:
                            await asyncio.wait_for(
                                self._condition.wait_for(lambda: bool(session.turn_id) or session.terminal),
                                timeout=max(0.0, deadline - loop.time()),
                            )
                    except TimeoutError as exc:
                        raise ActionableTimeoutError(
                            f"kill timed out: {session_id}; the interrupt request was not delivered",
                            next_action=KILL_NOT_DELIVERED_NEXT_ACTION,
                        ) from exc
                    if session.terminal:
                        await session.drain_registration()
                        return await self._kill_result_response_async(session, kill_requested=False, stop=stop)
                session.interrupt_requested = True
                session.touch()
                try:
                    await asyncio.wait_for(
                        backend.interrupt(session),
                        timeout=max(0.0, delivery_deadline - loop.time()),
                    )
                except TimeoutError:
                    session.interrupt_requested = False
                    session.touch()
                    await self._notify_waiters()
                    raise ActionableTimeoutError(
                        f"kill timed out: {session_id}; interrupt delivery is undetermined",
                        next_action=KILL_NOT_DELIVERED_NEXT_ACTION,
                    ) from None
                except Exception:
                    session.interrupt_requested = False
                    session.touch()
                    await self._notify_waiters()
                    raise
                requested = True
        finally:
            session.turn_control_lock.release()

        if not requested:
            await session.drain_registration()
            return await self._kill_result_response_async(session, kill_requested=False, stop=stop)
        if timeout > 0:
            assert deadline is not None
            try:
                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: session.result_available),
                        timeout=max(0.0, deadline - loop.time()),
                    )
            except TimeoutError as exc:
                raise ActionableTimeoutError(
                    f"kill timed out: {session_id}; the interrupt request was delivered but the turn did not terminate",
                    next_action="中断要求は配送済みのため`kill`を再発行しない。`atk agents wait`で終了を観測する",
                ) from exc
        await session.drain_registration()
        return await self._kill_result_response_async(session, kill_requested=True, stop=stop)

    async def close(self) -> None:
        task = self._close_task
        if task is None or task.done() and (task.cancelled() or task.exception() is not None):
            task = self._close_task = asyncio.create_task(self._close())
        await finish_task(task)

    async def _close(self) -> None:
        remove_terminal_listener(self._schedule_session_expiry)
        remove_lifecycle_listener(self._cancel_expiry_on_start)
        for timer in self._retention_timers.values():
            timer.cancel()
        self._retention_timers.clear()
        # 監視・記録taskや他backendの待機が停止要求の配送を妨げないよう、先に全backendを開始する。
        backend_tasks = [asyncio.create_task(backend.close()) for backend in tuple(self._backends.values())]
        errors: list[Exception] = []
        if self._resource_release_tasks:
            outcomes = await lifetime.wait_for_shutdown(self._resource_release_tasks.values())
            errors.extend(outcome for outcome in outcomes if isinstance(outcome, Exception))
            self._resource_release_tasks.clear()
        if self._auto_resume_task is not None:
            self._auto_resume_task.cancel()
            await lifetime.wait_for_shutdown((self._auto_resume_task,))
            self._auto_resume_task = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await lifetime.wait_for_shutdown((self._heartbeat_task,))
            self._heartbeat_task = None
        pending_resumes = tuple(self._pending_resumes.values())
        for pending in pending_resumes:
            pending.discard_previous_result()
        resume_tasks = tuple(pending.task for pending in pending_resumes)
        for task in resume_tasks:
            if not task.done():
                task.cancel()
        if resume_tasks:
            await lifetime.wait_for_shutdown(resume_tasks)
        try:
            # 1つのbackendの停止失敗で、他のbackendの停止と後段の結果公開・listener解除・writer停止を止めない。
            if backend_tasks:
                outcomes = await lifetime.wait_for_shutdown(backend_tasks)
                errors.extend(outcome for outcome in outcomes if isinstance(outcome, Exception))
            try:
                await self._publish_closed_sessions()
            except status_file.ProjectionWriteError:
                _LOG.exception("停止時の表示用状態保存に失敗しました。終了時の結果を保持して後始末を続けます")
            except Exception as error:
                errors.append(error)
        finally:
            remove_terminal_listener(self._carry_over_unavailable_candidate)
            remove_lifecycle_listener(self._record_resources)
            try:
                await self._drain_diagnostics()
            except Exception as error:
                errors.append(error)
            try:
                await self._drain_candidate_records()
            except Exception as error:
                if errors:
                    errors[0].add_note(f"候補除外記録の保存にも失敗しました: {error}")
                else:
                    errors.append(error)
            finally:
                if self._status_writer is not None:
                    remove_touch_listener(self._status_writer.schedule)
                    try:
                        await self._status_writer.drain(cancel_scheduled=True)
                    except Exception as error:
                        errors.append(error)
                    finally:
                        # 保存taskの回収に失敗しても予約を解除し、所有する状態ファイルを残さない。
                        try:
                            await self._status_writer.deactivate_async()
                        except Exception as error:
                            errors.append(error)
        if errors:
            for error in errors[1:]:
                errors[0].add_note(f"停止時の別の失敗: {error!r}")
            raise errors[0]
        await run_file_operation(logging_config.flush_logging)
