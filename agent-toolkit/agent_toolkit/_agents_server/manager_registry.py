"""managerが保持するsessionと、保持しないsessionのsession登録簿からの解決、一覧・表示・停止の応答。"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Sequence
from typing import Any, Literal, cast

from agent_toolkit._agents_server import (
    backends,
    responses,
    result_projection,
    session_registry,
    shared_layout,
    state,
    status_file,
)
from agent_toolkit._agents_server.responses import (
    EXPIRED_KILL_NEXT_ACTION,
    EXPIRED_SESSION_NEXT_ACTION,
    RUNNING_STOP_NEXT_ACTION,
    SESSION_ID_NEXT_ACTION,
    listed_public_session,
)
from agent_toolkit._agents_server.session_errors import ActionableRuntimeError
from agent_toolkit._agents_server.state import TERMINAL_STATUSES, SessionResumeState, SessionState, has_uncollected_result
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


def _terminal_turn_status(
    turns: Sequence[tuple[str, str]], turn_id: str | None
) -> Literal["completed", "failed", "interrupted"] | None:
    """`thread/read`のturn一覧から、元turnが終端して後続の進行中turnが無い場合の終端状態を返す。

    元turnの識別子を持たない旧形式の記録では、全turnが終端している場合に最後のturnの状態を返す。
    """
    if turn_id is None:
        if not turns or any(status not in TERMINAL_STATUSES for _, status in turns):
            return None
        return cast(Literal["completed", "failed", "interrupted"], turns[-1][1])
    index = next((position for position, (identifier, _) in enumerate(turns) if identifier == turn_id), None)
    if index is None or turns[index][1] not in TERMINAL_STATUSES:
        return None
    if any(status not in TERMINAL_STATUSES for _, status in turns[index + 1 :]):
        return None
    return cast(Literal["completed", "failed", "interrupted"], turns[index][1])


class ManagerRegistry(responses.ManagerResponses):
    """保持するsessionと、保持期限切れ・停止済み・登録簿のsessionを解決し、一覧・表示・停止に応答する。"""

    def _get_session(self, session_id: str) -> SessionState:
        """保持中のsessionを返し、未解決値は識別子体系と喪失に分けて診断する。"""
        if not isinstance(session_id, str) or not session_id:
            raise ActionableError("session_id must be a non-empty string", next_action=SESSION_ID_NEXT_ACTION)
        try:
            session = self.sessions[session_id]
        except KeyError as exc:
            if session_id in self.expired_sessions:
                raise ActionableError(
                    f"session retention expired: {session_id}", next_action=EXPIRED_SESSION_NEXT_ACTION
                ) from exc
            raise self._unresolved_session_error(session_id, label="session") from exc
        if session.retention_deadline is not None and asyncio.get_running_loop().time() >= session.retention_deadline:
            self._expire_session(session_id)
            raise ActionableError(f"session retention expired: {session_id}", next_action=EXPIRED_SESSION_NEXT_ACTION)
        return session

    def _resolve_expired_session(self, session_id: str) -> SessionResumeState | None:
        """保持期限を反映し、期限切れsessionの再開状態を返す。"""
        if not isinstance(session_id, str) or not session_id:
            return None
        session = self.sessions.get(session_id)
        if (
            session is not None
            and session.retention_deadline is not None
            and asyncio.get_running_loop().time() >= session.retention_deadline
        ):
            self._expire_session(session_id)
        return self.expired_sessions.get(session_id)

    def _expired_result_response(self, session_id: str, *, collector: str) -> dict[str, Any] | None:
        """期限切れsessionの未回収結果を1回だけ返す。"""
        resume_state = self._resolve_expired_session(session_id)
        if resume_state is None:
            return None
        if self._status_writer is not None and self._status_writer.result_state(session_id) == "consumed":
            resume_state = dataclasses.replace(resume_state, result_delivered=True)
            self.expired_sessions[session_id] = resume_state
        response = self._stopped_result_response(resume_state)
        if response is None:
            return {"status": "expired"}
        self.expired_sessions[session_id] = dataclasses.replace(resume_state, result_delivered=True)
        if self._status_writer is not None:
            self._status_writer.delete_result(session_id, collector=collector)
            self._status_writer.schedule()
        return response

    def _resolve_stopped_session(self, session_id: str) -> SessionResumeState | None:
        """破棄済みsessionを返し、別プロセスによる結果回収を反映する。"""
        resume_state = self.stopped_sessions.get(session_id)
        if resume_state is None:
            return None
        if (
            not resume_state.result_delivered
            and resume_state.finalized_at is not None
            and self._status_writer is not None
            and self._status_writer.result_state(session_id) == "consumed"
        ):
            resume_state = dataclasses.replace(resume_state, result_delivered=True)
            self.stopped_sessions[session_id] = resume_state
        return resume_state

    async def take_over_orphaned_session(self, session_id: str) -> None:
        """所有者のいない`running`の登録簿記録を、委譲先CLIの記録がturnの終端を示す場合だけ終端として公開する。

        登録簿が終端を示さない記録は、別のプロセスがturnを実行している可能性を排除できないため再開しない。
        ただし所有側が終端を公開せずに終了したCodexの記録は、その保護のままでは恒久的に回収できない。
        そこで次の2条件をともに満たす場合だけ終端を公開し、後続の`show`と`send_message`が通常の復元へ進めるようにする。
        生存の印が有効な状態ファイルがそのsessionを載せていないこと（書ける所有者がいない）と、
        `thread/read`が元turn（記録が無い場合は全turn）の終端を示し、それより後に進行中のturnが無いことである。
        経過時間だけでは終端と推定しない。照会の失敗、条件の不成立、Codex以外のengineでは何もせず、従来の拒否に委ねる。
        """
        if (
            not shared_layout.valid_session_id(session_id)
            or session_id in self.sessions
            or session_id in self._pending_resumes
            or session_id in self.stopped_sessions
            or session_id in self.expired_sessions
        ):
            return
        resolution = session_registry.resolve(session_id)
        info = resolution.resume_info
        if resolution.state is not session_registry.Resolution.RUNNING or info is None:
            return
        backend_type = backends.backend_class(info.engine)
        if backend_type is None or not backend_type.ORPHAN_TAKEOVER:
            return
        if status_file.live_writer_holds_session(session_id):
            return
        try:
            turns = await backends.turn_history(self._backend(info.engine)).read_thread_turns(session_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # 照会できない記録は引き継がず、従来どおり実行中の可能性があるものとして拒否する。
            _LOG.warning("orphaned_session_takeover_skipped session_id=%s reason=%s", session_id, exc)
            return
        status = _terminal_turn_status(turns, info.turn_id)
        if status is None:
            return
        session_registry.publish(
            session_id,
            terminal=True,
            engine=info.engine,
            fast_mode=info.fast_mode,
            cwd=info.cwd,
            model=info.model,
            effort=info.effort,
            model_type=info.model_type,
            launch_kind=info.launch_kind,
            turn_seq=info.turn_seq,
            status=status,
            launch_info=info.launch_info,
            started_at=info.started_at,
            session_updated_at=info.session_updated_at,
            turn_id=info.turn_id,
        )
        _LOG.info(
            "session_transition event=terminal session_id=%s writer=takeover status=%s turn_seq=%d",
            session_id,
            status,
            info.turn_seq,
        )

    def _restore_registry_session(
        self,
        session_id: str,
        *,
        resolution: session_registry.SessionResolution | None = None,
    ) -> SessionResumeState | None:
        """再起動前の終端sessionを登録簿から最小の再開状態へ復元する。

        `resolution`は、呼び出し元が同じ識別子を既に解決している場合に渡す。
        登録簿のファイル読取を1回の照会へ収めるための入力であり、省略時は自ら解決する。
        """
        if session_id in self.sessions or session_id in self.stopped_sessions or session_id in self.expired_sessions:
            return None
        if resolution is None:
            resolution = session_registry.resolve(session_id)
        if resolution.state is session_registry.Resolution.RUNNING:
            raise ActionableError(
                f"error.recovery=turn_unobserved: {session_id}",
                next_action=(
                    "別のagents_serverプロセスがturnを実行中の可能性があるため、新しいstartでやり直さない。"
                    "そのsessionを起動したroot sessionで`atk agents wait`を実行して終端を観測する"
                ),
            )
        if resolution.state in {session_registry.Resolution.MISSING, session_registry.Resolution.RELEASED}:
            return None
        if resolution.state is session_registry.Resolution.UNREADABLE:
            raise ActionableError(
                f"error.recovery=unreadable: {session_id}",
                next_action="`atk agents wait`で結果を観測し、得られなければ検証済みの状態から新規に起動する",
            )
        info = resolution.resume_info
        if info is None:
            raise ActionableError(
                f"error.recovery=no_resume_info: {session_id}",
                next_action=(
                    "同じsessionでは継続できない。未回収の結果は`atk agents wait`で受領し、"
                    "継続が必要なら検証済みの状態から新規に起動する"
                ),
            )
        persisted_result = self._status_writer.read_result(session_id) if self._status_writer is not None else None
        resume_state = SessionResumeState(
            session_id=session_id,
            cwd=info.cwd,
            model=info.model,
            effort=info.effort,
            engine=info.engine,
            fast_mode=info.fast_mode,
            model_type=info.model_type,
            launch_kind=info.launch_kind,
            **info.launch_info.as_kwargs(),
            started_at=info.started_at,
            updated_at=info.session_updated_at,
            turn_seq=persisted_result["turn_seq"] if persisted_result is not None else info.turn_seq,
            status=persisted_result["status"] if persisted_result is not None else info.status,
            agent_message=persisted_result["agent_message"] if persisted_result is not None else "",
            error=persisted_result.get("error") if persisted_result is not None else None,
            finalized_at=persisted_result["finalized_at"] if persisted_result is not None else None,
            result_delivered=persisted_result is None,
        )
        self.stopped_sessions[session_id] = resume_state
        return resume_state

    def _take_stopped_result(
        self,
        session_id: str,
        resume_state: SessionResumeState,
        *,
        collector: str,
    ) -> dict[str, Any] | None:
        """破棄済みsessionの未回収結果を返し、配送済みとして記録する。"""
        response = self._stopped_result_response(resume_state)
        if response is None:
            return None
        self.stopped_sessions[session_id] = dataclasses.replace(resume_state, result_delivered=True)
        if self._status_writer is not None:
            self._status_writer.delete_result(session_id, collector=collector)
            self._status_writer.schedule()
        return response

    def _expired_kill_response(self, session_id: str) -> dict[str, Any] | None:
        """期限切れsessionなら中断対象が無いことを示す成功応答を返す。"""
        resume_state = self._resolve_expired_session(session_id)
        if resume_state is None:
            return None
        return {"status": "expired", "kill_requested": False, "next_action": EXPIRED_KILL_NEXT_ACTION}

    def list_sessions(self, *, include_terminated: bool = False) -> dict[str, Any]:
        """保持中のsessionを開始時刻順の公開項目へ射影する。

        一覧の範囲を指定しない場合も、未回収結果を持つsessionは終端済みまたは期限切れでも表示する。
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
                        else self._status_writer.result_state(session.session_id) == "consumed",
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
        for session in self.expired_sessions.values():
            listed.setdefault(
                session.session_id,
                (
                    session.started_at,
                    self._listed_session(
                        session,
                        status="expired",
                        progress="",
                        result_available=has_uncollected_result(
                            session,
                            None
                            if self._status_writer is None
                            else self._status_writer.result_state(session.session_id) == "consumed",
                        ),
                    ),
                ),
            )
        for session in self.stopped_sessions.values():
            if session_registry.resolve(session.session_id).state is not session_registry.Resolution.TERMINAL:
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
                            else self._status_writer.result_state(session.session_id) == "consumed",
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

    def show_session(self, session_id: str, *, verbose: bool = False) -> dict[str, Any]:
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
        if session is None:
            resolution = session_registry.resolve(session_id)
            if resolution.state is session_registry.Resolution.RUNNING:
                raise ActionableError(
                    f"session {session_id} belongs to another writer's agents_server process and is still running",
                    next_action="そのsessionを起動したroot sessionで`atk agents wait`を実行して結果を受領する",
                )
            session = self._restore_registry_session(session_id, resolution=resolution)
            if session is None:
                raise self._unresolved_session_error(session_id, label="session", resolution=resolution)
        if session is None:
            raise self._unresolved_session_error(session_id, label="session")
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
        if status in TERMINAL_STATUSES and result_projection.nonempty_error(session.error):
            # 失敗で終端したsessionの原因を、結果の回収とは別に照会できるようにする。
            response["error"] = session.error
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
                    resume_info = session_registry.resolve(child_session_id).resume_info
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
            # モデルのturnは終わり、バックグラウンドタスクか孫sessionの終端、
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
        return response

    async def stop(self, session_id: str, *, retain_result: bool = False) -> dict[str, Any]:
        """終端済みsessionを破棄し、会話再開用の最小状態だけを保持する。

        `stopped_sessions`への在籍は、このプロセスが解放すべきbackend資源を
        持たないことを表す。解放後の同期失敗では再発行が同期だけを再試行する。
        """
        if not isinstance(session_id, str) or not session_id:
            raise ActionableError("session_id must be a non-empty string", next_action=SESSION_ID_NEXT_ACTION)
        if session_id in self._pending_resumes:
            raise ActionableError(f"session is running: {session_id}", next_action=RUNNING_STOP_NEXT_ACTION)
        stopped_state = self._resolve_stopped_session(session_id)
        if stopped_state is None:
            stopped_state = self._restore_registry_session(session_id)
        already_stopped = session_id in self.stopped_sessions
        session = self.sessions.get(session_id)
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
                raise self._unresolved_session_error(session_id, label="session")
        result_state = None if self._status_writer is None else self._status_writer.result_state(session_id)
        keep_result = retain_result and resume_state.finalized_at is not None and result_state != "consumed"
        resume_state = dataclasses.replace(resume_state, result_delivered=not keep_result)
        if session is not None:
            session.result_delivered = not keep_result
        if not already_stopped:
            await self._wait_for_resource_release(session_id)
            if session is not None:
                await self._backend(resume_state.engine).release_session(session)
        self._cancel_retention_timer(session_id)
        self._pending_unobserved_child_sessions.pop(session_id, None)
        self.sessions.pop(session_id, None)
        self.expired_sessions.pop(session_id, None)
        self.stopped_sessions[session_id] = resume_state
        session_registry.release(session_id, reason="stopped")
        if self._status_writer is not None:
            try:
                if not keep_result:
                    self._status_writer.delete_wait_target(session_id)
                if keep_result and result_state == "unpublished" and not self._status_writer.result_exists(session_id):
                    self._status_writer.retain_result(resume_state)
                elif not keep_result and (result_state == "published" or self._status_writer.result_exists(session_id)):
                    self._status_writer.delete_result(session_id, collector="stop")
                self._status_writer.schedule()
            except Exception as exc:
                raise ActionableRuntimeError(
                    f"backend resources released; state synchronization incomplete for session {session_id}: {exc}",
                    next_action=(
                        "資源は解放済みのため、同じ`session_id`へ`stop`を再発行してよい（状態の同期だけを再試行する）。"
                        "結果は`list`で確認する"
                    ),
                ) from exc
        return {}

    async def _stop_after_terminal_response(
        self,
        session_id: str,
        response: dict[str, Any],
        stop: bool,
    ) -> dict[str, Any]:
        """要求された終端応答に限りsessionを破棄し、元の応答を維持する。"""
        if stop and response.get("status") in {*TERMINAL_STATUSES, "expired"} and session_id not in self.stopped_sessions:
            await self.stop(session_id, retain_result=True)
        return response

    def _publish_closed_sessions(self) -> None:
        """backendの停止で終わった未終端のturnを`interrupted`へ遷移させ、登録簿と結果ファイルへ公開する。

        停止では所有側が自らturnを終了するため、ここで公開しないと登録簿が`running`のまま残る。
        登録簿が終端を示さない記録は、再起動後のプロセスが別プロセスの実行中と区別できず回収できなくなる。
        遷移はbackendごとに書かず、全engineに共通の停止処理として本関数へ集約する。
        """
        for session in tuple(self.sessions.values()):
            if not session.publish_registry or session.result_available or session.result_delivered:
                continue
            if not session.terminal:
                session.status = "interrupted"
            session.turn_completed = True
            session.turn_start_ambiguous = False
            session.awaiting_auto_resume = False
            session.interrupt_requested = False
            session.touch()
        if self._status_writer is not None:
            # 集約予約を待たずに結果ファイルを書き、状態ファイルの無効化より前に終端結果を残す。
            self._status_writer.flush()
