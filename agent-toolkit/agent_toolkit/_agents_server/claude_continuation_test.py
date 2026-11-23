"""claudeのcontinuationの契約を検証する。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import contextlib
import io
import json
import pathlib
from types import SimpleNamespace
from typing import Any

import claude_agent_sdk
import pytest
from claude_agent_sdk import ToolResultBlock, UserMessage
from mcp.types import CallToolRequestParams, CallToolResult

from agent_toolkit import atk
from agent_toolkit._agents_server import (
    agents_wait,
    claude,
    launch_requests,
    launch_requests_preflight,
    mcp_tools,
    responses,
    service_io,
    service_rpc,
    service_state,
    service_turns,
    session_errors,
    state,
)
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server.plugin_root import SERVER_PLUGIN_ROOT
from agent_toolkit._agents_server.service_resources import ResourceBroker
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._common.next_action import ActionableError
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
async def test_mcp_steers_running_claude_before_usage_and_turn_complete(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk_messages: asyncio.Queue[Any] = asyncio.Queue()
    observing, release_observation = asyncio.Event(), asyncio.Event()
    received: list[str] = []
    usage_replied = asyncio.Event()

    class Raw:
        def __init__(self, _options: Any) -> None:
            pass

        async def connect(self, prompt: Any = None) -> None:
            del prompt

        async def receive_messages(self):
            while True:
                yield await sdk_messages.get()

        async def get_context_usage(self) -> dict:
            observing.set()
            await release_observation.wait()
            usage_replied.set()
            return {}

        async def query(self, prompt: str, session_id: str = "default") -> None:
            del session_id
            received.append(prompt)
            if len(received) == 1:
                await sdk_messages.put(claude_agent_sdk.SystemMessage("init", {"session_id": "active-steer"}))

        async def disconnect(self) -> None:
            pass

    monkeypatch.setattr(
        claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace(plugins=[{"path": str(SERVER_PLUGIN_ROOT)}])
    )
    runtime = service_io.Runtime("G1", {"generation": "G1"}, ResourceBroker("G1"))
    with service_io.bind(runtime):
        async with (
            server_manager.AgentsServerManager() as manager,
            claude.ClaudeServerManager(manager.sessions, manager._condition, client_factory=Raw) as backend,
        ):
            install_backend(manager, "claude", backend)
            session = await backend.start("最初の指示", str(tmp_path))
            params = CallToolRequestParams(
                name="send_message", arguments={"session_id": session.session_id, "prompt": "追加する指示"}
            )
            token = mcp_tools._SELECTED_MANAGER.set(manager)
            try:
                async with asyncio.timeout(3):
                    await observing.wait()
                    reply = await mcp_tools.mcp._call_registered_tool(None, params)
                assert isinstance(reply, CallToolResult) and not reply.is_error
                assert reply.structured_content is not None and reply.structured_content["delivery"] == "steered"
                assert not usage_replied.is_set() and not session.turn_completed
                assert len(received) == 2 and received[0] == "最初の指示" and "\n追加する指示\n" in received[1]
            finally:
                release_observation.set()
                mcp_tools._SELECTED_MANAGER.reset(token)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_send_during_reconnect_waits_only_for_same_session(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, proceed, queued = asyncio.Event(), asyncio.Event(), asyncio.Event()
    received: list[tuple[str, str]] = []

    async def deliver(session: state.SessionState, prompt: str) -> dict[str, str]:
        received.append((session.session_id, prompt))
        if prompt == "再開":
            entered.set()
            await proceed.wait()
        return {"delivery": "reply_started"}

    async with claude.ClaudeServerManager() as manager, asyncio.TaskGroup() as group:
        monkeypatch.setattr(manager, "_send_message_owned", deliver)
        session = state.SessionState("current", str(tmp_path), engine="claude")
        other = state.SessionState("other", str(tmp_path), engine="claude")

        async def followup() -> None:
            queued.set()
            await manager.send_message(session, "取り消す")

        first = group.create_task(manager.send_message(session, "再開"))
        await asyncio.wait_for(entered.wait(), timeout=3)
        waiting = group.create_task(followup())
        await asyncio.wait_for(queued.wait(), timeout=3)
        await asyncio.wait_for(manager.send_message(other, "別の指示"), timeout=3)
        assert received == [("current", "再開"), ("other", "別の指示")]
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        proceed.set()
        await first
        await manager.send_message(session, "次の指示")

    assert received == [("current", "再開"), ("other", "別の指示"), ("current", "次の指示")]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_backend_adoption_changes_decoder_without_reconnecting_or_querying(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = ResourceBroker("G1")
    messages: asyncio.Queue = asyncio.Queue()
    calls = []
    ended = asyncio.Event()
    clients = []

    class Raw:
        _query: Any

        def __init__(self, _options: Any) -> None:
            self._query = self
            self.owner: asyncio.Task[Any] | None = None

        async def connect(self, prompt=None):
            del prompt
            self.owner = asyncio.current_task()
            calls.append("connect")

        async def query(self, prompt, session_id="default"):
            del session_id
            calls.append(prompt)
            await messages.put(claude_agent_sdk.SystemMessage("init", {"session_id": "adopted"}))
            await messages.put(claude_agent_sdk.SystemMessage("session_state_changed", {"state": "running"}))
            await messages.put(
                claude_agent_sdk.ResultMessage("success", 1, 1, False, 1, "adopted", result="状態: completed\n結果: 原結果")
            )
            await messages.put(claude_agent_sdk.SystemMessage("session_state_changed", {"state": "idle"}))

        async def receive_messages(self):
            while True:
                yield await messages.get()

        async def get_context_usage(self):
            return {"total_tokens": 0, "max_tokens": 200000}

        async def disconnect(self):
            assert asyncio.current_task() is self.owner
            calls.append("disconnect")

    async def rpc(_origin, operation, arguments, **_kwargs):
        return await getattr(broker, operation)(**arguments)

    def boundary(_session, client):
        client.pause()
        clients.append(client)
        ended.set()

    monkeypatch.setattr(service_rpc, "request", rpc)
    monkeypatch.setattr(
        claude,
        "_build_options",
        lambda *_args, **_kwargs: SimpleNamespace(plugins=[{"path": str(SERVER_PLUGIN_ROOT)}]),
    )
    first = service_io.Runtime("G1", {"generation": "G1"}, broker, boundary=boundary)
    third = service_io.Runtime("G3", {"generation": "G3"}, ResourceBroker("G3"))
    async with claude.ClaudeServerManager(client_factory=Raw) as source, claude.ClaudeServerManager() as target:
        async with asyncio.timeout(5):
            with service_io.bind(first):
                original = await source.start("first", str(tmp_path))
                await ended.wait()
                await asyncio.gather(*tuple(source._tasks))
                auxiliary = {"wait_compaction": {"legacy": True}}
            descriptor = await clients[0].detach()
            assert await broker.transfer(descriptor["identifier"], "G1", "G3")
            copied = service_state.restore(await service_state.snapshot(original))
            with service_io.bind(third):
                await target.adopt_session(copied, descriptor, auxiliary)
                while copied.cli_turn_state != "idle":
                    await asyncio.sleep(0)
                assert calls == ["connect", "first"]
                assert (await target.send_message(copied, "second"))["delivery"] == "reply_started"
                while not copied.terminal:
                    await asyncio.sleep(0)
                assert copied is target.sessions[original.session_id]
                assert copied.turn_seq == original.turn_seq + 1
                await target.release_session(copied)
    assert calls == ["connect", "first", "second", "disconnect"]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_admitted_reply_ticket_survives_freeze(tmp_path: pathlib.Path) -> None:
    client = FakeClaudeClient([[]])
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)
    session = state.SessionState("accepted", str(tmp_path), engine="claude")
    _complete(session, message="保存済み")
    channel = claude._CommandChannel()
    manager._channels[session.session_id] = channel
    gate = service_turns.TurnGate("old", {"generation": "old", "epoch": 1})
    with service_turns.bind(gate):
        async with gate.admit(new_turn=True, session_id=session.session_id):
            future = channel.send("prompt", "受理済み")
            freezing = asyncio.create_task(gate.freeze("update"))
            await asyncio.sleep(0)
            assert not freezing.done()
            await manager._handle_command(client, session, await channel.get(), None)
            assert (await future)[0] == "reply_started"
        await asyncio.wait_for(freezing, timeout=1)
    assert client.queries == ["受理済み"]
    queued = channel.send("prompt", "未配送")
    channel.close(service_turns.MoveRequired({"generation": "new", "epoch": 2}))
    with pytest.raises(service_turns.MoveRequired):
        await queued
    assert client.queries == ["受理済み"]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_old_generation_reply_is_returned_without_query(tmp_path: pathlib.Path) -> None:
    client = FakeClaudeClient([])
    manager = claude.ClaudeServerManager(client_factory=lambda _options: client)
    session = state.SessionState("moved", str(tmp_path), engine="claude")
    _complete(session, message="保存済み")
    before = session.turn_seq
    gate = service_turns.TurnGate("old", {"generation": "new", "epoch": 2})
    channel = claude._CommandChannel()
    manager._channels[session.session_id] = channel
    future = channel.send("prompt", "続行")
    with service_turns.bind(gate):
        await manager._handle_command(client, session, await channel.get(), None)
    with pytest.raises(service_turns.MoveRequired):
        await future
    assert not client.queries
    assert session.turn_seq == before
    channel.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_command_channel_resolves_pending_requests_when_closed() -> None:
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
    client = FakeClaudeClient([[AssistantMessage("reply中"), ResultMessage("状態: completed\n結果: reply結果")]])
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
    client = QueueClaudeClient(
        [
            [SystemMessage("claude-queue"), AssistantMessage("初回"), ResultMessage("状態: completed\n結果: 初回完了")],
            [AssistantMessage("新指示"), ResultMessage("状態: completed\n結果: 新指示完了")],
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
            assert initial["agent_message"] == "状態: completed\n結果: 初回完了"
            idle = SystemMessage("claude-queue")
            idle.subtype = "session_state_changed"
            idle.data = {"state": "idle"}
            events_by_kind: dict[str, list[Any]] = {
                "none": [],
                "result": [ResultMessage("状態: completed\n結果: 旧完了")],
                "assistant_result": [AssistantMessage("旧後始末"), ResultMessage("状態: completed\n結果: 旧完了")],
                "idle_result": [idle, ResultMessage("状態: completed\n結果: 旧完了")],
                "result_idle": [ResultMessage("状態: completed\n結果: 旧完了"), idle],
            }
            events = events_by_kind[old_events]
            for event in events:
                client.messages.put_nowait(event)
            reply = await asyncio.wait_for(manager.send_message(session.session_id, prompt), timeout=2)
            assert reply["delivery"] == "reply_started"
            result = await asyncio.wait_for(manager.wait(), timeout=2)
            assert result["agent_message"] == "状態: completed\n結果: 新指示完了"
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
            [AssistantMessage("子の結果を確認した"), ResultMessage("状態: completed\n結果: 子を確認した最終結果")],
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
            client.messages.put_nowait(ResultMessage("待機中: child", is_error=failed))
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
            assert result["agent_message"] == "状態: completed\n結果: 子を確認した最終結果"
            assert len(client.queries) == 2
        finally:
            await manager.close()
        assert client.disconnected and client.readers == 0


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_finished_task_send_message_keeps_previous_result_without_wait(tmp_path: pathlib.Path) -> None:
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


async def _cli_wait(manager: server_manager.AgentsServerManager) -> list[tuple[Any, Any]]:
    writer = manager._status_writer
    assert writer is not None

    def run() -> list[tuple[Any, Any]]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
            atk.main(["agents", "wait", "--root-session-id", writer.root_session_id])
        rows = [json.loads(line) for line in out.getvalue().splitlines() if line.startswith("{")]
        return [(row.get("status"), row.get("agent_message")) for row in rows]

    return await asyncio.to_thread(run)


class _WorkdirClaudeClient(QueueClaudeClient):
    """SDKの`SubprocessCLITransport.connect`と同じく、作業ディレクトリが無ければCLIを起動せずに接続を失敗させる。"""

    def __init__(self, cwd: str, streams: list[list[Any]]) -> None:
        super().__init__(streams)
        self.cwd = cwd

    async def connect(self) -> None:
        if not pathlib.Path(self.cwd).is_dir():
            raise claude_agent_sdk.CLIConnectionError(f"Working directory does not exist: {self.cwd}")
        await super().connect()


@pytest.mark.parametrize("received", [False, True], ids=["unreceived", "received_before_reply"])
@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_reply_without_working_directory_keeps_previous_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, received: bool
) -> None:
    """次のturnを新しいCLIで始める追送は、作業ディレクトリ（回収済みのworktree）が無ければ会話を変えずに失敗を返す。

    再送しても、前のturnの終了状態・turn番号・結果の受領状態と登録簿の記録は変わらず、未受領の結果は
    別プロセスの`atk agents wait`で受け取れる。作業ディレクトリを戻せば同じ会話の次のturnを始められ、
    受け取り済みの前の結果は追送の`previous_result`で再び返さない。
    """
    workdir = tmp_path / "work"
    workdir.mkdir()
    streams = [
        [SystemMessage("claude-gone-cwd"), AssistantMessage("初回"), ResultMessage("状態: completed\n結果: 初回完了")],
        [SystemMessage("claude-gone-cwd"), AssistantMessage("再開"), ResultMessage("状態: completed\n結果: 再開後の完了")],
    ]
    clients: list[_WorkdirClaudeClient] = []

    def factory(options: Any) -> _WorkdirClaudeClient:
        client = _WorkdirClaudeClient(options.cwd, streams if not clients else streams[1:])
        clients.append(client)
        return client

    monkeypatch.setattr(claude, "_build_options", lambda cwd, *_args, **_kwargs: SimpleNamespace(cwd=cwd))
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda *_args: 0.5)

    monkeypatch.setattr(launch_requests, "check_plugin_commands", launch_requests_preflight.check_plugin_commands)
    monkeypatch.setattr(launch_requests_preflight, "PREFLIGHT_COMMANDS", ())
    async with (
        server_manager.AgentsServerManager() as manager,
        claude.ClaudeServerManager(
            manager.sessions,
            manager._condition,
            client_factory=factory,
            expire_session=manager._expire_session,
            publish_registry=True,
        ) as backend,
    ):
        install_backend(manager, "claude", backend)
        manager._wait_timeouts["main"] = 1
        assert manager._status_writer is not None
        await manager._status_writer.activate_async()
        session = await backend.start("初回", str(workdir))
        session.announced = True
        async with manager._condition:
            await asyncio.wait_for(manager._condition.wait_for(lambda: session.result_available), 2)
        await manager.save_control_state()
        if received:
            assert await _cli_wait(manager) == [("completed", "状態: completed\n結果: 初回完了")]
            await manager.save_control_state()
        # 接続を設定で管理し始めると、`inherit`で始めた会話は次のturnを使用順の先頭の接続の新しいCLIで始める。
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_CONNECTIONS", "subscription")
        workdir.rmdir()

        with pytest.raises(ActionableError, match="作業ディレクトリが存在しない"):
            await asyncio.wait_for(manager.send_message(session.session_id, "次の指示"), 5)
        await manager.save_control_state()
        shown = await manager.show_session(session.session_id)
        assert (shown["status"], shown["result_available"], session.turn_seq) == ("completed", not received, 1)
        registered = session_registry.resolve(session.session_id)
        assert (registered.state, registered.recorded_turn_seq) == (session_registry.Resolution.TERMINAL, 1)

        # 旧CLIを止めた会話への再送は、所有タスクの無い会話の再開として同じく作業ディレクトリの不在を返す。
        with pytest.raises(ActionableError, match="作業ディレクトリが存在しない"):
            await asyncio.wait_for(manager.send_message(session.session_id, "次の指示"), 5)
        await manager.save_control_state()
        shown = await manager.show_session(session.session_id)
        assert (shown["status"], shown["result_available"]) == ("completed", not received)
        assert session_registry.resolve(session.session_id).recorded_turn_seq == 1
        if not received:
            assert await _cli_wait(manager) == [("completed", "状態: completed\n結果: 初回完了")]

        workdir.mkdir()
        reply = await asyncio.wait_for(manager.send_message(session.session_id, "次の指示"), 5)
        assert reply["delivery"] == "reply_started"
        assert "previous_result" not in reply
        following = await asyncio.wait_for(manager.wait(), 2)
        assert (following["status"], following["agent_message"]) == ("completed", "状態: completed\n結果: 再開後の完了")
        assert manager.sessions[session.session_id].turn_seq == 2
