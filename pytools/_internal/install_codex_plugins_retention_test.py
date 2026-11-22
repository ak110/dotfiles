# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""install_codex_pluginsが旧versionを経過日数で回収する試験。

期待値は「後継versionの導入から7日を過ぎた旧versionを削除し、導入中・目標のversion、期間内のversion、
稼働中のCodexの起動より後に後継が導入されたversion、使用中で移動できないversionは残す」という規則から導く。
導入時刻は各versionの`.codex-plugin/plugin.json`のmtimeとする。
"""

import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from pytools._internal import codex_processes, install_codex_plugins
from pytools._internal.install_codex_plugins_test_helpers import _empty_unused_plugins as _empty_unused_plugins
from pytools._internal.install_codex_plugins_test_helpers import (
    _installed_state,
    _local_marketplace,
    _recording_success,
    _set_json_responses,
)
from pytools._internal.install_codex_plugins_test_helpers import plugin_env_fixture as plugin_env_fixture

_DAY = 86400


def _cache() -> Path:
    return install_codex_plugins.CODEX_HOME / "plugins/cache/ak110-dotfiles/agent-toolkit"


def _version(name: str, installed_days_ago: float) -> Path:
    root = _cache() / name
    (root / ".codex-plugin").mkdir(parents=True, exist_ok=True)
    manifest = root / ".codex-plugin/plugin.json"
    manifest.write_text(f'{{"name": "agent-toolkit", "version": "{name}"}}', encoding="utf-8")
    (root / "marker").write_text(name, encoding="utf-8")
    installed_at = time.time() - installed_days_ago * _DAY
    os.utime(manifest, (installed_at, installed_at))
    return root


def _run_without_update(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_json_responses(monkeypatch, [_local_marketplace(plugin_env), _installed_state()])
    monkeypatch.setattr(install_codex_plugins, "_command", _recording_success([]))
    install_codex_plugins.run()


def _remaining() -> set[str]:
    return {path.name for path in _cache().iterdir()}


def test_removes_versions_retired_past_grace_period(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    _version("1.0.0", 30)

    _version("1.2.3", 25)
    _version("1.1.0", 20)

    leftover = _cache() / ".1.2.0-restore-abcd1234"
    leftover.mkdir()
    eight_days_ago = time.time() - 8 * _DAY
    os.utime(leftover, (eight_days_ago, eight_days_ago))

    _version("1.2.0", 5)

    _run_without_update(plugin_env, monkeypatch)

    assert _remaining() == {"1.2.3", "1.1.0", ".1.2.0-restore-abcd1234", "1.2.0"}
    assert (_cache() / "1.1.0/marker").read_text(encoding="utf-8") == "1.1.0"


def test_keeps_installed_version_while_updating_to_target(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    _version("1.0.0", 40)
    _version("1.2.2", 30)
    _version("1.3.0", 20)
    current: dict[str, Any] = {"version": "1.2.2", "enabled": True}

    def command(args: list[str]) -> bool:
        if args[:2] == ["plugin", "add"]:
            current.update(version="1.2.3")
        return True

    monkeypatch.setattr(
        install_codex_plugins,
        "_codex_json",
        lambda args: _local_marketplace(plugin_env) if args[1] == "marketplace" else _installed_state(**current),
    )
    monkeypatch.setattr(install_codex_plugins, "_command", command)
    install_codex_plugins.run()

    assert current["version"] == "1.2.3"
    assert _remaining() == {"1.2.2", "1.3.0", "1.2.3"}


@pytest.mark.parametrize(
    ("processes", "expected"),
    [
        # 25日前に起動したCodexは、その後に現行でなくなった1.1.0を使い得る。
        # 更新ループはplugin実体を使わないため、起動時刻が古くても保持の根拠にしない。
        pytest.param(
            (("session", 25), ("update-loop", 60)), {"1.1.0", "1.2.0", "1.2.3"}, id="session-started-before-retirement"
        ),
        # 最も早く起動したCodex（管理daemonを含む）を基準にする。
        pytest.param((("session", 1), ("managed-daemon", 35)), {"1.0.0", "1.1.0", "1.2.0", "1.2.3"}, id="earliest-start"),
        # 起動時刻を取得できないCodexは、全versionを使い得るものとして扱う。
        pytest.param((("session", None),), {"1.0.0", "1.1.0", "1.2.0", "1.2.3"}, id="unknown-start"),
    ],
)
def test_keeps_versions_that_running_codex_may_use(
    plugin_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    processes: tuple[tuple[codex_processes.CodexRole, float | None], ...],
    expected: set[str],
) -> None:
    """稼働中のCodexの起動より後に後継が導入された版は、期間を過ぎても残す。"""
    now = time.time()
    running = tuple(
        codex_processes.CodexProcess(
            4242 + index, "codex", role, started_at=None if days_ago is None else now - days_ago * _DAY
        )
        for index, (role, days_ago) in enumerate(processes)
    )
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: running)
    for name, installed_days_ago in (("1.0.0", 40), ("1.1.0", 30), ("1.2.0", 20)):
        _version(name, installed_days_ago)

    _run_without_update(plugin_env, monkeypatch)

    assert _remaining() == expected


def test_keeps_version_in_use_and_finishes_interrupted_removal(plugin_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    locked = _version("1.0.0", 40)
    _version("1.1.0", 30)
    _version("1.2.0", 20)
    original_rename = Path.rename
    original_rmtree = shutil.rmtree

    def rename(self: Path, target: Path) -> Path:
        if self == locked:
            raise PermissionError("in use")
        return original_rename(self, target)

    def rmtree(path: Any, ignore_errors: bool = False) -> None:
        if Path(path).name == "1.1.0~removing":
            raise PermissionError("injected removal failure")
        original_rmtree(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(Path, "rename", rename)
    monkeypatch.setattr(install_codex_plugins.shutil, "rmtree", rmtree)
    _run_without_update(plugin_env, monkeypatch)

    assert _remaining() == {"1.0.0", "1.1.0~removing", "1.2.0", "1.2.3"}
    assert (locked / "marker").read_text(encoding="utf-8") == "1.0.0"

    monkeypatch.setattr(install_codex_plugins.shutil, "rmtree", original_rmtree)
    _run_without_update(plugin_env, monkeypatch)

    assert _remaining() == {"1.0.0", "1.2.0", "1.2.3"}


__all__ = ["plugin_env_fixture"]
