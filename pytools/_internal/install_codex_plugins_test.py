"""install_codex_pluginsのテスト。"""

import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from pytools._internal import claude_common, install_codex_plugins


@pytest.fixture(autouse=True)
def _empty_external_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """専用テストで明示した対象以外の外部導入を無効にする。"""
    monkeypatch.setattr(install_codex_plugins, "_EXTERNAL_PLUGINS", ())


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
    monkeypatch.delenv("CODEX_HOME", raising=False)
    _cache_version("1.2.3", hook="current")
    return root


def _cache_root() -> Path:
    return install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit"


def _cache_version(version: str, *, hook: str = "hook") -> Path:
    path = _cache_root() / version
    (path / "scripts").mkdir(parents=True, exist_ok=True)
    (path / "scripts/claude_hook.py").write_text(hook, encoding="utf-8")
    return path


def _versions_path() -> Path:
    return install_codex_plugins.CODEX_HOME / "plugins/cache-compat/ak110-dotfiles/agent-toolkit/versions"


def _legacy_link(plugin_env: Path, name: str = "coding") -> Path:
    source = plugin_env / f"agent-toolkit/skills/{name}"
    source.mkdir(parents=True)
    destination = install_codex_plugins.CODEX_HOME / f"skills/{name}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(source, target_is_directory=True)
    return destination


def _broken_legacy_link(plugin_env: Path, name: str = "removed") -> Path:
    source = plugin_env / f"agent-toolkit/skills/{name}"
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


def _recording_success(calls: list[list[str]], *, daemon_running: bool = True) -> Callable[[list[str]], bool]:
    """引数を記録して成功を返すコマンドスタブを作成する。"""

    def command(args: list[str]) -> bool:
        calls.append(args)
        return daemon_running if args == ["app-server", "daemon", "version"] else True

    return command


def test_registers_installs_and_removes_expected_legacy_link(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = _legacy_link(plugin_env)
    calls: list[list[str]] = []
    responses: Iterator[dict[str, Any] | None] = iter(
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
    outcome = install_codex_plugins.run()
    assert outcome.changed is True
    assert [notice.command for notice in outcome.notices] == ["codex app-server daemon restart"]
    assert "app-server daemonを再起動してください" in outcome.notices[0].message
    assert ["plugin", "marketplace", "add", str(plugin_env)] in calls
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls
    assert not destination.exists()


def test_rejects_mismatched_marketplace_root(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        install_codex_plugins,
        "_codex_json",
        lambda _: {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env / "other")}]},
    )
    outcome = install_codex_plugins.run()
    assert outcome.changed is False
    assert not outcome.notices


def test_noop_state_skips_resync_and_removes_broken_legacy_link(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = _broken_legacy_link(plugin_env)
    state = _installed_state()
    responses = iter([{"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]}, state])
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))
    outcome = install_codex_plugins.run()
    assert outcome.changed is True
    assert not outcome.notices
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls
    assert ["app-server", "daemon", "version"] not in calls
    assert not destination.is_symlink()


def test_version_mismatch_reinstalls_plugin_and_returns_notice(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = {
        "installed": [
            {"pluginId": "agent-toolkit@ak110-dotfiles", "version": "1.2.2", "enabled": True},
        ]
    }
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            before,
            _installed_state(),
        ]
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert len(outcome.notices) == 1
    assert outcome.notices[0].command == "codex app-server daemon restart"
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls


@pytest.mark.parametrize(
    "plugin_state",
    [
        None,
        {},
        {"installed": None},
        {"installed": "x"},
        {"installed": [{"version": "1.2.2", "enabled": True}]},
        {"installed": [{"pluginId": "agent-toolkit@ak110-dotfiles", "version": 122, "enabled": True}]},
        {"installed": [{"pluginId": "agent-toolkit@ak110-dotfiles", "version": "1.2.2", "enabled": "true"}]},
    ],
)
def test_pre_install_json_failure_stops_before_plugin_add(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    plugin_state: dict[str, Any] | None,
) -> None:
    """更新前状態を取得できない場合はpluginを変更しない。"""
    responses: Iterator[dict[str, Any] | None] = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            plugin_state,
        ]
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls


def test_other_plugin_details_do_not_block_install(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """対象外pluginのversionとenabledは対象pluginの状態判定へ影響させない。"""
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            {"installed": [{"pluginId": "other@marketplace", "version": 1, "enabled": "true"}]},
            _installed_state(),
        ]
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls


def test_ledger_replace_failure_keeps_existing_ledger(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """台帳のatomic置換失敗時は既存内容を保持して更新を中止する。"""
    _cache_version("1.2.2", hook="previous")
    versions = _versions_path()
    versions.parent.mkdir(parents=True)
    versions.write_text("1.2.1\n", encoding="utf-8")
    before = {
        "installed": [
            {"pluginId": "agent-toolkit@ak110-dotfiles", "version": "1.2.2", "enabled": True},
        ]
    }
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            before,
        ]
    )
    calls: list[list[str]] = []
    original_replace = Path.replace

    def fail_ledger_replace(source: Path, target: str | Path) -> Path:
        if Path(target) == versions:
            raise OSError("injected ledger replace failure")
        return original_replace(source, target)

    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))
    monkeypatch.setattr(Path, "replace", fail_ledger_replace)

    with pytest.raises(OSError, match="injected ledger replace failure"):
        install_codex_plugins.run()

    assert versions.read_text(encoding="utf-8") == "1.2.1\n"
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls
    assert [path.name for path in versions.parent.iterdir()] == ["versions"]


def test_version_update_restores_all_safe_cache_names(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """更新で消えた実versionと互換リンクを新versionへ直接復元する。"""
    old = _cache_version("1.2.1", hook="old")
    previous = _cache_version("1.2.2", hook="previous")
    compat = _cache_root() / "1.1.0"
    compat.symlink_to(old.name, target_is_directory=True)
    (_cache_root() / "regular.txt").write_text("保持", encoding="utf-8")
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            {"installed": [{"pluginId": "agent-toolkit@ak110-dotfiles", "version": "1.2.2", "enabled": True}]},
            _installed_state(),
        ]
    )
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))

    def command(args: list[str]) -> bool:
        if args == ["plugin", "add", "agent-toolkit@ak110-dotfiles"]:
            shutil.rmtree(_cache_root())
            _cache_version("1.2.3", hook="current")
        return True

    monkeypatch.setattr(install_codex_plugins, "_command", command)

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert _versions_path().read_text(encoding="utf-8") == "1.1.0\n1.2.1\n1.2.2\n1.2.3\n"
    for version in ("1.1.0", "1.2.1", "1.2.2"):
        restored = _cache_root() / version
        assert restored.is_symlink()
        assert restored.readlink() == Path("1.2.3")
        assert (restored / "scripts/claude_hook.py").read_text(encoding="utf-8") == "current"
    assert previous != old


def test_same_version_restores_from_external_ledger(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同version再実行でもcache外の台帳から中断済みの復元を完了する。"""
    _cache_version("1.2.3", hook="current")
    _versions_path().parent.mkdir(parents=True)
    _versions_path().write_text("1.2.1\ninvalid/name\n", encoding="utf-8")
    state = _installed_state()
    responses = iter([{"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]}, state])
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    restored = _cache_root() / "1.2.1"
    assert outcome.changed is True
    assert restored.resolve() == (_cache_root() / "1.2.3").resolve()
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls


def test_codex_home_environment_controls_cache_paths(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CODEX_HOME指定時は固定ホームではなく指定先のcacheと台帳を使う。"""
    codex_home = plugin_env.parent / "custom-codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    current = codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.3"
    (current / "scripts").mkdir(parents=True)
    versions = codex_home / "plugins/cache-compat/ak110-dotfiles/agent-toolkit/versions"
    versions.parent.mkdir(parents=True)
    versions.write_text("1.2.2\n", encoding="utf-8")
    state = _installed_state()
    responses = iter([{"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]}, state])
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", lambda _: True)

    install_codex_plugins.run()

    assert (codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2").resolve() == current.resolve()


def test_cache_conflict_propagates_without_replacing_entry(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """互換名に通常エントリがある場合は保持して失敗を呼び出し元へ伝える。"""
    _cache_version("1.2.3")
    conflict = _cache_version("1.2.2", hook="keep")
    _versions_path().parent.mkdir(parents=True)
    _versions_path().write_text("1.2.2\n", encoding="utf-8")
    state = _installed_state()
    responses = iter([{"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]}, state])
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))

    with pytest.raises(FileExistsError):
        install_codex_plugins.run()

    assert (conflict / "scripts/claude_hook.py").read_text(encoding="utf-8") == "keep"


def test_version_mismatch_omits_notice_when_daemon_is_not_running(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """プラグイン更新後もdaemon未起動なら再起動を案内しない。"""
    before = {
        "installed": [
            {"pluginId": "agent-toolkit@ak110-dotfiles", "version": "1.2.2", "enabled": True},
        ]
    }
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            before,
            _installed_state(),
        ]
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls, daemon_running=False))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert not outcome.notices
    assert ["app-server", "daemon", "version"] in calls


def test_disabled_plugin_reinstalls_and_returns_notice(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = {
        "installed": [
            {"pluginId": "agent-toolkit@ak110-dotfiles", "version": "1.2.3", "enabled": False},
        ]
    }
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            before,
            _installed_state(),
        ]
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert len(outcome.notices) == 1
    assert outcome.notices[0].command == "codex app-server daemon restart"
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls


def test_plugin_add_failure_keeps_legacy_link(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = _legacy_link(plugin_env)
    responses: Iterator[dict[str, Any] | None] = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            {"installed": []},
        ]
    )
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", lambda _: False)
    outcome = install_codex_plugins.run()
    assert outcome.changed is False
    assert not outcome.notices
    assert destination.is_symlink()


def test_post_install_json_failure_keeps_legacy_link(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = _legacy_link(plugin_env)
    responses: Iterator[dict[str, Any] | None] = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            {"installed": []},
            None,
        ]
    )
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", lambda _: True)
    with pytest.raises(RuntimeError, match="更新後の状態"):
        install_codex_plugins.run()
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
    outcome = install_codex_plugins.run()
    assert outcome.changed is True
    assert not outcome.notices
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


class TestExternalPlugins:
    """Codex向け外部プラグインの登録と導入を検証する。"""

    _TARGET = ("compact-plus", "u-ichi/compact-plus", "compact-plus@compact-plus")

    def _setup_run(
        self,
        plugin_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        registered: bool,
        installed: bool,
        add_succeeds: bool = True,
        registered_source: str | None = None,
        registered_source_type: str = "git",
    ) -> list[list[str]]:
        monkeypatch.setattr(install_codex_plugins, "_EXTERNAL_PLUGINS", (self._TARGET,))
        calls: list[list[str]] = []
        marketplace_added = False

        def codex_json(args: list[str]) -> dict[str, Any]:
            calls.append(["json", *args])
            if args[:3] == ["plugin", "marketplace", "list"]:
                marketplaces: list[dict[str, Any]] = [{"name": "ak110-dotfiles", "root": str(plugin_env)}]
                if registered or marketplace_added:
                    marketplaces.append(
                        {
                            "name": self._TARGET[0],
                            "root": "/tmp/compact-plus",
                            "marketplaceSource": {
                                "sourceType": registered_source_type,
                                "source": registered_source or "https://github.com/u-ichi/compact-plus.git",
                            },
                        }
                    )
                return {"marketplaces": marketplaces}
            plugins = cast("list[dict[str, object]]", _installed_state()["installed"])
            if installed:
                plugins = [*plugins, {"pluginId": self._TARGET[2], "version": "1.0.0", "enabled": True}]
            return {"installed": plugins}

        def command(args: list[str]) -> bool:
            nonlocal marketplace_added
            calls.append(args)
            if args == ["plugin", "marketplace", "add", self._TARGET[1]]:
                marketplace_added = add_succeeds
                return add_succeeds
            return True

        monkeypatch.setattr(install_codex_plugins, "_codex_json", codex_json)
        monkeypatch.setattr(install_codex_plugins, "_command", command)
        return calls

    def test_registers_and_adds_when_absent(self, plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        destination = _broken_legacy_link(plugin_env)
        calls = self._setup_run(plugin_env, monkeypatch, registered=False, installed=False)

        outcome = install_codex_plugins.run()
        assert outcome.changed is True
        assert [notice.command for notice in outcome.notices] == ["codex app-server daemon restart"]
        assert ["plugin", "marketplace", "add", self._TARGET[1]] in calls
        assert ["plugin", "add", self._TARGET[2]] in calls
        assert calls[:3] == [
            ["json", "plugin", "marketplace", "list", "--json"],
            ["plugin", "marketplace", "add", self._TARGET[1]],
            ["json", "plugin", "marketplace", "list", "--json"],
        ]
        assert not destination.is_symlink()

    def test_skips_when_already_added(self, plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._setup_run(plugin_env, monkeypatch, registered=True, installed=True)

        outcome = install_codex_plugins.run()
        assert outcome.changed is False
        assert not outcome.notices
        assert ["plugin", "marketplace", "add", self._TARGET[1]] not in calls
        assert ["plugin", "add", self._TARGET[2]] not in calls

    def test_rejects_same_name_from_different_source(
        self, plugin_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        calls = self._setup_run(
            plugin_env,
            monkeypatch,
            registered=True,
            installed=False,
            registered_source="https://github.com/attacker/compact-plus.git",
        )

        outcome = install_codex_plugins.run()
        assert outcome.changed is False
        assert not outcome.notices
        assert ["plugin", "marketplace", "add", self._TARGET[1]] not in calls
        assert ["plugin", "add", self._TARGET[2]] not in calls
        assert "marketplace取得元が一致しないためスキップ" in caplog.text

    def test_rejects_plain_http_source(
        self, plugin_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        calls = self._setup_run(
            plugin_env,
            monkeypatch,
            registered=True,
            installed=False,
            registered_source="http://github.com/u-ichi/compact-plus.git",
        )

        outcome = install_codex_plugins.run()
        assert outcome.changed is False
        assert not outcome.notices
        assert ["plugin", "add", self._TARGET[2]] not in calls
        assert "marketplace取得元が一致しないためスキップ" in caplog.text

    def test_rejects_undefined_source_type(
        self, plugin_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        calls = self._setup_run(
            plugin_env,
            monkeypatch,
            registered=True,
            installed=False,
            registered_source_type="github",
        )

        outcome = install_codex_plugins.run()
        assert outcome.changed is False
        assert not outcome.notices
        assert ["plugin", "add", self._TARGET[2]] not in calls
        assert "marketplace取得元が一致しないためスキップ" in caplog.text

    def test_base_and_external_updates_deduplicate_restart_notice(
        self, plugin_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(install_codex_plugins, "_EXTERNAL_PLUGINS", (self._TARGET,))
        local_installed = False
        external_installed = False

        def codex_json(args: list[str]) -> dict[str, Any]:
            if args[:3] == ["plugin", "marketplace", "list"]:
                return {
                    "marketplaces": [
                        {"name": "ak110-dotfiles", "root": str(plugin_env)},
                        {
                            "name": self._TARGET[0],
                            "root": "/tmp/compact-plus",
                            "marketplaceSource": {
                                "sourceType": "git",
                                "source": "https://github.com/u-ichi/compact-plus.git",
                            },
                        },
                    ]
                }
            installed: list[dict[str, object]] = []
            if external_installed:
                installed.append({"pluginId": self._TARGET[2], "version": "1.0.0", "enabled": True})
            if local_installed:
                installed.extend(cast("list[dict[str, object]]", _installed_state()["installed"]))
            return {"installed": installed}

        def command(args: list[str]) -> bool:
            nonlocal external_installed, local_installed
            if args == ["plugin", "add", self._TARGET[2]]:
                external_installed = True
            if args == ["plugin", "add", "agent-toolkit@ak110-dotfiles"]:
                local_installed = True
            return True

        monkeypatch.setattr(install_codex_plugins, "_codex_json", codex_json)
        monkeypatch.setattr(install_codex_plugins, "_command", command)

        outcome = install_codex_plugins.run()

        assert outcome.changed is True
        assert [notice.command for notice in outcome.notices] == ["codex app-server daemon restart"]

    def test_rejects_mismatched_source_after_registration(
        self, plugin_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        calls = self._setup_run(
            plugin_env,
            monkeypatch,
            registered=False,
            installed=False,
            registered_source="https://github.com/attacker/compact-plus.git",
        )

        outcome = install_codex_plugins.run()
        assert outcome.changed is True
        assert not outcome.notices
        assert ["plugin", "marketplace", "add", self._TARGET[1]] in calls
        assert ["plugin", "add", self._TARGET[2]] not in calls
        assert "marketplace取得元が一致しないためスキップ" in caplog.text

    def test_continues_when_cli_fails(self, plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._setup_run(plugin_env, monkeypatch, registered=False, installed=False, add_succeeds=False)

        outcome = install_codex_plugins.run()
        assert outcome.changed is False
        assert not outcome.notices
        assert ["plugin", "marketplace", "add", self._TARGET[1]] in calls
        assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls
