"""SDK起動の接続隔離とMCP再接続を、managerの公開操作から検証する。"""

import asyncio
import contextlib
import json
import logging
import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import claude, mcp_tools, responses, resume_waits, state
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import claude_connection, config
from agent_toolkit._common.claude_usage_limit import UsageLimitState
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    QueueClaudeClient,
    _observed_input_params,
    install_backend,
)


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


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["専念型", "付随型"])
async def test_public_wi_owner_prefers_subscription_with_both_authentications(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, route: str, caplog: pytest.LogCaptureFixture
) -> None:
    """両認証があるpay-as-you-goのWI担当は公開startからbase・上位・subscriptionで起動する。"""
    path = tmp_path / "api.env"
    secret = "test-api-value-not-for-logs"
    path.write_text(f"ANTHROPIC_API_KEY={secret}\nCUSTOM_CONNECTION={secret}\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(path))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_MODE", "pay-as-you-go")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_BASE_ENGINE", "claude")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_ENGINE", "codex")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_HIGH_TIER_MODEL", "opus/high")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-oauth-value-not-for-logs")
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    options: list[Any] = []

    def factory(value: Any) -> QueueClaudeClient:
        options.append(value)
        return QueueClaudeClient(
            [[claude_agent_sdk.SystemMessage("init", {"session_id": "wi-owner"}), AssistantMessage("開始")]]
        )

    with caplog.at_level(logging.INFO):
        async with server_manager.AgentsServerManager() as manager:
            backend = claude.ClaudeServerManager(
                manager.sessions,
                manager._condition,  # pylint: disable=protected-access
                client_factory=factory,
            )
            install_backend(manager, "claude", backend)
            monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
            params = {**_observed_input_params("add-wi.subagent.md", tmp_path), "投入経路": route}
            result = await mcp_tools.start(str(tmp_path), subagent_md_path="add-wi", extra_params=params)
            session = manager.sessions[result["session_id"]]
            assert (session.engine, session.model, session.effort, session.model_type) == (
                "claude",
                "opus",
                "high",
                "high_tier",
            )
            assert session.claude_connection == "subscription"
    assert len(options) == 1
    assert options[0].env["ANTHROPIC_API_KEY"] == options[0].env["CUSTOM_CONNECTION"] == ""
    assert "claude_connection_attempt connection=subscription role=delegate resumed=false" in caplog.text
    assert "claude_connection_selected session_id=wi-owner connection=subscription" in caplog.text
    assert secret not in caplog.text
    assert "test-oauth-value-not-for-logs" not in caplog.text


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("failed", [False, True])
async def test_connection_initialization_logs_are_value_free(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture, resumed: bool, failed: bool
) -> None:
    """新規・再開の初期化成功と失敗を識別し、接続診断へ例外本文や認証値を転記しない。"""
    secret = "test-auth-value-not-for-logs"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setattr(
        claude_connection,
        "candidates",
        lambda *_args, **_kwargs: claude_connection.Candidates((claude_connection.Connection("api"),)),
    )
    session = state.SessionState("connection-test", str(tmp_path), engine="claude", claude_connection="api")
    async with claude.ClaudeServerManager() as backend:
        start = AsyncMock(side_effect=RuntimeError(secret)) if failed else AsyncMock(return_value=session)
        monkeypatch.setattr(backend, "_start_owned_task", start)
        with caplog.at_level(logging.INFO), contextlib.closing(state.ResumePrompt("続行")) as prompt:
            operation = (
                backend.resume(session.session_id, prompt, str(tmp_path), existing_session=session)
                if resumed
                else backend.start("開始", str(tmp_path))
            )
            if failed:
                with pytest.raises(RuntimeError):
                    await operation
            else:
                assert await operation is session
    assert f"claude_connection_attempt connection=api role=delegate resumed={str(resumed).lower()}" in caplog.text
    if failed:
        assert "claude_connection_failed connection=api phase=initialization error_type=RuntimeError" in caplog.text
        assert "claude_connection_selected" not in caplog.text
    else:
        assert "claude_connection_selected session_id=connection-test connection=api" in caplog.text
    assert secret not in caplog.text


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["authentication", "api_credits", "usage_limit"])
async def test_connection_turn_logs_record_reason_without_failure_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture, reason: str
) -> None:
    """初期化後の失敗理由とAPI切替を分類値で記録し、元エラーの認証値はログへ渡さない。"""
    secret = "test-turn-auth-value-not-for-logs"
    current = "api" if reason == "api_credits" else "subscription"
    following = "subscription" if current == "api" else "api"
    session = state.SessionState(
        "turn-log", str(tmp_path), engine="claude", claude_connection=current, claude_api_env_file="api.env"
    )
    session.availability_checked = reason != "authentication"
    if reason == "usage_limit":
        session.usage_limit = UsageLimitState("rejected", "five_hour", None)
    error: dict[str, Any] = {"message": f"credit balance is too low {secret}" if reason == "api_credits" else secret}
    if reason == "authentication":
        error["apiErrorStatus"] = 401
    monkeypatch.setattr(
        claude_connection,
        "candidates",
        lambda *_args, **_kwargs: claude_connection.Candidates(
            (claude_connection.Connection(current), claude_connection.Connection(following))
        ),
    )
    with caplog.at_level(logging.INFO):
        assert resume_waits.begin_usage_limit_wait(session, {"status": "failed", "error": error, "agent_message": ""})
    assert f"connection={current} phase=turn reason={reason}" in caplog.text
    assert f"previous={current} selected={following} reason={reason}" in caplog.text
    assert secret not in caplog.text


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
