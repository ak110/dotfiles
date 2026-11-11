"""claudeのsettings_ownershipの契約を検証する。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import contextlib
import pathlib
from types import SimpleNamespace
from typing import Any

import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import claude, claude_options, session_errors
from agent_toolkit._common import claude_connection as claude_settings
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    FakeClaudeClient,
    QueueClaudeClient,
    ResultMessage,
    SystemMessage,
)


@pytest.mark.parametrize(
    ("cmdline", "expected"),
    [
        (b"claude\0--settings=/tmp/settings.json\0", "/tmp/settings.json"),
        (b"claude\0--settings\0/tmp/settings.json\0", "/tmp/settings.json"),
        (b"claude\0--verbose\0", None),
        (b"claude\0--settings=\0", None),
        (b"claude\0--settings", None),
    ],
)
def test_settings_from_cmdline(cmdline: bytes, expected: str | None) -> None:
    """親プロセスの2種類のsettings引数だけから非空値を検出する。"""
    assert claude._settings_from_cmdline(cmdline) == expected  # pylint: disable=protected-access


def test_settings_files_have_independent_ownership(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(claude_settings, "state_dir", lambda: tmp_path / "state")
    parent = tmp_path / "parent.json"
    parent.write_text('{"env":{"OTHER":"parent"}}', encoding="utf-8")
    first = claude_settings.merge_settings(str(parent), {"KEY": "first"}, "root")
    second = claude_settings.merge_settings(str(parent), {"KEY": "second"}, "root")
    assert first != second
    claude_settings.cleanup_settings(SimpleNamespace(settings=first))
    assert not pathlib.Path(first).exists()
    assert pathlib.Path(second).exists()
    claude_settings.cleanup_settings(SimpleNamespace(settings=second))
    assert parent.exists()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "factory", "connect"])
async def test_settings_file_lives_until_backend_disconnect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, failure: str | None
) -> None:
    """CLIの設定読取と切断までファイルを保ち、正常終了と起動失敗で自身の分を回収する。"""
    parent = tmp_path / "parent.json"
    parent.write_text('{"env":{"OTHER":"parent"}}', encoding="utf-8")
    monkeypatch.setattr(claude_options, "_parent_settings", lambda: str(parent))
    monkeypatch.setattr(claude_settings, "state_dir", lambda: tmp_path / "state")
    paths: list[pathlib.Path] = []

    class Client(FakeClaudeClient):
        async def connect(self) -> None:
            assert paths[0].exists()
            if failure == "connect":
                raise RuntimeError("connect failed")
            await super().connect()

        async def disconnect(self) -> None:
            assert paths[0].exists()
            await super().disconnect()

    client = Client([[SystemMessage("settings-lifetime"), ResultMessage("完了")]])

    def factory(options: Any) -> Client:
        paths.append(pathlib.Path(options.settings))
        assert paths[0].exists()
        if failure == "factory":
            raise RuntimeError("factory failed")
        return client

    async with claude.ClaudeServerManager(client_factory=factory) as manager:
        if failure is None:
            await manager.start("調査", str(tmp_path))
            assert paths[0].exists()
        else:
            with pytest.raises(RuntimeError, match=f"{failure} failed"):
                await manager.start("調査", str(tmp_path))
    assert not paths[0].exists()
    assert parent.exists()


def test_dependency_check_reclaims_its_settings_file(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """CLIを起動しない依存確認も、生成した合成ファイルを保持しない。"""
    parent = tmp_path / "parent.json"
    parent.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(claude_options, "_parent_settings", lambda: str(parent))
    monkeypatch.setattr(claude_settings, "state_dir", lambda: tmp_path / "state")
    claude.check_dependencies()
    assert not list((tmp_path / "state").rglob("settings-*.json"))
    assert parent.exists()


@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.asyncio
async def test_explore_options_construction_owns_settings_until_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, failed: bool
) -> None:
    """探索のMCP定義読取後にoptions生成が失敗しても、生成したsettingsを回収する。"""
    parent = tmp_path / "parent.json"
    parent.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(claude_options, "_parent_settings", lambda: str(parent))
    paths: list[pathlib.Path] = []
    original_options = claude_agent_sdk.ClaudeAgentOptions

    def options(**kwargs: Any) -> Any:
        assert "pyfltr" in kwargs["mcp_servers"]
        assert isinstance(kwargs["settings"], claude_settings.SettingsFile)
        paths.append(pathlib.Path(kwargs["settings"]))
        assert paths[-1].exists()
        if failed:
            raise RuntimeError("options生成失敗")
        return original_options(**kwargs)

    monkeypatch.setattr(claude_agent_sdk, "ClaudeAgentOptions", options)
    client = FakeClaudeClient([[SystemMessage("settings-handoff"), ResultMessage("完了\n状態: completed")]])
    expected = pytest.raises(RuntimeError, match="options生成失敗") if failed else contextlib.nullcontext()
    with expected:
        async with claude.ClaudeServerManager(client_factory=lambda _options: client) as backend:
            await backend.start("探索の寿命確認", str(tmp_path), launch_kind="explore")
    assert len(paths) == 1 and not paths[0].exists()
    assert parent.exists()
    if not failed:
        assert client.disconnected


@pytest.mark.parametrize("registration_failure", [False, True])
@pytest.mark.asyncio
async def test_registration_drain_keeps_channel_and_settings_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, registration_failure: bool
) -> None:
    """停止時の登録保存が失敗しても、SDK切断、要求チャネル閉鎖とsettings回収へ到達する。"""
    parent = tmp_path / "parent.json"
    parent.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(claude_options, "_parent_settings", lambda: str(parent))
    paths: list[pathlib.Path] = []
    client = QueueClaudeClient([[SystemMessage("drain-cleanup"), AssistantMessage("作業中")]])

    def factory(options: Any) -> QueueClaudeClient:
        paths.append(pathlib.Path(options.settings))
        return client

    async with claude.ClaudeServerManager(client_factory=factory) as backend:
        session = await backend.start("登録保存の寿命確認", str(tmp_path))
        channel = backend._channels[session.session_id]
        original_drain = session.drain_registration
        calls = 0

        async def drain() -> None:
            nonlocal calls
            calls += 1
            if registration_failure and calls == 1:
                raise OSError("登録保存失敗")
            await original_drain()

        monkeypatch.setattr(session, "drain_registration", drain)
        await backend.close()
        assert calls >= 1
        assert client.disconnected and client.readers == 0
        with pytest.raises(session_errors.SessionOwnerGoneError):
            channel.send("prompt", "停止後の要求").cancel()
        assert session.session_id not in backend._channels
        assert len(paths) == 1 and not paths[0].exists()
    assert parent.exists()
