"""実機で確認した独立圧縮turnと、担当結果の保留・追送の境界を再現する。"""

# 通知と所有接続はbackendの境界から駆動し、公開show/waitで結果を検収する。
# pylint: disable=protected-access
from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock

import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import codex, manager, state, wait_compaction
from agent_toolkit._testing.agents_server_support import (
    FakeCodexClient,
    QueueClaudeClient,
    ResultMessage,
    SystemMessage,
    claude_manager,
    install_backend,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize("launch_kind", ["delegate", "write", "explore", "shell"])
@pytest.mark.asyncio
async def test_claude_compacts_a_background_bash_wait_without_a_child_session(tmp_path, monkeypatch, launch_kind):
    """全起動区分で、CI待機と同じ子sessionを伴わない背景Bashを所有キュー経由で圧縮する。"""
    entered, release, compacted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    delays = []
    original = wait_compaction.WaitCompaction

    async def sleep(seconds):
        delays.append(seconds)
        entered.set()
        await release.wait()

    def observer(*args: Any, **kwargs: Any):
        return original(*args, sleep=sleep, **kwargs)

    monkeypatch.setattr(wait_compaction, "WaitCompaction", observer)

    class Client(QueueClaudeClient):
        async def get_context_usage(self):
            return {"totalTokens": 400_000, "rawMaxTokens": 1_000_000}

        async def query(self, prompt):
            await super().query(prompt)
            if prompt == "/compact":
                compacted.set()

    client = Client(
        [
            [
                SystemMessage("wait-claude"),
                claude_agent_sdk.TaskStartedMessage(
                    "task_started",
                    {},
                    "ci-task",
                    "CI待機",
                    "task-event",
                    "wait-claude",
                    task_type="local_bash",
                ),
                claude_agent_sdk.SystemMessage("session_state_changed", {"state": "idle"}),
                ResultMessage("待機中"),
            ],
            [ResultMessage("圧縮完了"), claude_agent_sdk.SystemMessage("session_state_changed", {"state": "idle"})],
        ]
    )
    async with claude_manager(monkeypatch, client) as (_owner, backend):
        session = await backend.start("CIの完了待ち", str(tmp_path), launch_kind=launch_kind)
        await asyncio.wait_for(entered.wait(), 2)
        assert delays == [60]
        assert session.awaiting_auto_resume and not session.live_child_session_ids
        release.set()
        await asyncio.wait_for(compacted.wait(), 2)
        async with asyncio.timeout(2):
            while session.pending_result is None:
                await asyncio.sleep(0)
        assert session.awaiting_auto_resume and not session.terminal
        assert client.queries.count("/compact") == 1


@pytest.mark.parametrize("outcome", ["completed", "failed", "rejected", "stop"])
@pytest.mark.asyncio
async def test_codex_compaction_keeps_held_result_until_child_reply(tmp_path, monkeypatch, outcome):
    """圧縮の完了・失敗を担当の終了とせず、子の結果による通常追送を保つ。"""
    async with manager.AgentsServerManager(None) as owner, codex.AppServerManager(owner.sessions, owner._condition) as backend:
        install_backend(owner, "codex", backend)
        client = FakeCodexClient()
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
        session = await backend.start("待機する作業", str(tmp_path))
        session.agent_message = "待機中"
        await backend._handle_notification(
            {
                "method": "item/completed",
                "params": {
                    "threadId": session.session_id,
                    "turnId": session.turn_id,
                    "item": {
                        "id": "child-start",
                        "type": "subAgentActivity",
                        "kind": "started",
                        "agentThreadId": "child",
                        "agentPath": "/root/child",
                    },
                },
            }
        )
        await backend._handle_notification(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": session.session_id,
                    "turn": {"id": session.turn_id, "status": "completed", "items": [], "error": None},
                },
            }
        )
        original_turn = session.turn_id
        accepted = asyncio.Event()
        request = client.request

        async def compact_request(method, params=None, **kwargs):
            if method != "thread/compact/start":
                return await request(method, params, **kwargs)
            assert params == {"threadId": session.session_id}
            accepted.set()
            if outcome == "rejected":
                raise codex.JsonRpcResponseError(method, -32600, "圧縮の明示拒否")
            return {}

        monkeypatch.setattr(client, "request", compact_request)
        key = wait_compaction.wait_key(session)
        assert key is not None
        compacting = asyncio.create_task(backend._compact_wait(session, key))
        try:
            await asyncio.wait_for(accepted.wait(), 2)
            if outcome == "stop":
                result = await owner.kill(session.session_id, timeout=1, stop=True)
                assert result["status"] == "completed" and client.closed
                assert result["error"]["heldResultFinalized"]
                with pytest.raises(codex.AppServerError, match="圧縮"):
                    await asyncio.wait_for(compacting, 2)
                return
            if outcome == "rejected":
                with pytest.raises(codex.JsonRpcResponseError):
                    await asyncio.wait_for(compacting, 2)
            else:
                # Codex CLI 0.166.0のthread/compact/startは{}を返し、別turnの通知で完了する。
                await backend._handle_notification(
                    {
                        "method": "turn/started",
                        "params": {
                            "threadId": session.session_id,
                            "turn": {"id": "compact-turn", "status": "inProgress"},
                        },
                    }
                )
                await backend._handle_notification(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": session.session_id,
                            "turnId": "compact-turn",
                            "item": {"type": "agentMessage", "id": "compact-answer", "text": "担当の結果ではない"},
                        },
                    }
                )
                assert not compacting.done()
                assert session.turn_id == original_turn and session.turn_seq == 1
                assert (await owner.show_session(session.session_id))["result_held"]
                assert session.agent_message == "待機中"
                await backend._handle_notification(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": session.session_id,
                            "turn": {"id": "compact-turn", "status": outcome, "items": [], "error": None},
                        },
                    }
                )
                if outcome == "failed":
                    with pytest.raises(codex.AppServerError, match="圧縮"):
                        await asyncio.wait_for(compacting, 2)
                else:
                    await asyncio.wait_for(compacting, 2)
            assert (await owner.show_session(session.session_id))["result_held"]
            await backend._handle_notification(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "child",
                        "turn": {"id": "child-turn", "status": "completed"},
                    },
                }
            )
            reply = await backend.send_message(session, "子の結果を回収せよ")
            assert reply["delivery"] == "reply_started"
            await backend._handle_notification(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": session.session_id,
                        "turnId": session.turn_id,
                        "item": {"type": "agentMessage", "id": "reply", "text": "子の結果を回収した"},
                    },
                }
            )
            await backend._handle_notification(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": session.session_id,
                        "turn": {"id": session.turn_id, "status": "completed", "items": [], "error": None},
                    },
                }
            )
            assert (await owner.wait())["agent_message"] == "子の結果を回収した"
        finally:
            if not compacting.done():
                compacting.cancel()
            await asyncio.gather(compacting, return_exceptions=True)


@pytest.mark.asyncio
async def test_codex_late_completion_releases_a_cancelled_compaction(tmp_path, monkeypatch):
    """受付後の取消しだけで圧縮終了とせず、後着完了の観測で所有状態を解く。"""
    session = state.SessionState("parent", str(tmp_path), engine="codex", turn_id="work-turn")
    session.live_child_session_ids.add("child")
    session.awaiting_auto_resume = True
    session.pending_result = {"status": "completed", "agent_message": "待機中", "error": None}
    session.turn_completed = True
    async with codex.AppServerManager({session.session_id: session}) as backend:
        client = FakeCodexClient()
        backend._clients[session.session_id] = cast(codex.JsonRpcProcess, client)
        accepted = asyncio.Event()

        async def compact_request(method, params=None, **kwargs):
            del params, kwargs
            assert method == "thread/compact/start"
            accepted.set()
            return {}

        monkeypatch.setattr(client, "request", compact_request)
        key = wait_compaction.wait_key(session)
        assert key is not None
        task = asyncio.create_task(backend._compact_wait(session, key))
        await asyncio.wait_for(accepted.wait(), 2)
        operation = backend._wait_compaction_turns[session.session_id]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not operation.done.is_set()
        assert session.session_id in backend._wait_compaction_turns
        await backend._handle_notification(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": session.session_id,
                    "turn": {"id": "compact-turn", "status": "completed", "items": []},
                },
            }
        )
        assert operation.done.is_set()
        assert session.session_id not in backend._wait_compaction_turns
        assert session.pending_result is not None and session.turn_id == "work-turn"
