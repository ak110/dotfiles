# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""install_codex_pluginsのrecoveryの試験。"""

# pylint: disable=protected-access

import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from pytools._internal import (
    codex_processes,
    install_codex_plugins,
)
from pytools._internal.install_codex_plugins_test_helpers import (
    _TOOLKIT_PREFIX,
    _broken_legacy_link,
    _git_marketplace_config,
    _installed_state,
    _legacy_link,
    _local_marketplace,
    _recording_success,
    _set_json_responses,
)
from pytools._internal.install_codex_plugins_test_helpers import _empty_unused_plugins as _empty_unused_plugins
from pytools._internal.install_codex_plugins_test_helpers import plugin_env_fixture as plugin_env_fixture


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
        {"installed": [{"pluginId": "agent-toolkit@ak110-dotfiles", "version": "1.2.2", "enabled": None}]},
        {"installed": [{"pluginId": "agent-toolkit@ak110-dotfiles", "version": "1.2.2"}]},
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


def test_plugin_add_failure_keeps_legacy_link(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """plugin addの失敗は旧cacheを復旧し、導入失敗として扱ってlegacy linkを除去しない。"""
    destination = _legacy_link(plugin_env)
    previous = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2"
    previous.mkdir(parents=True)
    (previous / "marker").write_text("previous", encoding="utf-8")
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), {"installed": []}])

    def command(args: list[str]) -> bool:
        calls.append(args)
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(previous.parent)
        return False

    monkeypatch.setattr(install_codex_plugins, "_command", command)

    with caplog.at_level("WARNING"):
        outcome = install_codex_plugins.run()

    assert outcome.failure is None
    assert "plugin addに失敗" in caplog.text

    assert destination.is_symlink()
    assert (previous / "marker").read_text(encoding="utf-8") == "previous"
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls


def test_cache_restore_failure_is_reported(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """CLI失敗後に旧cacheも復旧できない場合は、両方の失敗を診断する。"""
    previous = install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2"
    previous.mkdir(parents=True)
    (previous / "marker").write_text("previous", encoding="utf-8")
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2")])

    def command(args: list[str]) -> bool:
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(previous.parent)
            return False
        return True

    original_copytree = shutil.copytree

    def copytree(*args: Any, **kwargs: Any) -> Path:
        destination = Path(args[1])
        if destination.is_relative_to(previous.parent):
            raise OSError("injected restore failure")
        return original_copytree(*args, **kwargs)

    monkeypatch.setattr(install_codex_plugins, "_command", command)
    monkeypatch.setattr(install_codex_plugins.shutil, "copytree", copytree)

    with caplog.at_level("WARNING"):
        outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert "plugin addと旧cacheの復旧に失敗" in caplog.text
    assert "injected restore failure" in caplog.text
    assert "表示された内容とパスをdotfilesの作者へ報告してください" in caplog.text


def test_post_install_verification_failure_keeps_legacy_link(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """導入後の状態検証に失敗した場合はスキップと数え、legacy linkを除去しない。"""
    destination = _legacy_link(plugin_env)
    calls: list[list[str]] = []
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), {"installed": []}, None])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    with caplog.at_level("WARNING"):
        outcome = install_codex_plugins.run()

    assert outcome.failure is None
    assert "更新後の状態が期待値と一致しない" in caplog.text

    assert destination.is_symlink()
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] in calls


def test_legacy_removal_failure_does_not_restore_links(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """導入済みpluginのlegacy link除去失敗時も削除済みlinkを復元しない。"""
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

    assert not destination.exists()


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
    source = plugin_env / f"{_TOOLKIT_PREFIX}/skills/coding"
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


def test_marketplace_local_origin_is_distinct_from_cached_root(plugin_env: Path) -> None:
    """Codexの表示rootが複製先でも、公開local sourceで登録元を照合する。"""
    data = {
        "marketplaces": [
            {
                "name": "ak110-dotfiles",
                "root": str(plugin_env / "cached"),
                "marketplaceSource": {"sourceType": "local", "source": str(plugin_env)},
            }
        ]
    }
    # 公開一覧の登録元を抽出する境界を検査する。
    # pylint: disable-next=protected-access
    assert install_codex_plugins._marketplace_root(data, "ak110-dotfiles") == plugin_env.resolve()


def test_migrates_codex_git_copy_to_local_marketplace(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Git導入の複製を使う登録を、更新するdotfilesのlocal sourceへ自動的にそろえる。"""
    cache = _git_marketplace_config()
    _set_json_responses(monkeypatch, [{"marketplaces": [{"name": "ak110-dotfiles", "root": str(cache)}]}, _installed_state()])
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))
    assert install_codex_plugins.run().changed
    assert calls[:2] == [
        ["plugin", "marketplace", "remove", "ak110-dotfiles"],
        ["plugin", "marketplace", "add", str(plugin_env)],
    ]


@pytest.mark.parametrize("remove_success", [True, False])
def test_marketplace_migration_failure_preserves_recoverable_source(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, remove_success: bool
) -> None:
    """追加失敗では元のGit URL・ref・sparseを復元し、除去失敗では追加へ進まない。"""
    cache = _git_marketplace_config()
    calls: list[list[str]] = []
    responses = iter([True, False, True] if remove_success else [False])

    def command(argv: list[str]) -> bool:
        calls.append(argv)
        return next(responses)

    monkeypatch.setattr(install_codex_plugins, "_command", command)
    with pytest.raises(RuntimeError, match="復元済み" if remove_success else "変更できなかった"):
        # 移行失敗時の原本保持を直接検査する。
        # pylint: disable-next=protected-access
        install_codex_plugins._migrate_cached_marketplace(plugin_env, "ak110-dotfiles", cache)
    if remove_success:
        assert calls[-1] == [
            "plugin",
            "marketplace",
            "add",
            "https://github.com/ak110/dotfiles.git",
            "--ref",
            "master",
            "--sparse",
            ".agents",
            "--sparse",
            "agent-toolkit-codex",
        ]
    else:
        assert len(calls) == 1


def test_removes_installed_unused_plugin_and_returns_notice(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """導入済みの不要pluginを公式CLIで除去し、daemon再起動を案内する。"""
    unused_state = {
        "installed": [
            {"pluginId": "compact-plus@compact-plus", "version": "1.3.2", "enabled": True},
            *cast("list[dict[str, object]]", _installed_state()["installed"]),
        ]
    }
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ("compact-plus@compact-plus",))
    monkeypatch.setattr(
        codex_processes,
        "codex_processes",
        lambda: (codex_processes.CodexProcess(4321, "codex app-server --managed-daemon", "managed-daemon"),),
    )
    _set_json_responses(monkeypatch, [unused_state, _local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is True
    assert ["plugin", "remove", "compact-plus@compact-plus"] in calls
    assert [notice.command for notice in outcome.notices] == ["/hooks", "codex app-server daemon restart"]


def test_running_codex_session_defers_unused_plugin_removal(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """不要pluginも稼働中のCodexセッションが参照し得る間は除去せず、延期として案内する。

    除去は旧版の回収と同じくplugin実体を消すため、更新と同じ参照の判定（`session`の役割）に従う。
    """
    unused_state = {
        "installed": [
            {"pluginId": "compact-plus@compact-plus", "version": "1.3.2", "enabled": True},
            *cast("list[dict[str, object]]", _installed_state()["installed"]),
        ]
    }
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ("compact-plus@compact-plus",))
    monkeypatch.setattr(
        codex_processes, "codex_processes", lambda: (codex_processes.CodexProcess(4321, "codex (pid 4321)", "session"),)
    )
    _set_json_responses(monkeypatch, [unused_state, _local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert ["plugin", "remove", "compact-plus@compact-plus"] not in calls
    assert outcome.deferred is True
    assert [notice for notice in outcome.notices if "compact-plus@compact-plus" in notice.message and "延期" in notice.message]


def test_skips_uninstalled_unused_plugin(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未導入の不要pluginは除去せず、ローカルpluginも変更しない。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ("compact-plus@compact-plus",))
    _set_json_responses(monkeypatch, [{"installed": []}, _local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert [notice.command for notice in outcome.notices] == ["/hooks"]
    assert ["plugin", "remove", "compact-plus@compact-plus"] not in calls


def test_skips_unused_plugin_when_list_fails(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """不要pluginの一覧取得失敗時は除去せず、ローカルplugin処理を継続する。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ("compact-plus@compact-plus",))
    _set_json_responses(monkeypatch, [None, _local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert [notice.command for notice in outcome.notices] == ["/hooks"]
    assert ["plugin", "remove", "compact-plus@compact-plus"] not in calls


def test_continues_when_unused_plugin_remove_fails(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """不要pluginの除去失敗時もローカルplugin処理を継続する。"""
    unused_state = {
        "installed": [
            {"pluginId": "compact-plus@compact-plus", "version": "1.3.2", "enabled": True},
            *cast("list[dict[str, object]]", _installed_state()["installed"]),
        ]
    }
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ("compact-plus@compact-plus",))
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    _set_json_responses(monkeypatch, [unused_state, _local_marketplace(plugin_env), unused_state])

    def command(args: list[str]) -> bool:
        calls.append(args)
        return args != ["plugin", "remove", "compact-plus@compact-plus"]

    monkeypatch.setattr(install_codex_plugins, "_command", command)

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert [notice.command for notice in outcome.notices] == ["/hooks"]
    assert ["plugin", "remove", "compact-plus@compact-plus"] in calls
    assert "plugin除去に失敗したため続行" in caplog.text


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


__all__ = ["plugin_env_fixture"]
