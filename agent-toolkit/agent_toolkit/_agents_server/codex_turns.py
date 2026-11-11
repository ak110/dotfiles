"""Codexターンの開始と待機圧縮。"""

from __future__ import annotations

import asyncio
import typing
from typing import Any

from agent_toolkit._agents_server import codex_resume_state, wait_compaction
from agent_toolkit._agents_server.codex_instructions import _current_service_tier
from agent_toolkit._agents_server.codex_protocol import (
    DEFAULT_WAIT_TIMEOUT,
    AppServerError,
    JsonRpcResponseError,
    StartCandidateUnavailable,
    TurnStartResponseError,
    _client_pid,
    _WaitCompactionTurn,
)
from agent_toolkit._agents_server.codex_rpc import JsonRpcProcess
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.state import SessionState

if typing.TYPE_CHECKING:
    from agent_toolkit._agents_server.codex_manager_contract import CodexManager


class CodexTurnsMixin:
    """Codexターンの開始と待機圧縮。

    AppServerManagerへ合成して使うため、各メソッドでは合成後の共有状態と他の責務を参照する。
    """

    async def _mark_failed(
        self: CodexManager,
        session: SessionState,
        error: BaseException,
    ) -> None:
        """要求開始の失敗を終了状態へ反映し、待機者を起こす。"""
        public_error = {"message": str(error) or error.__class__.__name__}
        writer_conflict = isinstance(error, codex_resume_state.ThreadWriterConflictError)
        if writer_conflict:
            public_error["codexErrorInfo"] = codex_resume_state.WRITER_CONFLICT
        failure = public_error.copy()
        if isinstance(error, JsonRpcResponseError) and isinstance(error.data, dict):
            # RPCの追加dataは内部の切替判定だけへ渡し、公開結果へ保存しない。
            failure.update(error.data)
        # 読取中に届いた中断要求は、続く保留判定でも維持する。
        session.interrupt_requested = False
        limit = (
            None
            if isinstance(error, StartCandidateUnavailable) or writer_conflict
            else await self._read_usage_limit_failure(session, failure)
        )
        session.turn_id = ""
        session.status = "failed"
        session.plan = []
        session.record_current_item_start(None)
        session.commentary = ""
        session.diff_changed = False
        session.error = public_error
        if (
            session.codex_model_provider
            and session.codex_model_provider != session.codex_subscription_provider
            and not isinstance(error, StartCandidateUnavailable)
            and not writer_conflict
        ):
            session.error = {"message": "Codex connection rejected the request"}
        session.agent_message = ""
        session.protocol_warnings = []
        session.record_turn_rejected()
        session.record_turn_completed()
        if (
            not isinstance(error, StartCandidateUnavailable)
            and not writer_conflict
            and not self._hold_usage_limit_failure(session, limit)
        ):
            self._hold_provider_failure(session, failure)
        session.touch()
        await self._notify_waiters()

    async def _mark_turn_start_ambiguous(self: CodexManager, session: SessionState, error: BaseException) -> None:
        """turn/startの応答が失われたことを未終了状態へ反映する。"""
        if session.terminal:
            return
        session.status = "running"
        session.error = {"message": str(error) or error.__class__.__name__}
        session.record_turn_ambiguous()
        session.interrupt_requested = False
        session.record_turn_running()
        session.touch()
        await self._notify_waiters()

    async def _start_turn(self: CodexManager, session: SessionState, prompt: str, client: JsonRpcProcess | None = None) -> None:
        if client is None:
            client = await self._acquire_owned_client(session.session_id, session.launch_kind)
        self._start_auto_reset_monitor(session, client)
        # 再起動したApp Serverでturnを始める場合も、実行するプロセスの識別を登録簿へ反映する。
        await session.attach_executor_async(_client_pid(client))
        params: dict[str, Any] = {
            "threadId": session.session_id,
            "input": [{"type": "text", "text": prompt}],
            "cwd": session.cwd,
            "approvalPolicy": "never",
            "sandboxPolicy": {"type": "dangerFullAccess"},
            "serviceTier": await run_file_operation(_current_service_tier),
        }
        if session.model is not None:
            params["model"] = session.model
        if session.effort is not None:
            params["effort"] = session.effort
        response = await client.request(
            "turn/start",
            params,
            on_sent=lambda: self._mark_turn_start_sent(session, fast_mode=params["serviceTier"] == "priority"),
        )
        turn = response.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise TurnStartResponseError("turn/start returned no turn.id")
        session.record_turn_started(turn_id)
        session.touch()
        await self._notify_waiters()

    async def _notify_waiters(self: CodexManager) -> None:
        for session in self._codex_sessions():
            if wait_compaction.wait_key(session) is not None:
                compactor = self._wait_compactors.get(session.session_id)
                if compactor is None and self._owned_client(session) is not None:

                    async def usage(current: SessionState = session) -> dict[str, object]:
                        return {"totalTokens": current.context_tokens, "rawMaxTokens": current.context_window}

                    async def enqueue(key: str, current: SessionState = session) -> None:
                        await self._compact_wait(current, key)

                    compactor = wait_compaction.WaitCompaction(usage, enqueue)
                    self._wait_compactors[session.session_id] = compactor
                if compactor is not None:
                    compactor.observe(session)
        async with self._condition:
            self._condition.notify_all()

    async def _compact_wait(self: CodexManager, session: SessionState, key: str) -> None:
        """保存条件を変えず、所有接続の公開圧縮APIと通常追送を直列化する。

        観測と再検証はdocs/development/audit-records.mdの
        「agent-toolkit/agent_toolkit/_agents_server/codex.py：待機中の独立圧縮turn：2026年11月2日」にある。
        """
        async with session.turn_control_lock:
            if wait_compaction.wait_key(session) != key:
                return
            client = self._owned_client(session)
            if client is None:
                return
            compacting = _WaitCompactionTurn(session.turn_id)
            self._wait_compaction_turns[session.session_id] = compacting
        try:
            async with asyncio.timeout(DEFAULT_WAIT_TIMEOUT):
                async with session.turn_control_lock:
                    if wait_compaction.wait_key(session) != key:
                        compacting.done.set()
                        return
                    await client.request("thread/compact/start", {"threadId": session.session_id})
                await compacting.done.wait()
            if compacting.error is not None:
                raise AppServerError(compacting.error)
        except JsonRpcResponseError:
            # RPCによる明示拒否では圧縮turnが無い。待機と通常の追送へ戻れる。
            self._wait_compaction_turns.pop(session.session_id, None)
            compacting.done.set()
            raise
        finally:
            # 時間上限だけでは実行終了としない。後着の完了を観測するまで通常追送も待つ。
            if compacting.done.is_set():
                self._wait_compaction_turns.pop(session.session_id, None)

    @staticmethod
    def _turn_start_response_is_ambiguous(client: Any, error: BaseException) -> bool:
        """turn/startの失敗が実行状態を判定できない応答喪失であるかを返す。"""
        if isinstance(error, (JsonRpcResponseError, StartCandidateUnavailable, codex_resume_state.ThreadWriterConflictError)):
            return False
        if bool(getattr(client, "closed", False)) or getattr(client, "reader_failure", None) is not None:
            return False
        process = getattr(client, "process", None)
        return process is None or getattr(process, "returncode", None) is None

    @staticmethod
    def _mark_turn_start_sent(session: SessionState, *, fast_mode: bool) -> None:
        """turn/startの送信完了を応答待ちより先に記録する。"""
        session.record_turn_sent()
        session.fast_mode = fast_mode
        session.touch()
