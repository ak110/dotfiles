"""claudeのcontinuationの契約を検証する。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import json
import pathlib
from types import SimpleNamespace
from typing import Any

import pytest
from claude_agent_sdk import ToolResultBlock, UserMessage

from agent_toolkit._agents_server import claude, responses, session_errors, state
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_claude_support import _finished_failed_claude_session
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    BlockingContinuationClaudeClient,
    FakeClaudeClient,
    QueueClaudeClient,
    ResultMessage,
    SynchronizedFailingClaudeClient,
    SystemMessage,
    _complete,
    _rejected_stream,
    _without_root,
    install_backend,
)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_command_channel_resolves_pending_requests_when_closed() -> None:
    """チャネル閉鎖時に滞留要求を解決し、閉鎖後の受理を拒否する。"""
    channel = claude._CommandChannel()
    future = channel.send("prompt", "続行")

    channel.close()

    with pytest.raises(session_errors.SessionOwnerGoneError, match="session owner task has ended"):
        await asyncio.wait_for(future, timeout=0.1)
    with pytest.raises(session_errors.SessionOwnerGoneError, match="session owner task has ended"):
        channel.send("prompt", "閉鎖後").cancel()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_command_classification_uses_state_when_dequeued(tmp_path: pathlib.Path) -> None:
    """投入後に終了した継続入力をreplyとして処理し、直前結果を退避する。"""
    client = FakeClaudeClient([[AssistantMessage("reply中"), ResultMessage("reply結果")]])
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        session = state.SessionState("claude-race", str(tmp_path), engine="claude")
        channel = claude._CommandChannel()
        manager.sessions[session.session_id] = session
        manager._channels[session.session_id] = channel

        send_task = asyncio.create_task(manager.send_message(session, "続行"))
        command = await channel.get()
        _complete(session, message="直前結果")
        iterator = await manager._handle_command(client, session, command, None)
        response = await send_task

        assert response == {
            "delivery": "reply_started",
            "previous_result": {
                "status": "completed",
                "agent_message": "直前結果",
            },
        }
        assert iterator is not None
        assert client.queries == ["続行"]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_message_stream_failure_resolves_retrieved_command(tmp_path: pathlib.Path) -> None:
    """message streamの例外と同じ待機バッチに入った継続要求を、所有タスクの終了として解決する。"""
    client = SynchronizedFailingClaudeClient()
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        session = await manager.start("調査", str(tmp_path))
        await asyncio.wait_for(client.message_waiting.wait(), timeout=0.1)
        channel = manager._channels[session.session_id]
        future = channel.send("prompt", "同時要求")
        client.release_message.set()

        with pytest.raises(session_errors.SessionOwnerGoneError, match="session owner task has ended"):
            await asyncio.wait_for(future, timeout=0.1)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_owner_cancellation_resolves_active_command(tmp_path: pathlib.Path) -> None:
    """所有タスクを取り消したときに処理中の継続要求を、所有タスクの終了として解決する。"""
    client = BlockingContinuationClaudeClient()
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        session = await manager.start("調査", str(tmp_path))
        await asyncio.wait_for(client.message_waiting.wait(), timeout=0.1)
        send_task = asyncio.create_task(manager.send_message(session, "継続"))
        await asyncio.wait_for(client.query_started.wait(), timeout=0.1)

        await manager.close()

        with pytest.raises(session_errors.SessionOwnerGoneError, match="session owner task has ended"):
            await asyncio.wait_for(send_task, timeout=0.1)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_timed_out_queued_prompt_is_not_delivered(tmp_path: pathlib.Path) -> None:
    """打ち切り済みで未処理の継続要求を所有タスクが後から配送しない。"""
    client = FakeClaudeClient([])
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        session = state.SessionState("claude-queued", str(tmp_path), engine="claude")
        channel = claude._CommandChannel()
        manager.sessions[session.session_id] = session
        manager._channels[session.session_id] = channel

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(manager.send_message(session, "打ち切り"), timeout=0.01)

        command = await channel.get()
        await manager._handle_command(client, session, command, None)

        assert not client.queries


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_finished_task_send_message_omits_previous_result_after_wait(tmp_path: pathlib.Path) -> None:
    """所有タスク終了後もwaitで回収済みの結果本文を再送しない。"""
    async with _finished_failed_claude_session(tmp_path) as (manager, backend, session):
        result = await manager.wait()
        assert result["error"]["message"] == "stream failed"
        assert result["error"]["engine"] == "claude"
        response = await manager.send_message(session.session_id, "続行")

        assert _without_root(response) == {"delivery": "reply_started", "label": ""}
        assert not manager.expired_sessions
        assert manager.sessions[session.session_id].status == "running"
        await backend.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.parametrize("old_events", ["none", "result", "assistant_result", "idle_result", "result_idle"])
@pytest.mark.parametrize("prompt", ["再レビュー", "後続作業"])
@pytest.mark.asyncio
async def test_claude_reply_drains_old_events_before_new_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, old_events: str, prompt: str
) -> None:
    """共通のSDK受信列に残った旧イベントを、新しい指示の完了として扱わない。"""
    client = QueueClaudeClient(
        [
            [SystemMessage("claude-queue"), AssistantMessage("初回"), ResultMessage("初回完了")],
            [AssistantMessage("新指示"), ResultMessage("新指示完了")],
        ]
    )
    async with server_manager.AgentsServerManager() as manager:
        async with claude.ClaudeServerManager(
            manager.sessions, manager._condition, client_factory=lambda _options: client
        ) as backend:
            monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
            install_backend(manager, "claude", backend)
            manager._wait_timeouts["main"] = 1
            session = await backend.start("初回", str(tmp_path))
            initial = await asyncio.wait_for(manager.wait(), timeout=2)
            assert initial["agent_message"] == "初回完了"
            idle = SystemMessage("claude-queue")
            idle.subtype = "session_state_changed"
            idle.data = {"state": "idle"}
            events_by_kind: dict[str, list[Any]] = {
                "none": [],
                "result": [ResultMessage("旧完了")],
                "assistant_result": [AssistantMessage("旧後始末"), ResultMessage("旧完了")],
                "idle_result": [idle, ResultMessage("旧完了")],
                "result_idle": [ResultMessage("旧完了"), idle],
            }
            events = events_by_kind[old_events]
            for event in events:
                client.messages.put_nowait(event)
            reply = await asyncio.wait_for(manager.send_message(session.session_id, prompt), timeout=2)
            assert reply["delivery"] == "reply_started"
            result = await asyncio.wait_for(manager.wait(), timeout=2)
            assert result["agent_message"] == "新指示完了"
            assert session.turn_seq == 2
            assert len(client.queries) == 2
            assert client.queries[0] == "初回"
            assert f"\n{prompt}\n" in client.queries[1]
            assert client.messages.empty()
            assert client.receive_calls == 1
            assert session.result_delivered
        assert client.readers == 0
        assert client.disconnected


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_usage_limit_idle_keeps_shared_stream_result_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """継続受信でCLIのidleが届いても、利用上限の解除待ちを終了時の結果にしない。"""
    idle = SystemMessage("claude-limited-queue")
    idle.subtype = "session_state_changed"
    idle.data = {"state": "idle"}
    client = QueueClaudeClient(
        [_rejected_stream("seven_day", None, before_init=False, session_id="claude-limited-queue") + [idle]]
    )
    async with server_manager.AgentsServerManager() as manager:
        async with claude.ClaudeServerManager(
            manager.sessions, manager._condition, client_factory=lambda _options: client
        ) as backend:
            monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
            install_backend(manager, "claude", backend)

            async def wait_for_idle() -> None:
                while session.cli_turn_state != "idle":
                    await asyncio.sleep(0)

            session = await backend.start("初回", str(tmp_path))
            await asyncio.wait_for(wait_for_idle(), timeout=2)
            assert session.awaiting_auto_resume
            assert session.usage_limit_resume_at is not None
            assert not session.result_available
            assert client.messages.empty()
        assert client.disconnected and client.readers == 0


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_send_message_reactivated_child_holds_claude_result_until_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, failed: bool
) -> None:
    """Claudeの親も追送で再開した子の終了まで結果を保留し、自動再開の最終結果を公開する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [("claude", "model", "high")]
    )
    client = QueueClaudeClient(
        [
            [SystemMessage("claude-parent"), AssistantMessage("親の作業を開始した")],
            [AssistantMessage("子の結果を確認した"), ResultMessage("子を確認した最終結果")],
        ]
    )
    async with (
        server_manager.AgentsServerManager(None) as manager,
        claude.ClaudeServerManager(manager.sessions, manager._condition, client_factory=lambda _options: client) as backend,
    ):
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        install_backend(manager, "claude", backend)
        session_registry.publish("child", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(tmp_path))
        try:
            started = await manager.start("execute", "親の作業", str(tmp_path))
            session = manager.sessions[started["session_id"]]
            session.terminal_child_session_ids.add("child")
            tool_use = AssistantMessage("")
            tool_use.content = [
                SimpleNamespace(
                    id="reply-child",
                    name="mcp__agents_server__send_message",
                    input={"session_id": "child", "prompt": "残作業を続ける"},
                )
            ]
            client.messages.put_nowait(tool_use)
            # SDKのUserMessage/ToolResultBlockの実型を通して構造化応答のJSONを配送する。
            client.messages.put_nowait(
                UserMessage(
                    content=[
                        ToolResultBlock(
                            tool_use_id="reply-child",
                            content=json.dumps(
                                responses.public_send_message_response({"delivery": "reply_started", "label": "子の担当"})
                            ),
                        )
                    ]
                )
            )
            client.messages.put_nowait(ResultMessage("親のturnの結果", is_error=failed))
            async with manager._condition:
                await asyncio.wait_for(manager._condition.wait_for(lambda: session.awaiting_auto_resume), 2)
            shown = await manager.show_session(session.session_id)
            assert shown["status"] == "running" and shown["result_held"]
            assert shown["live_child_sessions"] == [{"session_id": "child", "cwd": str(tmp_path)}]
            assert session.pending_result is not None
            assert session.pending_result["status"] == ("failed" if failed else "completed")
            session_registry.publish("child", state=session_registry.PublicationState.RESULT_COMPLETED, cwd=str(tmp_path))
            result = await asyncio.wait_for(manager.wait(), 2)
            if result["status"] == "running":
                async with manager._condition:
                    await asyncio.wait_for(manager._condition.wait_for(lambda: session.result_available), 2)
                result = await asyncio.wait_for(manager.wait(), 2)
            assert result["status"] == "completed"
            assert result["agent_message"] == "子を確認した最終結果"
            assert len(client.queries) == 2
        finally:
            await manager.close()
        assert client.disconnected and client.readers == 0


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_finished_task_send_message_keeps_previous_result_without_wait(tmp_path: pathlib.Path) -> None:
    """所有タスク終了後も未回収の結果本文を退避して会話を再開する。"""
    async with _finished_failed_claude_session(tmp_path) as (manager, backend, session):
        response = await manager.send_message(session.session_id, "続行")

        previous_error = response["previous_result"].pop("error")
        assert previous_error["message"] == "stream failed"
        assert previous_error["engine"] == "claude"
        assert _without_root(response) == {
            "delivery": "reply_started",
            "label": "",
            "previous_result": {
                "status": "failed",
                "agent_message": "",
            },
        }
        assert not manager.expired_sessions
        assert manager.sessions[session.session_id].status == "running"
        await backend.close()
