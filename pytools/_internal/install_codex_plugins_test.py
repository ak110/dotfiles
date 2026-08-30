"""install_codex_pluginsのテスト。"""

import json
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
        json.dumps(
            {
                "name": "ak110-dotfiles",
                "plugins": [
                    {
                        "name": "agent-toolkit",
                        "source": {"source": "local", "path": "./agent-toolkit"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (root / "agent-toolkit/.codex-plugin/plugin.json").write_text(
        json.dumps({"name": "agent-toolkit", "version": "1.2.3"}),
        encoding="utf-8",
    )
    (root / "agent-toolkit/scripts").mkdir()
    (root / "agent-toolkit/scripts/claude_hook.py").write_text("source", encoding="utf-8")
    (root / "agent-toolkit/skills").mkdir()
    (root / "agent-toolkit/plugin-note.txt").write_text("source-file", encoding="utf-8")
    monkeypatch.setattr(claude_common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(install_codex_plugins.shutil, "which", lambda _: "/bin/codex")
    monkeypatch.setattr(install_codex_plugins, "CODEX_HOME", tmp_path / ".codex")
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return root


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


def _installed_state(*, version: str = "1.2.3", enabled: bool = True) -> dict[str, object]:
    return {
        "installed": [
            {
                "pluginId": "agent-toolkit@ak110-dotfiles",
                "version": version,
                "enabled": enabled,
            }
        ]
    }


def _recording_success(calls: list[list[str]], *, daemon_running: bool = True) -> Callable[[list[str]], bool]:
    """引数を記録して成功を返すコマンドスタブを作成する。"""

    def command(args: list[str]) -> bool:
        calls.append(args)
        return daemon_running if args == ["app-server", "daemon", "version"] else True

    return command


def _local_marketplace(plugin_env: Path) -> dict[str, object]:
    return {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]}


def _set_json_responses(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[dict[str, Any] | None],
) -> None:
    iterator: Iterator[dict[str, Any] | None] = iter(responses)
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(iterator))


def test_registers_and_installs_with_official_cli(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未導入pluginは公式CLIへ導入を委譲し、legacy linkを除去する。"""
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
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert [notice.command for notice in outcome.notices] == ["codex app-server daemon restart"]
    assert ["plugin", "marketplace", "add", str(plugin_env)] in calls
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls
    assert not destination.exists()
    assert (cache_entry / "marker").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    ("before", "case"),
    [
        ({"installed": []}, "未導入"),
        (_installed_state(enabled=False), "無効"),
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
    assert len(outcome.notices) == 1


def test_same_version_enabled_is_unchanged(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """版数一致かつ有効なpluginは導入せず変更しない。"""
    calls: list[list[str]] = []
    state = _installed_state()
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), state])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert not outcome.notices
    assert not calls


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
def test_invalid_before_state_stops_before_plugin_add(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    plugin_state: dict[str, Any] | None,
) -> None:
    """更新前状態を取得できない場合はpluginを変更しない。"""
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), plugin_state])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls


def test_plugin_add_failure_keeps_legacy_link(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = _legacy_link(plugin_env)
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), {"installed": []}])

    def command(args: list[str]) -> bool:
        calls.append(args)
        return False

    monkeypatch.setattr(install_codex_plugins, "_command", command)

    with pytest.raises(RuntimeError, match="plugin addに失敗"):
        install_codex_plugins.run()

    assert destination.is_symlink()
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls


def test_post_install_verification_failure_keeps_legacy_link(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """導入後の状態検証に失敗した場合はlegacy linkを除去しない。"""
    destination = _legacy_link(plugin_env)
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), {"installed": []}, None])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    with pytest.raises(RuntimeError, match="更新後の状態が期待値と一致しない"):
        install_codex_plugins.run()

    assert destination.is_symlink()
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls


def test_legacy_removal_failure_restores_links(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """導入済みpluginのlegacy link除去失敗時は変更前のlinkを復元する。"""
    destination = _legacy_link(plugin_env)
    state = _installed_state()
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), state])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success([]))
    original_remove = install_codex_plugins._remove_legacy_links  # pylint: disable=protected-access

    def remove_then_fail(root: Path) -> bool:
        original_remove(root)
        raise OSError("injected legacy removal failure")

    monkeypatch.setattr(install_codex_plugins, "_remove_legacy_links", remove_then_fail)

    with pytest.raises(OSError, match="injected legacy removal failure"):
        install_codex_plugins.run()

    assert destination.is_symlink()
    assert destination.resolve() == (plugin_env / "agent-toolkit/skills/coding").resolve()


def test_removes_broken_legacy_link_and_keeps_unrelated_entries(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _broken_legacy_link(plugin_env)
    unrelated_source = plugin_env / "unrelated"
    unrelated_source.mkdir()
    unrelated = install_codex_plugins.CODEX_HOME / "skills/unrelated"
    unrelated.symlink_to(unrelated_source, target_is_directory=True)
    regular = install_codex_plugins.CODEX_HOME / "skills/regular"
    regular.mkdir(parents=True)
    state = _installed_state()
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), state])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success([]))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert not expected.exists()
    assert unrelated.is_symlink()
    assert regular.is_dir()


def test_codex_home_environment_controls_legacy_cleanup(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CODEX_HOME指定時は指定先のlegacy linkを除去する。"""
    codex_home = plugin_env.parent / "custom-codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    source = plugin_env / "agent-toolkit/skills/coding"
    source.mkdir(parents=True)
    destination = codex_home / "skills/coding"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(source, target_is_directory=True)
    state = _installed_state()
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), state])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success([]))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
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


def test_windows_junction_detection_and_removal_use_rmdir() -> None:
    """Windows junction相当ではis_junction判定後にrmdirを使う。"""

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
        assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls
        assert not destination.is_symlink()

    def test_skips_when_already_added(self, plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._setup_run(plugin_env, monkeypatch, registered=True, installed=True)

        outcome = install_codex_plugins.run()

        assert outcome.changed is False
        assert not outcome.notices
        assert ["plugin", "marketplace", "add", self._TARGET[1]] not in calls
        assert ["plugin", "add", self._TARGET[2]] not in calls

    @pytest.mark.parametrize(
        ("registered_source", "registered_source_type"),
        [
            ("https://github.com/attacker/compact-plus.git", "git"),
            ("http://github.com/u-ichi/compact-plus.git", "git"),
            (None, "github"),
        ],
    )
    def test_rejects_untrusted_marketplace(
        self,
        plugin_env: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        registered_source: str | None,
        registered_source_type: str,
    ) -> None:
        calls = self._setup_run(
            plugin_env,
            monkeypatch,
            registered=True,
            installed=False,
            registered_source=registered_source,
            registered_source_type=registered_source_type,
        )

        outcome = install_codex_plugins.run()

        assert outcome.changed is False
        assert not outcome.notices
        assert ["plugin", "add", self._TARGET[2]] not in calls
        assert "marketplace取得元が一致しないためスキップ" in caplog.text

    def test_base_and_external_updates_deduplicate_restart_notice(
        self,
        plugin_env: Path,
        monkeypatch: pytest.MonkeyPatch,
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

    def test_continues_when_marketplace_add_fails(self, plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._setup_run(plugin_env, monkeypatch, registered=False, installed=False, add_succeeds=False)

        outcome = install_codex_plugins.run()

        assert outcome.changed is False
        assert not outcome.notices
        assert ["plugin", "marketplace", "add", self._TARGET[1]] in calls
        assert ["plugin", "add", self._TARGET[2]] not in calls
