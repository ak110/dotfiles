"""managerの各責務が共有するsession状態、engine別のbackendの保持と終端の通知先。"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import logging
import os
from typing import Any

from agent_toolkit._agents_server import (
    backends,
    resource_snapshot,
    session_registry,
    shared_layout,
    shared_roots,
    status_file,
    unavailable_candidates,
)
from agent_toolkit._agents_server.resume_waits import record_unobserved_sessions
from agent_toolkit._agents_server.state import (
    ResumePrompt,
    SessionResumeState,
    SessionState,
    add_lifecycle_listener,
    add_terminal_listener,
    add_touch_listener,
    selected_candidate,
)


@dataclasses.dataclass
class PendingResume:
    """timeout後も同じsessionから観測・共有する再開操作。"""

    state: SessionResumeState
    task: asyncio.Task[SessionState]
    prompt: ResumePrompt
    previous_result: dict[str, Any] | None = None

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
    """managerの各責務が共有するsession状態とbackendを保持し、終端したsessionの記録を引き継ぐ。"""

    def __init__(
        self,
        status_writer: status_file.StatusFileWriter | None | object = _DEFAULT_STATUS_WRITER,
    ) -> None:
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
        self._pending_unobserved_child_sessions: dict[str, tuple[int, set[str]]] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._auto_resume_task: asyncio.Task[None] | None = None
        self._status_writer: status_file.StatusFileWriter | None
        if status_writer is _DEFAULT_STATUS_WRITER:
            identity = shared_roots.resolve_status_file_identity(os.environ)
            if identity is None:
                identity = shared_roots.create_process_root_identity()
            self._status_writer = status_file.StatusFileWriter(self.sessions, identity, expired_sessions=self.expired_sessions)
        else:
            assert status_writer is None or isinstance(status_writer, status_file.StatusFileWriter)
            self._status_writer = status_writer
        if self._status_writer is not None:
            add_touch_listener(self._status_writer.schedule)
        add_terminal_listener(self._carry_over_unavailable_candidate)
        add_terminal_listener(self._record_pending_unobserved_child_sessions)
        add_lifecycle_listener(self._record_resources)

    def _record_resources(self, session: SessionState, event: str) -> None:
        """自身が所有するsessionの遷移だけを共有状態とホスト資源へ対応付ける。"""
        if self._status_writer is None or self.sessions.get(session.session_id) is not session:
            return
        resource_snapshot.record(
            event,
            session,
            root_session_id=self._status_writer.root_session_id,
            sessions=self.sessions,
            state_root=self._status_writer.state_root,
        )

    async def _refresh_heartbeat(self) -> None:
        """MCPサーバーの生存中に状態ファイルの生存の印を更新する。"""
        assert self._status_writer is not None
        while True:
            await asyncio.sleep(status_file.HEARTBEAT_INTERVAL_SECONDS)
            self._status_writer.flush()

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
            )
            self._backends[engine] = backend
        return backend

    @staticmethod
    def _unavailable_reason(session: SessionState) -> str | None:
        """終端したsessionがengineの可用性を理由に失敗した場合、その除外理由をengineのbackendから得る。"""
        backend_type = backends.backend_class(session.engine)
        return None if backend_type is None else backend_type.unavailable_reason(session)

    def _expire_session(self, session_id: str) -> None:
        """期限に到達したsession本体を解放し、再開状態と未回収結果を保持する。"""
        self._pending_unobserved_child_sessions.pop(session_id, None)
        session = self.sessions.pop(session_id, None)
        if session is not None:
            if session.publish_registry:
                session_registry.release(session_id, reason="retention_expired")
            resume_state = SessionResumeState.from_session(session)
            self.expired_sessions[session_id] = resume_state
            if self._status_writer is not None:
                self._status_writer.retain_result(resume_state)
                self._status_writer.schedule()

    def _launcher_session_id(self) -> str | None:
        """作成するsessionの委譲元sessionの識別子を返す。

        状態ファイルの書込主体が射影する委譲元、所有ルート（プロセス専用ルートを除く）、
        呼び出しプロセスの環境の`CODEX_THREAD_ID`の順に解決する。
        """
        if self._status_writer is not None:
            launcher = self._status_writer.launcher_session_id()
            if launcher is not None:
                return launcher
        codex_thread_id = os.environ.get("CODEX_THREAD_ID")
        return codex_thread_id if codex_thread_id and shared_layout.valid_session_id(codex_thread_id) else None

    def _carry_over_unavailable_candidate(self, session: SessionState) -> None:
        """可用性を理由に終端した候補を、同じ起動条件の次回へ引き継ぐ。

        終端結果が確定した時点の通知として`SessionState.touch`から呼ぶ。
        結果本文の受領、`stop`による破棄、保持期限切れのいずれの場合も記録を保存できるよう、
        記録の契機を終端の確定点だけに置く。共有の通知先は全managerへ届くため、
        自身が保持するsessionだけを記録の対象とする。
        """
        if self.sessions.get(session.session_id) is not session:
            return
        candidate = selected_candidate(session)
        reason = self._unavailable_reason(session)
        if reason is None or candidate is None or session.model_type is None:
            return
        unavailable_candidates.record_unavailable_candidate(
            session.model_type,
            session.launch_kind,
            candidate,
            reason,
            now=datetime.datetime.now(datetime.UTC),
        )

    def _record_pending_unobserved_child_sessions(self, session: SessionState) -> None:
        """自動再開をまたいで保持した未観測の孫sessionを終端結果へ併合する。"""
        if self.sessions.get(session.session_id) is not session:
            return
        pending = self._pending_unobserved_child_sessions.get(session.session_id)
        if pending is None or pending[0] != session.turn_seq:
            return
        _, unobserved = self._pending_unobserved_child_sessions.pop(session.session_id)
        if unobserved:
            record_unobserved_sessions(session, unobserved)

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()
