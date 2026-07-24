"""pytools._internal.setup_claude_cliのテスト。"""

import os
import subprocess
from pathlib import Path

import httpx
import pytest

from pytools._internal import setup_claude_cli


def test_run_updates_existing_native_and_migrates_after_verification(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    launcher = tmp_path / ".local" / "bin" / "claude"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    calls: list[list[str]] = []
    events: list[str] = []
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)

    def fake_prepend(path: Path) -> None:
        events.append(f"path:{path}")

    def fake_migrate(*args: object) -> bool:
        del args
        events.append("migrate")
        return True

    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "prepend_path", fake_prepend)
    monkeypatch.setattr(
        setup_claude_cli.setup_cli_common,
        "migrate_npm_launchers",
        fake_migrate,
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(setup_claude_cli.claude_common, "run_subprocess", fake_run)

    assert setup_claude_cli.run()
    assert calls == [[str(launcher), "update"], [str(launcher), "--version"]]
    assert events == [f"path:{launcher.parent}", "migrate"]


def test_run_installs_with_powershell_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    launcher = tmp_path / ".local" / "bin" / "claude.exe"
    calls: list[list[str]] = []
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "win32")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: False)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        if command[0] == "pwsh":
            launcher.parent.mkdir(parents=True)
            launcher.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(setup_claude_cli.claude_common, "run_subprocess", fake_run)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"installer")))
    try:
        assert setup_claude_cli.run(client)
    finally:
        client.close()
    assert calls[0][:5] == ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert calls[1] == [str(launcher), "--version"]


def test_windows_install_prepends_canonical_path_before_noncanonical(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    launcher = tmp_path / ".local" / "bin" / "claude.exe"
    noncanonical = tmp_path / "node"
    noncanonical.mkdir()
    monkeypatch.setenv("PATH", str(noncanonical))
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "win32")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: False)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[0] == "pwsh":
            launcher.parent.mkdir(parents=True)
            launcher.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_claude_cli.claude_common, "run_subprocess", fake_run)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"installer")))
    try:
        assert setup_claude_cli.run(client)
    finally:
        client.close()

    assert os.environ["PATH"].split(os.pathsep)[0] == str(launcher.parent)


def test_run_does_not_migrate_when_verification_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    launcher = tmp_path / ".local" / "bin" / "claude"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)
    monkeypatch.setattr(
        setup_claude_cli.setup_cli_common,
        "migrate_npm_launchers",
        lambda *args: (_ for _ in ()).throw(AssertionError("移行してはならない")),
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(command, 1 if command[-1] == "--version" else 0, "", "")

    monkeypatch.setattr(setup_claude_cli.claude_common, "run_subprocess", fake_run)

    with pytest.raises(RuntimeError):
        setup_claude_cli.run()


def test_run_skips_all_work_when_windows_process_is_running(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "is_windows_cli_running", lambda *args: True)
    monkeypatch.setattr(
        setup_claude_cli.claude_common,
        "run_subprocess",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError((args, kwargs))),
    )

    assert not setup_claude_cli.run()


def test_run_handles_http_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500, request=request)))
    try:
        with pytest.raises(RuntimeError):
            setup_claude_cli.run(client)
    finally:
        client.close()


def test_run_handles_installer_failure(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)
    monkeypatch.setattr(
        setup_claude_cli.claude_common,
        "run_subprocess",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "installer failed"),
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"installer")))
    try:
        with pytest.raises(RuntimeError):
            setup_claude_cli.run(client)
    finally:
        client.close()


def test_run_handles_native_update_failure_and_keeps_legacy_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    native = tmp_path / ".local" / "bin" / "claude"
    native.parent.mkdir(parents=True)
    native.write_text("", encoding="utf-8")
    legacy = tmp_path / ".claude" / "local" / "claude"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy", encoding="utf-8")
    monkeypatch.setattr(setup_claude_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_claude_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)
    monkeypatch.setattr(
        setup_claude_cli.claude_common,
        "run_subprocess",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "update failed"),
    )

    with pytest.raises(RuntimeError):
        setup_claude_cli.run()
    assert legacy.read_text(encoding="utf-8") == "legacy"
