"""API接続への移行と、接続設定を保持した同じ会話の再開を検証する。"""

import asyncio
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import claude, claude_settings
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server.state import ResumePrompt
from agent_toolkit._common import session_registry
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    FakeClaudeClient,
    ResultMessage,
    SystemMessage,
    _rejected_stream,
    install_backend,
)


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

    async def open_manager() -> server_manager.AgentsServerManager:
        manager = server_manager.AgentsServerManager()
        backend = claude.ClaudeServerManager(
            manager.sessions,
            client_factory=factory,
            publish_registry=True,
        )
        install_backend(manager, "claude", backend)
        return manager

    async def terminal(manager: server_manager.AgentsServerManager, identifier: str) -> dict[str, Any]:
        for _ in range(200):
            result = await asyncio.wait_for(manager.wait(), timeout=3)
            if result["status"] in {"completed", "failed", "interrupted"}:
                assert result["session_id"] == identifier
                return result
            await asyncio.sleep(0)
        pytest.fail("会話が終端しない")

    manager = await open_manager()
    try:
        started = await manager.start("claude:sonnet/high", "作業する", str(tmp_path), launch_kind=launch_kind)
        result = await terminal(manager, "api-session")
        assert started["session_id"] == "api-session"
        assert result["status"] == "completed"
        assert len(options_seen) == 2
        assert options_seen[0].resume is None
        assert "ANTHROPIC_AUTH_TOKEN" not in options_seen[0].env
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
        shown = json.dumps(manager.show_session("api-session", verbose=True), ensure_ascii=False)
        assert "dummy-secret" not in shown and "claude_api_env_file" not in shown
        public = json.dumps([result, manager.list_sessions(include_terminated=True)], ensure_ascii=False)
        assert "dummy-secret" not in public and "claude_api_env_file" not in public
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", "")
        await manager.send_message("api-session", "追送")
        assert (await terminal(manager, "api-session"))["status"] == "completed"
        await manager.stop("api-session")
        assert not pathlib.Path(options_seen[1].settings).exists()
        info = session_registry.resolve("api-session").resume_info
        assert info is not None and info.claude_api_env_file == str(env_file)
        assert caplog.text.count("claude_api_switch session_id=api-session limit_type=seven_day") == 1
        assert "dummy-secret" not in caplog.text
    finally:
        await manager.close()

    env_file.write_text("ANTHROPIC_AUTH_TOKEN=rotated-secret\n", encoding="utf-8")
    manager = await open_manager()
    try:
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
        assert "ANTHROPIC_AUTH_TOKEN" not in options_seen[-1].env
        for path in session_registry.registry_directory().glob("*.json"):
            content = path.read_text(encoding="utf-8")
            assert "dummy-secret" not in content and "rotated-secret" not in content
    finally:
        await manager.close()
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

    backend = claude.ClaudeServerManager(client_factory=fail)
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

    manager = server_manager.AgentsServerManager()
    backend = claude.ClaudeServerManager(manager.sessions, client_factory=factory)
    install_backend(manager, "claude", backend)
    manager.activate()
    waiting: asyncio.Task[dict[str, Any]] | None = None
    try:
        await manager.start("claude:sonnet/high", "作業する", str(tmp_path))
        await asyncio.wait_for(disconnecting.wait(), timeout=3)
        shown = manager.show_session("migration")
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
