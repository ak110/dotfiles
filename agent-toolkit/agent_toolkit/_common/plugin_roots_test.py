"""分離した品質プラグインの導入元と曖昧さを確かめる。"""

import json
import subprocess

import pytest

from agent_toolkit._common import plugin_roots


def test_development_sibling_requires_manifest_and_skills(tmp_path):
    root = tmp_path / "agent-basics"
    (root / "skills").mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    assert plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "agent-toolkit") == root


def test_claude_root_uses_same_marketplace(tmp_path, monkeypatch):
    config = tmp_path / "config"
    (config / "plugins").mkdir(parents=True)
    root = tmp_path / "installed-basics"
    (root / "skills").mkdir(parents=True)
    data = {
        "plugins": {
            "agent-toolkit@mine": [{"installPath": str(tmp_path / "toolkit")}],
            "agent-basics@mine": [{"installPath": str(root)}],
            "agent-basics@other": [{"installPath": str(tmp_path / "unrelated")}],
        }
    }
    (config / "plugins/installed_plugins.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(plugin_roots.host_homes, "claude_config_dir", lambda: config)
    assert plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "toolkit") == root


@pytest.mark.parametrize("enabled", [True, False])
def test_codex_root_uses_enabled_same_marketplace_version(tmp_path, monkeypatch, enabled):
    home = tmp_path / "codex"
    root = home / "plugins/cache/mine/agent-basics/1.0.0"
    (root / "skills").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    installed = [
        {"name": "agent-toolkit", "marketplaceName": "mine", "installed": True, "enabled": True, "version": "2.0.0"},
        {"name": "agent-basics", "marketplaceName": "mine", "installed": True, "enabled": enabled, "version": "1.0.0"},
    ]

    def run(*args, **_kwargs):
        assert args == (["codex", "plugin", "list", "--json"],)
        assert _kwargs == {"capture_output": True, "encoding": "utf-8", "errors": "replace", "timeout": 15, "check": False}
        return subprocess.CompletedProcess(args[0], 0, json.dumps({"installed": installed}), "")

    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", run)
    if enabled:
        assert plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "toolkit", "codex") == root
    else:
        with pytest.raises(ValueError, match="一意"):
            plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "toolkit", "codex")
