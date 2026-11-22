import asyncio
import json
import os
import pathlib
import subprocess

import pytest

from agent_toolkit._common import plugin_roots
from agent_toolkit._testing.codex_plugin_cli import codex_plugin_cli


@pytest.mark.parametrize(
    "plugin,field", [("agent-toolkit", "marketplaceName"), ("agent-basics", "marketplaceName"), ("agent-basics", "version")]
)
@pytest.mark.parametrize("value", ["missing", None, "", "../other", "C:", 1])
def test_codex_missing_required_field_has_an_actionable_host_diagnostic(tmp_path, monkeypatch, plugin, field, value):
    installed = [
        {"name": name, "marketplaceName": "mine", "version": "1.0", "installed": True, "enabled": True}
        for name in ("agent-toolkit", "agent-basics")
    ]
    item = next(item for item in installed if item["name"] == plugin)
    if value == "missing":
        item.pop(field)
    else:
        item[field] = value
    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", codex_plugin_cli(installed, marketplaces=["mine"]))
    with pytest.raises(plugin_roots.PluginRootError) as caught:
        plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "toolkit", "codex", tmp_path)
    assert "Codex" in str(caught.value) and field in str(caught.value)
    assert "codex plugin list --json" in str(caught.value)


@pytest.mark.parametrize("duplicated", [False, True])
def test_codex_inventory_queries_each_configured_marketplace_and_keeps_uniqueness(tmp_path, monkeypatch, duplicated):
    home = tmp_path / "codex"
    root = home / "plugins/cache/mine/agent-basics/1"
    (root / "skills").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    installed = [
        {"name": "agent-toolkit", "marketplaceName": "mine", "installed": True, "enabled": True, "version": "2"},
        {"name": "agent-basics", "marketplaceName": "mine", "installed": True, "enabled": True, "version": "1"},
    ]
    if duplicated:
        installed.append(
            {"name": "agent-toolkit", "marketplaceName": "other", "installed": True, "enabled": True, "version": "2"}
        )
    calls: list[list[str]] = []
    cli = codex_plugin_cli(installed, marketplaces=["mine", "other"], calls=calls)
    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", cli)
    if duplicated:
        with pytest.raises(plugin_roots.PluginRootError, match="一意でない"):
            plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "toolkit", "codex", tmp_path)
    else:
        assert plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "toolkit", "codex", tmp_path) == root
    assert calls == [
        ["codex", "plugin", "marketplace", "list", "--json"],
        ["codex", "plugin", "list", "--json", "--marketplace", "mine"],
        ["codex", "plugin", "list", "--json", "--marketplace", "other"],
    ]


def test_codex_does_not_require_unused_versions_from_other_candidates(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    root = home / "plugins/cache/mine/agent-basics/1"
    (root / "skills").mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    installed = [
        {"name": name, "marketplaceName": marketplace, "installed": True, "enabled": True}
        for name, marketplace in (("agent-toolkit", "mine"), ("agent-basics", "mine"), ("agent-basics", "other"))
    ]
    installed[1]["version"] = "1"
    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", codex_plugin_cli(installed))
    assert plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "toolkit", "codex", tmp_path) == root


@pytest.mark.parametrize("host", ["agy", "opencode"])
@pytest.mark.parametrize("failure", ["missing", "malformed", "ambiguous", "relative", "physical", "identity"])
def test_native_root_failure_retains_host_specific_recovery(tmp_path, monkeypatch, host, failure):
    """native登録の失敗を他ホストへ逃がさず、原因と修復対象を返す。"""
    home = tmp_path / "home"
    monkeypatch.setattr(pathlib.Path, "home", lambda: home)
    root = tmp_path / "native/agent-basics-agy"
    (root / "skills").mkdir(parents=True)
    manifest = root / ("plugin.json" if host == "agy" else ".claude-plugin/plugin.json")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"name": "other" if failure == "identity" else "agent-basics"}), encoding="utf-8")
    registered = str(root) if failure != "relative" else "relative/agent-basics-agy"
    if failure == "physical":
        manifest.unlink()
    if host == "opencode":
        config = tmp_path / "opencode"
        monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(config))
        config.mkdir()
        body = '<atk-auto source="dotfiles" kind="opencode-host">\n'
        body += f"agent-basics plugin root: `{registered}`。\n"
        if failure == "ambiguous":
            body += f"agent-basics plugin root: `{tmp_path / 'other'}`。\n"
        path = config / "agent-toolkit.md"
        path.write_text("invalid" if failure == "malformed" else body, encoding="utf-8")
        recovery = "OpenCode"
    else:
        path = home / ".gemini/config/plugins.json"
        path.parent.mkdir(parents=True)
        entry = {"path": str(pathlib.Path(registered).parent), "include_only": ["agent-basics-agy"]}
        entries = [entry]
        if failure == "ambiguous":
            entries.append({"path": str(tmp_path / "other"), "include_only": ["agent-basics-agy"]})
        path.write_text("invalid" if failure == "malformed" else json.dumps({"entries": entries}), encoding="utf-8")
        recovery = "plugins.json"
    if failure == "missing":
        path.unlink()
    with pytest.raises(plugin_roots.PluginRootError) as caught:
        plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "cache/toolkit/9", host)
    assert recovery in str(caught.value)
    assert "再実行" in str(caught.value)


def test_development_sibling_requires_manifest_and_skills(tmp_path):
    root = tmp_path / "agent-basics"
    (root / "skills").mkdir(parents=True)
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    assert plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "agent-toolkit") == root


def test_agy_disabled_registration_does_not_use_other_hosts(tmp_path, monkeypatch):
    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    config = tmp_path / ".gemini/config"
    config.mkdir(parents=True)
    (config / "plugins.json").write_text(
        json.dumps({"entries": [{"path": str(tmp_path), "include_only": ["agent-basics-agy"]}]}), encoding="utf-8"
    )
    (config / "config.json").write_text('{"plugins":{"agent-basics-agy":{"enabled":false}}}', encoding="utf-8")
    with pytest.raises(plugin_roots.PluginRootError, match="無効.*config.json"):
        plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "cache/toolkit/9", "agy")


def test_claude_root_uses_same_marketplace(tmp_path, monkeypatch):
    root = tmp_path / "installed-basics"
    (root / "skills").mkdir(parents=True)
    data = [
        {"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(tmp_path / "toolkit")},
        {"id": "agent-basics@mine", "scope": "user", "enabled": True, "installPath": str(root)},
        {"id": "agent-basics@other", "scope": "user", "enabled": True, "installPath": str(tmp_path / "unrelated")},
    ]
    monkeypatch.setattr(
        plugin_roots.process_tree,
        "run_with_timeout",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(data), ""),
    )
    assert plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "toolkit") == root


def test_claude_directory_marketplace_uses_folder_actually_read(tmp_path, monkeypatch):
    # sourceがdirectoryのmarketplaceでは、Claude Code 2.1.326のplugin list --jsonが
    # 実際の読込元を`readFromFolder`で示し、project scopeの古い登録のinstallPathは読まれない（2026-11-17の観測）。
    project = tmp_path / "project"
    project.mkdir()
    folder = tmp_path / "marketplace/agent-basics"
    (folder / "skills").mkdir(parents=True)
    stale = tmp_path / "cache/agent-basics/1.3.0"
    (stale / "skills").mkdir(parents=True)
    current = tmp_path / "cache/agent-basics/1.28.2"
    (current / "skills").mkdir(parents=True)
    toolkit = tmp_path / "shared-copy/agent-toolkit"
    data = [
        {"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(tmp_path / "cache/toolkit")},
        {
            "id": "agent-basics@mine",
            "scope": "user",
            "enabled": True,
            "installPath": str(current),
            "readFromFolder": str(folder),
        },
        {
            "id": "agent-basics@mine",
            "scope": "project",
            "enabled": True,
            "installPath": str(stale),
            "projectPath": str(project),
            "readFromFolder": str(folder),
        },
    ]
    monkeypatch.setattr(
        plugin_roots.process_tree,
        "run_with_timeout",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(data), ""),
    )
    assert plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", project) == folder

    for item in data:
        item.pop("readFromFolder", None)
    assert plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", project) == stale


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

    cli = codex_plugin_cli(installed)

    def run(args, **kwargs):
        assert kwargs == {
            "capture_output": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 15,
            "check": False,
            "cwd": str(pathlib.Path.cwd()),
            "env": dict(os.environ),
        }
        return cli(args, **kwargs)

    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", run)
    if enabled:
        assert plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "toolkit", "codex") == root
    else:
        with pytest.raises(ValueError, match="一意"):
            plugin_roots.resolve_plugin_root("agent-basics", tmp_path / "toolkit", "codex")


@pytest.mark.parametrize("scope", ["user", "project", "local"])
def test_claude_effective_scope_and_project_do_not_share_cached_roots(tmp_path, monkeypatch, scope):
    # Claude Code plugin list --jsonの公開項目（2026-10-26の保存観測）を使う。
    project = tmp_path / "project"
    project.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    roots = {name: tmp_path / name for name in ("user", "project", "local", "unrelated")}
    for root in roots.values():
        (root / "skills").mkdir(parents=True)
    toolkit = tmp_path / "toolkit"
    data = [{"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(toolkit)}]
    for candidate in ("user", "project", "local"):
        if candidate == "local" and scope != "local" or candidate == "project" and scope == "user":
            continue
        data.append(
            {
                "id": "agent-basics@mine",
                "scope": candidate,
                "enabled": True,
                "installPath": str(roots[candidate]),
                "projectPath": str(project),
            }
        )
    data.append(
        {
            "id": "agent-basics@mine",
            "scope": "local",
            "enabled": True,
            "installPath": str(roots["unrelated"]),
            "projectPath": str(other),
            "lastUpdated": "2099-01-01T00:00:00Z",
        }
    )
    calls: list[str] = []

    def run(args, **kwargs):
        calls.append(kwargs["cwd"])
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", run)
    with plugin_roots.plugin_resolution_scope():
        assert plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", project) == roots[scope]
        assert plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", other) == roots["unrelated"]
        assert plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", project) == roots[scope]
    assert plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", project) == roots[scope]
    assert calls == [str(project), str(other), str(project)]


@pytest.mark.parametrize(
    "failure, message",
    [
        ("absent", "適用される導入情報が無い"),
        ("disabled", "無効"),
        ("ambiguous", "一意でない"),
        ("missing", "実体またはskills"),
        ("failed", "取得失敗"),
        ("json", "JSONが不正"),
        ("timeout", "取得失敗"),
    ],
)
def test_claude_failures_keep_the_cause(tmp_path, monkeypatch, failure, message):
    toolkit = tmp_path / "toolkit"
    root = tmp_path / "basics"
    root.mkdir()
    if failure != "missing":
        (root / "skills").mkdir()
    item = {"id": "agent-basics@mine", "scope": "user", "enabled": failure != "disabled", "installPath": str(root)}
    data = [{"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(toolkit)}]
    if failure != "absent":
        data.append(item)
    if failure == "ambiguous":
        data.append(dict(item))

    def run(args, **_kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 15)
        return subprocess.CompletedProcess(
            args, 1 if failure == "failed" else 0, "invalid" if failure == "json" else json.dumps(data), ""
        )

    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", run)
    with pytest.raises(plugin_roots.PluginRootError, match=message):
        plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", tmp_path)


@pytest.mark.asyncio
async def test_resolution_scope_keeps_concurrent_requests_separate_and_rechecks_physical_roots(tmp_path, monkeypatch):
    toolkit = tmp_path / "toolkit"
    roots = [tmp_path / f"generation-{index}" for index in range(3)]
    for root in roots:
        (root / "skills").mkdir(parents=True)
    calls: list[pathlib.Path] = []

    def run(args, **_kwargs):
        root = roots[len(calls)]
        calls.append(root)
        data = [
            {"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(toolkit)},
            {"id": "agent-basics@mine", "scope": "user", "enabled": True, "installPath": str(root)},
        ]
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", run)
    ready = asyncio.Event()

    async def request():
        with plugin_roots.plugin_resolution_scope():
            first = plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", tmp_path)
            if len(calls) == 2:
                ready.set()
            await ready.wait()
            assert plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", tmp_path) == first
            return first

    assert list(await asyncio.gather(request(), request())) == roots[:2]
    with plugin_roots.plugin_resolution_scope():
        assert plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", tmp_path) == roots[2]
        (roots[2] / "skills").rmdir()
        with pytest.raises(plugin_roots.PluginRootError, match="実体またはskills"):
            plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", tmp_path)
    assert calls == roots
