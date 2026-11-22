# pylint: disable=protected-access
"""世代移譲中のClaude受信停止がバックグラウンド保留を消さないことを検証する。"""

from __future__ import annotations

import asyncio
import pathlib
from types import SimpleNamespace
from typing import Any

import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import claude, service_io, service_rpc, service_state
from agent_toolkit._agents_server.plugin_root import SERVER_PLUGIN_ROOT
from agent_toolkit._agents_server.service_resources import ResourceBroker

SID = "held-move"


def _state(value: str) -> Any:
    return claude_agent_sdk.SystemMessage("session_state_changed", {"state": value})


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["local_bash", "local_agent"])
async def test_generation_move_keeps_background_wait(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, task_type: str
) -> None:
    broker = ResourceBroker("G1")
    messages: asyncio.Queue = asyncio.Queue()
    clients: list[Any] = []

    class Raw:
        _query: Any

        def __init__(self, _options: Any) -> None:
            self._query = self

        async def connect(self, prompt=None):
            del prompt

        async def query(self, prompt, session_id="default"):
            del prompt, session_id
            await messages.put(claude_agent_sdk.SystemMessage("init", {"session_id": SID}))
            await messages.put(_state("running"))
            await messages.put(
                claude_agent_sdk.TaskStartedMessage("task_started", {}, "bg-1", "背景", "u1", SID, task_type=task_type)
            )
            await messages.put(claude_agent_sdk.ResultMessage("success", 1, 1, False, 1, SID, result="待機中: bg-1"))
            await messages.put(_state("idle"))

        async def receive_messages(self):
            while True:
                yield await messages.get()

        async def get_context_usage(self):
            return {"total_tokens": 0, "max_tokens": 200000}

        async def disconnect(self):
            pass

    async def rpc(_origin, operation, arguments, **_kwargs):
        return await getattr(broker, operation)(**arguments)

    def boundary(_session, client):

        clients.append(client)

    monkeypatch.setattr(service_rpc, "request", rpc)
    monkeypatch.setattr(
        claude,
        "_build_options",
        lambda *_args, **_kwargs: SimpleNamespace(plugins=[{"path": str(SERVER_PLUGIN_ROOT)}]),
    )
    first = service_io.Runtime("G1", {"generation": "G1"}, broker, boundary=boundary)
    third = service_io.Runtime("G3", {"generation": "G3"}, ResourceBroker("G3"))
    async with claude.ClaudeServerManager(client_factory=Raw) as source, claude.ClaudeServerManager() as target:
        async with asyncio.timeout(60):
            with service_io.bind(first):
                session = await source.start("first", str(tmp_path))
                while not (session.awaiting_auto_resume and session.cli_turn_state == "idle"):
                    await asyncio.sleep(0)
                assert set(session.live_tasks) == {"bg-1"}

                auxiliary = await source.export_service_state(session.session_id)
                client = clients[0]
                client.pause()
                descriptor = await client.detach()

                await messages.put(_state("idle"))
                await asyncio.gather(*tuple(source._tasks))
                held_after_pause = session.awaiting_auto_resume
            assert await broker.transfer(descriptor["identifier"], "G1", "G3")
            copied = service_state.restore(await service_state.snapshot(session))
            with service_io.bind(third):
                await target.adopt_session(copied, descriptor, auxiliary)
                await messages.put(
                    claude_agent_sdk.TaskNotificationMessage(
                        "task_notification", {}, "bg-1", "completed", str(tmp_path / "out"), "done", "u2", SID
                    )
                )
                await messages.put(_state("running"))
                await messages.put(claude_agent_sdk.AssistantMessage([claude_agent_sdk.TextBlock("最終結果")], "claude-test"))
                await messages.put(
                    claude_agent_sdk.ResultMessage("success", 1, 1, False, 1, SID, result="状態: completed\n結果: 最終結果")
                )
                await messages.put(_state("idle"))
                while not (copied.terminal and not copied.awaiting_auto_resume and copied.cli_turn_state == "idle"):
                    await asyncio.sleep(0.01)
                await asyncio.sleep(0.2)
                final = (copied.status, copied.agent_message, copied.error)
                await target.release_session(copied)
    assert held_after_pause, "旧世代の受信列終了で保留を確定した"
    assert final == ("completed", "状態: completed\n結果: 最終結果", None)
