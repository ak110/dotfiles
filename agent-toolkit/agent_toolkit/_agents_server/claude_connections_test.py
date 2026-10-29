"""SDK起動の接続隔離とMCP再接続を、managerの公開操作から検証する。"""

import asyncio
import contextlib
import json
import logging
import os
import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import (
    api_credits,
    child_environment,
    claude,
    mcp_tools,
    responses,
    resume_waits,
    state,
)
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
    """両認証があり委譲先をサブスクリプション優先にしたWI担当は公開startからbase・上位・subscriptionで起動する。"""
    path = tmp_path / "api.env"
    secret = "test-api-value-not-for-logs"
    path.write_text(f"ANTHROPIC_API_KEY={secret}\nCUSTOM_CONNECTION={secret}\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE", str(path))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_CONNECTIONS", "subscription,payg")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", "claude:opus/high")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_MODEL", "codex:gpt-6-sol/medium")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-oauth-value-not-for-logs")
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setattr(child_environment, "_INHERITED", {})
    child_environment.prepare(os.environ)
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
                "execute",
            )
            assert session.claude_connection == "subscription"
    assert len(options) == 1
    assert "ANTHROPIC_API_KEY" not in options[0].env
    assert "CUSTOM_CONNECTION" not in options[0].env
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
        lambda *_args, **_kwargs: claude_connection.Candidates((claude_connection.Connection("credits"),)),
    )
    session = state.SessionState("connection-test", str(tmp_path), engine="claude", claude_connection="credits")
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
    assert f"claude_connection_attempt connection=credits role=delegate resumed={str(resumed).lower()}" in caplog.text
    if failed:
        assert "claude_connection_failed connection=credits phase=initialization error_type=RuntimeError" in caplog.text
        assert "claude_connection_selected" not in caplog.text
    else:
        assert "claude_connection_selected session_id=connection-test connection=credits" in caplog.text
    assert secret not in caplog.text


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["authentication", "api_credits", "usage_limit"])
async def test_connection_turn_logs_record_reason_without_failure_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture, reason: str
) -> None:
    """初期化後の失敗理由とAPI切替を分類値で記録し、元エラーの認証値はログへ渡さない。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_USAGE_LIMIT_WAIT", "never")
    secret = "test-turn-auth-value-not-for-logs"
    current = "credits" if reason == "api_credits" else "subscription"
    following = "subscription" if current == "credits" else "credits"
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
    # 残高不足だけを、同じプロセスで後から起動する委譲先へ共有する。
    assert api_credits.shortage_active("claude") is (reason == "api_credits")


@pytest.mark.usefixtures("agents_server_isolation")
def test_delegate_started_on_subscription_moves_to_api_on_usage_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """残高不足の共有でサブスクリプションから始めた委譲先も、turn中の利用上限ではAPIへ切り替える。

    設定上の接続順（api, subscription）で現在より前にあるAPIを、このsessionで未失敗の後続として選ぶ。
    """
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_USAGE_LIMIT_WAIT", "never")
    session = state.SessionState("started-on-subscription", str(tmp_path), engine="claude", claude_connection="subscription")
    session.availability_checked = True
    session.usage_limit = UsageLimitState("rejected", "five_hour", None)
    monkeypatch.setattr(
        claude_connection,
        "candidates",
        lambda *_args, **_kwargs: claude_connection.Candidates(
            (claude_connection.Connection("credits", "api.env", "api.env"), claude_connection.Connection("subscription"))
        ),
    )
    assert resume_waits.begin_usage_limit_wait(
        session, {"status": "failed", "error": {"message": "limit"}, "agent_message": ""}
    )
    assert session.claude_connection == "credits"
    assert session.claude_api_env_file == "api.env"
    assert session.claude_api_resume_pending


def _three_kinds(*_args: object, **_kwargs: object) -> claude_connection.Candidates:
    """設定上の使用順`credits,subscription,payg`の3種別の候補を返す。"""
    return claude_connection.Candidates(
        (
            claude_connection.Connection("credits", "credits.env", "credits.env"),
            claude_connection.Connection("subscription"),
            claude_connection.Connection("payg", "payg.env", "payg.env"),
        )
    )


@pytest.mark.usefixtures("agents_server_isolation")
def test_subscription_limit_after_credit_shortage_follows_effective_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """残高不足の共有中にサブスクリプションで始めた委譲先は、利用上限で後ろへ回したクレジットより先に従量課金へ移る。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_USAGE_LIMIT_WAIT", "never")
    monkeypatch.setattr(claude_connection, "candidates", _three_kinds)
    api_credits.record_shortage("claude")
    session = state.SessionState("effective-order", str(tmp_path), engine="claude", claude_connection="subscription")
    session.availability_checked = True
    session.usage_limit = UsageLimitState("rejected", "five_hour", None)
    assert resume_waits.begin_usage_limit_wait(
        session, {"status": "failed", "error": {"message": "limit"}, "agent_message": ""}
    )
    assert session.claude_connection == "payg"
    assert session.claude_api_env_file == "payg.env"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.parametrize("status", [401, 403])
def test_payg_rejection_after_successful_turn_moves_to_next_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, status: int
) -> None:
    """一度成功した従量課金の会話がAPIの認証・権限で拒否されたら、同じ会話を使用順の次の接続へ移す。"""
    monkeypatch.setattr(
        claude_connection,
        "candidates",
        lambda *_args, **_kwargs: claude_connection.Candidates(
            (claude_connection.Connection("payg", "payg.env", "payg.env"), claude_connection.Connection("subscription"))
        ),
    )
    session = state.SessionState(
        "payg-rejected", str(tmp_path), engine="claude", claude_connection="payg", claude_api_env_file="payg.env"
    )
    session.availability_checked = True
    error = {"message": "authentication_error", "apiErrorStatus": status}
    assert resume_waits.begin_usage_limit_wait(session, {"status": "failed", "error": error, "agent_message": ""})
    assert session.claude_connection == "subscription"
    assert session.claude_api_env_file is None
    assert session.claude_api_resume_pending
    assert "payg" in session.claude_connection_failures


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.parametrize(
    ("message", "shared"),
    [
        ("Credit balance is too low", True),
        ('{"type": "billing_error"}', True),
        ("API usage limit reached for this organization", False),
    ],
)
def test_only_api_credit_shortage_is_shared_across_new_delegates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, message: str, shared: bool
) -> None:
    """API接続の失敗のうち残高不足だけを共有し、APIの利用上限など他の理由の失敗は共有しない。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_USAGE_LIMIT_WAIT", "never")
    session = state.SessionState(
        "credit-share", str(tmp_path), engine="claude", claude_connection="credits", claude_api_env_file="api.env"
    )
    session.availability_checked = True
    monkeypatch.setattr(
        claude_connection,
        "candidates",
        lambda *_args, **_kwargs: claude_connection.Candidates(
            (claude_connection.Connection("credits"), claude_connection.Connection("subscription"))
        ),
    )
    resume_waits.begin_usage_limit_wait(session, {"status": "failed", "error": {"message": message}, "agent_message": ""})
    assert api_credits.shortage_active("claude") is shared


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize(("model_type", "reordered"), [(None, True), ("orchestrate", False)])
async def test_new_delegate_starts_from_subscription_while_credit_shortage_is_shared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, model_type: str | None, reordered: bool
) -> None:
    """残高不足の共有中は新しい委譲先だけがAPIを後ろへ回し、オーケストレーターの接続順は変えない。"""
    monkeypatch.setattr(
        claude_connection,
        "candidates",
        lambda *_args, **_kwargs: claude_connection.Candidates(
            (claude_connection.Connection("credits"), claude_connection.Connection("subscription"))
        ),
    )
    api_credits.record_shortage("claude")
    session = state.SessionState("ordered", str(tmp_path), engine="claude")
    async with claude.ClaudeServerManager() as backend:
        start = AsyncMock(return_value=session)
        monkeypatch.setattr(backend, "_start_owned_task", start)
        assert await backend.start("開始", str(tmp_path), model_type=model_type) is session
    assert start.await_args is not None
    assert start.await_args.kwargs["connection_kind"] == ("subscription" if reordered else "credits")


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
@pytest.mark.parametrize("injection", ["environment-and-settings", "settings-only"])
async def test_subscription_sdk_and_settings_isolate_custom_api_keys_on_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, launch_kind: state.LaunchKind, injection: str
) -> None:
    """全起動区分の新規・再開で、親settingsだけの注入も同じ接続キー集合で隔離する。"""
    path = tmp_path / "api.env"
    path.write_text(
        "CUSTOM_CONNECTION=api-only\nAWS_PROFILE=api-profile\nANTHROPIC_DEFAULT_SONNET_MODEL=api-model\n", encoding="utf-8"
    )
    connection = claude_connection.Connection("subscription", isolation_env_file=str(path))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_CREDITS_ENV_FILE", str(path))
    monkeypatch.setattr(claude_connection, "candidates", lambda *_args, **_kwargs: claude_connection.Candidates((connection,)))
    keys = ("CUSTOM_CONNECTION", "AWS_PROFILE", "ANTHROPIC_DEFAULT_SONNET_MODEL")
    for key in keys:
        if injection == "settings-only":
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, "parent-environment")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth")
    monkeypatch.setattr(
        claude, "_parent_settings", lambda: json.dumps({"env": {**dict.fromkeys(keys, "parent-settings"), "UNRELATED": "kept"}})
    )
    monkeypatch.setattr(child_environment, "_INHERITED", {})
    child_environment.prepare(os.environ)
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
        assert len(options) == 2
        for value in options:
            settings = json.loads(value.settings)
            for key in keys:
                assert key not in settings["env"]
                assert key not in value.env
                assert key not in {**os.environ, **value.env, **settings["env"]}
            assert settings["env"]["UNRELATED"] == "kept"
            assert value.env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth"
