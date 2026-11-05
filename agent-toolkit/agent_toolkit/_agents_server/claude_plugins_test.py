"""CLI一覧の境界を模擬し、子用入力の反映と監視資源の寿命を検証する。"""

# SDKの制御境界と監視資源の終了を直接検証する。
# pylint: disable=protected-access

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from agent_toolkit._agents_server import claude_plugins, plugin_root


def _plugin(root: Path, version: str = "1.0.0") -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "agent-toolkit", "version": version}), encoding="utf-8"
    )
    (root / "skills").mkdir()
    (root / "skills/content.md").write_text("初期内容", encoding="utf-8")
    return root


def _installed(root: Path, **overrides: Any) -> dict[str, Any]:
    # Claude plugin list --jsonの既存標本（plugin_roots_test.py）と同じ公開項目を使う。
    return {"id": "agent-toolkit@mine", "enabled": True, "scope": "user", "installPath": str(root), **overrides}


@pytest.fixture(name="inventory")
def _inventory(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    state = SimpleNamespace(items=[], returncode=0, stdout=None, commands=[])

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        state.commands.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, state.returncode, json.dumps(state.items) if state.stdout is None else state.stdout, ""
        )

    monkeypatch.setattr(claude_plugins.process_tree, "run_with_timeout", run)
    return state


def _options(tmp_path: Path, root: Path) -> dict[str, Any]:
    return {
        "cwd": str(tmp_path),
        "cli_path": "selected-claude",
        "plugins": [{"type": "local", "path": str(root)}],
        "setting_sources": ["user"],
        "settings": '{"env":{}}',
    }


@pytest.mark.asyncio
async def test_local_root_and_cli_settings_are_preserved(tmp_path: Path, inventory: SimpleNamespace) -> None:
    """明示した開発rootとCLIの設定読込条件を維持し、ローカル変更を通知する。"""
    root = _plugin(tmp_path / "checkout")
    inventory.items = [_installed(root)]
    options = _options(tmp_path, root)
    async with claude_plugins.ClaudePluginReloader(options, asyncio.get_running_loop()) as reloader:
        assert options["plugins"][0]["path"] == str(root)
        command, kwargs = inventory.commands[0]
        assert command == [
            "selected-claude",
            "--setting-sources",
            "user",
            "--settings",
            '{"env":{}}',
            "plugin",
            "list",
            "--json",
        ]
        assert kwargs["cwd"] == str(tmp_path)
        (root / "skills/content.md").write_text("更新内容", encoding="utf-8")
        await asyncio.wait_for(reloader.wait_for_change(), timeout=5)
        assert reloader.dirty


@pytest.mark.asyncio
async def test_versioned_input_is_separate_from_server_copy(tmp_path: Path, inventory: SimpleNamespace) -> None:
    """保持markerから現在版を選び、serverの保持内容を変えずに子用入力を用意する。"""
    original = tmp_path / "cache/mine/agent-toolkit/1.0.0"
    held = _plugin(tmp_path / "held/agent-toolkit")
    (held.parent / ".agent-toolkit-plugin-root.json").write_text(
        json.dumps({"source": str(original), "version": "1.0.0", "root_name": held.name}), encoding="utf-8"
    )
    current = _plugin(tmp_path / "cache/mine/agent-toolkit/2.0.0", "2.0.0")
    (current / "skills/content.md").write_text("現行版", encoding="utf-8")
    (current / ".venv").mkdir()
    (current / ".venv/excluded").touch()
    inventory.items = [_installed(current)]
    options = _options(tmp_path, held)
    async with claude_plugins.ClaudePluginReloader(options, asyncio.get_running_loop()) as reloader:
        mirror = Path(options["plugins"][0]["path"])
        area = mirror.parent
        assert (mirror / "skills/content.md").read_text(encoding="utf-8") == "現行版"
        assert (held / "skills/content.md").read_text(encoding="utf-8") == "初期内容"
        assert not (mirror / ".venv").exists()
        assert not mirror.is_symlink()
        assert not reloader.matches(mirror / "skills/content.md")
    assert not area.exists()


@pytest.mark.parametrize("failure", ["disabled", "other-project", "ambiguous", "bad-json", "nonzero"])
@pytest.mark.asyncio
async def test_invalid_inventory_never_enables_toolkit(tmp_path: Path, inventory: SimpleNamespace, failure: str) -> None:
    """無効・対象外・曖昧な一覧や取得失敗から子用toolkitを追加しない。"""
    root = _plugin(tmp_path / "cache/mine/agent-toolkit/1.0.0")
    inventory.items = [_installed(root)]
    if failure == "disabled":
        inventory.items[0]["enabled"] = False
    elif failure == "other-project":
        inventory.items[0].update(scope="project", projectPath=str(tmp_path / "elsewhere"))
    elif failure == "ambiguous":
        inventory.items.append(_installed(tmp_path / "other", id="agent-toolkit@other"))
    elif failure == "bad-json":
        inventory.stdout = "{"
    else:
        inventory.returncode = 1
    options = _options(tmp_path, root)
    options["setting_sources"] = ["user", "project"]
    with pytest.raises((claude_plugins.PluginReloadError, ValidationError)):
        async with claude_plugins.ClaudePluginReloader(options, asyncio.get_running_loop()):
            pytest.fail("不正な導入一覧が受理された")
    assert options["plugins"][0]["path"] == str(root)


@pytest.mark.asyncio
async def test_unloaded_scope_without_install_path_is_ignored(tmp_path: Path, inventory: SimpleNamespace) -> None:
    """CLIが読み込まないsynced項目の空installPathで、有効な導入一覧の解決を止めない。"""
    root = _plugin(tmp_path / "cache/mine/agent-toolkit/1.0.0")
    # Claude Codeは同名の手元導入に隠されたsynced項目をinstallPath空文字で返す。
    inventory.items = [_installed(root), _installed(root, id="agent-toolkit@synced", scope="synced", installPath="")]
    options = _options(tmp_path, root)
    async with claude_plugins.ClaudePluginReloader(options, asyncio.get_running_loop()) as reloader:
        assert reloader._source == root.resolve()


@pytest.mark.asyncio
async def test_copy_failure_preserves_previous_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inventory: SimpleNamespace
) -> None:
    """コピー途中の失敗では固定pathに以前の完全な内容を残し、SDK要求を送らない。"""
    root = _plugin(tmp_path / "cache/mine/agent-toolkit/1.0.0")
    inventory.items = [_installed(root)]
    options = _options(tmp_path, root)
    client = SimpleNamespace(_query=SimpleNamespace(_send_control_request=AsyncMock()))
    async with claude_plugins.ClaudePluginReloader(options, asyncio.get_running_loop()) as reloader:
        mirror = Path(options["plugins"][0]["path"])
        reloader.mark_changed()

        def copy_failure(_source: Path, target: Path, **_kwargs: Any) -> None:
            (target / "partial").touch()
            raise OSError("コピー失敗")

        monkeypatch.setattr(claude_plugins.shutil, "copytree", copy_failure)
        with pytest.raises(OSError, match="コピー失敗"):
            await reloader.reload(client)
        assert (mirror / "skills/content.md").read_text(encoding="utf-8") == "初期内容"
        assert not (mirror / "partial").exists()
        assert not list(mirror.parent.glob("generation-*"))
        assert reloader.dirty
        client._query._send_control_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_observer_threads_are_joined_on_cancel(tmp_path: Path, inventory: SimpleNamespace) -> None:
    """利用側の取消しでも所有observerとemitterを終了させる。"""
    root = _plugin(tmp_path / "checkout")
    inventory.items = [_installed(root)]
    ready = asyncio.Event()
    owner: list[claude_plugins.ClaudePluginReloader] = []

    async def run() -> None:
        async with claude_plugins.ClaudePluginReloader(_options(tmp_path, root), asyncio.get_running_loop()) as reloader:
            owner.append(reloader)
            ready.set()
            await reloader.wait_for_change()

    task = asyncio.create_task(run())
    await asyncio.wait_for(ready.wait(), timeout=5)
    observer = owner[0]._observer
    emitters = list(observer.emitters)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not observer.is_alive()
    assert all(not emitter.is_alive() for emitter in emitters)


def test_source_root_keeps_development_path(tmp_path: Path) -> None:
    """markerを持たないrootは導入済みcacheへ置換しない。"""
    assert plugin_root.plugin_source_root(tmp_path) == tmp_path


@pytest.mark.parametrize("new_version", ["1.0.0", "2.0.0"])
@pytest.mark.asyncio
async def test_reload_switches_complete_input_before_control_request(
    tmp_path: Path, inventory: SimpleNamespace, new_version: str
) -> None:
    """同版変更と版変更を固定pathへ先に反映し、SDK失敗時には前の完全な入力を復元する。"""
    root = _plugin(tmp_path / "cache/mine/agent-toolkit/1.0.0")
    inventory.items = [_installed(root)]
    options = _options(tmp_path, root)
    async with claude_plugins.ClaudePluginReloader(options, asyncio.get_running_loop()) as reloader:
        mirror = Path(options["plugins"][0]["path"])
        current = root if new_version == "1.0.0" else _plugin(root.parent / new_version, new_version)
        (current / "skills/content.md").write_text("更新後", encoding="utf-8")
        inventory.items = [_installed(current)]
        reloader.mark_changed()

        async def request(payload: dict[str, str], **_kwargs: Any) -> dict[str, Any]:
            assert payload == {"subtype": "reload_plugins"}
            assert Path(options["plugins"][0]["path"]) == mirror
            assert (mirror / "skills/content.md").read_text(encoding="utf-8") == "更新後"
            assert json.loads((mirror / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"] == new_version
            raise claude_plugins.PluginReloadError("SDK制御要求失敗")

        client = SimpleNamespace(_query=SimpleNamespace(_send_control_request=AsyncMock(side_effect=request)))
        with pytest.raises(claude_plugins.PluginReloadError, match="SDK制御要求失敗"):
            await reloader.reload(client)
        client._query._send_control_request.assert_awaited_once()
        assert (mirror / "skills/content.md").read_text(encoding="utf-8") == "初期内容"
        assert reloader.dirty


@pytest.mark.asyncio
async def test_settings_and_enabled_plugins_are_watched(tmp_path: Path, inventory: SimpleNamespace) -> None:
    """無効pluginと別projectを除外し、指定した設定と有効plugin変更を監視する。"""
    root = _plugin(tmp_path / "checkout")
    enabled = tmp_path / "enabled-plugin"
    enabled.mkdir()
    disabled = tmp_path / "disabled-plugin"
    disabled.mkdir()
    elsewhere = tmp_path / "other-project-plugin"
    elsewhere.mkdir()
    config = tmp_path / "config"
    config.mkdir()
    settings = tmp_path / "extra-settings.json"
    settings.write_text("{}", encoding="utf-8")
    inventory.items = [
        _installed(root),
        _installed(enabled, id="other@mine"),
        _installed(disabled, id="disabled@mine", enabled=False),
        _installed(elsewhere, id="other-project@mine", scope="project", projectPath=str(tmp_path / "other")),
    ]
    options = _options(tmp_path, root)
    options.update(settings=str(settings), setting_sources=["user", "project"], env={"CLAUDE_CONFIG_DIR": str(config)})
    async with claude_plugins.ClaudePluginReloader(options, asyncio.get_running_loop()) as reloader:
        assert reloader.matches(enabled / "skills/new.md")
        assert not reloader.matches(disabled / "skills/new.md")
        assert not reloader.matches(elsewhere / "skills/new.md")
        assert reloader.matches(settings)
        assert reloader.matches(config / "settings.json")
        assert reloader.matches(config / "plugins/installed_plugins.json")
        assert reloader.matches(tmp_path / ".claude/settings.json")
        assert not reloader.matches(tmp_path / ".claude/settings.local.json")
        assert not reloader.matches(root / ".git/index")
        settings.write_text('{"env":{"UPDATED":"1"}}', encoding="utf-8")
        await asyncio.wait_for(reloader.wait_for_change(), timeout=5)
        assert reloader.dirty


@pytest.mark.asyncio
async def test_changes_are_coalesced_until_reload(tmp_path: Path, inventory: SimpleNamespace) -> None:
    """連続通知は1回の待機で集約し、未反映状態はSDK成功まで保持する。"""
    root = _plugin(tmp_path / "checkout")
    inventory.items = [_installed(root)]
    async with claude_plugins.ClaudePluginReloader(_options(tmp_path, root), asyncio.get_running_loop()) as reloader:
        reloader.mark_changed()
        reloader.mark_changed()
        await asyncio.wait_for(reloader.wait_for_change(), timeout=5)
        assert reloader.dirty
        waiting = asyncio.create_task(reloader.wait_for_change())
        try:
            await asyncio.sleep(0)
            assert not waiting.done()
        finally:
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting


def _response(root: Path, version: str = "1.0.0") -> dict[str, Any]:
    # 2026-11-05にCLI 2.1.318 / SDK 0.2.177のinitialize→reload_pluginsで取得した応答を最小化。
    # builtinはversionを持たず、失敗件数の項目名はerror_countである。
    return {
        "plugins": [
            {"name": "agent-toolkit", "path": str(root), "source": "agent-toolkit@inline", "version": version},
            {"name": "cc-plugin-agents-md", "path": "builtin", "source": "cc-plugin-agents-md@builtin"},
        ],
        "error_count": 0,
    }


@pytest.mark.parametrize("new_version", ["1.0.0", "2.0.0"])
@pytest.mark.asyncio
async def test_successful_reload_confirms_updated_input(tmp_path: Path, inventory: SimpleNamespace, new_version: str) -> None:
    """同版の内容変更と導入先の版変更を固定dirへ反映し、成功時にdirtyを解除する。"""
    root = _plugin(tmp_path / "cache/mine/agent-toolkit/1.0.0")
    inventory.items = [_installed(root)]
    options = _options(tmp_path, root)
    async with claude_plugins.ClaudePluginReloader(options, asyncio.get_running_loop()) as reloader:
        mirror = Path(options["plugins"][0]["path"])
        current = root if new_version == "1.0.0" else _plugin(root.parent / new_version, new_version)
        (current / "skills/content.md").write_text("反映済み内容", encoding="utf-8")
        inventory.items = [_installed(current)]
        reloader.mark_changed()
        await asyncio.wait_for(reloader.wait_for_change(), timeout=5)

        async def request(payload: dict[str, str], **_kwargs: Any) -> dict[str, Any]:
            assert payload == {"subtype": "reload_plugins"}
            assert (mirror / "skills/content.md").read_text(encoding="utf-8") == "反映済み内容"
            return _response(mirror, new_version)

        client = SimpleNamespace(_query=SimpleNamespace(_send_control_request=AsyncMock(side_effect=request)))
        result = await reloader.reload(client)
        assert result["plugins"][0]["version"] == new_version
        assert options["plugins"][0]["path"] == str(mirror)
        assert not mirror.is_symlink()
        assert (mirror / "skills/content.md").read_text(encoding="utf-8") == "反映済み内容"
        assert not (mirror.parent / "previous").exists()
        assert not reloader.dirty
        client._query._send_control_request.assert_awaited_once()


@pytest.mark.parametrize("failure", ["error-count", "version", "missing", "unexpected", "shape"])
@pytest.mark.asyncio
async def test_invalid_reload_response_preserves_old_input(tmp_path: Path, inventory: SimpleNamespace, failure: str) -> None:
    """SDKの応答形式・plugin集合・版・エラー数が不正なら入力を復旧する。"""
    root = _plugin(tmp_path / "cache/mine/agent-toolkit/1.0.0")
    inventory.items = [_installed(root)]
    options = _options(tmp_path, root)
    async with claude_plugins.ClaudePluginReloader(options, asyncio.get_running_loop()) as reloader:
        mirror = Path(options["plugins"][0]["path"])
        (root / "skills/content.md").write_text("未確定内容", encoding="utf-8")
        reloader.mark_changed()
        response = _response(mirror)
        if failure == "error-count":
            response["error_count"] = 1
        elif failure == "version":
            response["plugins"][0]["version"] = "0.0.0"
        elif failure == "missing":
            response["plugins"] = response["plugins"][1:]
        elif failure == "unexpected":
            response["plugins"][0]["path"] = str(tmp_path / "unexpected")
        else:
            del response["error_count"]
        client = SimpleNamespace(_query=SimpleNamespace(_send_control_request=AsyncMock(return_value=response)))
        with pytest.raises(claude_plugins.PluginReloadError):
            await reloader.reload(client)
        assert (mirror / "skills/content.md").read_text(encoding="utf-8") == "初期内容"
        assert not (mirror.parent / "previous").exists()
        assert reloader.dirty


@pytest.mark.asyncio
async def test_change_during_reload_is_kept_pending(tmp_path: Path, inventory: SimpleNamespace) -> None:
    """制御要求の応答待ちに発生した更新通知を次のreloadまで保持する。"""
    root = _plugin(tmp_path / "checkout")
    inventory.items = [_installed(root)]
    async with claude_plugins.ClaudePluginReloader(_options(tmp_path, root), asyncio.get_running_loop()) as reloader:
        reloader.mark_changed()

        async def request(_payload: dict[str, str], **_kwargs: Any) -> dict[str, Any]:
            reloader.mark_changed()
            return _response(root)

        client = SimpleNamespace(_query=SimpleNamespace(_send_control_request=AsyncMock(side_effect=request)))
        await reloader.reload(client)
        assert reloader.dirty
        await asyncio.wait_for(reloader.wait_for_change(), timeout=5)


@pytest.mark.asyncio
async def test_updated_input_keeps_mcp_on_server_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inventory: SimpleNamespace
) -> None:
    """skill入力を新しい版へ交換しても、MCPの起動定義は所有serverの版へ固定する。"""
    runtime = _plugin(tmp_path / "held-runtime")
    original_server = {
        "command": "uv",
        "args": ["run", "${CLAUDE_PLUGIN_ROOT}/scripts/launch.py", "agent_toolkit/agents_server_mcp.py"],
    }
    (runtime / ".mcp.json").write_text(json.dumps({"mcpServers": {"agents_server": original_server}}), encoding="utf-8")
    monkeypatch.setattr(plugin_root, "SERVER_PLUGIN_ROOT", runtime)
    root = _plugin(tmp_path / "cache/mine/agent-toolkit/1.0.0")
    changed_configuration = {"mcpServers": {"agents_server": {"command": "changed"}, "other": {"command": "new-tool"}}}
    (root / ".mcp.json").write_text(json.dumps(changed_configuration), encoding="utf-8")
    inventory.items = [_installed(root)]
    options = _options(tmp_path, root)
    async with claude_plugins.ClaudePluginReloader(options, asyncio.get_running_loop()) as reloader:
        mirror = Path(options["plugins"][0]["path"])
        initial = json.loads((mirror / ".mcp.json").read_text(encoding="utf-8"))
        current = _plugin(root.parent / "2.0.0", "2.0.0")
        (current / ".mcp.json").write_text(json.dumps(changed_configuration), encoding="utf-8")
        inventory.items = [_installed(current)]
        reloader.mark_changed()
        client = SimpleNamespace(
            _query=SimpleNamespace(_send_control_request=AsyncMock(return_value=_response(mirror, "2.0.0")))
        )
        await reloader.reload(client)
        updated = json.loads((mirror / ".mcp.json").read_text(encoding="utf-8"))
        assert updated == initial
        assert updated["mcpServers"]["agents_server"]["args"][1] == str(runtime / "scripts/launch.py")
        assert updated["mcpServers"]["other"] == {"command": "new-tool"}


@pytest.mark.asyncio
async def test_unreclaimed_cli_keeps_input_but_stops_watch(tmp_path: Path, inventory: SimpleNamespace) -> None:
    """CLIの終了が未確認なら、監視を止めた後も読み込み中の入力を回収しない。"""
    root = _plugin(tmp_path / "cache/mine/agent-toolkit/1.0.0")
    inventory.items = [_installed(root)]
    options = _options(tmp_path, root)
    reloader = claude_plugins.ClaudePluginReloader(options, asyncio.get_running_loop())
    try:
        await reloader.prepare()
        mirror = Path(options["plugins"][0]["path"])
        await reloader.close(retain_input=True)
        assert mirror.is_dir()
        assert not reloader._observer.is_alive()
    finally:
        await reloader.close()
    assert not mirror.parent.exists()
