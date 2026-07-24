"""pytools._internal.setup_codex_cliのテスト。"""

import subprocess
from pathlib import Path

import pytest

from pytools._internal import setup_codex_cli


def test_run_installs_verifies_then_migrates(monkeypatch, tmp_path: Path) -> None:
    npm = tmp_path / "node" / "bin" / "npm"
    npm.parent.mkdir(parents=True)
    npm.write_text("", encoding="utf-8")
    prefix = tmp_path / "node"
    calls: list[list[str]] = []
    events: list[str] = []
    monkeypatch.setattr(setup_codex_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_codex_cli.shutil, "which", lambda name: str(npm) if name == "npm" else "/usr/bin/mise")
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)

    def fake_prepend(path: Path) -> None:
        events.append(f"path:{path}")

    def fake_migrate(*args: object) -> bool:
        del args
        events.append("migrate")
        return True

    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", fake_prepend)
    monkeypatch.setattr(
        setup_codex_cli.setup_cli_common,
        "migrate_npm_launchers",
        fake_migrate,
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        if command[1:3] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{prefix}\n", "")
        return subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", fake_run)

    assert setup_codex_cli.run()
    assert calls[1] == [str(npm), "install", "--global", "@openai/codex@latest"]
    assert calls[2] == [str(prefix / "bin/codex"), "--version"]
    assert calls[3][-4:] == ["uninstall", "--all", "--yes", "npm:@openai/codex"]
    assert events == [f"path:{prefix / 'bin'}", "migrate"]
    assert calls[-1][-1] == "reshim"


def test_run_keeps_old_versions_when_verification_fails(monkeypatch, tmp_path: Path) -> None:
    npm = tmp_path / "npm"
    npm.write_text("", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(setup_codex_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_codex_cli.shutil, "which", lambda name: str(npm) if name == "npm" else "/usr/bin/mise")
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        if command[1:3] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{tmp_path}\n", "")
        if command[-1] == "--version":
            return subprocess.CompletedProcess(command, 1, "", "broken")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", fake_run)

    with pytest.raises(RuntimeError):
        setup_codex_cli.run()
    assert not any("uninstall" in call for call in calls)


def test_run_skips_all_work_when_windows_process_is_running(monkeypatch) -> None:
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "is_windows_cli_running", lambda *args: True)
    monkeypatch.setattr(setup_codex_cli.shutil, "which", lambda name: (_ for _ in ()).throw(AssertionError(name)))

    assert not setup_codex_cli.run()


def test_run_reports_failure_when_npm_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)
    monkeypatch.setattr(setup_codex_cli.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError):
        setup_codex_cli.run()


@pytest.mark.parametrize("failure", ["prefix", "install"])
def test_run_keeps_old_versions_before_canonical_install_is_ready(monkeypatch, tmp_path: Path, failure: str) -> None:
    npm = tmp_path / "npm"
    npm.write_text("", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(setup_codex_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_codex_cli.shutil, "which", lambda name: str(npm) if name == "npm" else "/usr/bin/mise")
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        if command[1:3] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(
                command, 1 if failure == "prefix" else 0, f"{tmp_path}\n" if failure == "install" else "", "failed"
            )
        if "install" in command:
            return subprocess.CompletedProcess(command, 1 if failure == "install" else 0, "", "failed")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", fake_run)

    with pytest.raises(RuntimeError):
        setup_codex_cli.run()
    assert not any("uninstall" in call for call in calls)


@pytest.mark.parametrize("failure", ["mise_uninstall", "reshim", "npm_uninstall"])
def test_run_propagates_migration_failures(monkeypatch, tmp_path: Path, failure: str) -> None:
    npm = tmp_path / "npm"
    npm.write_text("", encoding="utf-8")
    monkeypatch.setattr(setup_codex_cli.sys, "platform", "linux")
    monkeypatch.setattr(setup_codex_cli.shutil, "which", lambda name: str(npm) if name == "npm" else "/usr/bin/mise")
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)

    def fake_migrate(*args: object) -> bool:
        del args
        if failure == "npm_uninstall":
            raise RuntimeError("旧npm版の削除に失敗")
        return False

    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", fake_migrate)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if command[1:3] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{tmp_path}\n", "")
        failed = (failure == "mise_uninstall" and "uninstall" in command) or (failure == "reshim" and "reshim" in command)
        return subprocess.CompletedProcess(command, 1 if failed else 0, "", "failed")

    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", fake_run)

    with pytest.raises(RuntimeError):
        setup_codex_cli.run()


def test_run_uses_windows_cmd_in_selected_prefix(monkeypatch, tmp_path: Path) -> None:
    npm = tmp_path / "node" / "npm.cmd"
    npm.parent.mkdir()
    npm.write_text("", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(setup_codex_cli.sys, "platform", "win32")
    monkeypatch.setattr(setup_codex_cli.shutil, "which", lambda name: str(npm) if name == "npm" else None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "is_windows_cli_running", lambda *args: False)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "prepend_path", lambda path: None)
    monkeypatch.setattr(setup_codex_cli.setup_cli_common, "migrate_npm_launchers", lambda *args: False)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(command)
        if command[1:3] == ["prefix", "--global"]:
            return subprocess.CompletedProcess(command, 0, f"{npm.parent}\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_codex_cli.claude_common, "run_subprocess", fake_run)

    assert setup_codex_cli.run()
    assert [str(npm.parent / "codex.cmd"), "--version"] in calls
