"""API接続への移行と、接続設定を保持した同じ会話の再開を検証する。"""

import asyncio
import contextlib
import json
import os
import pathlib
import types
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest
from claude_agent_sdk import ToolResultBlock, UserMessage

from agent_toolkit._agents_server import child_environment, claude, claude_settings, resume_waits
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server.state import ResumePrompt
from agent_toolkit._common import session_registry
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    FakeClaudeClient,
    QueueClaudeClient,
    ResultMessage,
    SystemMessage,
    _rejected_stream,
    install_backend,
)


@pytest.fixture(autouse=True)
def _subscription_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDKダブルが持つサブスクリプション認証を起動環境にも明示する。"""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-subscription")


@pytest.mark.asyncio
async def test_subscription_launch_drops_parent_api_auth(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """親の環境変数と親settingsにあるAPI認証を遮断し、OAuth接続を保持する。"""
    api = tmp_path / "api.env"
    api.write_text("ANTHROPIC_API_KEY=configured-key\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(api))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-key")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "parent-token")
    monkeypatch.setattr(
        claude,
        "_parent_settings",
        lambda: json.dumps(
            {
                "env": {"ANTHROPIC_API_KEY": "settings-key", "ANTHROPIC_AUTH_TOKEN": "settings-token", "CUSTOM": "kept"},
                "apiKeyHelper": "parent-helper",
            }
        ),
    )
    seen: list[Any] = []

    def factory(options: Any) -> FakeClaudeClient:
        seen.append(options)
        return FakeClaudeClient([[SystemMessage("isolated-auth"), AssistantMessage("応答"), ResultMessage("完了")]])

    async with claude.ClaudeServerManager(client_factory=factory) as backend:
        await backend.start("作業", str(tmp_path))
        options = seen[0]
        assert not options.env.get("ANTHROPIC_API_KEY") and not options.env.get("ANTHROPIC_AUTH_TOKEN")
        settings = json.loads(options.settings)
        assert "ANTHROPIC_API_KEY" not in settings["env"] and "ANTHROPIC_AUTH_TOKEN" not in settings["env"]
        assert settings["env"]["CUSTOM"] == "kept"
        assert not settings["apiKeyHelper"]


@pytest.mark.asyncio
async def test_explicit_subscription_orchestrator_without_api_file_isolates_sdk_auth(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """API環境未設定でも、明示したサブスク固定を新規SDK起動の環境とsettingsへ適用する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", "")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_API_ONLY", "false")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-api")
    monkeypatch.setattr(child_environment, "_INHERITED", {})
    child_environment.prepare(os.environ)
    monkeypatch.setattr(
        claude,
        "_parent_settings",
        lambda: json.dumps({"apiKeyHelper": "parent-helper", "env": {"ANTHROPIC_AUTH_TOKEN": "parent-token"}}),
    )
    seen: list[Any] = []
    environments: list[dict[str, str]] = []

    def factory(options: Any) -> FakeClaudeClient:
        seen.append(options)
        environments.append({**os.environ, **options.env})
        return FakeClaudeClient([[SystemMessage("subscription-orchestrator"), AssistantMessage("応答"), ResultMessage("完了")]])

    async with claude.ClaudeServerManager(client_factory=factory) as backend:
        session = await backend.start("作業", str(tmp_path), model_type="orchestrate")
        assert session.claude_connection == "subscription"
        assert environments[0].get("CLAUDE_CODE_OAUTH_TOKEN") == "test-subscription"
        assert not environments[0].get("ANTHROPIC_API_KEY")
        selected_settings = json.loads(seen[0].settings)
        assert not selected_settings["env"].get("ANTHROPIC_AUTH_TOKEN")
        assert not selected_settings["apiKeyHelper"]


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["inherit", "api"])
async def test_explicit_subscription_orchestrator_reselects_same_conversation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, initial: str
) -> None:
    """明示した固定を既存会話の次turnと冷再開へ適用し、同じSDK会話をサブスクで続ける。"""
    monkeypatch.setattr(child_environment, "_INHERITED", {})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-api")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", "")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_MODE", "pay-as-you-go")
    if initial == "api":
        path = tmp_path / "api.env"
        path.write_text("ANTHROPIC_API_KEY=selected-api\n", encoding="utf-8")
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(path))
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_API_ONLY", "true")
    child_environment.prepare(os.environ)
    seen: list[Any] = []
    environments: list[dict[str, str]] = []

    def factory(options: Any) -> FakeClaudeClient:
        seen.append(options)
        environments.append({**os.environ, **options.env})
        return FakeClaudeClient([[SystemMessage("fixed-conversation"), AssistantMessage("応答"), ResultMessage("完了")]])

    async with claude.ClaudeServerManager(client_factory=factory) as backend:
        session = await backend.start("当初の要求", str(tmp_path), model_type="orchestrate")
        await _until(lambda: session.terminal)
        assert session.claude_connection == initial
        assert environments[0].get("ANTHROPIC_API_KEY") == ("selected-api" if initial == "api" else "parent-api")
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", "")
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_API_ONLY", "false")
        await backend.send_message(session, "次の作業")
        await _until(lambda: session.terminal)
        assert len(seen) == 2
        assert seen[-1].resume == "fixed-conversation"
        assert session.claude_connection == "subscription"
        assert not environments[-1].get("ANTHROPIC_API_KEY")
        assert environments[-1].get("CLAUDE_CODE_OAUTH_TOKEN") == "test-subscription"
    async with claude.ClaudeServerManager(client_factory=factory) as backend:
        with contextlib.closing(ResumePrompt("保存後の作業")) as prompt:
            restored = await backend.resume(
                session.session_id, prompt, str(tmp_path), model_type="orchestrate", existing_session=session
            )
        assert restored.session_id == session.session_id
        assert restored.claude_connection == "subscription"
        assert seen[-1].resume == "fixed-conversation"
        assert not environments[-1].get("ANTHROPIC_API_KEY")
        assert environments[-1].get("CLAUDE_CODE_OAUTH_TOKEN") == "test-subscription"


@pytest.mark.asyncio
async def test_unconfigured_start_and_cold_resume_preserve_parent_auth(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開startと保存済み会話の再開で、APIキー・Bedrock・親settingsの認証を引き継ぐ。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", "")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "parent-key")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    monkeypatch.setattr(
        claude,
        "_parent_settings",
        lambda: json.dumps(
            {
                "apiKeyHelper": "parent-helper",
                "env": {"ANTHROPIC_AUTH_TOKEN": "settings-token"},
            }
        ),
    )
    seen: list[Any] = []

    def factory(options: Any) -> FakeClaudeClient:
        seen.append(options)
        effective = {**os.environ, **options.env}
        assert effective["ANTHROPIC_API_KEY"] == "parent-key"
        assert effective["CLAUDE_CODE_USE_BEDROCK"] == "1"
        settings = json.loads(options.settings)
        assert settings["apiKeyHelper"] == "parent-helper"
        assert settings["env"]["ANTHROPIC_AUTH_TOKEN"] == "settings-token"
        return FakeClaudeClient([[SystemMessage("inherited-auth"), AssistantMessage("応答"), ResultMessage("完了")]])

    async with server_manager.AgentsServerManager() as manager:
        install_backend(
            manager, "claude", claude.ClaudeServerManager(manager.sessions, client_factory=factory, publish_registry=True)
        )
        started = await manager.start("claude:sonnet/high", "作業", str(tmp_path))
        assert started["session_id"] == "inherited-auth"
        await manager.stop("inherited-auth")
        info = session_registry.resolve("inherited-auth").resume_info
        assert info is not None and info.claude_connection == "inherit"
    api = tmp_path / "later.env"
    api.write_text("ANTHROPIC_API_KEY=later-key\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(api))
    async with server_manager.AgentsServerManager() as manager:
        install_backend(
            manager, "claude", claude.ClaudeServerManager(manager.sessions, client_factory=factory, publish_registry=True)
        )
        await manager.send_message("inherited-auth", "継続")
        assert len(seen) == 2 and seen[-1].resume == "inherited-auth"


@pytest.mark.asyncio
@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "shell", "write"])
async def test_usage_limit_api_migration_and_cold_resume(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    launch_kind: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """起動直後の上限から同じ会話を移し、追送・stop・再起動でも保持したファイルを読み直す。"""
    env_file = tmp_path / "api.env"
    env_file.write_text("# 接続\nexport ANTHROPIC_AUTH_TOKEN='dummy-secret'\nCUSTOM_API_NAME=\"one=two\"\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(env_file))
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.01)
    options_seen: list[Any] = []
    settings_seen: list[str] = []
    clients: list[FakeClaudeClient] = []

    def factory(options: Any) -> FakeClaudeClient:
        options_seen.append(options)
        if len(options_seen) == 1:
            streams = [_rejected_stream("seven_day", None, before_init=True, session_id="api-session")]
        else:
            streams = [[SystemMessage(options.resume or "fresh-session"), AssistantMessage("応答"), ResultMessage("完了")]]
        streams.append([AssistantMessage("追送の応答"), ResultMessage("追送完了")])
        client = FakeClaudeClient(streams)
        clients.append(client)
        if options.resume:
            assert isinstance(options.settings, claude_settings.SettingsFile)
            path = pathlib.Path(options.settings)
            assert path.stat().st_mode & 0o777 == 0o600
            settings_seen.append(path.read_text(encoding="utf-8"))
        return client

    @contextlib.asynccontextmanager
    async def open_manager() -> AsyncGenerator[server_manager.AgentsServerManager]:
        async with (
            server_manager.AgentsServerManager() as manager,
            claude.ClaudeServerManager(
                manager.sessions,
                client_factory=factory,
                publish_registry=True,
            ) as backend,
        ):
            install_backend(manager, "claude", backend)
            yield manager

    async def terminal(manager: server_manager.AgentsServerManager, identifier: str) -> dict[str, Any]:
        for _ in range(200):
            result = await asyncio.wait_for(manager.wait(), timeout=3)
            if result["status"] in {"completed", "failed", "interrupted"}:
                assert result["session_id"] == identifier
                return result
            await asyncio.sleep(0)
        pytest.fail("会話が終了しない")

    async with open_manager() as manager:
        started = await manager.start("claude:sonnet/high", "作業する", str(tmp_path), launch_kind=launch_kind)
        result = await terminal(manager, "api-session")
        assert started["session_id"] == "api-session"
        assert result["status"] == "completed"
        assert len(options_seen) == 2
        assert options_seen[0].resume is None
        assert not options_seen[0].env.get("ANTHROPIC_AUTH_TOKEN")
        assert options_seen[1].resume == "api-session"
        assert options_seen[1].model == "sonnet" and options_seen[1].effort == "high"
        assert options_seen[1].env["ANTHROPIC_AUTH_TOKEN"] == "dummy-secret"
        assert "dummy-secret" not in str(options_seen[1].settings)
        assert json.loads(settings_seen[0])["env"]["CUSTOM_API_NAME"] == "one=two"
        assert clients[0].disconnected
        assert "API" not in clients[1].queries[0]
        session = manager.sessions["api-session"]
        assert session.api_error is None
        assert session.claude_api_env_file == str(env_file)
        shown = json.dumps((await manager.show_session("api-session", verbose=True)), ensure_ascii=False)
        assert "dummy-secret" not in shown and "claude_api_env_file" not in shown
        public = json.dumps([result, (await manager.list_sessions(include_terminated=True))], ensure_ascii=False)
        assert "dummy-secret" not in public and "claude_api_env_file" not in public
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", "")
        await manager.send_message("api-session", "追送")
        assert (await terminal(manager, "api-session"))["status"] == "completed"
        await manager.stop("api-session")
        assert not pathlib.Path(options_seen[1].settings).exists()
        info = session_registry.resolve("api-session").resume_info
        assert info is not None and info.claude_api_env_file == str(env_file)
        switches = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("claude_api_switch session_id=api-session ")
        ]
        assert len(switches) == 1
        assert "previous=subscription selected=api reason=usage_limit limit_type=seven_day" in switches[0]
        assert "dummy-secret" not in caplog.text

    env_file.write_text("ANTHROPIC_AUTH_TOKEN=rotated-secret\n", encoding="utf-8")
    async with open_manager() as manager:
        await manager.send_message("api-session", "残作業を続ける")
        assert (await terminal(manager, "api-session"))["status"] == "completed"
        assert options_seen[-1].env["ANTHROPIC_AUTH_TOKEN"] == "rotated-secret"
        manager.sessions["api-session"].retention_deadline = asyncio.get_running_loop().time() - 1
        env_file.write_text("ANTHROPIC_AUTH_TOKEN=expiry-secret\n", encoding="utf-8")
        await manager.send_message("api-session", "期限後の継続")
        assert (await terminal(manager, "api-session"))["status"] == "completed"
        assert options_seen[-1].env["ANTHROPIC_AUTH_TOKEN"] == "expiry-secret"
        fresh = await manager.start("claude:sonnet/high", "新規", str(tmp_path), launch_kind=launch_kind)
        await terminal(manager, fresh["session_id"])
        assert not options_seen[-1].env.get("ANTHROPIC_AUTH_TOKEN")
        for path in session_registry.registry_directory().glob("*.json"):
            content = path.read_text(encoding="utf-8")
            assert "dummy-secret" not in content and "rotated-secret" not in content
    for options in options_seen:
        if isinstance(options.settings, claude_settings.SettingsFile):
            assert not pathlib.Path(options.settings).exists()
    assert "rotated-secret" not in caplog.text and "expiry-secret" not in caplog.text


@pytest.mark.asyncio
async def test_api_settings_cleanup_on_connect_failure(tmp_path: pathlib.Path) -> None:
    """API接続開始に失敗しても所有する合成ファイルを回収する。"""
    env_file = tmp_path / "api.env"
    env_file.write_text("TOKEN=dummy-secret\n", encoding="utf-8")
    settings: list[pathlib.Path] = []

    def fail(options: Any) -> Any:
        settings.append(pathlib.Path(options.settings))
        raise RuntimeError("接続失敗")

    async with claude.ClaudeServerManager(client_factory=fail) as backend:
        prompt = ResumePrompt("再開")
        try:
            with pytest.raises(RuntimeError, match="接続失敗"):
                await backend.resume("api-session", prompt, str(tmp_path), api_env_file=str(env_file))
            assert settings and all(not path.exists() for path in settings)
        finally:
            prompt.close()
            await backend.close()


@pytest.mark.asyncio
async def test_api_migration_holds_result_until_old_connection_closes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """常駐監視が旧接続の切断を待つ間も、公開waitへ中断前の失敗を渡さない。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_USAGE_LIMIT_WAIT", "never")
    env_file = tmp_path / "api.env"
    env_file.write_text("TOKEN=dummy\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(env_file))
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.01)
    disconnecting = asyncio.Event()
    release = asyncio.Event()

    class Client(FakeClaudeClient):
        async def disconnect(self) -> None:
            disconnecting.set()
            await release.wait()
            await super().disconnect()

    def factory(options: Any) -> FakeClaudeClient:
        if options.resume is None:
            return Client([_rejected_stream("five_hour", None, before_init=False, session_id="migration")])
        return FakeClaudeClient([[SystemMessage("migration"), AssistantMessage("再開"), ResultMessage("完了")]])

    async with (
        server_manager.AgentsServerManager() as manager,
        claude.ClaudeServerManager(manager.sessions, client_factory=factory) as backend,
    ):
        install_backend(manager, "claude", backend)
        manager.activate()
        waiting: asyncio.Task[dict[str, Any]] | None = None
        try:
            await manager.start("claude:sonnet/high", "作業する", str(tmp_path))
            await asyncio.wait_for(disconnecting.wait(), timeout=3)
            shown = await manager.show_session("migration")
            assert shown["status"] == "running" and shown["result_held"]
            waiting = asyncio.create_task(manager.wait())
            await asyncio.sleep(0)
            assert not waiting.done()
            release.set()
            result = await asyncio.wait_for(waiting, timeout=3)
            assert result["status"] == "completed" and result["agent_message"] == "完了"
        finally:
            release.set()
            if waiting is not None and not waiting.done():
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            await manager.close()


def _pay_as_you_go_with_clock(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """`pay-as-you-go`の両認証環境と、解除予定時刻の判定が読む差し替え可能な時刻を用意する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_USAGE_LIMIT_WAIT", "never")
    env_file = tmp_path / "api.env"
    env_file.write_text("ANTHROPIC_AUTH_TOKEN=dummy-secret\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(env_file))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_MODE", "pay-as-you-go")
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.01)
    clock = {"now": 1_000_000.0}
    fake_time = types.SimpleNamespace(time=lambda: clock["now"])
    monkeypatch.setattr(resume_waits, "time", fake_time)
    monkeypatch.setattr(claude, "time", fake_time)
    return clock


@pytest.mark.asyncio
async def test_api_switched_delegate_returns_to_subscription_after_reset(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pay-as-you-go`で上限によりAPIへ移した委譲先は、解除予定時刻の後に始まる最初のturnで同じ会話をサブスクリプションへ戻す。

    解除予定時刻より前の追送はAPIのまま所有タスクへ送り、上限による即時切替は従来どおり行う。
    """
    clock = _pay_as_you_go_with_clock(tmp_path, monkeypatch)
    options_seen: list[Any] = []

    def factory(options: Any) -> FakeClaudeClient:
        options_seen.append(options)
        if len(options_seen) == 1:
            streams = [_rejected_stream("five_hour", int(clock["now"]) + 100, before_init=True, session_id="return-session")]
        else:
            streams = [
                [SystemMessage(options.resume or "fresh-session"), AssistantMessage("応答"), ResultMessage("完了")],
                [AssistantMessage("追送の応答"), ResultMessage("追送完了")],
            ]
        return FakeClaudeClient(streams)

    async def terminal(manager: server_manager.AgentsServerManager) -> dict[str, Any]:
        for _ in range(200):
            result = await asyncio.wait_for(manager.wait(), timeout=3)
            if result["status"] in {"completed", "failed", "interrupted"}:
                return result
            await asyncio.sleep(0)
        pytest.fail("会話が終了しない")

    async with server_manager.AgentsServerManager() as manager:
        install_backend(
            manager, "claude", claude.ClaudeServerManager(manager.sessions, client_factory=factory, publish_registry=True)
        )
        await manager.start("claude:sonnet/high", "作業する", str(tmp_path))
        assert (await terminal(manager))["status"] == "completed"
        assert len(options_seen) == 2
        assert options_seen[1].env["ANTHROPIC_AUTH_TOKEN"] == "dummy-secret"
        session = manager.sessions["return-session"]
        assert session.claude_connection == "api"

        clock["now"] += 50
        await manager.send_message("return-session", "解除前の追送")
        assert (await terminal(manager))["status"] == "completed"
        assert len(options_seen) == 2
        assert manager.sessions["return-session"].claude_connection == "api"

        clock["now"] += 100
        await manager.send_message("return-session", "解除後の追送")
        assert (await terminal(manager))["status"] == "completed"
        assert len(options_seen) == 3
        assert options_seen[2].resume == "return-session"
        assert not options_seen[2].env.get("ANTHROPIC_AUTH_TOKEN")
        assert manager.sessions["return-session"].claude_connection == "subscription"


async def _until(condition: Any, timeout: float = 5.0) -> None:
    """条件の成立を短い間隔で確かめ、上限までに成立しなければ失敗させる。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() >= deadline:
            pytest.fail("条件が成立しない")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["after-reset", "before-reset", "still-limited"])
async def test_api_switched_delegate_auto_resume_returns_to_subscription_after_reset(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """孫sessionの終了による自動再開も、解除予定時刻の後なら同じ会話をサブスクリプションで再開する。

    APIへ移した会話が孫sessionを待って結果を保留し、孫の終了で自動再開する。
    解除予定時刻より前の自動再開はAPIの所有タスクへ送り、後の自動再開は通常の追送と同じくサブスクリプションへ戻す。
    戻したturnがなお上限で拒否された場合は、既存の切替で同じ会話を再びAPIで続ける。
    """
    after_reset = mode != "before-reset"
    clock = _pay_as_you_go_with_clock(tmp_path, monkeypatch)
    options_seen: list[Any] = []
    api_client = QueueClaudeClient(
        [
            [SystemMessage("auto-session"), AssistantMessage("APIで作業を続ける")],
            [AssistantMessage("子の結果を確認した"), ResultMessage("APIの最終結果")],
        ]
    )

    def factory(options: Any) -> FakeClaudeClient:
        options_seen.append(options)
        if len(options_seen) == 1:
            return FakeClaudeClient(
                [_rejected_stream("five_hour", int(clock["now"]) + 100, before_init=True, session_id="auto-session")]
            )
        if len(options_seen) == 2:
            return api_client
        if mode == "still-limited" and len(options_seen) == 3:
            return FakeClaudeClient(
                [_rejected_stream("five_hour", int(clock["now"]) + 100, before_init=False, session_id="auto-session")]
            )
        final = "再びAPIの最終結果" if mode == "still-limited" else "サブスクの最終結果"
        return FakeClaudeClient([[SystemMessage("auto-session"), AssistantMessage("子の結果を確認した"), ResultMessage(final)]])

    async with server_manager.AgentsServerManager() as manager:
        condition = manager._condition  # pylint: disable=protected-access  # noqa: SLF001
        async with claude.ClaudeServerManager(
            manager.sessions, condition, client_factory=factory, publish_registry=True
        ) as backend:
            install_backend(manager, "claude", backend)
            session_registry.publish("child", terminal=False, cwd=str(tmp_path))
            # 公開待機が呼ばれるかによらず、上限からのAPI移行と孫sessionの終了による自動再開を常駐監視で進める。
            manager.activate()
            try:
                await manager.start("claude:sonnet/high", "作業する", str(tmp_path))
                await _until(lambda: len(options_seen) == 2 and "auto-session" in manager.sessions and bool(api_client.queries))
                session = manager.sessions["auto-session"]
                assert session.claude_connection == "api"
                tool_use = AssistantMessage("")
                tool_use.content = [
                    SimpleNamespace(
                        id="reply-child",
                        name="mcp__agents_server__send_message",
                        input={"session_id": "child", "prompt": "続ける"},
                    )
                ]
                api_client.messages.put_nowait(tool_use)
                api_client.messages.put_nowait(
                    UserMessage(
                        content=[
                            ToolResultBlock(
                                tool_use_id="reply-child",
                                content=json.dumps({"session_id": "child", "delivery": "reply_started", "status": "running"}),
                            )
                        ]
                    )
                )
                api_client.messages.put_nowait(ResultMessage("孫を待つ"))
                await _until(lambda: session.awaiting_auto_resume)
                if after_reset:
                    clock["now"] += 150
                session_registry.publish("child", terminal=True, status="completed", cwd=str(tmp_path))
                result = await asyncio.wait_for(manager.wait(), 3)
                while result["status"] == "running":
                    result = await asyncio.wait_for(manager.wait(), 3)
                assert result["status"] == "completed"
                if mode == "after-reset":
                    assert result["agent_message"] == "サブスクの最終結果"
                    assert len(options_seen) == 3
                    assert options_seen[2].resume == "auto-session"
                    assert not options_seen[2].env.get("ANTHROPIC_AUTH_TOKEN")
                    assert session.claude_connection == "subscription"
                elif mode == "still-limited":
                    assert result["agent_message"] == "再びAPIの最終結果"
                    assert len(options_seen) == 4
                    assert options_seen[2].resume == "auto-session" and not options_seen[2].env.get("ANTHROPIC_AUTH_TOKEN")
                    assert options_seen[3].resume == "auto-session"
                    assert options_seen[3].env["ANTHROPIC_AUTH_TOKEN"] == "dummy-secret"
                    assert session.claude_connection == "api"
                else:
                    assert result["agent_message"] == "APIの最終結果"
                    assert len(options_seen) == 2
                    assert session.claude_connection == "api"
            finally:
                await manager.close()
