# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""install_codex_pluginsの基本動作の試験。"""

# pylint: disable=protected-access

import dataclasses
import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pytools import post_apply
from pytools._internal import (
    codex_processes,
    common,
    host_roles,
    install_codex_plugins,
    plugin_warmup,
    post_apply_outcome,
)
from pytools._internal.install_codex_plugins_test_helpers import (
    _REAL_CODEX_PROCESSES,
    _SESSION,
    _git_marketplace_config,
    _hook_entry,
    _installed_state,
    _legacy_link,
    _local_marketplace,
    _recording_success,
    _set_json_responses,
)
from pytools._internal.install_codex_plugins_test_helpers import _empty_unused_plugins as _empty_unused_plugins
from pytools._internal.install_codex_plugins_test_helpers import plugin_env_fixture as plugin_env_fixture

from ._test_helpers import ResidentProcesses, _FakeResult


def test_registers_and_installs_with_official_cli(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未導入pluginは公式CLIへ導入を委譲し、legacy linkを除去する。"""
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: True)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: (_SESSION,))
    destination = _legacy_link(plugin_env)
    cache_entry = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2"
    cache_entry.mkdir(parents=True)
    (cache_entry / "marker").write_text("keep", encoding="utf-8")
    calls: list[list[str]] = []
    _set_json_responses(
        monkeypatch,
        [
            {"marketplaces": []},
            {"installed": []},
            _installed_state(),
        ],
    )

    def command(args: list[str]) -> bool:
        calls.append(args)
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(cache_entry)
        return True

    monkeypatch.setattr(install_codex_plugins, "_command", command)

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert [notice.command for notice in outcome.notices] == ["/hooks"]
    assert ["plugin", "marketplace", "add", str(plugin_env)] in calls
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls
    assert not destination.exists()
    assert (cache_entry / "marker").read_text(encoding="utf-8") == "keep"


def test_first_hook_transition_preserves_previous_cache(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """初回切替ではhook起動コマンドを先に配置し、CLIが除去した旧hook実体を復元する。"""
    wrapper = install_codex_plugins._hook_bin() / "atk-hook"  # pylint: disable=protected-access
    wrapper.unlink()
    previous = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2"
    previous.mkdir(parents=True)
    (previous / "hook.py").write_text("previous", encoding="utf-8")
    _set_json_responses(
        monkeypatch,
        [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()],
    )

    def command(args: list[str]) -> bool:
        if args[:2] == ["plugin", "add"]:
            assert wrapper.read_text(encoding="utf-8") == "hook wrapper"
            (previous / "hook.py").unlink()
            previous.rmdir()
        return True

    monkeypatch.setattr(install_codex_plugins, "_command", command)

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert (previous / "hook.py").read_text(encoding="utf-8") == "previous"


@pytest.mark.parametrize(
    ("old_version", "first_transition"),
    [
        ("1.2.2", False),
        ("1.2.2", True),
    ],
)
def test_running_codex_updates_and_preserves_previous_caches(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    old_version: str,
    first_transition: bool,
) -> None:
    """Codexの稼働中も更新し、CLIが削除した全旧版を同じ絶対パスへ戻す。"""
    cache = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit"
    older = cache / "1.2.1"
    (older / "skills/older").mkdir(parents=True)
    (older / "skills/older/SKILL.md").write_text("older skill", encoding="utf-8")
    previous = cache / old_version
    (previous / "skills/coding").mkdir(parents=True)
    (previous / "skills/coding/SKILL.md").write_text("old skill", encoding="utf-8")
    (previous / "agent_toolkit").mkdir(exist_ok=True)
    (previous / "agent_toolkit/agents_server_mcp.py").write_text("old mcp", encoding="utf-8")
    if first_transition:
        (install_codex_plugins._hook_bin() / "atk-hook").unlink()  # pylint: disable=protected-access
    current: dict[str, Any] = {"version": old_version, "enabled": True}
    calls: list[list[str]] = []
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: (_SESSION,))

    def codex_json(args: list[str]) -> dict[str, Any]:
        return _local_marketplace(plugin_env) if args[1] == "marketplace" else _installed_state(**current)

    def command(args: list[str]) -> bool:
        calls.append(args)
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(cache)
            latest = cache / "1.2.3"
            (latest / "agent_toolkit").mkdir(parents=True, exist_ok=True)
            (latest / "agent_toolkit/hook.py").write_text("new hook", encoding="utf-8")
            (latest / "agent_toolkit/agents_server_mcp.py").write_text("new mcp", encoding="utf-8")
            (latest / "skills/coding").mkdir(parents=True, exist_ok=True)
            (latest / "skills/coding/SKILL.md").write_text("new skill", encoding="utf-8")
            current.update(version="1.2.3", enabled=True)
        return True

    def run_subprocess(args: list[str], **_kwargs: object) -> _FakeResult:
        if args[1:] == ["app-server", "daemon", "restart"]:
            return _FakeResult(stdout="")
        assert args[1:] == ["plugin", "list", "--json", "--marketplace", "ak110-dotfiles"]
        if current["version"] == "1.2.3":
            shutil.rmtree(older, ignore_errors=True)
            if old_version != "1.2.3":
                shutil.rmtree(previous, ignore_errors=True)
        return _FakeResult(stdout=json.dumps(_installed_state(**current)))

    monkeypatch.setattr(install_codex_plugins, "_codex_json", codex_json)
    monkeypatch.setattr(install_codex_plugins, "_command", command)
    monkeypatch.setattr(common, "run_subprocess", run_subprocess)

    outcome = install_codex_plugins.run()
    assert outcome.changed is True
    assert outcome.deferred is False
    assert calls.count(["plugin", "add", "agent-toolkit@ak110-dotfiles"]) == 1
    assert current == {"version": "1.2.3", "enabled": True}
    assert (older / "skills/older/SKILL.md").read_text(encoding="utf-8") == "older skill"
    if old_version != "1.2.3":
        assert (previous / "skills/coding/SKILL.md").read_text(encoding="utf-8") == "old skill"
        assert (previous / "agent_toolkit/agents_server_mcp.py").read_text(encoding="utf-8") == "old mcp"
    latest = cache / "1.2.3"
    assert (latest / "skills/coding/SKILL.md").read_text(encoding="utf-8") == "new skill"
    assert (latest / "agent_toolkit/agents_server_mcp.py").read_text(encoding="utf-8") == "new mcp"
    assert (
        plugin_warmup.codex_plugin_script(
            plugin_id="agent-toolkit@ak110-dotfiles",
            plugin_name="agent-toolkit",
            relative_path=Path("agent_toolkit/agents_server_mcp.py"),
            tag="test",
        )
        == latest / "agent_toolkit/agents_server_mcp.py"
    )


@pytest.mark.skipif(sys.platform != "linux", reason="実行名をsymlinkで与える常駐プロセスはLinuxで起動する")
def test_running_codex_session_updates_without_removing_old_cache(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """実在するCodexセッションがあっても更新し、CLIが削除した旧cacheを復旧する。"""
    monkeypatch.setattr(codex_processes, "codex_processes", _REAL_CODEX_PROCESSES)
    monkeypatch.setattr(post_apply.sync_report, "REPORT_PATH", tmp_path / "state" / "sync-report.json")
    monkeypatch.setattr(post_apply, "_UPDATE_LOG_PATH", tmp_path / "update-dotfiles.log")
    cache = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit"
    previous = cache / "1.2.2"
    (previous / "agent_toolkit").mkdir(parents=True)
    (previous / "agent_toolkit/hook.py").write_text("old hook", encoding="utf-8")
    (previous / "skills/coding").mkdir(parents=True)
    (previous / "skills/coding/SKILL.md").write_text("old skill", encoding="utf-8")
    current: dict[str, Any] = {"version": "1.2.2", "enabled": True}
    calls: list[list[str]] = []

    def codex_json(args: list[str]) -> dict[str, Any]:
        return _local_marketplace(plugin_env) if args[1] == "marketplace" else _installed_state(**current)

    def command(args: list[str]) -> bool:
        calls.append(args)
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(previous)
            (cache / "1.2.3/agent_toolkit").mkdir(parents=True, exist_ok=True)
            (cache / "1.2.3/agent_toolkit/hook.py").write_text("new hook", encoding="utf-8")
            current.update(version="1.2.3", enabled=True)
            return True
        return args != ["app-server", "daemon", "version"]

    monkeypatch.setattr(install_codex_plugins, "_codex_json", codex_json)
    monkeypatch.setattr(install_codex_plugins, "_command", command)
    steps: list[tuple[str, Callable[[], post_apply_outcome.PostApplyOutcome]]] = [
        ("Codex plugin のインストール", install_codex_plugins.run)
    ]

    with ResidentProcesses(tmp_path, monkeypatch) as residents:
        residents.start("codex")
        with pytest.raises(SystemExit) as updated_exit:
            post_apply.main(runner=lambda: post_apply.run(steps=steps))
        assert updated_exit.value.code == 0
        assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls
        assert (previous / "agent_toolkit/hook.py").read_text(encoding="utf-8") == "old hook"
        assert (previous / "skills/coding/SKILL.md").read_text(encoding="utf-8") == "old skill"
        assert current == {"version": "1.2.3", "enabled": True}
        assert (cache / "1.2.3/agent_toolkit/hook.py").read_text(encoding="utf-8") == "new hook"


def test_plugin_update_does_not_query_running_codex(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧cacheの復旧でセッションを守るため、更新可否の判定にプロセス一覧を使わない。"""
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: pytest.fail("更新時にプロセス一覧を参照しない"))
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2"), _installed_state()])
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls, daemon_running=False))

    outcome = install_codex_plugins.run()
    assert outcome.changed is True
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls
    assert outcome.deferred is False


@pytest.mark.parametrize("enabled", [True, False])
def test_post_apply_update_continues_with_new_warmup_version(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    """既定の工程順で有効版だけを更新してwarmupへ渡し、無効状態を保持する。"""
    monkeypatch.setattr(post_apply.sys, "platform", "linux")
    current: dict[str, Any] = {"version": "1.2.2", "enabled": enabled}
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: (_SESSION,))
    target_script = (
        install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.3/agent_toolkit/agents_server_mcp.py"
    )

    def codex_json(args: list[str]) -> dict[str, Any]:
        return _local_marketplace(plugin_env) if args[1] == "marketplace" else _installed_state(**current)

    def command(args: list[str]) -> bool:
        if args[:2] == ["plugin", "add"]:
            target_script.parent.mkdir(parents=True, exist_ok=True)
            target_script.write_text("new mcp", encoding="utf-8")
            current.update(version="1.2.3", enabled=True)
        return args != ["app-server", "daemon", "version"]

    monkeypatch.setattr(install_codex_plugins, "_codex_json", codex_json)
    monkeypatch.setattr(install_codex_plugins, "_command", command)
    monkeypatch.setattr(
        common, "run_subprocess", lambda *_args, **_kwargs: _FakeResult(stdout=json.dumps(_installed_state(**current)))
    )
    reached: list[str] = []
    warmup_targets: list[Path | None] = []

    def record_step(name: str) -> Callable[[], post_apply_outcome.PostApplyOutcome]:
        def run() -> post_apply_outcome.PostApplyOutcome:
            reached.append(name)
            return post_apply_outcome.PostApplyOutcome()

        return run

    def warmup() -> post_apply_outcome.PostApplyOutcome:
        warmup_targets.append(
            plugin_warmup.codex_plugin_script(
                plugin_id="agent-toolkit@ak110-dotfiles",
                plugin_name="agent-toolkit",
                relative_path=Path("agent_toolkit/agents_server_mcp.py"),
                tag="test",
            )
        )
        reached.append("warmup")
        return post_apply_outcome.PostApplyOutcome()

    def install() -> post_apply_outcome.PostApplyOutcome:
        assert "Codex CLI の導入と更新" in reached
        assert "Codex の Claude MCP 登録削除" in reached
        assert "warmup" not in reached
        return install_codex_plugins.run()

    steps = [
        dataclasses.replace(step, run=record_step(step.name), host_resources=False)
        for step in post_apply.DEFAULT_STEPS  # pylint: disable=protected-access
    ]
    for index, step in enumerate(steps):
        if step.name == "Codex plugin のインストール":
            steps[index] = dataclasses.replace(step, run=install)
        if step.name == "agents_serverのuv環境ウォームアップ":
            steps[index] = dataclasses.replace(step, run=warmup)
    results, _ = post_apply.run(steps)
    assert all(result.ok for result in results)
    codex_result = next(result for result in results if result.name == "Codex plugin のインストール")
    assert codex_result.changed is enabled
    assert codex_result.deferred is False
    assert warmup_targets == [target_script if enabled else None]
    assert current == {"version": "1.2.3" if enabled else "1.2.2", "enabled": enabled}
    assert "Codex plugin snapshot の生成" in reached
    assert "Claude Code plugin のインストール" in reached
    assert "旧Codex User scope MCP登録の移行" in reached
    assert "atk serve 自動起動セットアップ (Linux)" in reached


@pytest.mark.parametrize(
    ("before", "case"),
    [
        ({"installed": []}, "未導入"),
        (_installed_state(version="1.2.2"), "版数不一致"),
    ],
)
def test_reinstalls_when_state_requires_it(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    before: dict[str, Any],
    case: str,
) -> None:
    """導入状態が要件を満たさない場合は公式CLIを呼び出して検証する。"""
    del case
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), before, _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls
    assert calls.count(["plugin", "add", "agent-toolkit@ak110-dotfiles"]) == 1
    assert [notice.command for notice in outcome.notices] == ["/hooks"]


@pytest.mark.parametrize("version", ["1.2.2", "1.2.3"])
@pytest.mark.parametrize("migrate_marketplace", [False, True])
def test_disabled_plugin_keeps_state_and_cache(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    version: str,
    migrate_marketplace: bool,
) -> None:
    """明示的な無効状態は版相違とmarketplace移行の後も同期によって有効化しない。"""
    state = _installed_state(version=version, enabled=False)
    marketplace = (
        {"marketplaces": [{"name": "ak110-dotfiles", "root": str(_git_marketplace_config())}]}
        if migrate_marketplace
        else _local_marketplace(plugin_env)
    )
    previous = install_codex_plugins.CODEX_HOME / f"plugins/cache/ak110-dotfiles/agent-toolkit/{version}/disabled.txt"
    previous.parent.mkdir(parents=True, exist_ok=True)
    previous.write_text("disabled cache", encoding="utf-8")
    legacy = _legacy_link(plugin_env)
    calls: list[list[str]] = []

    def codex_json(args: list[str]) -> dict[str, Any]:
        return marketplace if args[1] == "marketplace" else state

    def command(args: list[str]) -> bool:
        calls.append(args)
        if args[:2] == ["plugin", "add"]:
            state.update(_installed_state())
        return True

    monkeypatch.setattr(install_codex_plugins, "_codex_json", codex_json)
    monkeypatch.setattr(install_codex_plugins, "_command", command)
    outcome = install_codex_plugins.run()

    assert state == _installed_state(version=version, enabled=False)
    assert previous.read_text(encoding="utf-8") == "disabled cache"
    assert legacy.is_symlink()
    assert outcome.changed is migrate_marketplace
    assert not outcome.deferred
    assert not outcome.notices
    assert calls == (
        [["plugin", "marketplace", "remove", "ak110-dotfiles"], ["plugin", "marketplace", "add", str(plugin_env)]]
        if migrate_marketplace
        else []
    )


def test_enabled_companion_update_preserves_disabled_plugin(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同じmarketplaceの有効pluginを更新しても、無効pluginの状態と実体を保つ。"""
    monkeypatch.setattr(install_codex_plugins, "_MANAGED_PLUGINS", ("agent-basics", "agent-toolkit"))
    marketplace = plugin_env / ".agents/plugins/marketplace.json"
    data = json.loads(marketplace.read_text(encoding="utf-8"))
    data["plugins"].append({"name": "agent-basics", "source": {"source": "local", "path": "./agent-basics"}})
    marketplace.write_text(json.dumps(data), encoding="utf-8")
    manifest = plugin_env / "agent-basics/.codex-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"name": "agent-basics", "version": "1.2.3"}), encoding="utf-8")
    state: dict[str, Any] = _installed_state(version="1.2.2", enabled=False)
    state["installed"].append({"pluginId": "agent-basics@ak110-dotfiles", "version": "1.2.2", "enabled": True})
    previous = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2/disabled.txt"
    previous.parent.mkdir(parents=True)
    previous.write_text("disabled cache", encoding="utf-8")
    calls: list[list[str]] = []

    def codex_json(args: list[str]) -> dict[str, Any]:
        return _local_marketplace(plugin_env) if args[1] == "marketplace" else state

    def command(args: list[str]) -> bool:
        calls.append(args)
        if args[:2] == ["plugin", "add"]:
            plugin_id = args[2]
            current = next(item for item in state["installed"] if item["pluginId"] == plugin_id)
            current.update(version="1.2.3", enabled=True)
            skill = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-basics/1.2.3/skills/a/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("new skill", encoding="utf-8")
        return True

    monkeypatch.setattr(install_codex_plugins, "_codex_json", codex_json)
    monkeypatch.setattr(install_codex_plugins, "_command", command)
    outcome = install_codex_plugins.run()

    assert outcome.changed
    assert not outcome.deferred
    assert state["installed"] == [
        {"pluginId": "agent-toolkit@ak110-dotfiles", "version": "1.2.2", "enabled": False},
        {"pluginId": "agent-basics@ak110-dotfiles", "version": "1.2.3", "enabled": True},
    ]
    assert calls == [["plugin", "add", "agent-basics@ak110-dotfiles"]]
    assert previous.read_text(encoding="utf-8") == "disabled cache"


def test_same_version_enabled_is_unchanged(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """版数一致かつ有効なpluginは導入せず変更しない。"""
    calls: list[list[str]] = []
    state = _installed_state()
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), state])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))
    monkeypatch.setattr(
        install_codex_plugins,
        "_hooks_list",
        lambda: {
            "data": [{"cwd": str(plugin_env), "hooks": [_hook_entry(trustStatus="trusted")], "warnings": [], "errors": []}]
        },
    )
    monkeypatch.setattr(
        install_codex_plugins.common,
        "run_subprocess",
        lambda *_args, **_kwargs: pytest.fail("無変更時にdaemonを再起動してはいけない"),
    )

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert not outcome.notices
    assert not calls


def test_running_codex_with_current_plugin_reports_no_deferral(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """版が一致して有効な導入は更新すべき差が無く、Codexの稼働中でも延期の案内を返さない。"""
    monkeypatch.setattr(host_roles, "is_linux_server", lambda: True)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: (_SESSION,))
    monkeypatch.setattr(
        install_codex_plugins,
        "_codex_json",
        lambda args: _local_marketplace(plugin_env) if args[1] == "marketplace" else _installed_state(),
    )
    monkeypatch.setattr(install_codex_plugins, "_command", lambda _args: pytest.fail("差が無い導入へaddを呼ばない"))

    outcome = install_codex_plugins.run()

    assert outcome.deferred is False
    assert not [notice for notice in outcome.notices if "を延期しました" in notice.message]


__all__ = ["plugin_env_fixture"]
