"""Codex会話への追送と中断。"""

from __future__ import annotations

import asyncio
import contextlib
import typing
from collections.abc import AsyncGenerator
from typing import Any

from agent_toolkit._agents_server import codex_resume_state, result_projection, resume_waits
from agent_toolkit._agents_server.codex_protocol import (
    DEFAULT_WAIT_TIMEOUT,
    AppServerError,
    JsonRpcResponseError,
    _response_turn_id,
)
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.input_validation import validate_prompt
from agent_toolkit._agents_server.state import SessionState, begin_reply, initialize_turn
from agent_toolkit._atk.usage_limit_policy import should_wait
from agent_toolkit._common import codex_usage_limit
from agent_toolkit._common.next_action import ActionableError

if typing.TYPE_CHECKING:
    from agent_toolkit._agents_server.codex_manager_contract import CodexManager


class CodexRepliesMixin:
    """Codex会話への追送と中断。

    AppServerManagerへ合成して使うため、各メソッドでは合成後の共有状態と他の責務を参照する。
    """

    async def send_message(self: CodexManager, session: SessionState, prompt: str) -> dict[str, Any]:
        """実行中turnへ追加指示を送り、終了競合時は同じthreadのreplyを開始する。"""
        validate_prompt(prompt)
        async with self._input_turn_control(session):
            if (
                session.awaiting_auto_resume
                and not resume_waits.is_observation_preserving_reply(session)
                and not session.codex_provider_resume_pending
                and session.pending_result is not None
            ):
                resume_waits.finalize_pending_result(session, touch=False)
            if session.awaiting_auto_resume and session.pending_result is not None:
                delivery, status, _ = await self._start_reply_locked(session, prompt)
                return {"delivery": delivery, "previous_result": {}, **status}
            if session.terminal:
                previous_result = self._capture_result(session)
                delivery, status, error = await self._start_reply_locked(session, prompt)
                if error is not None and delivery not in {"reply_failed", "reply_ambiguous"}:
                    raise error from None
                result = {
                    "delivery": delivery,
                    "previous_result": previous_result,
                    **status,
                }
                return result
            if session.interrupt_requested:
                raise ActionableError(
                    "the active Codex turn is being interrupted", next_action=result_projection.RESEND_AFTER_WAIT_NEXT_ACTION
                )
            if not session.turn_id:
                raise ActionableError(
                    "the active Codex turn has no turn_id", next_action=result_projection.RESEND_AFTER_WAIT_NEXT_ACTION
                )
            client = self._owned_client(session)
            if client is None:
                raise AppServerError("Codex App Server client is unavailable for steering")
            expected_turn_id = session.turn_id
            try:
                response = await client.request(
                    "turn/steer",
                    {
                        "threadId": session.session_id,
                        "expectedTurnId": expected_turn_id,
                        "input": [{"type": "text", "text": prompt}],
                    },
                )
            except JsonRpcResponseError as exc:
                outcome = await self._wait_after_steer_rejection(session, expected_turn_id, client)
                if outcome == "completed":
                    previous_result = self._capture_result(session)
                    delivery, status, error = await self._start_reply_locked(session, prompt)
                    if error is not None and delivery not in {"reply_failed", "reply_ambiguous"}:
                        raise error from None
                    return {
                        "delivery": delivery,
                        "previous_result": previous_result,
                        **status,
                    }
                raise exc from None
            response_turn_id = _response_turn_id(response)
            if response_turn_id != expected_turn_id:
                raise AppServerError(
                    f"turn/steer returned an unexpected turn.id: expected {expected_turn_id}, got {response_turn_id or 'none'}"
                )
            session.touch()
            return {
                "delivery": "steered",
                **session.public_status(),
            }

    @contextlib.asynccontextmanager
    async def _input_turn_control(self: CodexManager, session: SessionState) -> AsyncGenerator[None]:
        # 圧縮の完了待ちでは停止用のロックを保持しない。取得後も予約との競合を再確認する。
        async with asyncio.timeout(DEFAULT_WAIT_TIMEOUT):
            while True:
                compacting = self._wait_compaction_turns.get(session.session_id)
                if compacting is not None:
                    await compacting.done.wait()
                await session.turn_control_lock.acquire()
                if session.session_id not in self._wait_compaction_turns:
                    break
                session.turn_control_lock.release()
        try:
            await self._cancel_auto_reset_check(session.session_id)
            yield
        finally:
            session.turn_control_lock.release()

    async def release_session(self: CodexManager, session: SessionState) -> None:
        """sessionが所有するApp Serverと子孫（threadごとのMCPサーバーと子孫threadの資源）を終了させる。

        購読解除はApp Serverがthreadの資源を猶予の後に解放する非同期の要求であり、解放の完了を表さない。
        このため所有するApp Serverそのものを終了させ、App Serverと全ての子孫の終了を確認してから戻る。
        終了を確認できない対象が残る場合は`ResourceReleaseError`を送出し、接続の所有を保って再試行に備える。
        他のsessionの接続は変更しない。解放後の`send_message`は新しいApp Serverで`thread/resume`して会話を復元する。
        """
        await self._release_owned_client(session.session_id)
        session.codex_subagent_thread_ids.clear()

    async def interrupt(self: CodexManager, session: SessionState) -> None:
        """公開killから対象turnへ中断要求を送り、受理を待つ。

        結果を保留している間はモデルのturnが終わっているため、中断要求を送らずに保留した結果を確定する。
        """
        await self._cancel_auto_reset_check(session.session_id)
        if session.awaiting_auto_resume and session.pending_result is not None:
            resume_waits.finalize_pending_result(session)
            await self._notify_waiters()
            return
        if session.terminal:
            return
        if not session.turn_id:
            raise ActionableError(
                "the active Codex turn has no turn_id",
                next_action="`atk agents wait`で状態を確認し、turnが続いていて中断が必要なら`kill`を再発行する",
            )
        client = self._owned_client(session)
        if client is None:
            raise AppServerError("Codex App Server client is unavailable for interrupt")
        try:
            await client.request(
                "turn/interrupt",
                {"threadId": session.session_id, "turnId": session.turn_id},
            )
        except JsonRpcResponseError:
            if session.terminal:
                return
            raise

    async def _start_reply_locked(
        self: CodexManager,
        session: SessionState,
        prompt: str,
    ) -> tuple[str, dict[str, Any], BaseException | None]:
        """lock取得済みのsessionへreplyを開始し、公開状態と失敗分類を返す。"""
        held = resume_waits.HeldTurn.capture(session)
        # 解除待ちの連鎖では、獲得resetによるweekly解除も同じ接続へ継続する。
        switch_needed = self._provider_failure(session) and session.usage_limit_wait_count == 0
        previous_error = session.pending_result["error"] if session.pending_result is not None else session.error
        if (
            switch_needed
            and isinstance(previous_error, dict)
            and previous_error.get("codexErrorInfo") == "usageLimitExceeded"
            and not isinstance(previous_error.get("usageLimit"), dict)
        ):
            # 子session待ちが上限の通知より先に結果を保留した場合も、同じ解除待ち方針を使う。
            limit = await run_file_operation(codex_usage_limit.from_thread, session.session_id)
            switch_needed = not should_wait("codex", limit.limit_type if limit else None)
        if session.codex_provider_resume_pending:
            # 選定には保留理由が必要。選定条件を確定してから今回の切替待ちを消費する。
            resume_waits.finalize_pending_result(session, touch=False)
        previous_status = session.status
        switched = False
        try:
            if switch_needed:
                # cold resumeも追送の排他区間に含まれるため、kill/stopへ終了時の結果として渡さない。
                session.status = "starting"
                session.touch()
            client = await self._acquire_owned_client(session.session_id, session.launch_kind)
            if switch_needed:
                switched = await self._switch_provider(session, client)
                if not switched or session.interrupt_requested:
                    if held is not None and not session.interrupt_requested:
                        resume_waits.finalize_pending_result(session)
                    elif not session.interrupt_requested:
                        session.status = previous_status
                    session.touch()
                    return "reply_failed", session.public_status(), None
            writer_session_id = self._writer_session_ids.get(session.session_id)
            if not switched:
                writer_session_id = await self._resume_thread(session, client, writer_session_id)
            if writer_session_id is not None:
                self._writer_session_ids[session.session_id] = writer_session_id
            begin_reply(session, preserve_waits=held is not None)
        except asyncio.CancelledError:
            if held is not None:
                held.restore(session)
            elif switch_needed:
                session.status = previous_status
                session.touch()
            raise
        except codex_resume_state.IncompatibleResumeError as exc:
            if held is not None:
                held.restore(session)
            await self._mark_incompatible_resume(session, exc, new_reply=True)
            return "reply_failed", session.public_status(), None
        except codex_resume_state.ThreadWriterConflictError as exc:
            if session.pending_result is not None:
                resume_waits.finalize_pending_result(session, touch=False)
            begin_reply(session, preserve_waits=held is not None)
            await self._mark_failed(session, exc)
            return "reply_failed", {**session.public_status(), "error": session.error}, exc
        except Exception as exc:
            if held is not None:
                held.restore(session)
                await self._notify_waiters()
                return "reply_failed", session.public_status(), exc
            await self._mark_failed(session, exc)
            return "reply_failed", session.public_status(), exc
        try:
            await self._start_turn(session, prompt, client)
        except asyncio.CancelledError:
            if held is not None and not session.turn_start_sent:
                held.restore(session)
            elif held is not None:
                session.record_turn_ambiguous()
                session.touch()
            raise
        except Exception as exc:
            if self._turn_start_response_is_ambiguous(client, exc):
                await self._mark_turn_start_ambiguous(session, exc)
                return "reply_ambiguous", session.public_status(), None
            if held is not None:
                held.restore(session)
                await self._notify_waiters()
                return "reply_failed", session.public_status(), exc
            await self._mark_failed(session, exc)
            return "reply_failed", session.public_status(), exc
        return "reply_started", session.public_status(), None

    async def _wait_after_steer_rejection(
        self: CodexManager,
        session: SessionState,
        expected_turn_id: str,
        client: Any,
    ) -> str:
        """steer拒否後に終了競合だけを待ち、優先順位付きの判定結果を返す。"""
        timed_out = False

        def _changed() -> bool:
            return bool(
                getattr(client, "closed", False)
                or getattr(client, "reader_failure", None) is not None
                or session.turn_id != expected_turn_id
                or session.result_available
            )

        try:
            async with self._condition:
                await asyncio.wait_for(self._condition.wait_for(_changed), timeout=DEFAULT_WAIT_TIMEOUT)
        except TimeoutError:
            timed_out = True
        if getattr(client, "closed", False) or getattr(client, "reader_failure", None) is not None:
            return "client_failure"
        if session.turn_id != expected_turn_id:
            return "turn_changed"
        if timed_out:
            return "timeout"
        if session.result_available:
            return "completed"
        return "timeout"

    @staticmethod
    def _capture_result(session: SessionState) -> dict[str, Any]:
        """継続入力へ直前turnの結果を退避する。"""
        return session.previous_result()

    @staticmethod
    def _initialize_turn(session: SessionState) -> None:
        initialize_turn(session)

    @staticmethod
    def _begin_reply(session: SessionState) -> None:
        begin_reply(session)
