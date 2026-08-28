"""install_codex_pluginsのテスト。"""

import json
import os
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
        )
    )
    (root / "agent-toolkit/.codex-plugin/plugin.json").write_text(json.dumps({"name": "agent-toolkit", "version": "1.2.3"}))
    (root / "agent-toolkit/scripts").mkdir()
    (root / "agent-toolkit/scripts/claude_hook.py").write_text("source", encoding="utf-8")
    (root / "agent-toolkit/skills").mkdir()
    (root / "agent-toolkit/plugin-note.txt").write_text("source-file", encoding="utf-8")
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
    (path / ".codex-plugin").mkdir(parents=True, exist_ok=True)
    (path / ".codex-plugin/plugin.json").write_text(json.dumps({"name": "agent-toolkit", "version": version}), encoding="utf-8")
    (path / "scripts").mkdir(parents=True, exist_ok=True)
    (path / "scripts/claude_hook.py").write_text(hook, encoding="utf-8")
    (path / "skills").mkdir(exist_ok=True)
    (path / "plugin-note.txt").write_text(hook, encoding="utf-8")
    return path


def _source_connected_version(plugin_env: Path, version: str = "1.2.3") -> Path:
    """原本の各直下資源へ相対リンクした通常versionディレクトリを作成する。"""
    path = _cache_root() / version
    if path.exists():
        shutil.rmtree(path)
    elif path.is_symlink():
        path.unlink()
    path.mkdir(parents=True, exist_ok=True)
    source = plugin_env / "agent-toolkit"
    for source_entry in source.iterdir():
        destination = path / source_entry.name
        relative_target = Path(os.path.relpath(source_entry, start=destination.parent))
        destination.symlink_to(relative_target, target_is_directory=source_entry.is_dir())
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
    current = _cache_root() / "1.2.3"
    assert current.is_dir()
    assert not current.is_symlink()
    assert (current / "scripts").resolve() == (plugin_env / "agent-toolkit/scripts").resolve()
    assert (current / "plugin-note.txt").resolve() == (plugin_env / "agent-toolkit/plugin-note.txt").resolve()
    assert _versions_path().read_text(encoding="utf-8") == "1.2.3\n"
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
    responses = iter([{"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]}, state, state])
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))
    outcome = install_codex_plugins.run()
    assert outcome.changed is True
    assert not outcome.notices
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls
    assert ["app-server", "daemon", "version"] not in calls
    assert not destination.is_symlink()


def test_same_version_with_correct_source_connection_is_noop(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """正しい原本接続と台帳がある同version再実行は変更しない。"""
    _source_connected_version(plugin_env)
    _versions_path().parent.mkdir(parents=True)
    _versions_path().write_text("1.2.3\n", encoding="utf-8")
    state = _installed_state()
    responses = iter([{"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]}, state])
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    assert outcome.changed is False
    assert not outcome.notices
    assert not calls


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
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls
    assert (_cache_root() / "1.2.3").is_dir()


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
            _installed_state(),
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
    """更新で消えた実versionと互換リンクを同じ原本へ接続する。"""
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
        assert restored.is_dir()
        assert not restored.is_symlink()
        assert (restored / "scripts/claude_hook.py").read_text(encoding="utf-8") == "source"
    assert previous != old


def test_same_version_restores_from_external_ledger(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同version再実行でもcache外の台帳から中断済みの復元を完了する。"""
    _cache_version("1.2.3", hook="current")
    _versions_path().parent.mkdir(parents=True)
    _versions_path().write_text("1.2.1\ninvalid/name\n", encoding="utf-8")
    state = _installed_state()
    responses = iter([{"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]}, state, state])
    calls: list[list[str]] = []
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success(calls))

    outcome = install_codex_plugins.run()

    restored = _cache_root() / "1.2.1"
    assert outcome.changed is True
    assert (restored / "scripts/claude_hook.py").resolve() == (plugin_env / "agent-toolkit/scripts/claude_hook.py").resolve()
    assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls


def test_codex_home_environment_controls_cache_paths(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CODEX_HOME指定時は固定ホームではなく指定先のcacheと台帳を使う。"""
    codex_home = plugin_env.parent / "custom-codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    current = codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.3"
    for name in (".codex-plugin", "scripts", "skills"):
        (current / name).parent.mkdir(parents=True, exist_ok=True)
        (current / name).symlink_to(plugin_env / "agent-toolkit" / name, target_is_directory=True)
    versions = codex_home / "plugins/cache-compat/ak110-dotfiles/agent-toolkit/versions"
    versions.parent.mkdir(parents=True)
    versions.write_text("1.2.2\n", encoding="utf-8")
    state = _installed_state()
    responses = iter([{"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]}, state, state])
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", lambda _: True)

    install_codex_plugins.run()

    assert (codex_home / "plugins/cache/ak110-dotfiles/agent-toolkit/1.2.2/scripts/claude_hook.py").resolve() == (
        plugin_env / "agent-toolkit/scripts/claude_hook.py"
    ).resolve()


def test_cache_conflict_propagates_without_replacing_entry(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """plugin構造を確認できない通常エントリは保持して失敗を呼び出し元へ伝える。"""
    _cache_version("1.2.3")
    conflict = _cache_root() / "1.2.2"
    (conflict / "scripts").mkdir(parents=True)
    (conflict / "scripts/claude_hook.py").write_text("keep", encoding="utf-8")
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
    with pytest.raises(RuntimeError, match="plugin addに失敗"):
        install_codex_plugins.run()
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


def test_post_install_failure_restores_cache_ledger_and_legacy_link(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """導入後の状態検証失敗時にcache、台帳及びlegacyリンクを復元する。"""
    current = _cache_root() / "1.2.3"
    old = _cache_version("1.2.2", hook="old")
    destination = _legacy_link(plugin_env)
    original_current = (current / "scripts/claude_hook.py").read_text(encoding="utf-8")
    versions = _versions_path()
    versions.parent.mkdir(parents=True)
    versions.write_bytes(b"1.2.2\n")
    responses: Iterator[dict[str, Any] | None] = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            {"installed": []},
            None,
        ]
    )
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success([]))

    with pytest.raises(RuntimeError) as exc_info:
        install_codex_plugins.run()

    assert "codex app-server daemon restart" in str(exc_info.value)
    assert (current / "scripts/claude_hook.py").read_text(encoding="utf-8") == original_current
    assert (old / "scripts/claude_hook.py").read_text(encoding="utf-8") == "old"
    assert versions.read_bytes() == b"1.2.2\n"
    assert destination.is_symlink()
    assert destination.resolve() == (plugin_env / "agent-toolkit/skills/coding").resolve()


def test_partial_cache_backup_failure_restores_every_entry(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cache退避と局所巻き戻しの連続失敗後も全entryを復元する。"""
    _cache_version("1.2.2", hook="previous")
    unrelated = _cache_root() / "metadata.txt"
    unrelated.write_text("keep", encoding="utf-8")
    expected = {
        "1.2.3": (_cache_root() / "1.2.3/scripts/claude_hook.py").read_bytes(),
        "1.2.2": (_cache_root() / "1.2.2/scripts/claude_hook.py").read_bytes(),
        "metadata.txt": unrelated.read_bytes(),
    }
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            _installed_state(),
        ]
    )
    original_rename = Path.rename
    backup_moves = 0
    rollback_failed = False

    def fail_backup_and_first_rollback(source: Path, target: str | Path) -> Path:
        nonlocal backup_moves, rollback_failed
        target_path = Path(target)
        if source.parent == _cache_root() and target_path.parent.name == "cache-backup":
            backup_moves += 1
            if backup_moves == 3:
                raise OSError("injected cache backup failure")
        elif source.parent.name == "cache-backup" and not rollback_failed:
            rollback_failed = True
            raise OSError("injected cache rollback failure")
        return original_rename(source, target)

    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(Path, "rename", fail_backup_and_first_rollback)

    with pytest.raises(OSError) as exc_info:
        install_codex_plugins.run()

    error_message = str(exc_info.value)
    actual = {
        "1.2.3": (
            (_cache_root() / "1.2.3/scripts/claude_hook.py").read_bytes()
            if (_cache_root() / "1.2.3/scripts/claude_hook.py").exists()
            else None
        ),
        "1.2.2": (
            (_cache_root() / "1.2.2/scripts/claude_hook.py").read_bytes()
            if (_cache_root() / "1.2.2/scripts/claude_hook.py").exists()
            else None
        ),
        "metadata.txt": unrelated.read_bytes() if unrelated.exists() else None,
    }
    assert (
        "injected cache backup failure" in error_message,
        "injected cache rollback failure" in error_message,
        actual,
    ) == (True, True, expected)


def test_unrestored_partial_cache_backup_is_preserved(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """共通復元も失敗したentryは一時領域の削除から保護する。"""
    _cache_version("1.2.2", hook="previous")
    (_cache_root() / "metadata.txt").write_text("keep", encoding="utf-8")
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            _installed_state(),
        ]
    )
    original_rename = Path.rename
    backup_moves = 0
    failed_backup_name: str | None = None

    def fail_backup_and_restore(source: Path, target: str | Path) -> Path:
        nonlocal backup_moves, failed_backup_name
        target_path = Path(target)
        if source.parent == _cache_root() and target_path.parent.name == "cache-backup":
            backup_moves += 1
            if backup_moves == 3:
                raise OSError("injected cache backup failure")
        elif source.parent.name == "cache-backup":
            failed_backup_name = source.name
            raise OSError(f"injected persistent cache restore failure: {source.name}")
        return original_rename(source, target)

    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(Path, "rename", fail_backup_and_restore)

    with pytest.raises(OSError) as exc_info:
        install_codex_plugins.run()

    assert failed_backup_name is not None
    stage_roots = tuple((install_codex_plugins.CODEX_HOME / "plugins").glob(".agent-toolkit-*"))
    assert len(stage_roots) == 1
    assert (stage_roots[0] / "cache-backup" / failed_backup_name).exists()
    assert "injected cache backup failure" in str(exc_info.value)
    assert "injected persistent cache restore failure" in str(exc_info.value)
    assert f"未復元のcache退避物を保持: {stage_roots[0]}" in str(exc_info.value)


def test_keyboard_interrupt_during_partial_cache_restore_preserves_primary_error_and_backup(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """退避巻き戻しの中断後も一次例外と未復元entryを保持する。"""
    _cache_version("1.2.2", hook="previous")
    (_cache_root() / "metadata.txt").write_text("keep", encoding="utf-8")
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            _installed_state(),
        ]
    )
    original_rename = Path.rename
    backup_moves = 0

    def interrupt_cache_restore(source: Path, target: str | Path) -> Path:
        nonlocal backup_moves
        target_path = Path(target)
        if source.parent == _cache_root() and target_path.parent.name == "cache-backup":
            backup_moves += 1
            if backup_moves == 3:
                raise OSError("injected cache backup failure")
        elif source.parent.name == "cache-backup":
            raise KeyboardInterrupt(f"injected cache restore interrupt: {source.name}")
        return original_rename(source, target)

    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(Path, "rename", interrupt_cache_restore)

    with pytest.raises(BaseException) as exc_info:
        install_codex_plugins.run()

    stage_roots = tuple((install_codex_plugins.CODEX_HOME / "plugins").glob(".agent-toolkit-*"))
    cache_names = {entry.name for entry in _cache_root().iterdir()}
    backup_names = {entry.name for entry in (stage_roots[0] / "cache-backup").iterdir()} if stage_roots else set()
    assert (isinstance(exc_info.value, OSError), cache_names, len(stage_roots), backup_names) == (
        True,
        {"metadata.txt"},
        1,
        {"1.2.2", "1.2.3"},
    )
    assert "injected cache backup failure" in str(exc_info.value)
    assert "injected cache restore interrupt" in str(exc_info.value)
    assert f"未復元のcache退避物を保持: {stage_roots[0]}" in str(exc_info.value)


def test_keyboard_interrupt_during_cache_restore_continues_other_state_restoration(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cache復元の中断後も残りのcache、台帳及びlegacy linkを復元する。"""
    _cache_version("1.2.2", hook="previous")
    (_cache_root() / "metadata.txt").write_text("keep", encoding="utf-8")
    original_names = {entry.name for entry in _cache_root().iterdir()}
    versions = _versions_path()
    versions.parent.mkdir(parents=True)
    versions.write_bytes(b"1.2.0\n")
    first = _legacy_link(plugin_env, "coding")
    second = _legacy_link(plugin_env, "writing")
    state = _installed_state()
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            state,
            state,
        ]
    )
    original_remove = install_codex_plugins._remove_legacy_links  # pylint: disable=protected-access
    original_rename = Path.rename
    interrupted_name: str | None = None

    def remove_then_fail(root: Path) -> bool:
        original_remove(root)
        raise OSError("injected post-removal failure")

    def interrupt_first_cache_restore(source: Path, target: str | Path) -> Path:
        nonlocal interrupted_name
        if source.parent.name == "cache-backup" and interrupted_name is None:
            interrupted_name = source.name
            raise KeyboardInterrupt(f"injected cache restore interrupt: {source.name}")
        return original_rename(source, target)

    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_remove_legacy_links", remove_then_fail)
    monkeypatch.setattr(Path, "rename", interrupt_first_cache_restore)

    with pytest.raises(BaseException) as exc_info:
        install_codex_plugins.run()

    assert interrupted_name is not None
    stage_roots = tuple((install_codex_plugins.CODEX_HOME / "plugins").glob(".agent-toolkit-*"))
    backup_names = {entry.name for entry in (stage_roots[0] / "cache-backup").iterdir()} if stage_roots else set()
    cache_names = {entry.name for entry in _cache_root().iterdir()}
    assert isinstance(exc_info.value, OSError)
    assert len(stage_roots) == 1
    assert backup_names == {interrupted_name}
    assert cache_names | backup_names == original_names
    assert versions.read_bytes() == b"1.2.0\n"
    assert first.is_symlink()
    assert second.is_symlink()
    assert "injected post-removal failure" in str(exc_info.value)
    assert "injected cache restore interrupt" in str(exc_info.value)
    assert f"未復元のcache退避物を保持: {stage_roots[0]}" in str(exc_info.value)


@pytest.mark.parametrize("interrupted_location", ["backup", "destination"])
def test_cache_restore_presence_interrupt_continues_other_entries(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    interrupted_location: str,
) -> None:
    """退避元・復元先の存在確認中断後も他entryと管理状態を復元する。"""
    _cache_version("1.2.2", hook="previous")
    (_cache_root() / "metadata.txt").write_text("keep", encoding="utf-8")
    original_names = {entry.name for entry in _cache_root().iterdir()}
    versions = _versions_path()
    versions.parent.mkdir(parents=True)
    versions.write_bytes(b"1.2.0\n")
    first = _legacy_link(plugin_env, "coding")
    second = _legacy_link(plugin_env, "writing")
    state = _installed_state()
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            state,
            state,
        ]
    )
    original_remove = install_codex_plugins._remove_legacy_links  # pylint: disable=protected-access
    original_path_exists = install_codex_plugins._path_exists  # pylint: disable=protected-access
    restoring = False
    destination_check_name: str | None = None
    interrupted_name: str | None = None

    def remove_then_fail(root: Path) -> bool:
        nonlocal restoring
        original_remove(root)
        restoring = True
        raise OSError("injected post-removal failure")

    def interrupt_presence_check(path: Path) -> bool:
        nonlocal destination_check_name, interrupted_name
        is_backup = path.parent.name == "cache-backup"
        is_destination = path.parent == _cache_root()
        if restoring and interrupted_location == "destination" and destination_check_name is None and is_backup:
            destination_check_name = path.name
            return False
        if (
            restoring
            and interrupted_name is None
            and (
                (interrupted_location == "backup" and is_backup)
                or (interrupted_location == "destination" and is_destination and path.name == destination_check_name)
            )
        ):
            interrupted_name = path.name
            raise KeyboardInterrupt(f"injected {interrupted_location} presence interrupt: {path.name}")
        return original_path_exists(path)

    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_remove_legacy_links", remove_then_fail)
    monkeypatch.setattr(install_codex_plugins, "_path_exists", interrupt_presence_check)

    with pytest.raises(BaseException) as exc_info:
        install_codex_plugins.run()

    assert interrupted_name is not None
    stage_roots = tuple((install_codex_plugins.CODEX_HOME / "plugins").glob(".agent-toolkit-*"))
    backup_names = {entry.name for entry in (stage_roots[0] / "cache-backup").iterdir()} if stage_roots else set()
    cache_names = {entry.name for entry in _cache_root().iterdir()}
    assert isinstance(exc_info.value, OSError)
    assert len(stage_roots) == 1
    assert backup_names == {interrupted_name}
    assert cache_names | backup_names == original_names
    assert versions.read_bytes() == b"1.2.0\n"
    assert first.is_symlink()
    assert second.is_symlink()
    assert "injected post-removal failure" in str(exc_info.value)
    assert f"injected {interrupted_location} presence interrupt" in str(exc_info.value)
    assert f"未復元のcache退避物を保持: {stage_roots[0]}" in str(exc_info.value)


def test_absent_cache_root_restore_interrupt_continues_other_entries(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """元にcache rootが無い復元でも1 entryの除去中断後に残りを除去する。"""
    shutil.rmtree(_cache_root())
    versions = _versions_path()
    versions.parent.mkdir(parents=True)
    versions.write_bytes(b"1.2.1\n1.2.2\n")
    first = _legacy_link(plugin_env, "coding")
    second = _legacy_link(plugin_env, "writing")
    state = _installed_state()
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            state,
            state,
        ]
    )
    original_remove_links = install_codex_plugins._remove_legacy_links  # pylint: disable=protected-access
    original_remove_entry = install_codex_plugins._remove_entry  # pylint: disable=protected-access
    restoring = False
    attempted_names: list[str] = []

    def remove_then_fail(root: Path) -> bool:
        nonlocal restoring
        original_remove_links(root)
        restoring = True
        raise OSError("injected post-removal failure")

    def interrupt_first_entry_removal(path: Path) -> None:
        if restoring and path.parent == _cache_root():
            attempted_names.append(path.name)
            if len(attempted_names) == 1:
                raise KeyboardInterrupt(f"injected cache entry removal interrupt: {path.name}")
        original_remove_entry(path)

    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_remove_legacy_links", remove_then_fail)
    monkeypatch.setattr(install_codex_plugins, "_remove_entry", interrupt_first_entry_removal)

    with pytest.raises(BaseException) as exc_info:
        install_codex_plugins.run()

    stage_roots = tuple((install_codex_plugins.CODEX_HOME / "plugins").glob(".agent-toolkit-*"))
    assert isinstance(exc_info.value, OSError)
    assert set(attempted_names) == {"1.2.1", "1.2.2", "1.2.3"}
    assert not _cache_root().exists()
    assert versions.read_bytes() == b"1.2.1\n1.2.2\n"
    assert first.is_symlink()
    assert second.is_symlink()
    assert not stage_roots
    assert "injected post-removal failure" in str(exc_info.value)
    assert "injected cache entry removal interrupt" in str(exc_info.value)


def test_cleanup_keyboard_interrupt_is_appended_to_primary_error(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一時領域回収の中断で一次例外を上書きしない。"""
    state = _installed_state()
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            state,
        ]
    )
    original_rmtree = shutil.rmtree

    def fail_verification(_: str, __: str) -> None:
        raise OSError("injected primary failure")

    def interrupt_stage_cleanup(path: str | Path, *args: Any, **kwargs: Any) -> None:
        if Path(path).name.startswith(".agent-toolkit-"):
            raise KeyboardInterrupt("injected cleanup interrupt")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_verify_expected_state", fail_verification)
    monkeypatch.setattr(install_codex_plugins.shutil, "rmtree", interrupt_stage_cleanup)

    with pytest.raises(BaseException) as exc_info:
        install_codex_plugins.run()

    assert isinstance(exc_info.value, OSError)
    assert "injected primary failure" in str(exc_info.value)
    assert "injected cleanup interrupt" in str(exc_info.value)


def test_backup_inspection_keyboard_interrupt_preserves_stage_root(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """退避物の確認を中断した場合は未確認の一時領域を保持する。"""
    state = _installed_state()
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            state,
        ]
    )
    original_iterdir = Path.iterdir

    def fail_verification(_: str, __: str) -> None:
        raise OSError("injected primary failure")

    def interrupt_backup_inspection(path: Path) -> Iterator[Path]:
        if path.name == "cache-backup":
            raise KeyboardInterrupt("injected backup inspection interrupt")
        return original_iterdir(path)

    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_verify_expected_state", fail_verification)
    monkeypatch.setattr(Path, "iterdir", interrupt_backup_inspection)

    with pytest.raises(BaseException) as exc_info:
        install_codex_plugins.run()

    stage_roots = tuple((install_codex_plugins.CODEX_HOME / "plugins").glob(".agent-toolkit-*"))
    assert isinstance(exc_info.value, OSError)
    assert len(stage_roots) == 1
    assert "injected primary failure" in str(exc_info.value)
    assert "injected backup inspection interrupt" in str(exc_info.value)
    assert f"cache退避物の確認失敗により一時領域を保持: {stage_roots[0]}" in str(exc_info.value)


def test_ledger_restore_keyboard_interrupt_continues_legacy_restoration(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """台帳復元の中断後もlegacy linkを復元して一次例外を保持する。"""
    versions = _versions_path()
    versions.parent.mkdir(parents=True)
    versions.write_bytes(b"1.2.0\n")
    first = _legacy_link(plugin_env, "coding")
    second = _legacy_link(plugin_env, "writing")
    state = _installed_state()
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            state,
            state,
        ]
    )
    original_remove = install_codex_plugins._remove_legacy_links  # pylint: disable=protected-access
    original_write_bytes = Path.write_bytes

    def remove_then_fail(root: Path) -> bool:
        original_remove(root)
        raise OSError("injected post-removal failure")

    def interrupt_ledger_restore(path: Path, data: bytes) -> int:
        if path == versions and data == b"1.2.0\n":
            raise KeyboardInterrupt("injected ledger restore interrupt")
        return original_write_bytes(path, data)

    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_remove_legacy_links", remove_then_fail)
    monkeypatch.setattr(Path, "write_bytes", interrupt_ledger_restore)

    with pytest.raises(BaseException) as exc_info:
        install_codex_plugins.run()

    assert isinstance(exc_info.value, OSError)
    assert first.is_symlink()
    assert second.is_symlink()
    assert "injected post-removal failure" in str(exc_info.value)
    assert "injected ledger restore interrupt" in str(exc_info.value)


@pytest.mark.parametrize("restore_error_type", [OSError, KeyboardInterrupt])
def test_legacy_link_restore_failure_does_not_skip_other_links(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    restore_error_type: type[BaseException],
) -> None:
    """1件のlegacy link復元失敗後も他のlinkを復元する。"""
    first = _legacy_link(plugin_env, "coding")
    second = _legacy_link(plugin_env, "writing")
    state = _installed_state()
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            state,
            state,
        ]
    )
    original_remove = install_codex_plugins._remove_legacy_links  # pylint: disable=protected-access
    original_symlink_to = Path.symlink_to
    restore_failed = False

    def remove_then_fail(root: Path) -> bool:
        original_remove(root)
        raise OSError("injected post-removal failure")

    def fail_first_restore(
        destination: Path,
        target: str | Path,
        target_is_directory: bool = False,
    ) -> None:
        nonlocal restore_failed
        if destination.parent == first.parent and not restore_failed:
            restore_failed = True
            raise restore_error_type("injected legacy restore failure")
        original_symlink_to(destination, target, target_is_directory=target_is_directory)

    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_remove_legacy_links", remove_then_fail)
    monkeypatch.setattr(Path, "symlink_to", fail_first_restore)

    with pytest.raises(BaseException) as exc_info:
        install_codex_plugins.run()

    assert isinstance(exc_info.value, OSError)
    assert "injected post-removal failure" in str(exc_info.value)
    assert "injected legacy restore failure" in str(exc_info.value)
    assert first.is_symlink() is False
    assert second.is_symlink() is True


def test_legacy_link_failure_restores_cache_and_absent_ledger(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """legacyリンク除去失敗時に配置と不在だった台帳を復元する。"""
    destination = _legacy_link(plugin_env)
    current = _cache_root() / "1.2.3"
    original_current = (current / "scripts/claude_hook.py").read_text(encoding="utf-8")
    state = _installed_state()
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            state,
            state,
        ]
    )
    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success([]))

    def fail_remove(_: Path) -> bool:
        raise OSError("injected legacy removal failure")

    monkeypatch.setattr(install_codex_plugins, "_remove_legacy_links", fail_remove)

    with pytest.raises(OSError, match="injected legacy removal failure"):
        install_codex_plugins.run()

    assert (current / "scripts/claude_hook.py").read_text(encoding="utf-8") == original_current
    assert not _versions_path().exists()
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


def test_windows_syncs_files_and_uses_directory_links(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows相当の更新でディレクトリリンクと通常ファイル同期を使う。"""
    responses = iter(
        [
            {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]},
            _installed_state(),
            _installed_state(),
        ]
    )
    directory_calls: list[str] = []

    def sync_directory_link(destination: Path, target: Path) -> bool:
        directory_calls.append(target.name)
        destination.symlink_to(target, target_is_directory=True)
        return True

    monkeypatch.setattr(install_codex_plugins, "_codex_json", lambda _: next(responses))
    monkeypatch.setattr(install_codex_plugins, "_command", lambda _: True)
    monkeypatch.setattr(install_codex_plugins.sys, "platform", "win32")
    monkeypatch.setattr(
        install_codex_plugins.setup_codex_links,
        "sync_directory_link",
        sync_directory_link,
    )

    outcome = install_codex_plugins.run()

    current = _cache_root() / "1.2.3"
    assert outcome.changed is True
    assert set(directory_calls) == {".codex-plugin", "scripts", "skills"}
    assert (current / "plugin-note.txt").is_file()
    assert not (current / "plugin-note.txt").is_symlink()
    assert (current / "plugin-note.txt").read_text(encoding="utf-8") == "source-file"


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
        assert calls[:3] == [
            ["json", "plugin", "marketplace", "list", "--json"],
            ["plugin", "marketplace", "add", self._TARGET[1]],
            ["json", "plugin", "marketplace", "list", "--json"],
        ]
        assert not destination.is_symlink()

    def test_skips_when_already_added(self, plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._setup_run(plugin_env, monkeypatch, registered=True, installed=True)

        outcome = install_codex_plugins.run()
        assert outcome.changed is True
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
        assert outcome.changed is True
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
        assert outcome.changed is True
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
        assert outcome.changed is True
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
        assert outcome.changed is True
        assert not outcome.notices
        assert ["plugin", "marketplace", "add", self._TARGET[1]] in calls
        assert ["plugin", "add", "agent-toolkit@ak110-dotfiles"] not in calls
