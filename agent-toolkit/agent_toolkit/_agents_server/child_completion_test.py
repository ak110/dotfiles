"""Claudeの結果生成から、子完了の継続と最終wait受領までの観測寿命を検証する。

SDKの入出力だけをキューで代替する。2番目の子集合は、待機未起動から復元された
所有状態の入力として与え、過去の継続を消費した後でも観測責任が残る条件を作る。
"""

import asyncio
from types import SimpleNamespace

# SDK受信とManagerの公開観測の間を決定的に同期する。
# pylint: disable=protected-access
import pytest
from claude_agent_sdk import SystemMessage as SdkSystemMessage

from agent_toolkit._agents_server import claude, manager, wait_output_tracking
from agent_toolkit._atk import config
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    QueueClaudeClient,
    ResultMessage,
    SystemMessage,
    install_backend,
)


@pytest.mark.parametrize("duplicate", [False, True])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_consumed_resume_waits_for_uncollected_child_result(tmp_path, monkeypatch, duplicate):
    """idle・過去の継続後でも未回収子を保留し、新しい完了ごとに1回だけ継続する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [("claude", "model", "high")])
    client = QueueClaudeClient(
        [
            [SystemMessage("completion-parent"), AssistantMessage("初回作業")],
            [AssistantMessage("最初の子を確認した")],
            [
                AssistantMessage("次の子も確認した"),
                ResultMessage("全工程を確認した最終結果"),
                SdkSystemMessage(subtype="session_state_changed", data={"state": "idle"}),
            ],
        ]
    )
    async with (
        manager.AgentsServerManager(None) as owner,
        claude.ClaudeServerManager(owner.sessions, owner._condition, client_factory=lambda _options: client) as backend,
    ):
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        install_backend(owner, "claude", backend)
        started = await owner.start("execute", "子の結果を確認する", str(tmp_path))
        parent = owner.sessions[started["session_id"]]
        parent.live_child_session_ids.add("first-child")
        session_registry.publish("first-child", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(tmp_path))
        client.messages.put_nowait(SdkSystemMessage(subtype="session_state_changed", data={"state": "idle"}))
        client.messages.put_nowait(ResultMessage("最初の子を待つ"))
        async with owner._condition:
            await asyncio.wait_for(owner._condition.wait_for(lambda: parent.awaiting_auto_resume), 2)
        session_registry.publish("first-child", state=session_registry.PublicationState.RESULT_COMPLETED, cwd=str(tmp_path))
        await owner.wait()
        async with owner._condition:
            await asyncio.wait_for(
                owner._condition.wait_for(lambda: len(client.queries) == 2 and not parent.awaiting_auto_resume), 2
            )

        # 回収済みの同じ終了応答は、新しい継続対象に戻さない。
        if duplicate:
            wait_output_tracking.consume_agents_server_tool_result(
                parent, "kill", {"session_id": "first-child"}, {"status": "completed"}
            )
            assert "first-child" not in parent.live_child_session_ids | parent.terminal_child_session_ids
        parent.live_child_session_ids.add("second-child")
        session_registry.publish("second-child", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(tmp_path))
        client.messages.put_nowait(SdkSystemMessage(subtype="session_state_changed", data={"state": "idle"}))
        client.messages.put_nowait(ResultMessage("待機コマンドは未起動だが次の子が残る"))
        async with owner._condition:
            await asyncio.wait_for(
                owner._condition.wait_for(lambda: parent.pending_result is not None or parent.result_available), 2
            )
        shown = await owner.show_session(parent.session_id)
        assert shown["status"] == "running" and shown["result_held"]
        assert not parent.result_available
        assert (await owner.wait())["status"] == "running"
        assert len(client.queries) == 2
        session_registry.publish("second-child", state=session_registry.PublicationState.RESULT_COMPLETED, cwd=str(tmp_path))
        result = await owner.wait()
        if result["status"] == "running":
            async with owner._condition:
                await asyncio.wait_for(owner._condition.wait_for(lambda: parent.result_available), 2)
            result = await owner.wait()
        assert result["status"] == "completed" and result["agent_message"] == "全工程を確認した最終結果"
        assert len(client.queries) == 3
        assert not parent.live_child_session_ids and not parent.terminal_child_session_ids
    assert client.disconnected and client.readers == 0
