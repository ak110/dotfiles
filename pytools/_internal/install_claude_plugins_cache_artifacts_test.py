"""plugin cache の版ディレクトリから開発用生成物を除く処理のテスト。

directory型marketplaceからの`claude plugin install`は主作業ツリーのgitignore対象（`.venv`など）ごと
版ディレクトリへ複製する。導入後処理`install_claude_plugins.run()`を通して、cacheの内側では生成物だけを除き、
配布物・cacheの外・symlink・参照中の生成物を残すことを確かめる。
"""

# pylint: disable=protected-access

import json
import pathlib
import shutil
import sys
import typing

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import claude_marketplace as _claude_marketplace
from pytools._internal import claude_plugin_references as _claude_plugin_references
from pytools._internal import common as _common
from pytools._internal import deferred_updates as _deferred_updates
from pytools._internal import install_claude_plugins as _install_claude_plugins

from ._test_helpers import ResidentProcesses

_NAME = "agent-toolkit"
_REAL_SCAN = _claude_plugin_references.scan


def _cache_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "plugins" / "cache" / "ak110-dotfiles" / _NAME / "1.0.0"


def _write_plugin_copy(install_path: pathlib.Path) -> None:
    """Claude Codeが主作業ツリーから複製した版ディレクトリを再現する（配布物と開発用生成物）。"""
    for relative in _install_claude_plugins._plugin_cache_required_files(_NAME):
        target = install_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
    (install_path / "agent_toolkit").mkdir()
    (install_path / "agent_toolkit" / "hook.py").write_text("hook", encoding="utf-8")
    for artifact in _install_claude_plugins._PLUGIN_CACHE_DEVELOPMENT_ARTIFACTS:
        (install_path / artifact / "lib").mkdir(parents=True)
        (install_path / artifact / "lib" / "payload.bin").write_bytes(b"x" * 16)


@pytest.fixture(name="install_into")
def _install_into(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> typing.Callable[..., None]:
    """未導入のpluginを1件導入する後処理を準備し、導入先と複製処理を指定できる関数を返す。

    `claude plugin install`の代わりに、`installed_plugins.json`の登録と版ディレクトリの複製を作成する。
    """
    installed = tmp_path / "plugins" / "installed_plugins.json"
    installed.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_claude_common, "INSTALLED_PLUGINS_PATH", installed)
    monkeypatch.setattr(_install_claude_plugins, "_prerequisites_ok", lambda: True)
    monkeypatch.setattr(_install_claude_plugins, "_remove_unused_plugins", lambda: False)
    monkeypatch.setattr(_common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(_claude_marketplace, "ensure_marketplace", lambda: True)
    monkeypatch.setattr(_claude_marketplace, "is_directory_type_registered", lambda: True)
    monkeypatch.setattr(_install_claude_plugins, "_read_target_info", lambda _root: ({_NAME: "1.0.0"}, set()))
    monkeypatch.setattr(_install_claude_plugins, "_read_installed_plugins_from_file", lambda: [])
    monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: {})
    monkeypatch.setattr(_install_claude_plugins, "_auto_disable_plugins", lambda _raw, _enabled: (0, 0))
    monkeypatch.setattr(_install_claude_plugins, "compute_recommended_commands", lambda _raw, _enabled: [])
    monkeypatch.setattr(_install_claude_plugins, "_verify_target_plugins", lambda _targets, _failed=(): None)
    monkeypatch.setattr(_claude_plugin_references, "scan", lambda: ())

    def configure(install_path: pathlib.Path, writer: typing.Callable[[pathlib.Path], None] = _write_plugin_copy) -> None:
        def fake_install(name: str) -> bool:
            installed.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "plugins": {
                            f"{name}@{_claude_common.MARKETPLACE_NAME}": [
                                {"scope": "user", "version": "1.0.0", "installPath": str(install_path)}
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            writer(install_path)
            return True

        monkeypatch.setattr(_install_claude_plugins, "_install_plugin", fake_install)

    return configure


def test_removes_only_development_artifacts_inside_cache(
    tmp_path: pathlib.Path, install_into: typing.Callable[..., None]
) -> None:
    """cacheの内側の導入先では開発用生成物だけを除き、配布物と必須ファイルを残す。"""
    cache_dir = _cache_dir(tmp_path)
    install_into(cache_dir)

    outcome = _install_claude_plugins.run()

    assert outcome.failure is None
    assert not outcome.deferred
    for artifact in _install_claude_plugins._PLUGIN_CACHE_DEVELOPMENT_ARTIFACTS:
        assert not (cache_dir / artifact).exists(), artifact
    assert (cache_dir / "agent_toolkit" / "hook.py").read_text(encoding="utf-8") == "hook"
    assert not _install_claude_plugins._missing_plugin_cache_files(_NAME, cache_dir)


def test_keeps_artifacts_outside_cache(tmp_path: pathlib.Path, install_into: typing.Callable[..., None]) -> None:
    """導入先がplugin cacheの外にある場合は削除しない。"""
    outside = tmp_path / "elsewhere" / "1.0.0"
    install_into(outside)

    _install_claude_plugins.run()

    assert (outside / ".venv" / "lib" / "payload.bin").exists()


def test_keeps_symlinked_artifact(tmp_path: pathlib.Path, install_into: typing.Callable[..., None]) -> None:
    """symlinkの生成物はリンク先ごと残す。"""
    cache_dir = _cache_dir(tmp_path)
    shared = tmp_path / "shared-venv"
    shared.mkdir()
    (shared / "payload.bin").write_bytes(b"x")

    def write_with_symlink(install_path: pathlib.Path) -> None:
        _write_plugin_copy(install_path)
        shutil.rmtree(install_path / ".venv")
        (install_path / ".venv").symlink_to(shared, target_is_directory=True)

    install_into(cache_dir, write_with_symlink)

    _install_claude_plugins.run()

    assert (cache_dir / ".venv").is_symlink()
    assert (shared / "payload.bin").exists()
    assert not (cache_dir / ".mypy_cache").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="実行名をsymlinkで与える常駐プロセスはLinuxで起動する")
def test_defers_artifact_referenced_by_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, install_into: typing.Callable[..., None]
) -> None:
    """生成物のパスを参照するプロセスがある間は削除せず延期し、他の生成物は除く。"""
    monkeypatch.setattr(_deferred_updates, "has_retry_timer", lambda: False)
    cache_dir = _cache_dir(tmp_path)
    install_into(cache_dir)
    real_install = _install_claude_plugins._install_plugin

    with ResidentProcesses(tmp_path, monkeypatch) as residents:

        def install_then_hold(name: str) -> bool:
            result = real_install(name)
            residents.start("python3", cwd=cache_dir / ".venv" / "lib")
            monkeypatch.setattr(_claude_plugin_references, "scan", _REAL_SCAN)
            return result

        monkeypatch.setattr(_install_claude_plugins, "_install_plugin", install_then_hold)
        outcome = _install_claude_plugins.run()

        assert outcome.deferred
        assert (cache_dir / ".venv" / "lib" / "payload.bin").exists()
        assert not (cache_dir / ".mypy_cache").exists()


def test_removal_failure_is_reported_as_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, install_into: typing.Callable[..., None]
) -> None:
    """削除に失敗した生成物があれば残りを処理し、後処理の失敗として件数を返す。"""
    cache_dir = _cache_dir(tmp_path)
    install_into(cache_dir)
    real_rmtree = shutil.rmtree

    def flaky_rmtree(path: str | pathlib.Path, **kwargs: typing.Any) -> None:
        del kwargs
        if pathlib.Path(path).name == ".venv":
            raise PermissionError("denied")
        real_rmtree(path)

    monkeypatch.setattr(_install_claude_plugins.shutil, "rmtree", flaky_rmtree)

    outcome = _install_claude_plugins.run()

    assert outcome.failure is not None
    assert "開発用生成物 1 件の削除に失敗" in outcome.failure
    assert (cache_dir / ".venv").exists()
    assert not (cache_dir / ".mypy_cache").exists()
