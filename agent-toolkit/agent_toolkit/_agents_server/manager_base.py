"""managerの各責務が共有するsession状態、engine別のbackendの保持と終了の通知先。"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import datetime
import logging
import os
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import (
    backends,
    record_paths,
    resource_snapshot,
    session_identity,
    shared_layout,
    shared_roots,
    status_file,
    unavailable_candidates,
)
from agent_toolkit._agents_server.file_operations import finish_task, run_file_operation
from agent_toolkit._agents_server.state import (
    ResumePrompt,
    SessionResumeState,
    SessionState,
    add_lifecycle_listener,
    add_terminal_listener,
    add_touch_listener,
    selected_candidate,
)
from agent_toolkit._common import session_registry


@dataclasses.dataclass
class PendingResume:
    """timeout後も同じsessionから観測・共有する再開操作。"""

    state: SessionResumeState
    task: asyncio.Task[SessionState]
    prompt: ResumePrompt
    previous_result: dict[str, Any] | None = None

    @property
    def delivery(self) -> str:
        """backendが要求単位で保持した配送結果を返す。"""
        return self.prompt.delivery

    def discard_previous_result(self) -> None:
        """配送または明示的な破棄の後に退避済み結果本文を除く。"""
        self.previous_result = None

    def take_previous_result(self) -> dict[str, Any] | None:
        """退避済み結果を返し、進行中再開から本文を取り除く。"""
        result = self.previous_result
        self.discard_previous_result()
        return result


_DEFAULT_STATUS_WRITER = object()


_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


class ManagerBase:
    """managerの各責務が共有するsession状態とbackendを保持し、終了したsessionの記録を引き継ぐ。"""

    def __init__(
        self,
        status_writer: status_file.StatusFileWriter | None | object = _DEFAULT_STATUS_WRITER,
        *,
        launcher_environment: Mapping[str, str] | None = None,
    ) -> None:
        environment = os.environ if launcher_environment is None else launcher_environment
        self._launcher_codex_thread_id = environment.get("CODEX_THREAD_ID")
        self.sessions: dict[str, SessionState] = (
            status_writer.sessions if isinstance(status_writer, status_file.StatusFileWriter) else {}
        )
        self.expired_sessions: dict[str, SessionResumeState] = (
            status_writer.expired_sessions if isinstance(status_writer, status_file.StatusFileWriter) else {}
        )
        self.stopped_sessions: dict[str, SessionResumeState] = {}
        self._pending_resumes: dict[str, PendingResume] = {}
        self._condition = asyncio.Condition()
        self._resume_lock = asyncio.Lock()
        self._backends: dict[str, backends.Backend] = {}
        self._wait_timeouts: dict[str, float] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._auto_resume_task: asyncio.Task[None] | None = None
        self._retention_timers: dict[str, asyncio.TimerHandle] = {}
        self._resource_release_tasks: dict[str, asyncio.Task[None]] = {}
        self._candidate_tasks: set[asyncio.Task[None]] = set()
        self._diagnostic_tasks: set[asyncio.Task[None]] = set()
        self._diagnostic_lock = asyncio.Lock()
        self._status_writer: status_file.StatusFileWriter | None
        if status_writer is _DEFAULT_STATUS_WRITER:
            identity = shared_roots.resolve_status_file_identity(environment)
            if identity is None:
                identity = shared_roots.create_process_root_identity()
            self._status_writer = status_file.StatusFileWriter(self.sessions, identity, expired_sessions=self.expired_sessions)
        else:
            assert status_writer is None or isinstance(status_writer, status_file.StatusFileWriter)
            self._status_writer = status_writer
        self._validate_session_id = session_identity.identity_validator(
            self.sessions,
            None if self._status_writer is None else self._status_writer.root_session_id,
            self.expired_sessions,
            self.stopped_sessions,
        )
        if self._status_writer is not None:
            add_touch_listener(self._status_writer.schedule)
        add_terminal_listener(self._carry_over_unavailable_candidate)
        add_terminal_listener(self._schedule_session_expiry)
        add_lifecycle_listener(self._record_resources)
        add_lifecycle_listener(self._cancel_expiry_on_start)

    def _cancel_retention_timer(self, session_id: str) -> None:
        """再開・明示解放により不要になった期限通知を取り消す。"""
        timer = self._retention_timers.pop(session_id, None)
        if timer is not None:
            timer.cancel()

    def _cancel_expiry_on_start(self, session: SessionState, event: str) -> None:
        """同じsessionで次のturnを始めたら、旧turnの保持期限を取り消す。"""
        if event == "start" and self.sessions.get(session.session_id) is session:
            self._cancel_retention_timer(session.session_id)

    def _schedule_session_expiry(self, session: SessionState) -> None:
        """終了の確定時に期限通知を登録し、ツール照会の無いsessionも解放する。"""
        if self.sessions.get(session.session_id) is not session or session.retention_deadline is None:
            return
        self._cancel_retention_timer(session.session_id)
        self._retention_timers[session.session_id] = asyncio.get_running_loop().call_at(
            session.retention_deadline, self._expire_retained_session, session
        )

    def _expire_retained_session(self, session: SessionState) -> None:
        """期限通知が指す同じ終了状態だけを解放し、再開後のturnを解放しない。"""
        if self.sessions.get(session.session_id) is not session or session.retention_deadline is None:
            return
        if asyncio.get_running_loop().time() < session.retention_deadline:
            self._schedule_session_expiry(session)
            return
        self._expire_session(session.session_id)

    def _finish_resource_release(self, session_id: str, task: asyncio.Task[None]) -> None:
        """解放taskの失敗を記録し、成功済みのtaskを保持しない。"""
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOG.error("保持期限に到達したsessionの資源解放に失敗しました: %s", error, exc_info=error)
        elif self._resource_release_tasks.get(session_id) is task:
            del self._resource_release_tasks[session_id]

    async def _wait_for_resource_release(self, session_id: str) -> None:
        """期限解放が進行中なら完了を待ち、再開したthreadを遅れて解放する競合を防ぐ。"""
        task = self._resource_release_tasks.get(session_id)
        if task is not None:
            try:
                await finish_task(task)
            finally:
                if task.done() and self._resource_release_tasks.get(session_id) is task:
                    del self._resource_release_tasks[session_id]

    def _record_resources(self, session: SessionState, event: str) -> None:
        """自身が所有するsessionの遷移だけを共有状態とホスト資源へ対応付ける。"""
        if self._status_writer is None or self.sessions.get(session.session_id) is not session:
            return
        task = asyncio.create_task(
            self._record_resource_snapshot(
                event,
                copy.copy(session),
                root_session_id=self._status_writer.root_session_id,
                sessions={key: copy.copy(value) for key, value in self.sessions.items()},
                state_root=self._status_writer.state_root,
            )
        )
        self._diagnostic_tasks.add(task)

    async def _record_resource_snapshot(self, *args: Any, **kwargs: Any) -> None:
        async with self._diagnostic_lock:
            await run_file_operation(resource_snapshot.record, *args, **kwargs)

    async def _drain_diagnostics(self) -> None:
        """取得を開始した診断の完了を全件回収する。"""
        tasks = tuple(self._diagnostic_tasks)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self._diagnostic_tasks.difference_update(tasks)
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _refresh_heartbeat(self) -> None:
        """MCPサーバーの生存中に状態ファイルの生存の印を更新する。"""
        assert self._status_writer is not None
        while True:
            await asyncio.sleep(status_file.HEARTBEAT_INTERVAL_SECONDS)
            try:
                await self._status_writer.flush_async()
            except OSError:
                _LOG.exception(
                    "heartbeatの保存に失敗しました。次回の更新で再試行します: writer=%s sessions=%s",
                    self._status_writer.path,
                    sorted(self.sessions),
                )

    def _backend(self, engine: str) -> backends.Backend:
        """engineのbackendを返す。初めて使うengineではbackendを生成して保持する。"""
        backend = self._backends.get(engine)
        if backend is None:
            backend = backends.create_backend(
                engine,
                self.sessions,
                self._condition,
                expire_session=self._expire_session,
                root_session_id=None if self._status_writer is None else self._status_writer.root_session_id,
                log_directory=self._status_writer.path.parent / "logs" if self._status_writer is not None else None,
                validate_session_id=self._validate_session_id,
            )
            self._backends[engine] = backend
        return backend

    @staticmethod
    def _unavailable_reason(session: SessionState) -> str | None:
        """終了したsessionがengineの可用性を理由に失敗した場合、その除外理由をengineのbackendから得る。"""
        backend_type = backends.backend_class(session.engine)
        return None if backend_type is None else backend_type.unavailable_reason(session)

    def _expire_session(self, session_id: str) -> None:
        """期限に到達したsession本体を解放し、再開状態と未回収結果を保持する。"""
        self._cancel_retention_timer(session_id)
        session = self.sessions.pop(session_id, None)
        if session is not None:
            resume_state = SessionResumeState.from_session(session)
            task = asyncio.create_task(self._release_expired_session(session, resume_state))
            self._resource_release_tasks[session_id] = task
            task.add_done_callback(lambda completed: self._finish_resource_release(session_id, completed))
            self.expired_sessions[session_id] = resume_state
            if self._status_writer is not None:
                self._status_writer.schedule()

    async def _release_expired_session(self, session: SessionState, resume_state: SessionResumeState) -> None:
        expected_owner = (session_registry.owner_server_instance(), session.turn_seq)
        try:
            await session.drain_registration()
            if self._status_writer is not None:
                await self._status_writer.retain_result_async(resume_state)
            await self._backend(session.engine).release_session(session)
            if session.publish_registry:
                await run_file_operation(
                    session_registry.release, session.session_id, reason="retention_expired", expected_owner=expected_owner
                )
        except BaseException:
            # 解放に失敗したbackendを、公開stopが再試行できる所有状態へ戻す。
            # 期限の自動再発火は抑え、保存済みの結果と資源の登録を維持する。
            if self.expired_sessions.get(session.session_id) is resume_state:
                self.expired_sessions.pop(session.session_id)
                session.retention_deadline = None
                self.sessions[session.session_id] = session
                if self._status_writer is not None:
                    self._status_writer.schedule()
            raise

    def launcher_session_id(self) -> str | None:
        """作成するsessionの委譲元sessionの識別子を返す。

        状態ファイルの書込主体が射影する委譲元、所有ルート（プロセス専用ルートを除く）、
        呼び出しプロセスの環境の`CODEX_THREAD_ID`の順に解決する。
        公開`start`は通常起動の全体作業契約と専用記録先の解決にも同じ値を使う。
        """
        if self._status_writer is not None:
            launcher = self._status_writer.launcher_session_id()
            if launcher is not None:
                return launcher
        codex_thread_id = self._launcher_codex_thread_id
        return codex_thread_id if codex_thread_id and shared_layout.valid_session_id(codex_thread_id) else None

    def launcher_record_context(self) -> dict[str, Any]:
        """直接の委譲元と、一意に解決した記録ファイルをtask本文へ渡す。"""
        launcher = self.launcher_session_id()
        if launcher is None:
            return {"status": "unresolved"}
        context: dict[str, Any] = {"launcher_session_id": launcher}
        try:
            record = record_paths.find_session_record(launcher)
        except OSError as error:
            _LOG.warning("委譲元の記録を取得できません: session_id=%s", launcher, exc_info=True)
            return {**context, "status": "unreadable", "reason": str(error)}
        if record is None or not record.paths:
            return {**context, "status": "missing"}
        context["record_engine"] = record.engine
        if len(record.paths) != 1:
            return {**context, "status": "ambiguous", "record_candidates": [str(p.resolve()) for p in record.paths]}
        return {**context, "status": "resolved", "record_path": str(record.paths[0].resolve())}

    def _carry_over_unavailable_candidate(self, session: SessionState) -> None:
        """可用性を理由に終了した候補を、同じ起動条件の次回へ引き継ぐ。

        終了時の結果が確定した時点の通知として`SessionState.touch`から呼ぶ。
        結果本文の受領、`stop`による破棄、保持期限切れのいずれの場合も記録を保存できるよう、
        記録の契機を終了の確定点だけに置く。共有の通知先は全managerへ届くため、
        自身が保持するsessionだけを記録の対象とする。
        """
        if self.sessions.get(session.session_id) is not session:
            return
        candidate = selected_candidate(session)
        reason = self._unavailable_reason(session)
        if reason is None or candidate is None or session.model_type is None:
            return
        task = asyncio.create_task(
            run_file_operation(
                unavailable_candidates.record_unavailable_candidate,
                session.model_type,
                session.launch_kind,
                candidate,
                reason,
                now=datetime.datetime.now(datetime.UTC),
            )
        )
        self._candidate_tasks.add(task)
        task.add_done_callback(self._finish_candidate_record)

    def _finish_candidate_record(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            _LOG.error("候補除外記録の保存に失敗しました", exc_info=error)
        else:
            self._candidate_tasks.discard(task)

    async def _drain_candidate_records(self) -> None:
        """保存失敗も呼出元へ渡し、観測済みのtaskを保持し続けない。"""
        tasks = tuple(self._candidate_tasks)
        try:
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
        finally:
            self._candidate_tasks.difference_update(task for task in tasks if task.done())

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()
