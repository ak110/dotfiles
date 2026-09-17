"""pytools._internal.setup_agy_cliのテスト。"""

import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from pytools._internal import setup_agy_cli


def _fake_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_run_skips_when_the_launcher_already_exists(monkeypatch, tmp_path: Path) -> None:
    """導入済みの環境ではインストーラーを取得しない（更新はAntigravity CLIが自動で行う）。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_agy_cli.sys, "platform", "linux")
    launcher = tmp_path / ".local" / "bin" / "agy"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("導入済みの環境で外部コマンドを実行した")

    monkeypatch.setattr(setup_agy_cli.claude_common, "run_subprocess", fail)

    assert setup_agy_cli.run() is False


def test_run_installs_and_prepends_path(monkeypatch, tmp_path: Path) -> None:
    """未導入の環境では公式インストーラーを実行し、ランチャーの位置をPATHの先頭へ加える。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_agy_cli.sys, "platform", "linux")
    launcher = tmp_path / ".local" / "bin" / "agy"
    requested: list[str] = []
    commands: list[list[str]] = []
    prepended: list[Path] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=b"#!/bin/bash\n")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        if command[0] == "bash":
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(setup_agy_cli.claude_common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_agy_cli.setup_cli_common, "prepend_path", prepended.append)

    assert setup_agy_cli.run(_fake_client(handler)) is True
    assert requested == ["https://antigravity.google/cli/install.sh"]
    assert commands[0][0] == "bash"
    assert commands[-1] == [str(launcher), "--version"]
    assert prepended == [launcher.parent]


def test_run_installs_with_powershell_file_on_windows(monkeypatch, tmp_path: Path) -> None:
    """Windowsではローカル配下のランチャーを対象とし、PowerShellでインストーラーを実行する。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_agy_cli.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    launcher = tmp_path / "AppData" / "Local" / "agy" / "bin" / "agy.exe"
    requested: list[str] = []
    commands: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, content=b"Write-Host")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        if command[0] == "pwsh":
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(setup_agy_cli.claude_common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_agy_cli.setup_cli_common, "prepend_path", lambda path: None)

    assert setup_agy_cli.run(_fake_client(handler)) is True
    assert requested == ["https://antigravity.google/cli/install.ps1"]
    assert commands[0][:2] == ["pwsh", "-NoProfile"]
    assert commands[0][-1].endswith(".ps1")


@pytest.mark.parametrize("status", [403, 500])
def test_run_warns_instead_of_raising_when_the_installer_is_unreachable(
    monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture, status: int
) -> None:
    """バイナリの取得先へ到達できない環境でも例外を送出せず、警告1行の記録だけで`False`を返す。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_agy_cli.sys, "platform", "linux")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(status)

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("インストーラーの取得に失敗した後で外部コマンドを実行した")

    monkeypatch.setattr(setup_agy_cli.claude_common, "run_subprocess", fail)

    with caplog.at_level("WARNING"):
        assert setup_agy_cli.run(_fake_client(handler)) is False

    assert len(caplog.records) == 1


def test_run_warns_when_the_verification_fails(monkeypatch, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """導入後の確認が失敗した場合も例外を送出せず、PATHへ追加しない。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_agy_cli.sys, "platform", "linux")
    launcher = tmp_path / ".local" / "bin" / "agy"

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"#!/bin/bash\n")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[0] == "bash":
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "ok", "")
        return subprocess.CompletedProcess(command, 1, "", "not authenticated")

    def fail_prepend(path: Path) -> None:
        raise AssertionError("確認に失敗した状態でPATHへ追加した")

    monkeypatch.setattr(setup_agy_cli.claude_common, "run_subprocess", fake_run)
    monkeypatch.setattr(setup_agy_cli.setup_cli_common, "prepend_path", fail_prepend)

    with caplog.at_level("WARNING"):
        assert setup_agy_cli.run(_fake_client(handler)) is False

    assert len(caplog.records) == 1
