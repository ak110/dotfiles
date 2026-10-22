"""OpenCodeの導入有無、失敗時の案内、導入後確認を検証する。"""

import logging
import subprocess
from pathlib import Path

import pytest

from pytools._internal import common, setup_opencode_cli


@pytest.mark.parametrize("installed", [False, True])
def test_opencode_install_only_when_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, installed: bool) -> None:
    """未導入のときだけ公式npmコマンドと導入後の版確認を実行する。"""
    calls: list[list[str]] = []

    def resolve(name: str) -> Path | None:
        return None if name == "opencode" and not installed and not calls else tmp_path / name

    def execute(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "1.19.3", "")

    monkeypatch.setattr(common, "resolve_executable", resolve)
    monkeypatch.setattr(setup_opencode_cli.shutil, "which", lambda name: str(resolve(name)))
    monkeypatch.setattr(common, "run_subprocess", execute)
    result = setup_opencode_cli.run()
    assert result.changed is (not installed)
    assert result.failure is None
    assert calls == (
        []
        if installed
        else [[str(tmp_path / "npm"), "install", "--global", "opencode-ai"], [str(tmp_path / "opencode"), "--version"]]
    )


@pytest.mark.parametrize("failure", ["npm-missing", "install", "verification"])
def test_opencode_install_failure_is_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    """導入前提・取得・実行確認の失敗をスキップし、診断と次の操作を伝える。"""
    calls: list[list[str]] = []

    def resolve(name: str) -> Path | None:
        if (name == "opencode" and not calls) or (name == "npm" and failure == "npm-missing"):
            return None
        return tmp_path / name

    def execute(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        failed = failure == "install" or "--version" in command
        return subprocess.CompletedProcess(command, int(failed), "", "registry or executable failure" if failed else "")

    monkeypatch.setattr(common, "resolve_executable", resolve)
    monkeypatch.setattr(
        setup_opencode_cli.shutil, "which", lambda name: None if failure == "npm-missing" else str(tmp_path / name)
    )
    monkeypatch.setattr(common, "run_subprocess", execute)
    with caplog.at_level(logging.WARNING):
        result = setup_opencode_cli.run()
    assert not result.changed
    assert result.failure is None
    assert "update-dotfiles" in caplog.text
    if failure != "npm-missing":
        assert "registry or executable failure" in caplog.text
