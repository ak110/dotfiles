"""managerが保持するsessionと、保持しないsessionのsession登録簿からの解決、一覧・表示・停止の応答。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import pathlib
from collections.abc import Sequence
from typing import Any, Literal, cast

from agent_toolkit._agents_server import (
    backends,
    logging_config,
    owner_lost,
    responses,
    result_projection,
    shared_layout,
    state,
    status_file,
)
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.responses import (
    EXPIRED_KILL_NEXT_ACTION,
    EXPIRED_SESSION_NEXT_ACTION,
    RUNNING_STOP_NEXT_ACTION,
    SESSION_ID_NEXT_ACTION,
    listed_public_session,
)
from agent_toolkit._agents_server.session_errors import ActionableRuntimeError
from agent_toolkit._agents_server.state import TERMINAL_STATUSES, SessionResumeState, SessionState, has_uncollected_result
from agent_toolkit._common import session_registry
from agent_toolkit._common.next_action import ActionableError, next_action_line

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


class _RegistryResumeState(SessionResumeState):
    """別プロセスの共有登録から復元し、照会時に鮮度確認を要する再開状態。"""


def _terminal_turn_status(
    turns: Sequence[tuple[str, str]], turn_id: str | None
) -> Literal["completed", "failed", "interrupted"] | None:
    """`thread/read`のturn一覧から、元turnが終了して後続の進行中turnが無い場合の終了状態を返す。

    元turnの識別子を持たない旧形式の記録では、全turnが終了している場合に最後のturnの状態を返す。
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

    async def resolve_handoff_predecessor(self, session_id: str) -> SessionState | SessionResumeState:
        """引き継ぎ元の終了を確認し、結果の回収状態を変えずに起動入力を返す。"""
        if not shared_layout.valid_session_id(session_id):
            raise ActionableError("前任session_idの形式が不正です", next_action=SESSION_ID_NEXT_ACTION)
        await self.take_over_orphaned_session(session_id)
        session = (
            self.sessions.get(session_id) or self.expired_sessions.get(session_id) or self.stopped_sessions.get(session_id)
        )
        if isinstance(session, SessionResumeState):
            session = await self._refresh_resume_state(session)
        running_action = (
            f"前任`{session_id}`の終了を`atk agents wait`で待つか、同じ前任へ`kill`で中断を要求して終了後に引き継ぐ"
        )
        if session_id in self._pending_resumes or (
            session is not None
            and (session.status not in TERMINAL_STATUSES or isinstance(session, SessionState) and not session.result_available)
        ):
            raise ActionableError(f"前任は実行中です: {session_id}", next_action=running_action)
        if session is not None:
            if isinstance(session, SessionState):
                await session.drain_registration()
            return session
        resolution = await run_file_operation(session_registry.resolve, session_id)
        if resolution.state is session_registry.Resolution.RUNNING:
            raise ActionableError(f"前任は実行中です: {session_id}", next_action=running_action)
        info = resolution.handoff_info or resolution.resume_info
        if info is None or info.status not in TERMINAL_STATUSES:
            raise ActionableError(
                f"前任の引き継ぎ情報を復元できません: {session_id}; registry={resolution.state}",
                next_action="前任の記録と起動入力を確かめ、確認した残工程を通常の`start`へ渡す",
            )
        return SessionResumeState(
            session_id,
            info.cwd,
            info.model,
            info.effort,
            info.engine,
            model_type=info.model_type,
            launch_kind=info.launch_kind,
            status=info.status,
            turn_seq=info.turn_seq,
            result_contract_recovery_consumed=info.result_contract_recovery_consumed,
            **info.launch_info.as_kwargs(),
        )

    async def _get_session(self, session_id: str) -> SessionState:
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
            raise (await self._unresolved_session_error(session_id, label="session")) from exc
        if session.retention_deadline is not None and asyncio.get_running_loop().time() >= session.retention_deadline:
            self._expire_session(session_id)
            raise ActionableError(f"session retention expired: {session_id}", next_action=EXPIRED_SESSION_NEXT_ACTION)
        return session

    async def _resolve_expired_session(self, session_id: str) -> SessionResumeState | None:
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
        resume_state = self.expired_sessions.get(session_id)
        return (await self._refresh_resume_state(resume_state)) if resume_state is not None else None

    async def _refresh_resume_state(self, resume_state: SessionResumeState) -> SessionResumeState:
        """共有登録の現在turnを確認し、退避した終了を現在状態として使えるか判定する。"""
        if (
            not isinstance(resume_state, _RegistryResumeState)
            or resume_state.session_id in self.sessions
            or resume_state.session_id in self._pending_resumes
        ):
            return resume_state
        resolution = await run_file_operation(session_registry.resolve, resume_state.session_id)
        if resolution.state is session_registry.Resolution.RUNNING:
            raise await self._running_elsewhere_error(
                resume_state.session_id,
                resolution,
                next_action="そのsessionを起動したroot sessionで`atk agents wait`を実行して終了を観測する",
            )
        info = resolution.resume_info
        if info is not None and (
            info.turn_seq != resume_state.turn_seq or info.status != resume_state.status or resume_state.finalized_at is None
        ):
            self.stopped_sessions.pop(resume_state.session_id, None)
            self.expired_sessions.pop(resume_state.session_id, None)
            restored = await self._restore_registry_session(resume_state.session_id, resolution=resolution)
            if restored is not None:
                return restored
        return resume_state

    async def _expired_result_response(self, session_id: str, *, collector: str) -> dict[str, Any] | None:
        """期限切れsessionの未回収結果を1回だけ返す。"""
        resume_state = await self._resolve_expired_session(session_id)
        if resume_state is None:
            return None
        if self._status_writer is not None and (await self._status_writer.result_state_async(session_id)) == "consumed":
            resume_state = dataclasses.replace(resume_state, result_delivered=True)
            self.expired_sessions[session_id] = resume_state
        response = self._stopped_result_response(resume_state)
        if response is None:
            return {"status": "expired"}
        self.expired_sessions[session_id] = dataclasses.replace(resume_state, result_delivered=True)
        if self._status_writer is not None:
            (await self._status_writer.delete_result_async(session_id, collector=collector))
            self._status_writer.schedule()
        return response

    async def _resolve_stopped_session(self, session_id: str) -> SessionResumeState | None:
        """破棄済みsessionを返し、別プロセスによる結果回収を反映する。"""
        resume_state = self.stopped_sessions.get(session_id)
        if resume_state is None:
            return None
        resume_state = await self._refresh_resume_state(resume_state)
        if (
            not resume_state.result_delivered
            and resume_state.finalized_at is not None
            and self._status_writer is not None
            and (await self._status_writer.result_state_async(session_id)) == "consumed"
        ):
            resume_state = dataclasses.replace(resume_state, result_delivered=True)
            self.stopped_sessions[session_id] = resume_state
        return resume_state

    async def take_over_orphaned_session(self, session_id: str) -> None:
        """所有者のいない`running`の登録簿記録を、turnの終了を確かめられる場合だけ終了として公開する。

        登録簿が終了を示さない記録は、別のプロセスがturnを実行している可能性を排除できないため再開しない。
        ただし所有側が終了を公開せずに終了した記録は、この拒否を続ける限り回収できない。
        そこで次のいずれかの場合に終了を公開し、後続の`show`と`send_message`が通常の復元へ進めるようにする。
        1つはengineを問わず、記録された所有プロセスと実行プロセスが全て存在せず、`resolve`が`interrupted`の
        終了と解決した場合である。もう1つは`ORPHAN_TAKEOVER`のengine（Codex）で、所有状態が
        `session_registry.RunningOwnership.ORPHANED`（記録プロセスを持たない旧形式の記録か、委譲先CLIが実在しない記録）
        であり、`thread/read`が元turn（記録が無い場合は全turn）の終了を示し、それより後に進行中のturnが無い場合である。
        委譲先CLIが実在する記録（所有サーバーを失った実行中を含む）は引き継がない。
        別プロセスで実行中のturnを、専用App Serverの`thread/read`が終了と返すことを観測したためである。
        どちらも生存の印が有効な状態ファイルがそのsessionを載せていない（書ける所有者がいない）ことを前提とする。
        経過時間だけでは終了と推定しない。照会の失敗と条件の不成立では何もせず、従来の拒否に委ねる。
        """
        if (
            not shared_layout.valid_session_id(session_id)
            or session_id in self.sessions
            or session_id in self._pending_resumes
            or session_id in self.stopped_sessions
            or session_id in self.expired_sessions
        ):
            return
        resolution = await run_file_operation(session_registry.resolve, session_id)
        info = resolution.resume_info
        if info is None or not (resolution.state is session_registry.Resolution.RUNNING or resolution.inferred_terminal):
            return
        writer_alive = await run_file_operation(status_file.live_writer_holds_session, session_id)
        if writer_alive:
            return
        status: Literal["completed", "failed", "interrupted"] | None
        if resolution.inferred_terminal:
            status = "interrupted"
        else:
            if session_registry.running_ownership(resolution, writer_alive=writer_alive) is not (
                session_registry.RunningOwnership.ORPHANED
            ):
                return
            backend_type = backends.backend_class(info.engine)
            if backend_type is None or not backend_type.ORPHAN_TAKEOVER:
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
        await self._publish_registry_terminal(session_id, info, status, interruption=resolution.interruption)
        _LOG.info(
            "session_transition event=terminal session_id=%s writer=takeover status=%s turn_seq=%d",
            session_id,
            status,
            info.turn_seq,
        )

    @staticmethod
    async def _publish_registry_terminal(
        session_id: str,
        info: session_registry.ResumeInfo,
        status: Literal["completed", "failed", "interrupted"],
        *,
        interruption: session_registry.InterruptionReason | None,
    ) -> None:
        """自プロセスが所有しない記録へ終了を公開する。元の所有サーバーの起動単位と所有ルートは保つ。

        記録プロセスは載せない。終了の記録はプロセスの実在で解決しないため、以後の照会は公開した終了に従う。
        """
        await run_file_operation(
            session_registry.publish,
            session_id,
            terminal=True,
            engine=info.engine,
            fast_mode=info.fast_mode,
            codex_model_provider=info.codex_model_provider,
            codex_subscription_provider=info.codex_subscription_provider,
            codex_state_provider=info.codex_state_provider,
            claude_api_env_file=info.claude_api_env_file,
            claude_connection=info.claude_connection,
            claude_role=info.claude_role,
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
            interruption=interruption,
            preserve_server_instance=True,
            result_contract_recovery_consumed=info.result_contract_recovery_consumed,
        )

    def _own_root_session_id(self) -> str | None:
        return None if self._status_writer is None else self._status_writer.root_session_id

    def _state_root(self) -> pathlib.Path | None:
        return None if self._status_writer is None else self._status_writer.state_root

    async def _diagnose_owner_lost(
        self, session_id: str, resolution: session_registry.SessionResolution
    ) -> owner_lost.OwnerLost | None:
        """登録簿から解決した記録が所有サーバーを失った実行中かを判定する。"""
        if resolution.state is not session_registry.Resolution.RUNNING or not resolution.live_executors:
            return None
        return await run_file_operation(
            owner_lost.diagnose,
            session_id,
            resolution,
            own_root_session_id=self._own_root_session_id(),
            state_root=self._state_root(),
        )

    async def _running_elsewhere_error(
        self, session_id: str, resolution: session_registry.SessionResolution, *, next_action: str
    ) -> ActionableError:
        """別プロセスが実行中と解決した記録への拒否を返す。所有サーバーを失った実行中には待つかkillの案内を返す。"""
        diagnosis = await self._diagnose_owner_lost(session_id, resolution)
        if diagnosis is not None:
            return ActionableError(diagnosis.message(), next_action=owner_lost.OWNER_LOST_NEXT_ACTION)
        return ActionableError(f"error.recovery=turn_unobserved: {session_id}", next_action=next_action)

    @staticmethod
    def _is_registry_resume_state(resume_state: object) -> bool:
        """別プロセスの共有登録から復元した再開状態かを返す。"""
        return isinstance(resume_state, _RegistryResumeState)

    async def _raise_if_owner_lost(self, session_id: str) -> None:
        """所有サーバーを失った実行中のsessionなら、回収した通知と待つかkillの案内を持つエラーを送出する。

        追送は新しいbackendで会話を再開せず、登録簿へ終了も公開しない。旧委譲先CLIが会話の書込主体を保持しており、
        再開は書込主体の競合で失敗するためである。
        """
        resolution = await run_file_operation(session_registry.resolve, session_id)
        diagnosis = await self._diagnose_owner_lost(session_id, resolution)
        if diagnosis is None:
            return
        message = diagnosis.message()
        notices = await self._take_registry_notices(session_id)
        if notices:
            message += "; notices=" + json.dumps(notices, ensure_ascii=False, separators=(",", ":"))
        raise ActionableError(message, next_action=owner_lost.OWNER_LOST_NEXT_ACTION)

    async def _kill_owner_lost(self, session_id: str) -> dict[str, Any] | None:
        """所有サーバーを失った実行中のsessionの委譲先CLIを止め、停止を確かめてから`interrupted`を公開する。

        所有サーバーを失った実行中でなければ`None`を返し、従来の`kill`へ委ねる。
        停止するのは登録簿のpidと起動時刻が一致する委譲先CLIだけとし、所有サーバーのプロセスは止めない。
        停止を確かめられないプロセスが残る場合は終了を公開せず、その理由を返す。
        """
        resolution = await run_file_operation(session_registry.resolve, session_id)
        diagnosis = await self._diagnose_owner_lost(session_id, resolution)
        if diagnosis is None:
            return None
        stopped = list(resolution.live_executors)
        refused = [
            reason
            for executor in stopped
            if (reason := await run_file_operation(owner_lost.stop_recorded_executor, executor)) is not None
        ]
        if refused:
            raise ActionableError(
                f"kill could not stop the delegate CLI of session {session_id}: " + "; ".join(refused),
                next_action=(
                    "停止できない理由を解消してから`kill`を再発行する。解消できない間は`atk agents wait`で実行の終了を待つ"
                ),
            )
        current = await run_file_operation(session_registry.resolve, session_id)
        info = current.resume_info
        if info is not None and (
            current.state is session_registry.Resolution.RUNNING and not current.live_executors or current.inferred_terminal
        ):
            # 明示の中断で終えたturnとして公開する。所有サーバーの停止による中断の理由は付けない。
            await self._publish_registry_terminal(session_id, info, "interrupted", interruption=None)
            current = await run_file_operation(session_registry.resolve, session_id)
            _LOG.info(
                "session_transition event=terminal session_id=%s writer=owner-lost-kill status=interrupted turn_seq=%d",
                session_id,
                info.turn_seq,
            )
        if current.state is not session_registry.Resolution.TERMINAL or current.resume_info is None:
            raise ActionableError(
                f"kill stopped the delegate CLI but could not publish the terminal state of session {session_id}; "
                f"registry={current.state}",
                next_action="`show`で同じsessionの状態を確かめ、終了していれば同じ`session_id`へ`send_message`する",
            )
        response: dict[str, Any] = {
            "status": current.resume_info.status,
            "kill_requested": True,
            "stopped_processes": [session_registry.process_payload(process) for process in stopped],
            "next_action": "同じ`session_id`へ`send_message`すると、停止したturnの会話を再開する",
        }
        return self._response_with_notices(response, await self._take_registry_notices(session_id))

    async def _take_registry_notices(self, session_id: str) -> list[dict[str, str]]:
        """登録簿から解決したsessionの通知を、自身のルートと所有者のいない他のルートから回収する。"""
        notices = await self._take_notices_async(session_id)
        notices.extend(
            await run_file_operation(
                owner_lost.take_stranded_notices, session_id, self._own_root_session_id(), self._state_root()
            )
        )
        return notices

    async def _restore_registry_session(
        self,
        session_id: str,
        *,
        resolution: session_registry.SessionResolution | None = None,
    ) -> SessionResumeState | None:
        """再起動前の終了sessionを登録簿から最小の再開状態へ復元する。

        `resolution`は、呼び出し元が同じ識別子を既に解決している場合に渡す。
        登録簿のファイル読取を1回の照会へ収めるための入力であり、省略時は自ら解決する。
        """
        if session_id in self.sessions:
            return None
        cached = self.stopped_sessions.get(session_id) or self.expired_sessions.get(session_id)
        if cached is not None:
            refreshed = await self._refresh_resume_state(cached)
            return refreshed if refreshed is not cached else None
        if resolution is None:
            resolution = await run_file_operation(session_registry.resolve, session_id)
        if resolution.state is session_registry.Resolution.RUNNING:
            raise await self._running_elsewhere_error(
                session_id,
                resolution,
                next_action=(
                    "別のagents_serverプロセスがturnを実行中の可能性があるため、新しいstartでやり直さない。"
                    "そのsessionを起動したroot sessionで`atk agents wait`を実行して終了を観測する"
                ),
            )
        if resolution.state is session_registry.Resolution.MISSING or (
            resolution.state is session_registry.Resolution.RELEASED and resolution.resume_info is None
        ):
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
        persisted_result = (
            (await run_file_operation(self._status_writer.read_result, session_id)) if self._status_writer is not None else None
        )
        if persisted_result is not None and persisted_result["turn_seq"] != info.turn_seq:
            # 過去turnの未回収結果は共有ファイルへ残し、現在turnの状態とは分けて扱う。
            persisted_result = None
        # 実行プロセスの不在から中断と解決した記録は、結果ファイルを持たなくても終了の理由を`show`へ返す。
        registry_error = result_projection.resolution_interruption_error(resolution)
        resume_state = _RegistryResumeState(
            session_id=session_id,
            cwd=info.cwd,
            model=info.model,
            effort=info.effort,
            engine=info.engine,
            fast_mode=info.fast_mode,
            codex_model_provider=info.codex_model_provider,
            codex_subscription_provider=info.codex_subscription_provider,
            codex_state_provider=info.codex_state_provider,
            claude_api_env_file=info.claude_api_env_file,
            claude_connection=info.claude_connection,
            claude_role=info.claude_role,
            model_type=info.model_type,
            launch_kind=info.launch_kind,
            **info.launch_info.as_kwargs(),
            started_at=info.started_at,
            updated_at=info.session_updated_at,
            turn_seq=persisted_result["turn_seq"] if persisted_result is not None else info.turn_seq,
            status=persisted_result["status"] if persisted_result is not None else info.status,
            agent_message=persisted_result["agent_message"] if persisted_result is not None else "",
            error=persisted_result.get("error") if persisted_result is not None else registry_error,
            finalized_at=persisted_result["finalized_at"] if persisted_result is not None else None,
            result_delivered=persisted_result is None,
            result_contract_recovery_consumed=info.result_contract_recovery_consumed,
        )
        self.stopped_sessions[session_id] = resume_state
        return resume_state

    async def _take_stopped_result(
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
            (await self._status_writer.delete_result_async(session_id, collector=collector))
            self._status_writer.schedule()
        return response

    async def _expired_kill_response(self, session_id: str) -> dict[str, Any] | None:
        """期限切れsessionなら中断対象が無いことを示す成功応答を返す。"""
        resume_state = await self._resolve_expired_session(session_id)
        if resume_state is None:
            return None
        return {"status": "expired", "kill_requested": False, "next_action": EXPIRED_KILL_NEXT_ACTION}

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
        stopped_state = await self._resolve_stopped_session(session_id)
        if stopped_state is None:
            stopped_state = await self._restore_registry_session(session_id)
        already_stopped = session_id in self.stopped_sessions
        session = self.sessions.get(session_id)
        if session is None and isinstance(stopped_state, _RegistryResumeState):
            live = await run_file_operation(_foreign_live_processes, session_id)
            if live:
                # 所有側が生存する記録を解放済みへ置き換えると、所有側の再開と終了の公開が別の記録を上書きする。
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
        result_state = None if self._status_writer is None else (await self._status_writer.result_state_async(session_id))
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
        try:
            if session is not None:
                await session.drain_registration()
            await run_file_operation(session_registry.release, session_id, reason="stopped")
            if self._status_writer is not None:
                if not keep_result:
                    (await run_file_operation(self._status_writer.delete_wait_target, session_id))
                if (
                    keep_result
                    and result_state == "unpublished"
                    and not (await run_file_operation(self._status_writer.result_exists, session_id))
                ):
                    await self._status_writer.retain_result_async(resume_state)
                elif not keep_result and (
                    result_state == "published" or (await run_file_operation(self._status_writer.result_exists, session_id))
                ):
                    await self._status_writer.delete_result_async(session_id, collector="stop")
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
        """要求された終了応答に限りsessionを破棄し、元の応答を維持する。"""
        if stop and response.get("status") in {*TERMINAL_STATUSES, "expired"} and session_id not in self.stopped_sessions:
            await self.stop(session_id, retain_result=True)
        return response

    async def _publish_closed_sessions(self) -> None:
        """backendの停止で終わった未終了のturnを`interrupted`へ遷移させ、登録簿と結果ファイルへ公開する。

        停止では所有側が自らturnを終了するため、ここで公開しないと登録簿が`running`のまま残る。
        登録簿が終了を示さない記録は、再起動後のプロセスが別プロセスの実行中と区別できず回収できなくなる。
        遷移はbackendごとに書かず、全engineに共通の停止処理として本関数へ集約する。
        """
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
            session.turn_completed = True
            session.turn_start_ambiguous = False
            session.awaiting_auto_resume = False
            session.interrupt_requested = False
            session.touch()
        if self._status_writer is not None:
            # 集約予約を待たずに結果ファイルを書き、状態ファイルの無効化より前に終了時の結果を残す。
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
    """登録簿の記録プロセスのうち、自プロセス以外で実在するものを返す。"""
    if not shared_layout.valid_session_id(session_id):
        return []
    resolution = session_registry.resolve(session_id)
    own = session_registry.owner_process()
    candidates = [resolution.owner_process, *resolution.executor_processes]
    return [process for process in candidates if process is not None and process != own and process.exists()]
