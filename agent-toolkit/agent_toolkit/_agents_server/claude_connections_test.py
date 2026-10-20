"""SDK起動の接続隔離とMCP再接続を、managerの公開操作から検証する。"""

import asyncio
import json
import pathlib
from types import SimpleNamespace
from typing import Any

import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import claude, responses, state
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import claude_connection, config
from agent_toolkit._testing.agents_server_support import AssistantMessage, QueueClaudeClient, install_backend


class _McpClient(QueueClaudeClient):
    def __init__(self, error: str, recovered: bool) -> None:
        # SDK 0.2.168のSystemMessageとMcpStatusResponseの公開フィールドを使う。
        self.servers = [{"name": "plugin:agent-toolkit:agents_server", "status": "failed", "error": error}]
        self.recovered = recovered
        self.reconnections: list[str] = []
        super().__init__(
            [
                [
                    claude_agent_sdk.SystemMessage("init", {"session_id": "claude-mcp", "mcp_servers": self.servers}),
                    AssistantMessage("作業開始"),
                ]
            ]
        )

    async def get_mcp_status(self) -> dict[str, Any]:
        return {"mcpServers": list(self.servers)}

    async def reconnect_mcp_server(self, server_name: str) -> None:
        self.reconnections.append(server_name)
        if self.recovered:
            self.servers = [{"name": server_name, "status": "connected"}]
        else:
            raise RuntimeError("CONNECT_TIMEOUT")


class _DelayedMcpClient(_McpClient):
    def __init__(self) -> None:
        super().__init__("CONNECT_TIMEOUT", True)
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def reconnect_mcp_server(self, server_name: str) -> None:
        await self.release.wait()
        await super().reconnect_mcp_server(server_name)

    async def get_mcp_status(self) -> dict[str, Any]:
        result = await super().get_mcp_status()
        self.finished.set()
        return result


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_slow_mcp_recovery_keeps_start_budget_and_updates_show(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """回復待ちの予算経過後もstartを返し、同じ所有taskが回復して診断を更新する。"""
    client = _DelayedMcpClient()
    monkeypatch.setattr(
        state,
        "HOST_BACKGROUND_THRESHOLD_SECONDS",
        state.SESSION_INITIALIZATION_TIMEOUT * state.SESSION_INITIALIZATION_ATTEMPTS
        + server_manager.START_AVAILABILITY_TIMEOUT,
    )
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [("claude", "model", "high")])
    async with server_manager.AgentsServerManager() as manager:
        backend = claude.ClaudeServerManager(
            manager.sessions,
            manager._condition,  # pylint: disable=protected-access
            client_factory=lambda _options: client,
        )
        install_backend(manager, "claude", backend)
        public = responses.public_start_response(
            await asyncio.wait_for(manager.start("plan", "調査", str(tmp_path)), timeout=2)
        )
        assert "回復中" in public["warnings"][0]
        assert "CONNECT_TIMEOUT" in public["warnings"][0]
        client.release.set()
        await asyncio.wait_for(client.finished.wait(), timeout=2)
        assert "warnings" not in await manager.show_session("claude-mcp")
        assert client.reconnections == ["plugin:agent-toolkit:agents_server"]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["Skipping connection (recent failure cached; retry later)", "CONNECT_TIMEOUT"])
@pytest.mark.parametrize("recovered", [True, False])
async def test_mcp_reconnect_is_once_per_new_and_resumed_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, error: str, recovered: bool, caplog: pytest.LogCaptureFixture
) -> None:
    """新規と再開の各プロセスで再試行し、未回復の接続だけを起動応答とshowへ公開する。"""
    clients: list[_McpClient] = []

    def factory(_options: Any) -> _McpClient:
        client = _McpClient(error, recovered)
        clients.append(client)
        return client

    async with server_manager.AgentsServerManager() as manager:
        backend = claude.ClaudeServerManager(
            manager.sessions,
            manager._condition,  # pylint: disable=protected-access
            client_factory=factory,
        )
        install_backend(manager, "claude", backend)
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        monkeypatch.setattr(
            config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [("claude", "model", "high")]
        )
        public = responses.public_start_response(await manager.start("plan", "調査", str(tmp_path)))
        shown = await manager.show_session("claude-mcp")
        assert bool(public.get("warnings")) is not recovered
        assert shown.get("warnings") == public.get("warnings")
        if not recovered:
            assert "plugin:agent-toolkit:agents_server" in public["warnings"][0]
            assert error in public["warnings"][0]
            assert "claude_mcp_recovery_failed" in caplog.text
        prompt = state.ResumePrompt("再開")
        try:
            await backend.resume("claude-mcp", prompt, str(tmp_path), existing_session=backend.sessions["claude-mcp"])
        finally:
            prompt.close()
        assert len(clients) == 2
        assert all(client.reconnections == ["plugin:agent-toolkit:agents_server"] for client in clients)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "write", "shell"])
async def test_subscription_sdk_and_settings_isolate_custom_api_keys_on_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, launch_kind: state.LaunchKind
) -> None:
    """全起動区分とsubscription再開で、親環境とsettingsからAPI専用の任意キーを隔離する。"""
    path = tmp_path / "api.env"
    path.write_text("CUSTOM_CONNECTION=api-only\nAWS_PROFILE=api-profile\n", encoding="utf-8")
    connection = claude_connection.Connection("subscription", isolation_env_file=str(path))
    monkeypatch.setattr(claude_connection, "candidates", lambda *_args, **_kwargs: claude_connection.Candidates((connection,)))
    monkeypatch.setenv("CUSTOM_CONNECTION", "parent")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth")
    monkeypatch.setattr(
        claude, "_parent_settings", lambda: json.dumps({"env": {"CUSTOM_CONNECTION": "settings", "UNRELATED": "kept"}})
    )
    options: list[Any] = []

    def factory(value: Any) -> QueueClaudeClient:
        options.append(value)
        return QueueClaudeClient(
            [[claude_agent_sdk.SystemMessage("init", {"session_id": "claude-env"}), AssistantMessage("開始")]]
        )

    async with claude.ClaudeServerManager(client_factory=factory) as backend:
        session = await backend.start("調査", str(tmp_path), launch_kind=launch_kind)
        prompt = state.ResumePrompt("続行")
        try:
            await backend.resume(session.session_id, prompt, str(tmp_path), launch_kind=launch_kind, existing_session=session)
        finally:
            prompt.close()
        for value in options:
            assert value.env["CUSTOM_CONNECTION"] == value.env["AWS_PROFILE"] == ""
            settings = json.loads(value.settings)
            assert settings["env"]["CUSTOM_CONNECTION"] == settings["env"]["AWS_PROFILE"] == ""
            assert settings["env"]["UNRELATED"] == "kept"
            assert "CLAUDE_CODE_OAUTH_TOKEN" not in value.env
