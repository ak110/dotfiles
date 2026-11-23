"""導入処理の横断契約を検証する。"""

import os
import pathlib
import shutil
import stat
import subprocess
import typing

import pytest

from _test_helpers import (
    REPO_ROOT,
    installer_runners,
    make_command_stubs,
    read_log_lines,
    run_installer,
    serve_repository,
)


@pytest.fixture(name="raw_base_url", scope="module")
def local_raw_base_url() -> typing.Iterator[str]:
    yield from serve_repository()


@pytest.mark.parametrize("kind", installer_runners())
def test_configures_both_agents_without_deploying_rules(kind: str, tmp_path: pathlib.Path, raw_base_url: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)
    legacy_dir = home / ".claude" / "rules" / "agent-basics"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "01-agent.md").write_text("# 旧配布\n", encoding="utf-8")
    rules_dir = home / ".claude" / "rules" / "agent-toolkit"
    rules_dir.mkdir(parents=True)
    (rules_dir / "obsolete.md").write_text("# 旧ファイル\n", encoding="utf-8")

    result = run_installer(kind, home, raw_base_url, stub_bin=stub_bin, stub_log=stub_log)

    if kind == "sh":
        hook_wrapper = home / ".local" / "bin" / "atk-hook"
        assert hook_wrapper.read_bytes() == (REPO_ROOT / "agent-toolkit" / "scripts" / "atk-hook").read_bytes()
        assert hook_wrapper.stat().st_mode & stat.S_IXUSR
    assert not legacy_dir.exists()
    assert not rules_dir.exists()
    joined = "\n".join(read_log_lines(stub_log))
    expected = [
        "claude plugin marketplace add ak110/dotfiles --scope=user",
        "claude plugin marketplace update ak110-dotfiles",
        "claude plugin uninstall edit-guardrails@ak110-dotfiles",
        "claude plugin install agent-toolkit@ak110-dotfiles --scope=user",
        "claude plugin update agent-toolkit@ak110-dotfiles --scope=user",
        "codex plugin marketplace add ak110/dotfiles --json",
        "codex plugin marketplace upgrade ak110-dotfiles --json",
        "codex plugin add agent-toolkit@ak110-dotfiles --json",
        "uv run --no-cache --no-config --no-project --python 3.15 python",
        "scripts/launch.py",
        "agents_server_mcp.py --check-dependencies",
    ]
    last_index = -1
    for command in expected:
        index = joined.find(command, last_index + 1)
        assert index > last_index, f"未呼び出しまたは順序違反: {command!r}\nlog={joined}"
        last_index = index
    assert not any("claude mcp add" in line or "claude mcp remove" in line for line in read_log_lines(stub_log))
    assert result.stderr.splitlines()[-1] == "codex app-server daemon restart"
    assert result.stderr.count("Codex pluginを更新しました。") == 1
    assert "Windows PowerShell 5.1ではコマンドを直接呼ばず" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1の標準エラー処理を検証する")
@pytest.mark.parametrize("exit_code", [0, 23])
def test_daemon_restart_guide_preserves_native_exit(tmp_path: pathlib.Path, exit_code: int) -> None:
    powershell = shutil.which("powershell.exe")
    assert powershell is not None
    (tmp_path / "codex.cmd").write_text(
        f'@echo off\n>&2 echo {{"status":"shutdown_requested"}}\necho {{"status":"restarted"}}\nexit /b {exit_code}\n',
        encoding="ascii",
    )
    guide = (REPO_ROOT / "docs/guide/codex-guide.md").read_text(encoding="utf-8")
    recipe = guide.split("```powershell\n", 1)[1].split("```", 1)[0]
    strict = "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n$env:PATH = \"$PSScriptRoot;$env:PATH\"\n"
    shell = [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    old_runner = tmp_path / "old-restart.ps1"
    old_runner.write_text(
        strict + "codex app-server daemon restart 2>&1 | ForEach-Object { \"$_\" }\n'REACHED'\n",
        encoding="utf-8-sig",
    )
    old = subprocess.run([*shell, str(old_runner)], capture_output=True, text=True, check=False, timeout=30)
    assert old.returncode != 0
    assert "NativeCommandError" in old.stderr
    assert "REACHED" not in old.stdout
    runner = tmp_path / "restart.ps1"
    runner.write_text(
        strict + recipe + "if ($ErrorActionPreference -ne 'Stop') { exit 99 }\nexit $restartExit\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [*shell, str(runner)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == exit_code, (result.stdout, result.stderr)
    assert '{"status":"shutdown_requested"}' in result.stdout
    assert '{"status":"restarted"}' in result.stdout
