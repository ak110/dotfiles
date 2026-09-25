"""prune_claude_plugin_cacheのテスト。

期待値は「現行版と、後継版の導入から7日以内の版と、後継の無い版を残し、それ以外の非現行版を削除する」
という判定規則から導く。導入時刻は各版の`plugin.json`のmtime（欠く場合は版ディレクトリのmtime）とする。
"""

import json
import os
import time
from pathlib import Path

import pytest

from pytools._internal import prune_claude_plugin_cache

_DAY = 86400


@pytest.fixture(name="plugins_root")
def _plugins_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "plugins"
    (root / "cache").mkdir(parents=True)
    monkeypatch.setattr(prune_claude_plugin_cache, "_INSTALLED_PLUGINS_PATH", root / "installed_plugins.json")
    return root


def _write_installed(plugins_root: Path, install_paths: dict[str, list[Path]]) -> None:
    data = {
        "version": 2,
        "plugins": {
            key: [{"scope": "user", "installPath": str(path), "version": path.name} for path in paths]
            for key, paths in install_paths.items()
        },
    }
    (plugins_root / "installed_plugins.json").write_text(json.dumps(data), encoding="utf-8")


def _make_version(
    plugins_root: Path, marketplace: str, name: str, version: str, days_ago: float, *, manifest: bool = True
) -> Path:
    """導入時刻が`days_ago`日前の版ディレクトリを作成する。版ディレクトリ自体のmtimeは現在時刻のまま残す。"""
    version_dir = plugins_root / "cache" / marketplace / name / version
    (version_dir / ".venv").mkdir(parents=True)
    installed_at = time.time() - days_ago * _DAY
    if manifest:
        manifest_path = version_dir / ".claude-plugin" / "plugin.json"
        manifest_path.parent.mkdir()
        manifest_path.write_text("{}", encoding="utf-8")
        os.utime(manifest_path, (installed_at, installed_at))
    else:
        os.utime(version_dir, (installed_at, installed_at))
    return version_dir


def test_removes_only_versions_superseded_more_than_grace_days_ago(plugins_root: Path) -> None:
    old = _make_version(plugins_root, "mkt", "tool", "1.0.0", 30)
    superseded_long_ago = _make_version(plugins_root, "mkt", "tool", "1.1.0", 20)
    superseded_recently = _make_version(plugins_root, "mkt", "tool", "1.2.0", 10)
    current = _make_version(plugins_root, "mkt", "tool", "1.3.0", 3)
    _write_installed(plugins_root, {"tool@mkt": [current]})

    assert prune_claude_plugin_cache.run() is True

    # 1.0.0と1.1.0は後継の導入（20日前・10日前）から7日を超えたため削除する。
    assert not old.exists()
    assert not superseded_long_ago.exists()
    # 1.2.0の後継1.3.0は3日前の導入で猶予内のため残す。
    assert superseded_recently.is_dir()
    assert current.is_dir()


def test_keeps_newest_non_current_version_without_successor(plugins_root: Path) -> None:
    current = _make_version(plugins_root, "mkt", "tool", "1.0.0", 30)
    newest = _make_version(plugins_root, "mkt", "tool", "2.0.0", 20)
    _write_installed(plugins_root, {"tool@mkt": [current]})

    assert prune_claude_plugin_cache.run() is False

    assert current.is_dir()
    assert newest.is_dir()


def test_ignores_plugins_missing_from_installed_list(plugins_root: Path) -> None:
    other_old = _make_version(plugins_root, "mkt", "other", "1.0.0", 30)
    _make_version(plugins_root, "mkt", "other", "2.0.0", 20)
    current = _make_version(plugins_root, "mkt", "tool", "1.0.0", 3)
    _write_installed(plugins_root, {"tool@mkt": [current]})

    assert prune_claude_plugin_cache.run() is False

    assert other_old.is_dir()


def test_does_not_follow_or_remove_symlinked_versions(plugins_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside / ".claude-plugin").mkdir(parents=True)
    manifest = outside / ".claude-plugin" / "plugin.json"
    manifest.write_text("{}", encoding="utf-8")
    long_ago = time.time() - 40 * _DAY
    os.utime(manifest, (long_ago, long_ago))
    link = plugins_root / "cache" / "mkt" / "tool" / "0.9.0"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)
    _make_version(plugins_root, "mkt", "tool", "1.0.0", 30)
    current = _make_version(plugins_root, "mkt", "tool", "2.0.0", 20)
    _write_installed(plugins_root, {"tool@mkt": [current]})

    prune_claude_plugin_cache.run()

    assert link.is_symlink()
    assert manifest.is_file()


def test_uses_directory_mtime_when_manifest_is_missing(plugins_root: Path) -> None:
    incomplete = _make_version(plugins_root, "mkt", "tool", "0.1.0", 40, manifest=False)
    current = _make_version(plugins_root, "mkt", "tool", "1.0.0", 30)
    _write_installed(plugins_root, {"tool@mkt": [current]})

    assert prune_claude_plugin_cache.run() is True

    assert not incomplete.exists()
    assert current.is_dir()


@pytest.mark.parametrize("content", [None, "{broken", json.dumps({"version": 2, "plugins": []})])
def test_does_nothing_when_installed_list_is_unavailable(plugins_root: Path, content: str | None) -> None:
    old = _make_version(plugins_root, "mkt", "tool", "1.0.0", 30)
    _make_version(plugins_root, "mkt", "tool", "2.0.0", 20)
    if content is not None:
        (plugins_root / "installed_plugins.json").write_text(content, encoding="utf-8")

    assert prune_claude_plugin_cache.run() is False

    assert old.is_dir()


def test_repeated_runs_keep_current_and_recently_superseded_versions(plugins_root: Path) -> None:
    recent = _make_version(plugins_root, "mkt", "tool", "1.0.0", 6)
    current = _make_version(plugins_root, "mkt", "tool", "1.1.0", 5)
    _write_installed(plugins_root, {"tool@mkt": [current]})

    for _ in range(2):
        prune_claude_plugin_cache.run()
        assert recent.is_dir()
        assert current.is_dir()


def test_orders_versions_by_install_time_not_by_name(plugins_root: Path) -> None:
    """commit hashなど自然順に並ばない版名でも導入時刻で後継を決める。"""
    older = _make_version(plugins_root, "official", "tool", "ffff000", 30)
    current = _make_version(plugins_root, "official", "tool", "0000aaa", 20)
    _write_installed(plugins_root, {"tool@official": [current]})

    assert prune_claude_plugin_cache.run() is True

    assert not older.exists()
    assert current.is_dir()
