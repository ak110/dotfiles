"""更新の検知からidle・次query・終了回収までの所有順序を検証する。"""

import asyncio
import json
import pathlib
from types import SimpleNamespace
from typing import Any

import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import claude, claude_plugins, launch_prompts, launch_requests
from agent_toolkit._testing.agents_server_support import AssistantMessage, QueueClaudeClient, ResultMessage

# 所有loopと実helperの接続、およびSDK制御要求の故障境界を直接検証する。
# pylint: disable=protected-access


@pytest.mark.parametrize("versioned_input", [False, True])
@pytest.mark.asyncio
async def test_plugin_reload_waits_for_idle_and_precedes_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, versioned_input: bool
) -> None:
    """busyで検知した更新はidleへ進めた後、同じ接続の次queryより先に反映する。"""
    changed = asyncio.Event()
    observed = asyncio.Event()
    reloaded = asyncio.Event()
    closed = asyncio.Event()
    order: list[str] = []
    mirror = tmp_path / "input/agent-toolkit"
    for name in ("agent-toolkit", "agent-basics"):
        root = mirror.parent / name
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        (root / "skills").mkdir()

    class Reloader(claude_plugins.ClaudePluginReloader):
        async def prepare(self) -> None:
            if versioned_input:
                self.options["plugins"][0]["path"] = str(mirror)

        async def wait_for_change(self) -> None:
            await changed.wait()
            changed.clear()
            observed.set()

        async def reload(self, client: Any) -> dict[str, Any]:
            del client
            order.append("reload")
            reloaded.set()
            return {}

        async def close(self, *, retain_input: bool = False) -> None:
            assert not retain_input
            closed.set()

    class Client(QueueClaudeClient):
        async def query(self, prompt: str) -> None:
            order.append("query")
            await super().query(prompt)

    # session_state_changedはCLIが報告する同じSystemMessage形式で渡す。
    client = Client(
        [
            [
                claude_agent_sdk.SystemMessage("init", {"session_id": "reload-order"}),
                claude_agent_sdk.SystemMessage("session_state_changed", {"state": "running"}),
                AssistantMessage("作業中"),
            ],
            [AssistantMessage("継続"), ResultMessage("完了")],
        ]
    )
    monkeypatch.setattr(claude, "ClaudePluginReloader", Reloader)
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as backend:
        original = launch_prompts.plugin_resource_instructions(claude.plugin_roots.SERVER_PLUGIN_ROOT, "claude", str(tmp_path))
        session = await backend.start(launch_requests.wrap_delivery_body(original + "\n\n初回"), str(tmp_path))
        assert client.queries[0].count("agent-toolkit plugin root:") == 1
        if versioned_input:
            assert f"agent-toolkit plugin root: {mirror}\n" in client.queries[0]
        changed.set()
        await asyncio.wait_for(observed.wait(), timeout=2)
        assert order == ["query"]
        client.messages.put_nowait(ResultMessage("初回完了"))
        client.messages.put_nowait(claude_agent_sdk.SystemMessage("session_state_changed", {"state": "idle"}))
        await asyncio.wait_for(reloaded.wait(), timeout=2)
        response = await backend.send_message(session, "後続")
        assert response["delivery"] == "reply_started"
        assert order == ["query", "reload", "query"]
    assert closed.is_set()
    assert client.disconnected
    assert client.readers == 0


@pytest.mark.parametrize("failure", ["manifest", "copy", "sdk", "inventory"])
@pytest.mark.asyncio
async def test_reload_failure_keeps_connection_and_completes_followup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, failure: str
) -> None:
    """実helperの更新失敗を所有loopへ渡し、同じ接続の後続queryと旧入力を保持する。"""
    changed = asyncio.Event()
    attempted = asyncio.Event()
    reloaders: list[claude_plugins.ClaudePluginReloader] = []
    root = tmp_path / "cache/mine/agent-toolkit/1.0.0"
    (root / ".claude-plugin").mkdir(parents=True)
    manifest = root / ".claude-plugin/plugin.json"
    manifest.write_text(json.dumps({"name": "agent-toolkit", "version": "1.0.0"}), encoding="utf-8")
    (root / "skills").mkdir()
    (root / "skills/content.md").write_text("旧入力", encoding="utf-8")
    broken = False

    class Reloader(claude_plugins.ClaudePluginReloader):
        async def prepare(self) -> None:
            self.options["plugins"] = [{"type": "local", "path": str(root)}]
            await super().prepare()
            basics = pathlib.Path(self.options["plugins"][0]["path"]).parent / "agent-basics"
            (basics / ".claude-plugin").mkdir(parents=True)
            (basics / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "agent-basics"}), encoding="utf-8")
            (basics / "skills").mkdir()
            reloaders.append(self)

        def _inventory(self) -> list[claude_plugins._InstalledPlugin]:
            if broken and failure == "inventory":
                raise ValueError("不正な導入一覧")
            return [claude_plugins._InstalledPlugin(id="agent-toolkit@mine", enabled=True, scope="user", installPath=str(root))]

        async def wait_for_change(self) -> None:
            await changed.wait()
            changed.clear()

        async def reload_if_idle(self, client: Any, session: Any) -> None:
            await super().reload_if_idle(client, session)
            if broken:
                attempted.set()

    async def control(_payload: dict[str, str], **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("SDK制御要求の失敗")

    def copy_failure(_source: pathlib.Path, _target: pathlib.Path, **_kwargs: Any) -> None:
        raise OSError("複製失敗")

    class Client(QueueClaudeClient):
        _query = SimpleNamespace(_send_control_request=control)

    client = Client(
        [
            [claude_agent_sdk.SystemMessage("init", {"session_id": "reload-failure"}), ResultMessage("初回完了")],
            [AssistantMessage("同じ接続の成果"), ResultMessage("完了")],
        ]
    )
    monkeypatch.setattr(claude, "ClaudePluginReloader", Reloader)
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as backend:
        session = await backend.start("初回", str(tmp_path))
        async with backend._condition:
            await asyncio.wait_for(backend._condition.wait_for(lambda: session.result_available), timeout=5)
        reloader = reloaders[0]
        mirror = pathlib.Path(reloader.options["plugins"][0]["path"])
        broken = True
        if failure == "manifest":
            manifest.write_text("{", encoding="utf-8")
        elif failure == "copy":
            monkeypatch.setattr(claude_plugins.shutil, "copytree", copy_failure)
        reloader.mark_changed()
        changed.set()
        await asyncio.wait_for(attempted.wait(), timeout=5)
        assert reloader.dirty and not reloader.updated
        assert (mirror / "skills/content.md").read_text(encoding="utf-8") == "旧入力"
        assert (await backend.send_message(session, "後続"))["delivery"] == "reply_started"
        async with backend._condition:
            await asyncio.wait_for(backend._condition.wait_for(lambda: session.result_available), timeout=5)
        assert session.status == "completed" and session.agent_message == "完了"
        assert session.session_id == "reload-failure" and len(client.queries) == 2
    assert client.disconnected and client.readers == 0
