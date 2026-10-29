"""OpenCodeの導入有無、失敗時の案内、導入後確認を検証する。"""

import logging
import os
import stat
import subprocess
from pathlib import Path

import pytest

from pytools._internal import common, setup_mise, setup_opencode_cli

_SUFFIX = ".cmd" if os.name == "nt" else ""


def _executable(path: Path) -> Path:
    """実行権限付きの空の実行ファイルを作る。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("stub", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_shim_only_path_uses_installed_opencode_without_reinstall(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """PATHがmiseのshimと`~/.local/bin`だけでも、導入後確認が成功し2回目は再導入しない。

    mise管理のnodeに付属するnpmは、nodeの導入先へ`opencode`を置く。その導入先はPATHに無く、
    `mise reshim`がshimを生成した後にだけPATHから起動できる。
    """
    mise_data = tmp_path / "mise"
    shims = mise_data / "shims"
    local_bin = tmp_path / "home" / ".local" / "bin"
    node_bin = mise_data / "installs" / "node" / "24.22.1" / "bin"
    npm = _executable(shims / ("npm" + _SUFFIX))
    mise = _executable(local_bin / ("mise" + _SUFFIX))
    shim = shims / ("opencode" + _SUFFIX)
    installed = node_bin / ("opencode" + _SUFFIX)
    monkeypatch.setenv("PATH", os.pathsep.join((str(shims), str(local_bin))))
    monkeypatch.setenv("MISE_DATA_DIR", str(mise_data))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_DOTFILES_MODE", "full")
    calls: list[list[str]] = []

    def execute(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command == [str(npm), "install", "--global", "opencode-ai"]:
            _executable(installed)
        elif command == [str(mise), "reshim"] and installed.exists():
            _executable(shim)
        elif command == [str(shim), "--version"] and not installed.exists():
            return subprocess.CompletedProcess(command, 1, "", "opencode is not installed")
        return subprocess.CompletedProcess(command, 0, "1.19.3", "")

    monkeypatch.setattr(common, "run_subprocess", execute)

    first = setup_opencode_cli.run()
    second = setup_opencode_cli.run()

    assert first.changed
    assert first.failure is None
    assert not second.changed
    assert second.failure is None
    assert [command for command in calls if command[0] == str(npm)] == [[str(npm), "install", "--global", "opencode-ai"]]
    assert calls[-1] == [str(shim), "--version"]


def test_stale_shim_triggers_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """有効なnodeの版に導入されていない古いshimは、導入済みと判定しない。"""
    shim = tmp_path / "opencode"
    npm = tmp_path / "npm"
    installed: list[bool] = []
    calls: list[list[str]] = []

    def execute(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[0] == str(npm):
            installed.append(True)
        failed = command == [str(shim), "--version"] and not installed
        return subprocess.CompletedProcess(command, int(failed), "", "stale shim" if failed else "")

    monkeypatch.setattr(setup_opencode_cli.shutil, "which", lambda name: str(tmp_path / name))
    monkeypatch.setattr(setup_mise, "find_mise_binary", lambda: None)
    monkeypatch.setattr(common, "run_subprocess", execute)
    result = setup_opencode_cli.run()
    assert result.changed
    assert calls == [
        [str(shim), "--version"],
        [str(npm), "install", "--global", "opencode-ai"],
        [str(shim), "--version"],
    ]


@pytest.mark.parametrize("failure", ["npm-missing", "install", "not-found", "verification"])
def test_opencode_install_failure_is_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    """導入前提・取得・導入後の解決・実行確認の失敗をスキップし、失敗ごとの診断と次の操作を伝える。"""
    calls: list[list[str]] = []

    def which(name: str) -> str | None:
        if name == "npm":
            return None if failure == "npm-missing" else str(tmp_path / name)
        return str(tmp_path / name) if calls and failure == "verification" else None

    def execute(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        failed = failure == "install" or "--version" in command
        return subprocess.CompletedProcess(command, int(failed), "", "registry or executable failure" if failed else "")

    monkeypatch.setattr(setup_opencode_cli.shutil, "which", which)
    monkeypatch.setattr(setup_mise, "find_mise_binary", lambda: None)
    monkeypatch.setattr(common, "run_subprocess", execute)
    with caplog.at_level(logging.WARNING):
        result = setup_opencode_cli.run()
    assert not result.changed
    assert result.failure is None
    assert "update-dotfiles" in caplog.text
    if failure in ("install", "verification"):
        assert "registry or executable failure" in caplog.text
    assert ("PATH上で`opencode`が見つからない" in caplog.text) is (failure == "not-found")
    assert ("--version`が失敗" in caplog.text) is (failure == "verification")
