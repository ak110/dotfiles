"""分離した品質プラグインの導入元と曖昧さを確かめる。"""

import asyncio
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

from agent_toolkit._common import plugin_roots
from agent_toolkit._common.plugin_roots import _managed_settings_directories, _plugin_inventory
from agent_toolkit._testing.codex_plugin_cli import codex_plugin_cli


@pytest.mark.parametrize(
    "plugin,field", [("agent-toolkit", "marketplaceName"), ("agent-basics", "marketplaceName"), ("agent-basics", "version")]
)
@pytest.mark.parametrize("value", ["missing", None, "", "../other", "C:", 1])
def test_codex_missing_required_field_has_an_actionable_host_diagnostic(tmp_path, monkeypatch, plugin, field, value):
    """公開一覧の必須field欠落を、利用側が受理するhost診断へ変換する。"""
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
    """リモートカタログへ接続する無指定の一覧を使わず、設定済みmarketplaceの全件から導入元の一意性を判定する。"""
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


@pytest.mark.parametrize(
    "platform,wsl,expected",
    [
        ("linux", False, ["/etc/claude-code"]),
        ("darwin", False, ["/Library/Application Support/ClaudeCode"]),
        ("win32", False, ["C:/Program Files/ClaudeCode"]),
        ("linux", True, ["/etc/claude-code", "/mnt/c/Program Files/ClaudeCode"]),
    ],
)
def test_inventory_managed_file_sources_cover_supported_operating_systems(monkeypatch, platform, wsl, expected):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    if wsl:
        monkeypatch.setenv("WSL_DISTRO_NAME", "fixture")
    assert _managed_settings_directories() == [pathlib.Path(path) for path in expected]


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32", "wsl"])
@pytest.mark.parametrize(
    "changed",
    [
        "settings.json",
        "plugins/installed_plugins.json",
        "plugins/known_marketplaces.json",
        "project",
        "local",
        "main-local",
        "managed",
        "drop-in",
        "environment",
        "executable",
    ],
)
def test_hook_inventory_reuses_only_unchanged_file_inputs(tmp_path, monkeypatch, platform, changed):
    """全OSで設定変更・追加・削除を失効させ、CLI失敗後も古い一覧へ戻らない。"""
    home = tmp_path / "config"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    managed = tmp_path / "managed"
    executable = tmp_path / "claude"
    executable.write_text("host", encoding="utf-8")
    monkeypatch.setattr(sys, "platform", "linux" if platform == "wsl" else platform)
    if platform == "wsl":
        monkeypatch.setenv("WSL_DISTRO_NAME", "fixture")
    gitdir = tmp_path / "main/.git/worktrees/tree"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..", encoding="utf-8")
    (project / ".git").write_text(f"gitdir: {gitdir}", encoding="utf-8")
    monkeypatch.setattr(plugin_roots.host_homes, "claude_config_dir", lambda: home)
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kw: str(executable))
    monkeypatch.setattr(plugin_roots, "_managed_settings_directories", lambda: [managed])
    state: dict = {}
    monkeypatch.setattr(plugin_roots, "read_state", lambda _session: state.copy())
    monkeypatch.setattr(plugin_roots, "update_state", lambda _session, mutate: state.update(mutate(state)))
    calls = []

    def claude_inventory(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps([{"generation": len(calls)}]), "")

    monkeypatch.setattr(
        plugin_roots.process_tree, "run_with_timeout", codex_plugin_cli([], calls=calls, other=claude_inventory)
    )

    def request():
        with plugin_roots.plugin_resolution_scope(session_id="hook"):
            first = _plugin_inventory("claude", project)
            assert _plugin_inventory("claude", project) == first
            return first

    assert request() == request() == [{"generation": 1}]
    paths = {
        "project": project / ".claude/settings.json",
        "local": project / ".claude/settings.local.json",
        "main-local": tmp_path / "main/.claude/settings.local.json",
        "managed": managed / "managed-settings.json",
        "drop-in": managed / "managed-settings.d/one.json",
        "executable": executable,
    }
    path = paths.get(changed, home / changed)
    if changed == "environment":
        monkeypatch.setenv("CLAUDE_CONFIG_TEST", "changed")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"changed":true}', encoding="utf-8")
    assert request() == request() == [{"generation": 2}]
    if changed not in {"environment", "executable"}:
        path.unlink()
        assert request() == [{"generation": 3}]
    with plugin_roots.plugin_resolution_scope(session_id="hook"):
        _plugin_inventory("claude", tmp_path / "another-project")
    assert calls[-1][0] == "claude"
    count = len(calls)
    with plugin_roots.plugin_resolution_scope(session_id="hook"):
        _plugin_inventory("codex", project)
    assert calls[count:] == [
        ["codex", "plugin", "marketplace", "list", "--json"],
        ["codex", "plugin", "list", "--json", "--marketplace", "mine"],
    ]


@pytest.mark.parametrize("failure", ["failed", "json", "timeout", "changed-during-fetch", "corrupt-state"])
def test_hook_inventory_retries_failed_or_unstable_fetches(tmp_path, monkeypatch, failure):
    executable = tmp_path / "claude"
    executable.touch()
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kw: str(executable))
    state: dict = {"plugin_inventory": []} if failure == "corrupt-state" else {}
    monkeypatch.setattr(plugin_roots, "read_state", lambda _session: state.copy())
    monkeypatch.setattr(plugin_roots, "update_state", lambda _session, mutate: state.update(mutate(state)))
    calls = []

    def acquire(args, **_kwargs):
        calls.append(args)
        if len(calls) == 1:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(args, 15)
            if failure == "changed-during-fetch":
                monkeypatch.setenv("CLAUDE_CONFIG_TEST", "changed")
            return subprocess.CompletedProcess(args, int(failure == "failed"), "bad" if failure == "json" else "[]", "")
        return subprocess.CompletedProcess(args, 0, "[]", "")

    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", acquire)
    if failure in {"failed", "json", "timeout"}:
        with pytest.raises(plugin_roots.PluginRootError), plugin_roots.plugin_resolution_scope(session_id="hook"):
            _plugin_inventory("claude", tmp_path)
    else:
        with plugin_roots.plugin_resolution_scope(session_id="hook"):
            assert _plugin_inventory("claude", tmp_path) == []
    with plugin_roots.plugin_resolution_scope(session_id="hook"):
        assert _plugin_inventory("claude", tmp_path) == []
    assert len(calls) == (1 if failure == "corrupt-state" else 2)


@pytest.mark.parametrize("change", ["scope", "disabled", "ambiguous", "marketplace", "install-path", "config-dir", "session"])
def test_hook_cached_inventory_reselects_after_installation_changes(tmp_path, monkeypatch, change):
    """保持rootを維持し、導入変更と新sessionでは公開一覧から選び直す。"""
    toolkit = tmp_path / "cache/mine/agent-toolkit/9"
    roots = [tmp_path / "basics-1", tmp_path / "basics-2"]
    for root in roots:
        (root / "skills").mkdir(parents=True)
    executable = tmp_path / "claude"
    executable.touch()
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kw: str(executable))
    home = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    registry = home / "plugins/installed_plugins.json"
    registry.parent.mkdir(parents=True)
    data = [
        {"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(toolkit)},
        {"id": "agent-basics@mine", "scope": "user", "enabled": True, "installPath": str(roots[0])},
    ]
    registry.write_text(json.dumps(data), encoding="utf-8")
    calls = []

    def acquire(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, registry.read_text(encoding="utf-8"), "")

    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", acquire)
    # 継承された状態を模擬するため、sessionを変えても同じ保存値を返す。
    state: dict = {}
    monkeypatch.setattr(plugin_roots, "read_state", lambda _session: state.copy())
    monkeypatch.setattr(plugin_roots, "update_state", lambda _session, mutate: state.update(mutate(state)))
    for _ in range(2):
        with plugin_roots.plugin_resolution_scope(session_id="original"):
            assert plugin_roots.resolve_plugin_root("agent-basics", toolkit, project=tmp_path) == roots[0]
    assert len(calls) == 1
    expected = roots[0]
    if change in {"scope", "install-path", "config-dir"}:
        data[1]["installPath"] = str(roots[1])
        expected = roots[1]
    if change == "scope":
        data[1].update(scope="local", projectPath=str(tmp_path))
    elif change == "disabled":
        data[1]["enabled"] = False
    elif change == "ambiguous":
        data.append(data[1].copy())
    elif change == "marketplace":
        data[1]["id"] = "agent-basics@other"
    elif change == "config-dir":
        registry = tmp_path / "other-config/plugins/installed_plugins.json"
        registry.parent.mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(registry.parent.parent))
    if change != "session":
        registry.write_text(json.dumps(data), encoding="utf-8")
    with plugin_roots.plugin_resolution_scope(session_id="new" if change == "session" else "original"):
        if change in {"disabled", "ambiguous", "marketplace"}:
            with pytest.raises(plugin_roots.PluginRootError):
                plugin_roots.resolve_plugin_root("agent-basics", toolkit, project=tmp_path)
        else:
            assert plugin_roots.resolve_plugin_root("agent-basics", toolkit, project=tmp_path) == expected
    assert len(calls) == 2


def test_hook_cached_inventory_rechecks_root_and_held_marketplace(tmp_path, monkeypatch):
    toolkit = tmp_path / "cache/mine/agent-toolkit/9"
    basics = tmp_path / "cache/mine/agent-basics/1"
    (basics / "skills").mkdir(parents=True)
    executable = tmp_path / "claude"
    executable.touch()
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kw: str(executable))
    data = [
        {"id": "agent-toolkit@mine", "scope": "user", "enabled": False, "installPath": str(toolkit)},
        {"id": "agent-toolkit@other", "scope": "user", "enabled": True, "installPath": str(tmp_path / "other")},
        {"id": "agent-basics@mine", "scope": "user", "enabled": True, "installPath": str(basics)},
    ]
    calls = []

    def acquire(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(data), "")

    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", acquire)
    for _ in range(2):
        with plugin_roots.plugin_resolution_scope(session_id="held-root"):
            assert plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", tmp_path) == basics
    assert len(calls) == 1
    (basics / "skills").rmdir()
    with (
        plugin_roots.plugin_resolution_scope(session_id="held-root"),
        pytest.raises(plugin_roots.PluginRootError, match="実体またはskills"),
    ):
        plugin_roots.resolve_plugin_root("agent-basics", toolkit, "claude", tmp_path)
    assert len(calls) == 1


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
    """正式生成rootの登録があっても、agyの無効設定を維持する。"""
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
