"""MCPの待機操作で、保持するsessionの終端と保持期限内の結果を待って返す処理。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent_toolkit._agents_server import (
    manager_resume,
)
from agent_toolkit._agents_server.responses import (
    turn_elapsed_seconds,
)
from agent_toolkit._agents_server.state import (
    SessionState,
)
from agent_toolkit._common import wait_schedule as _wait_schedule

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


class ManagerWait(manager_resume.ManagerResume):
    """保持するsessionの終端と保持期限内の結果を待ち、到着した結果と通知を返す。"""

    async def _resolve_wait_timeout(self, request_bucket: str) -> float:
        """bucketごとに省略時に使う待機上限を導出し、同じbucketの以降の呼び出しへ再利用する。

        導出は`claude auth status`の実行を伴い得るため、イベントループ上で直接実行しない。
        """
        cached = self._wait_timeouts.get(request_bucket)
        if cached is not None:
            return cached
        resolved = await asyncio.to_thread(_wait_schedule.get_wait_timeout, request_bucket)
        self._wait_timeouts[request_bucket] = resolved
        return resolved

    def _wait_target_ids(self) -> list[str]:
        """待機の対象となる保持中sessionを識別子順に返す。

        未回収の終端結果を持つ破棄済みまたは期限切れsessionも対象へ含め、
        委譲元が回収する前に結果本文を失わないようにする。
        """
        targets = set(self.sessions) | set(self._pending_resumes)
        for session_id, resume_state in (*self.expired_sessions.items(), *self.stopped_sessions.items()):
            if self._stopped_result_response(resume_state) is not None:
                targets.add(session_id)
        return sorted(targets)

    def _retained_result_response(self, session_id: str, *, collector: str) -> dict[str, Any] | None:
        """破棄済みまたは期限切れsessionの未回収の終端結果だけを返す。"""
        stopped_state = self._resolve_stopped_session(session_id)
        if stopped_state is not None:
            response = self._take_stopped_result(session_id, stopped_state, collector=collector)
            if response is None:
                return None
            return self._response_with_notices(response, self._take_notices(session_id))
        if self._resolve_expired_session(session_id) is None:
            return None
        response = self._expired_result_response(session_id, collector=collector)
        if response is None or "agent_message" not in response:
            return None
        return self._response_with_notices(response, self._take_notices(session_id))

    async def wait(self) -> dict[str, Any]:
        """委譲先の終端を待ち、終端時だけ結果本文を返す。

        引数を受け取らない。対象は本MCPサーバープロセスが保持する起動中のsession全体とし、最初に終端した1件の結果を返す。
        残るsessionの終端結果は次の呼び出しまで保持する。
        待機上限はプロンプトキャッシュの保持期間から導出した値とし、委譲先として起動されたセッションでは240秒を上限とする。
        この上限へ達した応答は`status`と`elapsed_seconds`を返す。
        保持中のsessionの最終活動時刻と停滞の印は`show`が返す。待機せずに現状態を確認する場合は`show`を発行する。
        以下の`/goal`の条件に該当しない場合は、本ツールを前景で発行する。
        委譲元のセッションに`/goal`が設定され、未完了のバックグラウンドタスクが本ツールの呼び出しを移したものだけになる場合は、
        公開MCP toolではなく、`atk agents wait`を実行ホストの前景またはバックグラウンドタスクとして起動する。
        委譲先がバックグラウンドタスクを残してturnを終えた場合は、同じsessionを一度だけ自動的に再開し、再開したturnの終端まで待つ。
        委譲元はバックグラウンドタスクの完了後に`send_message`で再開を指示しない。
        終端前に`status: running`が返った場合は、本ツールを再発行して待機を継続する。
        終端結果は委譲元が最初の呼び出しで受領するまで保持し、経過時間では解放しない。
        受領した終端結果のsessionを破棄する場合は`stop`を発行する。
        終端結果を残さずにsessionが失われた場合だけ、`status`が`expired`の応答を返す。
        委譲先が実行中に`atk agents-notify`で送った通知が未回収である場合は、終端前でもその通知を`notices`へ載せて復帰する。
        再待機の要否は`notices`の有無ではなく`status`で判定する。
        `status`が`completed`、`failed`、`interrupted`のいずれかである応答は終端であり、`notices`を含む場合も結果本文とともに受領して本ツールを再発行しない。
        応答へ載せた通知は回収済みとして再び返さない。
        """
        timeout = await self._resolve_wait_timeout("main")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        ordered_ids = self._wait_target_ids()
        # 保留中の結果を進める判定は待機の刻みごとに1回だけ行う。
        # backendはバックグラウンドタスクの完了通知で受け取った再開turnの結果へ保留中の結果を差し替えるため、
        # 通知のたびに判定するとその差し替えの前に保留中の結果を確定してしまう。
        advance_pending = True

        while True:
            for session_id in ordered_ids:
                retained_response = self._retained_result_response(session_id, collector="mcp-wait")
                if retained_response is not None:
                    return {"session_id": session_id, **retained_response}
                session = self.sessions.get(session_id)
                if session is not None and advance_pending:
                    await self._advance_child_session_wait(session)
            advance_pending = False
            async with self._condition:
                terminal: list[SessionState] = []
                for session_id in ordered_ids:
                    candidate = self.sessions.get(session_id)
                    if candidate is not None and candidate.result_available and not candidate.result_delivered:
                        if self._status_writer is not None and self._status_writer.result_state(session_id) == "consumed":
                            candidate.result_delivered = True
                            continue
                        terminal.append(candidate)
                terminal.sort(key=lambda candidate: (candidate.finalized_at or "", candidate.session_id))
                if terminal:
                    session = terminal[0]
                    if self._status_writer is not None and self._status_writer.result_state(session.session_id) == "published":
                        claimed, claim_error = self._status_writer.take_result(session.session_id, collector="mcp-wait")
                        if claim_error is not None:
                            raise RuntimeError(f"終端結果を回収できません: {session.session_id}: {claim_error}")
                        if claimed is None:
                            session.result_delivered = True
                            continue
                    _LOG.info("result_collected session_id=%s collector=mcp-wait", session.session_id)
                    response = self._response_with_notices(
                        self._result_response(session), self._take_notices(session.session_id)
                    )
                    return {"session_id": session.session_id, **response}

                for session_id in ordered_ids:
                    session = self.sessions.get(session_id)
                    if session is None:
                        continue
                    notices = self._take_notices(session_id)
                    if notices:
                        response = self._response_with_notices(self._result_response(session), notices)
                        return {"session_id": session_id, **response}

                retained = [self.sessions[session_id] for session_id in ordered_ids if session_id in self.sessions]
                pending_ids = [session_id for session_id in ordered_ids if session_id in self._pending_resumes]
                if not retained and not pending_ids:
                    if not ordered_ids:
                        return {"status": "expired"}
                    session_id = ordered_ids[0]
                    return {"session_id": session_id, "status": "expired"}

                remaining = deadline - loop.time()
                if remaining <= 0:
                    if not retained:
                        session_id = pending_ids[0]
                        response = self._pending_resume_status(self._pending_resumes[session_id])
                        return {"session_id": session_id, **response}
                    undelivered = next((candidate for candidate in retained if not candidate.result_delivered), None)
                    if undelivered is not None:
                        return {"session_id": undelivered.session_id, **self._result_response(undelivered)}
                    session = retained[0]
                    timeout_response: dict[str, Any] = {"status": "running", "progress": ""}
                    elapsed_seconds = turn_elapsed_seconds(session.started_at)
                    if elapsed_seconds is not None:
                        timeout_response["elapsed_seconds"] = elapsed_seconds
                    return {"session_id": session.session_id, **timeout_response}
                interval = 0.1 if any(candidate.awaiting_auto_resume for candidate in retained) else 1.0
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=min(interval, remaining))
                except TimeoutError:
                    advance_pending = True
