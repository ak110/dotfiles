"""install_codex_pluginsのテスト。"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from pytools._internal import claude_common, install_codex_plugins


@pytest.fixture(name="plugin_env")
def plugin_env_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Codexの設定とdotfilesを一時ディレクトリへ分離する。"""
    root = tmp_path / "dotfiles"
    (root / ".agents/plugins").mkdir(parents=True)
    (root / "agent-toolkit/.codex-plugin").mkdir(parents=True)
    (root / ".agents/plugins/marketplace.json").write_text(
        json.dumps({"name": "ak110-dotfiles", "plugins": [{"name": "agent-toolkit"}]})
    )
    (root / "agent-toolkit/.codex-plugin/plugin.json").write_text(json.dumps({"version": "1.2.3"}))
    monkeypatch.setattr(claude_common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(install_codex_plugins.shutil, "which", lambda _: "/bin/codex")
    monkeypatch.setattr(install_codex_plugins, "CODEX_HOME", tmp_path / ".codex")
    return root


def _legacy_link(plugin_env: Path, name: str = "coding") -> Path:
    source = plugin_env / f"agent-toolkit/skills/{name}"
    source.mkdir(parents=True)
    destination = install_codex_plugins.CODEX_HOME / f"skills/{name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(source, target_is_directory=True)
    return destination


def _installed_state() -> dict[str, object]:
    return {
        "installed": [
            {
                "pluginId": "agent-toolkit@ak110-dotfiles",
                "version": "1.2.3",
                "enabled": True,
            }
        ]
    }


def test_registers_installs_and_removes_expected_legacy_link(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = _legacy_link(plugin_env)
    calls: list[list[str]] = []
    responses: Iterator[dict[str, object] | None] = iter(
        [
            {"marketplaces": []},
            {"installed": []},
            {"installed": [{"pluginId": "agent-toolkit@ak110-dotfiles", "version": "1.2.3", "enabled": True}]},
        ]
    )
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda args: next(responses))

    def command(args: list[str]) -> bool:
        calls.append(args)
        return True

    monkeypatch.setattr(install_codex_plugins, "_command", command)
    assert install_codex_plugins.run() is True
    assert ["plugin", "marketplace", "add", str(plugin_env)] in calls
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls
    assert not destination.exists()


def test_rejects_mismatched_marketplace_root(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        install_codex_plugins,
        "_codex_json",
        lambda _: {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env / "other")}]},
    )
    assert install_codex_plugins.run() is False


def test_noop_state_still_resyncs_plugin(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _installed_state()
    responses = iter([{"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]}, state, state])
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", lambda _: True)
    assert install_codex_plugins.run() is False


def test_plugin_add_failure_keeps_legacy_link(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = _legacy_link(plugin_env)
    responses: Iterator[dict[str, object] | None] = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            {"installed": []},
        ]
    )
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", lambda _: False)
    assert install_codex_plugins.run() is False
    assert destination.is_symlink()


def test_post_install_json_failure_keeps_legacy_link(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = _legacy_link(plugin_env)
    responses: Iterator[dict[str, object] | None] = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            {"installed": []},
            None,
        ]
    )
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", lambda _: True)
    assert install_codex_plugins.run() is False
    assert destination.is_symlink()


def test_migration_keeps_unrelated_link_and_regular_directory(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = _legacy_link(plugin_env)
    unrelated_source = plugin_env / "unrelated"
    unrelated_source.mkdir()
    unrelated = install_codex_plugins.CODEX_HOME / "skills/unrelated"
    unrelated.symlink_to(unrelated_source, target_is_directory=True)
    regular = install_codex_plugins.CODEX_HOME / "skills/regular"
    regular.mkdir()
    state = _installed_state()
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            state,
            state,
        ]
    )
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", lambda _: True)
    assert install_codex_plugins.run() is True
    assert not expected.exists()
    assert unrelated.is_symlink()
    assert regular.is_dir()


def test_windows_junction_detection_and_removal_use_rmdir() -> None:
    """Windows junction相当では`is_junction`判定後に`rmdir`を使う。"""

    class JunctionPath:
        def __init__(self) -> None:
            self.removed = False

        def is_symlink(self) -> bool:
            return False

        def is_junction(self) -> bool:
            return True

        def rmdir(self) -> None:
            self.removed = True

    junction = JunctionPath()
    path = cast("Path", junction)
    assert install_codex_plugins._is_link(path) is True  # pylint: disable=protected-access
    install_codex_plugins._unlink(path)  # pylint: disable=protected-access
    assert junction.removed is True
