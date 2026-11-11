"""委譲先sessionの開始、継続、中断と停止を受け付け、engine別のbackendと共有のsession状態を管理する。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Self

from agent_toolkit._agents_server import (
    backends,
    codex_resume_state,
    launch_requests,
    lifetime,
    logging_config,
    manager_start,
    result_projection,
    state,
    status_file,
)
from agent_toolkit._agents_server.file_operations import finish_task, run_file_operation
from agent_toolkit._agents_server.input_validation import validate_prompt
from agent_toolkit._agents_server.launch_requests import wrap_delivery_body
from agent_toolkit._agents_server.responses import KILL_NOT_DELIVERED_NEXT_ACTION
from agent_toolkit._agents_server.session_errors import ActionableTimeoutError, SessionOwnerGoneError
from agent_toolkit._agents_server.state import (
    SessionResumeState,
    SessionState,
    remove_lifecycle_listener,
    remove_terminal_listener,
    remove_touch_listener,
)
from agent_toolkit._common import session_release
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


DEFAULT_KILL_TIMEOUT = 270.0


DEFAULT_SEND_MESSAGE_TIMEOUT = 270.0


REPLY_DELIVERIES = frozenset({"reply_started", "reply_failed", "reply_ambiguous"})


START_AVAILABILITY_TIMEOUT = state.START_AVAILABILITY_TIMEOUT


class AgentsServerManager(manager_start.ManagerStart):  # pylint: disable=too-many-ancestors  # 責務別のmixinへ分割したため。
    """エンジン別バックエンドと共有session状態を管理する。"""

    _close_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exception: object) -> None:
        await self.close()

    def activate(self) -> None:
        """状態ファイル出力を有効化する。"""
        self._auto_resume_task = asyncio.create_task(self._monitor_auto_resume())
        if self._status_writer is not None:
            self._status_writer.activate()
            self._heartbeat_task = asyncio.create_task(self._refresh_heartbeat())

    async def activate_async(self) -> None:
        """初回保存を待ってから常駐監視を開始する。"""
        if self._status_writer is not None:
            await self._status_writer.activate_async()
            self._heartbeat_task = asyncio.create_task(self._refresh_heartbeat())
        self._auto_resume_task = asyncio.create_task(self._monitor_auto_resume())

    async def _monitor_auto_resume(self) -> None:
        """公開待機操作が呼ばれるかによらず、孫session終了後の自動再開を進める。"""
        while True:
            await session_release.process_requests(
                self.sessions,
                self.stop,
                state_root=None if self._status_writer is None else self._status_writer.state_root,
            )
            awaiting = [session for session in self.sessions.values() if session.awaiting_auto_resume]
            for session in awaiting:
                await self._advance_child_session_wait(session)
            await asyncio.sleep(0.1 if awaiting else 1.0)

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
        """実行中turnを継続し、終了済みなら同じsessionでreplyを開始する。

        状態ファイルの書込主体を持つ場合は、`start`・`list`と同じく`root_session_id`を応答へ加える。
        再起動した会話が既存sessionへの`send_message`から再開しても、PostToolUseがこの値で別名索引を書けるようにするためである。
        """
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
        response = await self._deliver_message(session_id, prompt, timeout)
        session = self.sessions.get(session_id)
        if session is not None and response["delivery"] != "reply_failed":
            session.followup_prompts += (prompt,)
            session.touch()
            await session.drain_registration()
        if self._status_writer is not None:
            if session is not None:
                # 開始時と同じbest-effort公開で、配送結果と同じsessionの待機登録を保つ。
                await self._save_started_session(session)
            response = {**response, "root_session_id": self._status_writer.root_session_id}
        if registry_resolved:
            response = self._response_with_notices(dict(response), await self._take_registry_notices(session_id))
        return response

    async def _deliver_message(self, session_id: str, prompt: str, timeout: float) -> dict[str, Any]:
        """`send_message`の配送本体。応答の`root_session_id`は呼び出し元が加える。"""
        validate_prompt(prompt)
        await self._wait_for_resource_release(session_id)
        if self._status_writer is not None:
            await self._status_writer.drain()
        await self.take_over_orphaned_session(session_id)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ActionableError(
                "timeout must be positive", next_action="`timeout`を省略して270秒で待つか、正の秒数を指定する"
            )
        prompt = wrap_delivery_body(prompt)
        try:
            async with asyncio.timeout(float(timeout)):
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
                            previous_result = None
                            if stopped_state is not None:
                                previous_result = await self._take_stopped_result(
                                    session_id,
                                    stopped_state,
                                    collector="send-message",
                                )
                            elif (
                                self._status_writer is None
                                or (await self._status_writer.result_state_async(session_id)) != "consumed"
                            ):
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
                    try:
                        result = await self._send_with_resources(session, prompt)
                    except SessionOwnerGoneError:
                        async with self._resume_lock:
                            if self.sessions.get(session_id) is not session:
                                continue
                            previous_result = session.previous_result() if session.result_available else None
                            self._expire_session(session_id)
                            resume_state = self.expired_sessions[session_id]
                            return await self._resume_and_reply(
                                resume_state,
                                prompt,
                                previous_result,
                            )
                    if self._status_writer is not None:
                        await self._status_writer.delete_result_async(session_id, collector="send-message")
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
                        previous_result = result["previous_result"]
                        if previous_result:
                            response["previous_result"] = previous_result
                    return response
        except TimeoutError as exc:
            raise ActionableTimeoutError(
                f"send_message timed out: {session_id}; delivery is undetermined",
                next_action="`atk agents wait`で状態を確認し、指示が届いていないと判断した場合だけ`send_message`を再送する",
            ) from exc

    async def _kill_result_response_async(self, session: SessionState, *, kill_requested: bool) -> dict[str, Any]:
        """結果の回収をイベントループ上で確定し、競合する保存処理の終了と結果ファイルの削除を待ってから、killの応答本文を返す。"""
        response = await self._result_response_async(session, include_progress=False)
        response["kill_requested"] = kill_requested
        response = self._response_with_notices(response, await self._take_notices_async(session.session_id))
        if "agent_message" in response and self._status_writer is not None:
            await self._status_writer.delete_result_async(session.session_id, collector="kill")
        return response

    async def kill(
        self,
        session_id: str,
        timeout: float = DEFAULT_KILL_TIMEOUT,
        stop: bool = False,
    ) -> dict[str, Any]:
        """実行中turnへ中断を要求し、指定時間まで終了を待つ。"""
        await self._drain_diagnostics()
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
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            raise ActionableError(
                "timeout must be non-negative", next_action="`timeout`を省略して270秒で待つか、0以上の秒数を指定する"
            )
        stopped_state = await self._resolve_stopped_session(session_id)
        recovered_state: SessionResumeState | None = None
        if stopped_state is None and session_id not in self.sessions and session_id not in self._pending_resumes:
            await self.take_over_orphaned_session(session_id)
            owner_lost_response = await self._kill_owner_lost(session_id)
            if owner_lost_response is not None:
                return await self._stop_after_terminal_response(session_id, owner_lost_response, stop)
        if stopped_state is None:
            recovered_state = await self._restore_registry_session(session_id)
            stopped_state = recovered_state
        if recovered_state is not None:
            response = self._recovered_result_response(recovered_state)
            response["kill_requested"] = False
            return self._response_with_notices(response, await self._take_registry_notices(session_id))
        if stopped_state is not None:
            stopped_response = await self._take_stopped_result(session_id, stopped_state, collector="kill")
            if stopped_response is not None:
                stopped_response["kill_requested"] = False
                notices = await self._take_notices_async(session_id)
                return self._response_with_notices(stopped_response, notices)
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
                response = await self._kill_result_response_async(session, kill_requested=True)
                return await self._stop_after_terminal_response(session_id, response, stop)
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
            response = await self._kill_result_response_async(session, kill_requested=False)
            return await self._stop_after_terminal_response(session_id, response, stop)

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
                        response = await self._kill_result_response_async(session, kill_requested=False)
                        return await self._stop_after_terminal_response(session_id, response, stop)
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
            response = await self._kill_result_response_async(session, kill_requested=False)
            return await self._stop_after_terminal_response(session_id, response, stop)
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
        response = await self._kill_result_response_async(session, kill_requested=True)
        return await self._stop_after_terminal_response(session_id, response, stop)

    async def close(self) -> None:
        """初期化済みバックエンドを停止する。"""
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
