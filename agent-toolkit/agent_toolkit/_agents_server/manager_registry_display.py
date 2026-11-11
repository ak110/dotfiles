"""保持中・復元済みsessionの一覧と詳細表示。"""

from __future__ import annotations

import asyncio
from typing import Any

from agent_toolkit._agents_server import manager_registry_restore, owner_lost, result_projection, state
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.manager_registry_restore import _RegistryResumeState
from agent_toolkit._agents_server.responses import listed_public_session
from agent_toolkit._agents_server.state import TERMINAL_STATUSES, SessionResumeState, SessionState, has_uncollected_result
from agent_toolkit._common import session_registry
from agent_toolkit._common.next_action import ActionableError


class ManagerRegistryDisplay(manager_registry_restore.ManagerRegistryRestore):
    """状態と活動の公開項目を一覧・詳細へ射影する。"""

    async def list_sessions(self, *, include_terminated: bool = False) -> dict[str, Any]:
        """保持中のsessionを開始時刻順の公開項目へ射影する。

        一覧の範囲を指定しない場合も、未回収結果を持つsessionは終了済みまたは期限切れでも表示する。
        """
        loop_time = asyncio.get_running_loop().time()
        for session_id, session in tuple(self.sessions.items()):
            if session.retention_deadline is not None and loop_time >= session.retention_deadline:
                self._expire_session(session_id)

        listed: dict[str, tuple[str | None, dict[str, Any]]] = {}
        for session in self.sessions.values():
            listed[session.session_id] = (
                session.started_at,
                self._listed_session(
                    session,
                    status=session.status,
                    progress=session.progress,
                    result_available=has_uncollected_result(
                        session,
                        None
                        if self._status_writer is None
                        else (await self._status_writer.result_state_async(session.session_id)) == "consumed",
                    ),
                ),
            )
        for pending in self._pending_resumes.values():
            session = pending.state
            listed.setdefault(
                session.session_id,
                (
                    session.started_at,
                    self._listed_session(session, status="running", progress="", result_available=False),
                ),
            )
        for previous in tuple(self.expired_sessions.values()):
            try:
                session = await self._refresh_resume_state(previous)
            except ActionableError:
                continue
            listed.setdefault(
                session.session_id,
                (
                    session.started_at,
                    self._listed_session(
                        session,
                        status="expired" if session.session_id in self.expired_sessions else session.status,
                        progress="",
                        result_available=has_uncollected_result(
                            session,
                            None
                            if self._status_writer is None
                            else (await self._status_writer.result_state_async(session.session_id)) == "consumed",
                        ),
                    ),
                ),
            )
        for previous in tuple(self.stopped_sessions.values()):
            try:
                session = await self._refresh_resume_state(previous)
            except ActionableError:
                continue
            if (
                await run_file_operation(session_registry.resolve, session.session_id)
            ).state is not session_registry.Resolution.TERMINAL:
                continue
            listed.setdefault(
                session.session_id,
                (
                    session.started_at,
                    self._listed_session(
                        session,
                        status=session.status,
                        progress="",
                        result_available=has_uncollected_result(
                            session,
                            None
                            if self._status_writer is None
                            else (await self._status_writer.result_state_async(session.session_id)) == "consumed",
                        ),
                    ),
                ),
            )
        sessions = [entry for _, entry in sorted(listed.values(), key=lambda item: (item[0] is None, item[0] or ""))]
        if include_terminated:
            response: dict[str, Any] = {
                "sessions": [listed_public_session(session) for session in sessions],
            }
        else:
            visible = [
                session
                for session in sessions
                if session["result_available"] or session["status"] not in TERMINAL_STATUSES | {"expired"}
            ]
            response = {"sessions": [listed_public_session(session) for session in visible]}
            omitted = len(sessions) - len(visible)
            if omitted:
                response["omitted"] = omitted
        if self._status_writer is not None:
            response["root_session_id"] = self._status_writer.root_session_id
        return response

    def status_owner(self) -> tuple[str | None, str | None]:
        """このManagerが状態ファイルを書くルートsession識別子と書込主体を返す。"""
        writer = self._status_writer
        return (None, None) if writer is None else (writer.root_session_id, writer.path.name)

    async def _interrupted_turns(self, session_id: str) -> list[dict[str, Any]]:
        """登録簿が記録する中断turnの履歴を、診断参照付きの公開形で返す。読めない記録は空とする。"""
        try:
            resolution = await run_file_operation(session_registry.resolve, session_id)
        except OSError, ValueError:
            return []
        return result_projection.interrupted_turns_projection(resolution.interrupted_turns)

    async def show_session(self, session_id: str, *, verbose: bool = False) -> dict[str, Any]:
        """保持中または再開可能なsessionの復旧用詳細を返す。

        自プロセスの保持状態に無い識別子は、共有の登録簿に記録されているか確かめて在否を判定する。
        そのsessionを別のMCPサーバープロセスが実行中である場合と、登録簿にレコードが無い場合を
        区別せずに喪失として案内すると、照会した主体が新しいsessionの起動へ進む。
        """
        session: SessionState | SessionResumeState | None = self.sessions.get(session_id)
        if session is None and session_id in self._pending_resumes:
            session = self._pending_resumes[session_id].state
        if session is None:
            session = self.expired_sessions.get(session_id) or self.stopped_sessions.get(session_id)
            if session is not None:
                session = await self._refresh_resume_state(session)
        registry_resolved = False
        if session is None:
            resolution = await run_file_operation(session_registry.resolve, session_id)
            if resolution.state is session_registry.Resolution.RUNNING:
                diagnosis = await self._diagnose_owner_lost(session_id, resolution)
                if diagnosis is not None:
                    return await self._owner_lost_show_response(diagnosis, verbose=verbose)
                raise ActionableError(
                    f"session {session_id} belongs to another writer's agents_server process and is still running",
                    next_action="そのsessionを起動したroot sessionで`atk agents wait`を実行して結果を受領する",
                )
            session = await self._restore_registry_session(session_id, resolution=resolution)
            if session is None:
                raise await self._unresolved_session_error(session_id, label="session", resolution=resolution)
            registry_resolved = True
        if session is None:
            raise await self._unresolved_session_error(session_id, label="session")
        status = "running" if session_id in self._pending_resumes else session.status
        result_available = bool(
            isinstance(session, SessionState) and session.result_available and not session.result_delivered
        ) or bool(
            isinstance(session, SessionResumeState) and session.status in TERMINAL_STATUSES and not session.result_delivered
        )
        response: dict[str, Any] = {
            "session_id": session.session_id,
            "status": status,
            "launch_kind": session.launch_kind,
            "model_type": session.model_type,
            "label": session.label,
            "prompt": session.prompt,
            "cwd": session.cwd,
            "result_available": result_available,
        }
        activity = result_projection.activity_projection(
            updated_at=session.updated_at,
            output_updated_at=session.output_updated_at,
            started_at=session.started_at,
            api_error=getattr(session, "api_error", None) if status == "running" else None,
        )
        response.update(activity)
        if isinstance(session, SessionState):
            diagnostics = session.claude_connection_diagnostics + session.claude_mcp_diagnostics
            if diagnostics:
                response["warnings"] = list(diagnostics)
        if status in TERMINAL_STATUSES and result_projection.nonempty_error(session.error):
            # 失敗で終了したsessionの原因を、結果の回収とは別に照会できるようにする。
            response["error"] = result_projection.refresh_error_diagnostics(session.error)
        interrupted_turns = await self._interrupted_turns(session_id)
        if interrupted_turns:
            # 再開・解放の後も、過去に中断したturnと終了診断を識別できるようにする。
            response["interrupted_turns"] = interrupted_turns
        if status == "running" and isinstance(session, SessionState):
            active_tool_uses = session.active_tool_uses()
            if active_tool_uses:
                response["active_tool_uses"] = active_tool_uses
        if status == "running" and isinstance(session, SessionState) and session.live_child_session_ids:
            # 委譲元がその識別子へ`send_message`と`kill`を発行できるよう、許可判定の入力となる`cwd`を併記する。
            # `cwd`を解決できない識別子は、対を持たない側の項目として区別できる形で返す。
            resolved: list[dict[str, str]] = []
            unresolved: list[str] = []
            for child_session_id in sorted(session.live_child_session_ids):
                try:
                    resume_info = (await run_file_operation(session_registry.resolve, child_session_id)).resume_info
                except Exception:
                    unresolved.append(child_session_id)
                    continue
                child_cwd = resume_info.cwd if resume_info is not None else ""
                if child_cwd:
                    resolved.append({"session_id": child_session_id, "cwd": child_cwd})
                else:
                    unresolved.append(child_session_id)
            response["live_child_sessions"] = resolved
            if unresolved:
                response["live_child_session_ids_without_cwd"] = unresolved
        if status == "running" and isinstance(session, SessionState) and session.awaiting_auto_resume:
            # モデルのturnは終わり、バックグラウンドタスクか孫sessionの終了、
            # または過負荷後の継続と利用上限の解除の待機のために結果を保留している。
            # 活動時刻が進まないため、委譲元が停滞と区別できるよう保留と待機対象を公開する。
            response["result_held"] = True
            if session.live_tasks:
                response["live_background_tasks"] = [
                    {
                        "task_id": task_id,
                        "task_type": task.task_type,
                        "description": task.description,
                        "seconds_since_start": result_projection.elapsed_seconds(task.started_at),
                    }
                    for task_id, task in sorted(session.live_tasks.items())
                ]
        if verbose:
            response.update(
                engine=session.engine,
                model=session.model,
                effort=session.effort,
                started_at=session.started_at,
                turn_seq=session.turn_seq,
                updated_at=session.updated_at,
                output_updated_at=session.output_updated_at,
            )
            response.update(state.fast_mode_fields(session.engine, session.fast_mode))
            if self._status_writer is not None:
                response["root_session_id"] = self._status_writer.root_session_id
        if registry_resolved or isinstance(session, _RegistryResumeState):
            # 自プロセスが起動していないsessionの通知は、委譲元のサーバーが替わると旧ルートに残るため併せて回収する。
            response = self._response_with_notices(response, await self._take_registry_notices(session_id))
        return response

    async def _owner_lost_show_response(self, diagnosis: owner_lost.OwnerLost, *, verbose: bool) -> dict[str, Any]:
        """所有サーバーを失った実行中のsessionを、登録簿の起動情報と診断・案内付きの実行中として返す。"""
        session_id = diagnosis.session_id
        info = diagnosis.resolution.resume_info
        assert info is not None
        response: dict[str, Any] = {
            "session_id": session_id,
            "status": "running",
            "launch_kind": info.launch_kind,
            "model_type": info.model_type,
            "label": info.launch_info.label,
            "prompt": info.launch_info.prompt,
            "cwd": info.cwd,
            "result_available": False,
        }
        response.update(
            result_projection.activity_projection(
                updated_at=info.session_updated_at,
                output_updated_at=None,
                started_at=info.started_at,
                api_error=None,
            )
        )
        response.update(diagnosis.diagnostics())
        response["next_action"] = owner_lost.OWNER_LOST_NEXT_ACTION
        interrupted_turns = result_projection.interrupted_turns_projection(diagnosis.resolution.interrupted_turns)
        if interrupted_turns:
            response["interrupted_turns"] = interrupted_turns
        if verbose:
            response.update(
                engine=info.engine,
                model=info.model,
                effort=info.effort,
                started_at=info.started_at,
                turn_seq=info.turn_seq,
                updated_at=info.session_updated_at,
            )
            response.update(state.fast_mode_fields(info.engine, info.fast_mode))
            if self._status_writer is not None:
                response["root_session_id"] = self._status_writer.root_session_id
        return self._response_with_notices(response, await self._take_registry_notices(session_id))
