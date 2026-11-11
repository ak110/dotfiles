"""期限切れ・停止済み・登録簿のsessionの復元と結果回収。"""

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
    owner_lost,
    responses,
    result_projection,
    retained_results,
    shared_layout,
    status_file,
    status_reader,
)
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.responses import EXPIRED_KILL_NEXT_ACTION, EXPIRED_SESSION_NEXT_ACTION, SESSION_ID_NEXT_ACTION
from agent_toolkit._agents_server.state import TERMINAL_STATUSES, SessionResumeState, SessionState
from agent_toolkit._common import recorded_process, session_registry
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


@dataclasses.dataclass(frozen=True)
class _RegistryResumeState(SessionResumeState):
    """別プロセスの共有登録から復元し、照会時に鮮度確認を要する再開状態。"""

    result_root_session_id: str | None = None
    result_owner_status_file: str = "root.json"


def _terminal_turn_status(
    turns: Sequence[tuple[str, str]], turn_id: str | None
) -> Literal["completed", "failed", "interrupted"] | None:
    """`thread/read`のturn一覧から、元turnが終了して後続の進行中turnが無い場合の終了状態を返す。"""
    if turn_id is None:
        return None
    index = next((position for position, (identifier, _) in enumerate(turns) if identifier == turn_id), None)
    if index is None or turns[index][1] not in TERMINAL_STATUSES:
        return None
    if any(status not in TERMINAL_STATUSES for _, status in turns[index + 1 :]):
        return None
    return cast(Literal["completed", "failed", "interrupted"], turns[index][1])


class ManagerRegistryRestore(responses.ManagerResponses):
    """登録簿の所有状態と退避した結果から再開可能なsessionを解決する。"""

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
        info = resolution.resume_info or resolution.handoff_info
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
        response = self._stopped_result_response(resume_state)
        if response is None:
            return {"status": "expired"}
        claimed = await self._claim_resume_result(resume_state, response, collector=collector)
        if self.expired_sessions.get(session_id) is resume_state:
            self.expired_sessions[session_id] = dataclasses.replace(resume_state, result_delivered=True)
        return claimed or {"status": "expired"}

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
        if resolution.resume_pending:
            return
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
            state=session_registry.PublicationState.from_status(status, result_available=True),
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
            if (reason := await run_file_operation(recorded_process.stop_recorded_executor, executor)) is not None
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
        recipient = await self._status_writer.launcher_session_id_async() if self._status_writer is not None else None
        notices.extend(
            await run_file_operation(
                owner_lost.take_stranded_notices,
                session_id,
                self._own_root_session_id(),
                self._state_root(),
                recipient_session_id=recipient,
            )
        )
        return notices

    async def _restore_registry_session(
        self,
        session_id: str,
        *,
        resolution: session_registry.SessionResolution | None = None,
        include_handoff: bool = False,
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
            resolution.state is session_registry.Resolution.RELEASED
            and resolution.resume_info is None
            and not (include_handoff and resolution.handoff_info is not None)
        ):
            return None
        if resolution.state is session_registry.Resolution.UNREADABLE:
            raise ActionableError(
                f"error.recovery=unreadable: {session_id}",
                next_action="`atk agents wait`で結果を観測し、得られなければ検証済みの状態から新規に起動する",
            )
        info = resolution.resume_info or (resolution.handoff_info if include_handoff else None)
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
        result_root = self._own_root_session_id() if persisted_result is not None else None
        if persisted_result is None:
            roots = (
                [resolution.owner_root_session_id]
                if resolution.owner_root_session_id is not None
                else (
                    await run_file_operation(
                        status_reader.ownerless_root_session_ids, self._own_root_session_id(), self._state_root()
                    )
                )
            )
            for root in roots:
                payload = await run_file_operation(retained_results.read_retained_result, root, session_id, self._state_root())
                if payload is not None and (
                    isinstance(payload.get("agent_message"), str)
                    and payload.get("turn_seq") == info.turn_seq
                    and isinstance(payload.get("finalized_at"), str)
                ):
                    persisted_result, result_root = payload, root
                    break
        if persisted_result is not None and persisted_result["turn_seq"] != info.turn_seq:
            # 過去turnの未回収結果は共有ファイルへ残し、現在turnの状態とは分けて扱う。
            persisted_result = None
        result_owner = persisted_result.get("owner_status_file") if persisted_result is not None else None
        if result_owner is not None and not isinstance(result_owner, str):
            raise ActionableError(
                f"保存結果の書込主体が不正です: {session_id}",
                next_action="結果の原本を保全し、owner_status_fileが文字列であるか確認して同じwaitを再実行する",
            )
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
            result_root_session_id=result_root,
            result_owner_status_file=result_owner or "root.json",
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
        claimed = await self._claim_resume_result(resume_state, response, collector=collector)
        if self.stopped_sessions.get(session_id) is resume_state:
            self.stopped_sessions[session_id] = dataclasses.replace(resume_state, result_delivered=True)
        return claimed

    async def _claim_resume_result(
        self, resume_state: SessionResumeState, response: dict[str, Any], *, collector: str
    ) -> dict[str, Any] | None:
        """退避元の違いによらず、同じturnの排他回収に成功した本文だけを返す。"""
        session_id = resume_state.session_id
        if isinstance(resume_state, _RegistryResumeState) and resume_state.result_root_session_id is not None:
            claimed, error = await run_file_operation(
                retained_results.take_result,
                resume_state.result_root_session_id,
                session_id,
                resume_state.result_owner_status_file,
                collector=collector,
                state_root=self._state_root(),
                expected_turn_seq=resume_state.turn_seq,
            )
            if error is not None:
                raise ActionableError(
                    f"保存済みの子の結果を回収できない: {session_id}: {error}",
                    next_action="結果ファイルの読み取り権限を確認して同じwaitを再実行する",
                )
            return None if claimed is None else {**response, **claimed}
        if self._status_writer is not None:
            await self._status_writer.retain_result_async(resume_state)
            claimed, error = await self._status_writer.take_result_async(
                session_id, collector=collector, expected_turn_seq=resume_state.turn_seq
            )
            if error is not None:
                raise ActionableError(
                    f"保存済みの子の結果を回収できない: {session_id}: {error}",
                    next_action="結果ファイルの読み取り権限を確認して同じwaitを再実行する",
                )
            return None if claimed is None else response
        return response

    async def _expired_kill_response(self, session_id: str) -> dict[str, Any] | None:
        """期限切れsessionなら中断対象が無いことを示す成功応答を返す。"""
        resume_state = await self._resolve_expired_session(session_id)
        if resume_state is None:
            return None
        return {"status": "expired", "kill_requested": False, "next_action": EXPIRED_KILL_NEXT_ACTION}
