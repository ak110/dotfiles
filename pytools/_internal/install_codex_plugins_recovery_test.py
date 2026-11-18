# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""install_codex_pluginsのrecoveryの試験。"""

# pylint: disable=protected-access

import errno
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


def _cache() -> Path:
    return install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit"


def _saved_root() -> Path:
    return install_codex_plugins.CODEX_HOME / ".dotfiles-plugin-cache-saved"


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


def test_cache_restore_failure_is_reported_and_recovered_by_next_run(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """CLI失敗後に旧cacheも復旧できない場合は両方の失敗を診断し、次回の実行が退避先から戻す。"""
    previous = _cache() / "1.2.2"
    previous.mkdir(parents=True)
    (previous / "marker").write_text("previous", encoding="utf-8")
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2")])

    def command(args: list[str]) -> bool:
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(_cache())
            return False
        return True

    original_rename = Path.rename
    original_copytree = shutil.copytree

    def rename(self: Path, target: Path) -> Path:
        if Path(target).is_relative_to(_cache()):
            raise OSError("injected restore failure")
        return original_rename(self, target)

    def copytree(source: Any, destination: Any, *args: Any, **kwargs: Any) -> Any:
        if Path(destination).is_relative_to(_cache()):
            raise OSError("injected restore failure")
        return original_copytree(source, destination, *args, **kwargs)

    monkeypatch.setattr(install_codex_plugins, "_command", command)
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(install_codex_plugins.shutil, "copytree", copytree)

    with caplog.at_level("WARNING"):
        outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert "plugin addと旧cacheの復旧に失敗" in caplog.text
    assert "injected restore failure" in caplog.text
    assert "表示された内容とパスをdotfilesの作者へ報告してください" in caplog.text
    assert not previous.exists()

    monkeypatch.setattr(Path, "rename", original_rename)
    monkeypatch.setattr(install_codex_plugins.shutil, "copytree", original_copytree)
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state()])
    install_codex_plugins.run()

    assert (previous / "marker").read_text(encoding="utf-8") == "previous"
    assert not _saved_root().exists()


class _Killed(BaseException):
    """更新処理の強制終了を模す。"""


def test_interrupted_update_is_recovered_by_next_run(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """退避中に強制終了した更新が残した旧versionを、更新の無い次回の実行が同じパスへ戻す。"""
    for version in ("1.2.1", "1.2.2"):
        (_cache() / version).mkdir(parents=True)
        (_cache() / version / "marker").write_text(version, encoding="utf-8")
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2")])

    def killed(args: list[str]) -> bool:
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(_cache())
            raise _Killed
        return True

    monkeypatch.setattr(install_codex_plugins, "_command", killed)
    with pytest.raises(_Killed):
        install_codex_plugins.run()
    assert not (_cache() / "1.2.1").exists()

    # 強制終了の後に、atk-hookなどが同じ版を作り直した場合はその内容を残す。
    (_cache() / "1.2.2").mkdir(parents=True)
    (_cache() / "1.2.2/marker").write_text("recreated", encoding="utf-8")
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success([]))
    install_codex_plugins.run()

    assert (_cache() / "1.2.1/marker").read_text(encoding="utf-8") == "1.2.1"
    assert (_cache() / "1.2.2/marker").read_text(encoding="utf-8") == "recreated"
    assert not _saved_root().exists()


def _update_with_locked_version(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, calls: list[list[str]], *, copy_fails: bool
) -> None:
    """1.2.1を移動できない状態で1.2.2から1.2.3へ更新する。CLIは`plugin add`で全旧版を削除する。"""
    for version in ("1.2.1", "1.2.2"):
        (_cache() / version).mkdir(parents=True)
        (_cache() / version / "marker").write_text(version, encoding="utf-8")
    locked = _cache() / "1.2.1"
    original_rename = Path.rename
    original_copytree = shutil.copytree

    def rename(self: Path, target: Path) -> Path:
        if self == locked:
            raise PermissionError("in use")
        return original_rename(self, target)

    def copytree(*args: Any, **kwargs: Any) -> Path:
        if copy_fails:
            raise OSError("injected copy failure")
        return original_copytree(*args, **kwargs)

    current: dict[str, Any] = {"version": "1.2.2", "enabled": True}

    def codex_json(args: list[str]) -> dict[str, Any]:
        return _local_marketplace(plugin_env) if args[1] == "marketplace" else _installed_state(**current)

    def command(args: list[str]) -> bool:
        calls.append(args)
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(_cache())
            (_cache() / "1.2.3/agent_toolkit").mkdir(parents=True)
            (_cache() / "1.2.3/agent_toolkit/hook.py").write_text("hook 1.2.3", encoding="utf-8")
            current.update(version="1.2.3")
        return True

    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(install_codex_plugins.shutil, "copytree", copytree)
    monkeypatch.setattr(install_codex_plugins, "_codex_json", codex_json)
    monkeypatch.setattr(install_codex_plugins, "_command", command)
    install_codex_plugins.run()


def test_version_that_cannot_be_moved_is_copied_and_restored(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """移動できない版（Windowsで使用中など）は複製して退避し、CLIが削除した後に同じパスへ戻す。"""
    _update_with_locked_version(plugin_env, monkeypatch, [], copy_fails=False)

    assert {path.name for path in _cache().iterdir()} == {"1.2.1", "1.2.2", "1.2.3"}
    assert (_cache() / "1.2.1/marker").read_text(encoding="utf-8") == "1.2.1"
    assert (_cache() / "1.2.2/marker").read_text(encoding="utf-8") == "1.2.2"
    assert not _saved_root().exists()


def test_snapshot_failure_returns_moved_versions(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """退避の途中で失敗した場合は、移動済みの版を元へ戻してから失敗を伝え、`plugin add`を実行しない。"""
    calls: list[list[str]] = []
    with pytest.raises(OSError, match="injected copy failure"):
        _update_with_locked_version(plugin_env, monkeypatch, calls, copy_fails=True)

    assert (_cache() / "1.2.1/marker").read_text(encoding="utf-8") == "1.2.1"
    assert (_cache() / "1.2.2/marker").read_text(encoding="utf-8") == "1.2.2"
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls
    assert not _saved_root().exists()


def test_cache_on_other_volume_is_copied_back(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cacheと退避先が別ボリュームで名前の変更ができない場合も、複製で退避して同じパスへ戻す。"""
    for version in ("1.2.1", "1.2.2"):
        (_cache() / version).mkdir(parents=True)
        (_cache() / version / "marker").write_text(version, encoding="utf-8")
    original_rename = Path.rename

    def rename(self: Path, target: Path) -> Path:
        if self.is_relative_to(_saved_root()) != Path(target).is_relative_to(_saved_root()):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return original_rename(self, target)

    current: dict[str, Any] = {"version": "1.2.2", "enabled": True}

    def command(args: list[str]) -> bool:
        if args[:2] == ["plugin", "add"]:
            shutil.rmtree(_cache())
            (_cache() / "1.2.3/agent_toolkit").mkdir(parents=True)
            (_cache() / "1.2.3/agent_toolkit/hook.py").write_text("hook 1.2.3", encoding="utf-8")
            current.update(version="1.2.3")
        return True

    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(
        install_codex_plugins,
        "_codex_json",
        lambda args: _local_marketplace(plugin_env) if args[1] == "marketplace" else _installed_state(**current),
    )
    monkeypatch.setattr(install_codex_plugins, "_command", command)
    install_codex_plugins.run()

    assert {path.name for path in _cache().iterdir()} == {"1.2.1", "1.2.2", "1.2.3"}
    assert (_cache() / "1.2.1/marker").read_text(encoding="utf-8") == "1.2.1"
    assert (_cache() / "1.2.2/marker").read_text(encoding="utf-8") == "1.2.2"
    assert not _saved_root().exists()


def test_interrupted_rollback_keeps_saved_version_for_next_run(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """plugin add失敗後の巻き戻しが元のパスを途中まで消して失敗しても、次回の実行が退避した内容で置き換える。"""
    (_cache() / "1.2.1").mkdir(parents=True)
    (_cache() / "1.2.1/marker").write_text("1.2.1", encoding="utf-8")
    locked = _cache() / "1.2.1"
    original_rename = Path.rename
    original_rmtree = shutil.rmtree

    def rename(self: Path, target: Path) -> Path:
        if self == locked:
            raise PermissionError("in use")
        return original_rename(self, target)

    def rmtree(path: Any, ignore_errors: bool = False) -> None:
        if Path(path) == locked:
            # Windowsでは開かれていないファイルだけが消え、使用中のファイルで削除が止まる。
            (locked / "marker").unlink(missing_ok=True)
            raise PermissionError("in use")
        original_rmtree(path, ignore_errors=ignore_errors)

    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2")])
    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(install_codex_plugins.shutil, "rmtree", rmtree)
    monkeypatch.setattr(install_codex_plugins, "_command", lambda args: args[:2] != ["plugin", "add"])
    with caplog.at_level("WARNING"):
        install_codex_plugins.run()
    assert "plugin addと旧cacheの復旧に失敗" in caplog.text
    assert not (locked / "marker").exists()

    monkeypatch.setattr(Path, "rename", original_rename)
    monkeypatch.setattr(install_codex_plugins.shutil, "rmtree", original_rmtree)
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success([]))
    install_codex_plugins.run()

    assert (locked / "marker").read_text(encoding="utf-8") == "1.2.1"
    assert not _saved_root().exists()


def test_disabled_plugin_still_recovers_saved_versions(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """無効化したpluginの版更新は保留しても、前回の実行が残した退避は戻す。"""
    saved = _saved_root() / "ak110-dotfiles/agent-toolkit/1.2.1"
    saved.mkdir(parents=True)
    (saved / "marker").write_text("1.2.1", encoding="utf-8")
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state(version="1.2.2", enabled=False)])
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    install_codex_plugins.run()

    assert (_cache() / "1.2.1/marker").read_text(encoding="utf-8") == "1.2.1"
    assert not _saved_root().exists()
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls


def test_interrupted_restore_leftovers_are_cleaned_by_next_run(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """復元の途中で中断した実行が残した印と複製は、次回の実行が片付けて退避先を空にする。

    版を元のパスへ戻した直後の中断では退避の無い`~replace`の印だけが残り、
    複製の途中の中断後に元のパスが再作成されると`~restoring`の複製が残る。
    """
    saved = _saved_root() / "ak110-dotfiles/agent-toolkit"
    saved.mkdir(parents=True)
    (saved / "1.2.0~replace").touch()
    (saved / "1.2.1").mkdir()
    (saved / "1.2.1/marker").write_text("1.2.1", encoding="utf-8")
    for version in ("1.2.0", "1.2.1", "1.2.1~restoring"):
        (_cache() / version).mkdir(parents=True)
        (_cache() / version / "marker").write_text(version, encoding="utf-8")
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success([]))

    install_codex_plugins.run()

    assert {path.name for path in _cache().iterdir()} == {"1.2.0", "1.2.1", "1.2.3"}
    assert not _saved_root().exists()


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
