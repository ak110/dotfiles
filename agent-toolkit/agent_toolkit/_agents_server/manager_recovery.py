"""親のMCP再起動時に、保存された直接の子を観測または同一会話から復旧する。"""

from __future__ import annotations

import asyncio
import logging

from agent_toolkit._agents_server import manager_resume, notify, shared_roots, status_file, wait_targets
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._common import recorded_process, session_registry
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")
_CONTINUE = (
    "親の会話の再開に伴い、中断された同じ会話を復旧した。"
    "起動時の依頼、追送、作業契約と引き継ぎ記録を読み、実際の成果と未完了操作を確認して続行せよ。"
    "成否不明のcommit・外部操作・実行中処理は状態を照会してから判断し、重複実行するな。"
    "起動済みの子は同じ識別子で観測し、完了未回収の結果を受領せよ。"
)


class ManagerRecovery(manager_resume.ManagerResume):
    """直接の子だけを復旧し、孫以降は各子のMCPが同じ処理で復旧する。"""

    async def prepare_delegation_recovery(self) -> None:
        """起動時の直接親と一致する保存済みIDを待機対象へ戻す。"""
        if self._recovery_initialized:
            return
        launcher = await run_file_operation(self.launcher_session_id)
        identifiers = (
            ()
            if launcher is None
            else await run_file_operation(session_registry.child_session_ids, launcher, state_root=self._state_root())
        )
        self._recovery_targets.update(identifiers)
        if self._status_writer is not None:
            await run_file_operation(
                wait_targets.retain_wait_targets,
                self._status_writer.root_session_id,
                self._status_writer.path.name,
                list(identifiers),
                self._state_root(),
            )
        self._recovery_initialized = True

    async def monitor_delegation_recovery(self) -> None:
        """生存する所有者は観測し、失われた実行だけを復旧する。"""
        while self._recovery_targets:
            for identifier in tuple(self._recovery_targets):
                await self._recover_one(identifier)
            await self._notify_waiters()
            if self._recovery_targets:
                pending = {task for task in self._recovery_attempts.values() if not task.done()}
                if pending:
                    await asyncio.wait(pending, timeout=1, return_when=asyncio.FIRST_COMPLETED)
                else:
                    await asyncio.sleep(1)

    async def initialize_delegation_recovery(self) -> None:
        """最初の要求を返す前に復旧の起動を確定し、CLIが読める状態まで公開する。"""
        for identifier in tuple(self._recovery_targets):
            await self._recover_one(identifier)
        if self._recovery_attempts:
            # 待つのはbackendへの接続とturn開始だけであり、モデルの仕事の完了ではない。
            await asyncio.gather(*self._recovery_attempts.values(), return_exceptions=True)
        for identifier in tuple(self._recovery_targets):
            await self._recover_one(identifier)
        if self._status_writer is not None:
            await self._status_writer.flush_async()

    async def _recover_one(self, identifier: str) -> None:
        try:
            await self._recover_delegation(identifier)
        except Exception as error:  # pylint: disable=broad-exception-caught
            # 一つの子の復旧失敗で、独立した他の子の復旧を中断しない。waitへ原因を返す。
            self._recovery_errors[identifier] = error
            self._recovery_targets.discard(identifier)
            attempt = self._recovery_attempts.pop(identifier, None)
            _LOG.exception(
                "delegation_recovery_failed session_id=%s。次の操作: %s", identifier, self._recovery_next_action(identifier)
            )
            if attempt is None:
                await self._notify_recovery_failure(identifier, error)

    async def _notify_recovery_failure(self, identifier: str, error: Exception) -> None:
        if self._status_writer is None:
            return
        try:
            await run_file_operation(
                notify.save_notification,
                f"agents_serverによる子の自動復旧に失敗した: {identifier}: {error}。"
                f"次の操作: {self._recovery_next_action(identifier)}",
                shared_roots.StatusFileIdentity(self._status_writer.root_session_id, f"{identifier}.json", identifier),
                state_root=self._state_root(),
                actor="server",
            )
        except OSError as notice_error:
            error.add_note(f"自動復旧の失敗通知も保存できない: {notice_error}")

    @staticmethod
    def _recovery_next_action(identifier: str) -> str:
        return (
            f"`atk agents show {identifier}`で診断を確認し、原因を解消して"
            f"agents_serverのsend_messageへsession_id={identifier}と続行するpromptを渡す。"
        )

    async def _recover_delegation(self, identifier: str) -> None:
        attempt = self._recovery_attempts.get(identifier)
        if attempt is not None:
            if not attempt.done():
                return
            try:
                attempt.result()
            except manager_resume.ResumeClaimedElsewhere:
                # 別の起動がバックエンド接続中でも、元turnは終了のままである。次の公開を待つ。
                self.stopped_sessions.pop(identifier, None)
                self.expired_sessions.pop(identifier, None)
                del self._recovery_attempts[identifier]
                return
            else:
                del self._recovery_attempts[identifier]
                self._recovery_targets.discard(identifier)
                return
        if identifier in self.sessions:
            self._recovery_targets.discard(identifier)
            return
        resolution = await run_file_operation(session_registry.resolve, identifier, state_root=self._state_root())
        info = resolution.resume_info or resolution.handoff_info
        if resolution.resume_pending:
            return
        if resolution.state is session_registry.Resolution.RUNNING:
            writer_alive = await run_file_operation(status_file.live_writer_holds_session, identifier, self._state_root())
            if writer_alive:
                return
            if resolution.live_executors:
                # PIDと起動時刻の一致を停止側で再確認する。別sessionを持つ所有サーバーは停止しない。
                for process in resolution.live_executors:
                    failure = await run_file_operation(recorded_process.stop_recorded_executor, process)
                    if failure is not None:
                        raise ActionableError(
                            f"子の復旧前の終了を確認できない: {identifier}: {failure}",
                            next_action=f"`atk agents show {identifier}`で記録プロセスを確認する",
                        )
                current = await run_file_operation(session_registry.resolve, identifier, state_root=self._state_root())
                if (
                    current.recorded_turn_seq != resolution.recorded_turn_seq
                    or current.server_instance != resolution.server_instance
                ):
                    return
                if await run_file_operation(status_file.live_writer_holds_session, identifier, self._state_root()):
                    return
                if any(process.exists() for process in current.executor_processes):
                    return
                if info is None:
                    raise ActionableError(
                        f"子の再開情報を復元できない: {identifier}",
                        next_action=f"`atk agents show {identifier}`で起動入力を確認する",
                    )
                await self._publish_registry_terminal(
                    identifier, info, "interrupted", interruption=session_registry.OWNER_SERVER_STOPPED
                )
            else:
                await self.take_over_orphaned_session(identifier)
            resolution = await run_file_operation(session_registry.resolve, identifier, state_root=self._state_root())
            if resolution.state is session_registry.Resolution.RUNNING:
                return
            info = resolution.resume_info or resolution.handoff_info
        if resolution.state is session_registry.Resolution.UNREADABLE:
            raise ActionableError(
                f"子の登録簿を読めない: {identifier}",
                next_action=f"`atk agents show {identifier}`で診断と登録簿の所在を確認する",
            )
        if info is None:
            self._recovery_targets.discard(identifier)
            return
        if resolution.inferred_terminal:
            await self.take_over_orphaned_session(identifier)
            resolution = await run_file_operation(session_registry.resolve, identifier, state_root=self._state_root())
        previous = await self._restore_registry_session(identifier, resolution=resolution, include_handoff=True)
        if previous is None:
            previous = self.stopped_sessions.get(identifier) or self.expired_sessions.get(identifier)
        if previous is not None and resolution.interruption in (
            session_registry.OWNER_PROCESSES_EXITED,
            session_registry.OWNER_SERVER_STOPPED,
        ):
            # 以前のinterrupted結果は最終成果ではない。同じ会話の再開後の結果を待つ。
            pending, _ = self._start_resume(previous, _CONTINUE, None, on_failure=self._notify_recovery_failure)
            self._recovery_attempts[identifier] = pending.task
            return
        # 完了・失敗の未回収結果は再実行せず、通常のwaitへ渡す。明示中止は復活させない。
        self._recovery_targets.discard(identifier)
