from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any

from agent_toolkit._agents_server import logging_config, manager_registry_display, result_projection, shared_layout
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.responses import RUNNING_STOP_NEXT_ACTION, SESSION_ID_NEXT_ACTION
from agent_toolkit._agents_server.session_errors import ActionableRuntimeError
from agent_toolkit._agents_server.state import TERMINAL_STATUSES, RegistryResumeState, SessionResumeState, SessionState
from agent_toolkit._atk.orchestrator import PROCESS_LOOP_SESSION_ENV
from agent_toolkit._common import runtime_environment, session_record_paths, session_registry, session_review, task_documents
from agent_toolkit._common.next_action import ActionableError, next_action_line

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


def _register_stopped_lane(state: SessionResumeState, parent: str | None) -> None:
    if parent is None or runtime_environment.environment().get(PROCESS_LOOP_SESSION_ENV) != "1":
        return
    role = task_documents.launched_task_document(state.prompt)
    if role is None or role.name not in {"exec.subagent.md", "repository-process.subagent.md"}:
        return
    summary = state.agent_message.strip().partition("\n")[0]
    if state.status != "completed" or not summary or (role.name == "exec.subagent.md" and summary != "統合完了"):
        return
    owner = session_record_paths.find_session_record(parent)
    if owner is None or owner.engine not in {"claude", "codex"}:
        return
    lane_record = session_record_paths.find_session_record(state.session_id)
    if lane_record is None or len(lane_record.paths) != 1 or lane_record.engine != state.engine:
        raise ValueError("振り返りへ登録するレーンのセッション記録を一意に特定できない")
    session_review.register_lane(
        state.session_id, state.cwd, parent, owner.engine, lane_record.paths[0], state.started_at, summary[:240]
    )


class ManagerRegistry(manager_registry_display.ManagerRegistryDisplay):
    async def stop(self, session_id: str, *, retain_result: bool = False) -> dict[str, Any]:
        """終了済みsessionを破棄し、会話再開用の最小状態だけを保持する。

        `stopped_sessions`への在籍は、このプロセスが解放すべきbackend資源を
        持たないことを表す。解放後の同期失敗では再発行が同期だけを再試行する。
        """
        await self._drain_diagnostics()
        if not isinstance(session_id, str) or not session_id:
            raise ActionableError("session_id must be a non-empty string", next_action=SESSION_ID_NEXT_ACTION)
        if session_id in self._pending_resumes:
            raise ActionableError(f"session is running: {session_id}", next_action=RUNNING_STOP_NEXT_ACTION)
        current = self.sessions.get(session_id)
        if current is not None:
            await current.drain_registration()
        await self._drain_candidate_records()
        if self._status_writer is not None:
            await self._status_writer.drain()
        previous = self.stopped_sessions.get(session_id)
        stopped_state = await self._resolve_stopped_session(session_id)
        if not retain_result and previous is not None:
            # 現在の稼働状態を確認しても、再試行する破棄の対象は元の所有者・turnのままとする。
            stopped_state = previous
        if stopped_state is None:
            stopped_state = await self._restore_registry_session(session_id)
        already_stopped = session_id in self.stopped_sessions
        session = self.sessions.get(session_id)
        if session is None and isinstance(stopped_state, RegistryResumeState):
            live = await run_file_operation(_foreign_live_processes, session_id)
            if live:
                raise ActionableError(
                    f"session {session_id} is recorded by processes still alive outside this agents_server: "
                    + ",".join(str(process.pid) for process in live),
                    next_action=(
                        "そのsessionを起動したroot sessionのagents_serverで`stop`する。"
                        "実行中なら`atk agents wait`で終了を待つか、`kill`で委譲先CLIを止めてから`stop`する"
                    ),
                )
        if (
            session is not None
            and session.retention_deadline is not None
            and asyncio.get_running_loop().time() >= session.retention_deadline
        ):
            self._expire_session(session_id)
            session = None
        if session is not None:
            if not session.terminal:
                raise ActionableError(f"session is running: {session_id}", next_action=RUNNING_STOP_NEXT_ACTION)
            resume_state = SessionResumeState.from_session(session)
        else:
            resume_state = stopped_state or self.expired_sessions.get(session_id)
            if resume_state is None:
                raise await self._unresolved_session_error(session_id, label="session")
        result_state = (
            None
            if not retain_result or self._status_writer is None
            else (await self._status_writer.result_state_async(session_id, session=resume_state))
        )
        registration = await run_file_operation(session_registry.resolve, session_id)
        if (
            not retain_result
            and isinstance(resume_state, RegistryResumeState)
            and registration.owner_root_session_id is not None
            and resume_state.result_root_session_id != registration.owner_root_session_id
            and self._status_writer is not None
        ):
            source = await run_file_operation(
                self._status_writer.stopped_result_writer, session_id, registration.owner_root_session_id
            )
            resume_state = dataclasses.replace(
                resume_state, result_root_session_id=source.root_session_id, result_owner_status_file=source.file_name
            )
        expected_owner = (
            registration.server_instance,
            None if registration.recorded_turn_seq is None else resume_state.turn_seq,
        )
        keep_result = (
            retain_result
            and not resume_state.result_delivered
            and resume_state.finalized_at is not None
            and result_state != "consumed"
        )
        resume_state = dataclasses.replace(resume_state, result_delivered=not keep_result)
        if session is not None:
            session.result_delivered = not keep_result
        if not already_stopped:
            await self._wait_for_resource_release(session_id)
            if session is not None:
                await self._backend(resume_state.engine).release_session(session)
        self._cancel_retention_timer(session_id)
        self.sessions.pop(session_id, None)
        self.expired_sessions.pop(session_id, None)
        self.stopped_sessions[session_id] = resume_state
        try:
            if session is not None:
                await session.drain_registration()
            released = await run_file_operation(
                session_registry.release, session_id, reason="stopped", expected_owner=expected_owner
            )
            if released and not retain_result:
                parent = registration.owner_root_session_id
                writer = self._status_writer
                if writer is not None and (parent is None or parent == writer.root_session_id):
                    parent = await writer.launcher_session_id_async() or parent
                await run_file_operation(_register_stopped_lane, resume_state, parent)
            if released and self._status_writer is not None:
                if retain_result and not keep_result:
                    (await run_file_operation(self._status_writer.delete_wait_target, session_id))
                if not retain_result:
                    await self._status_writer.discard_stopped_session_async(resume_state, expected_owner=expected_owner)
                elif keep_result and result_state == "unpublished":
                    await self._status_writer.retain_result_async(resume_state)
                elif not keep_result and resume_state.finalized_at is not None:
                    if isinstance(resume_state, RegistryResumeState) and resume_state.result_root_session_id is not None:
                        await self._claim_resume_result(resume_state, {}, collector="stop")
                    else:
                        await self._status_writer.delete_result_async(
                            session_id, collector="stop", expected_end=(resume_state.turn_seq, resume_state.finalized_at)
                        )
                self._status_writer.schedule()
        except Exception as exc:
            raise ActionableRuntimeError(
                f"backend resources released; state synchronization incomplete for session {session_id}: {exc}",
                next_action=(
                    "資源は解放済みのため、同じ`session_id`へ`stop`を再発行してよい（状態の同期だけを再試行する）。"
                    "結果は`list`で確認する"
                ),
            ) from exc
        if not released:
            self.stopped_sessions.pop(session_id, None)
            if self._status_writer is not None:
                self._status_writer.schedule()
            raise ActionableError(
                f"停止対象の所有者またはturnが現在の登録簿と一致しないため、現在の記録と結果を保持した: {session_id}",
                next_action="同じ`session_id`を`show`で再確認し、実行中なら`atk agents wait`で終了を観測する",
            )
        return {}

    async def _stop_after_terminal_response(
        self,
        session_id: str,
        response: dict[str, Any],
        stop: bool,
    ) -> dict[str, Any]:
        if stop and response.get("status") in {*TERMINAL_STATUSES, "expired"} and session_id not in self.stopped_sessions:
            await self.stop(session_id, retain_result=True)
        return response

    async def _publish_closed_sessions(self) -> None:
        interrupted: list[SessionState] = []
        for session in tuple(self.sessions.values()):
            if not session.publish_registry or session.result_available or session.result_delivered:
                continue
            executor = session.executor_process
            if not session.terminal and executor is not None and await run_file_operation(executor.exists):
                # backendの停止で委譲先CLIの終了を確かめられないturnは進行中の可能性があるため、記録を実行中のまま残す。
                # 登録簿は委譲先CLIの実在から実行中と解決し、後続のMCPサーバーは所有サーバーを失った実行中として扱う。
                _LOG.warning(
                    "停止後も委譲先CLIが残るため中断を公開しません: session_id=%s pid=%d",
                    session.session_id,
                    executor.pid,
                )
                continue
            if not session.terminal:
                session.status = "interrupted"
                session.error = result_projection.interruption_error(
                    session_registry.OWNER_SERVER_STOPPED,
                    server_instance=session_registry.owner_server_instance(),
                    turn_seq=session.turn_seq,
                )
                interrupted.append(session)
            session.record_turn_completed()
            session.clear_hold()
            session.interrupt_requested = False
            session.touch()
        if self._status_writer is not None:
            await self._status_writer.flush_async()
        for session in tuple(self.sessions.values()):
            await session.drain_registration()
        if interrupted:
            logging_config.record_instance_event(
                "sessions_interrupted",
                sessions=[{"session_id": session.session_id, "turn_seq": session.turn_seq} for session in interrupted],
            )
        for session in interrupted:
            try:
                await run_file_operation(
                    session_registry.record_interrupted_turn,
                    session.session_id,
                    turn_seq=session.turn_seq,
                    reason=session_registry.OWNER_SERVER_STOPPED,
                )
            except OSError:
                _LOG.exception(
                    "中断turnの履歴を登録簿へ保存できませんでした: session_id=%s。"
                    "この中断の過去履歴（interrupted_turns）が欠けるだけで、停止処理は続く。%s",
                    session.session_id,
                    next_action_line("登録簿ファイルの書込権限と状態ディレクトリの空き容量を確認してください。"),
                )


def _foreign_live_processes(session_id: str) -> list[session_registry.ProcessIdentity]:
    if not shared_layout.valid_session_id(session_id):
        return []
    resolution = session_registry.resolve(session_id)
    own = session_registry.owner_process()
    candidates = [resolution.owner_process, *resolution.executor_processes]
    return [process for process in candidates if process is not None and process != own and process.exists()]
