"""統合インストーラーの主題別公開契約を検証する。"""

import os
import pathlib
import shutil
import stat
import subprocess

import pytest

from _test_helpers import INSTALL_PS1, installer_runners, make_command_stubs, read_log_lines, run_installer
from _test_helpers import rules_url_fixture as _rules_url_fixture  # noqa: F401  # pytestがfixtureとして収集する。


@pytest.mark.parametrize("kind", installer_runners())
@pytest.mark.parametrize("missing_command", ["claude", "codex", "uv"])
def test_exits_before_writing_when_required_command_is_missing(
    kind: str,
    missing_command: str,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path, omit=missing_command)

    result = run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log, check=False)

    assert result.returncode != 0
    assert not (home / ".claude" / "rules" / "agent-toolkit").exists()


@pytest.mark.parametrize("kind", installer_runners())
@pytest.mark.parametrize(
    "fail_pattern",
    [
        "claude plugin marketplace update",
        "claude plugin update",
        "codex plugin marketplace upgrade",
        "codex plugin add",
    ],
)
def test_propagates_required_setup_failures(
    kind: str,
    fail_pattern: str,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        fail_pattern=fail_pattern,
        check=False,
    )

    assert result.returncode != 0


_CLAUDE_DIAGNOSTIC_STUB = """#!/bin/sh
# CLAUDE_FAIL_COMMANDと一致する呼び出しのCLAUDE_FAIL_OCCURRENCE回目だけ、標準出力と標準エラーへ別々の診断を書いて失敗する。
# toolkitとbasicsは同じマーケットプレイス更新コマンドを使うため、回数で失敗させる側を選ぶ。
printf 'claude %s\\n' "$*" >> "$CLI_STUB_LOG"
if [ "claude $*" = "$CLAUDE_FAIL_COMMAND" ] && \\
    [ "$(grep -cxF -- "claude $*" "$CLI_STUB_LOG")" = "$CLAUDE_FAIL_OCCURRENCE" ]; then
    echo "stdout diagnostic: marketplace unreachable"
    echo "stderr diagnostic: authentication required" >&2
    exit 7
fi
exit 0
"""


@pytest.mark.parametrize("kind", installer_runners())
@pytest.mark.parametrize(
    ("command", "occurrence"),
    [
        pytest.param("claude plugin marketplace update ak110-dotfiles", 1, id="basics-marketplace"),
        pytest.param("claude plugin update agent-basics@ak110-dotfiles --scope=user", 1, id="basics-plugin"),
        pytest.param("claude plugin marketplace update ak110-dotfiles", 2, id="toolkit-marketplace"),
        pytest.param("claude plugin update agent-toolkit@ak110-dotfiles --scope=user", 1, id="toolkit-plugin"),
    ],
)
def test_required_claude_update_failure_keeps_diagnostics_and_next_action(
    kind: str,
    command: str,
    occurrence: int,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    """必須のClaude更新が失敗したら、両ストリームの元診断・失敗コマンド・終了コード・次の操作を残して止まる。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)
    claude_stub = stub_bin / "claude"
    claude_stub.write_text(_CLAUDE_DIAGNOSTIC_STUB, encoding="utf-8")

    result = run_installer(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        check=False,
        extra_env={"CLAUDE_FAIL_COMMAND": command, "CLAUDE_FAIL_OCCURRENCE": str(occurrence)},
    )

    assert result.returncode != 0
    lines = read_log_lines(stub_log)
    # 失敗した呼び出しで導入を止め、後続のコマンドを起動しない。
    assert lines[-1] == command
    assert lines.count(command) == occurrence
    failed_command = f"失敗したコマンド: {command}（終了コード: 7）"
    next_action = "次の操作: 上の診断から原因を解消し、失敗したコマンドを再実行"
    script_name = "install-claude.sh" if kind == "sh" else "install-claude.ps1"
    for expected in (
        "stdout diagnostic: marketplace unreachable",
        "stderr diagnostic: authentication required",
        failed_command,
        next_action,
        f"{script_name}を再実行してください。",
        "dotfilesの作者へ報告してください。",
    ):
        assert expected in result.stderr
    assert result.stderr.index("stdout diagnostic:") < result.stderr.index(failed_command) < result.stderr.index(next_action)
    assert "設定が完了しました。" not in result.stdout


@pytest.mark.parametrize("kind", installer_runners())
@pytest.mark.parametrize(
    "fail_pattern",
    ["claude plugin marketplace add", "claude plugin install", "claude plugin uninstall"],
)
def test_ignorable_claude_failures_continue_without_failure_guidance(
    kind: str,
    fail_pattern: str,
    tmp_path: pathlib.Path,
    rules_url: str,
) -> None:
    """後続の更新成功で目的を達成できるClaude呼び出しの失敗では、導入を続け、失敗の案内を出さない。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log, fail_pattern=fail_pattern)

    assert "claude plugin update agent-toolkit@ak110-dotfiles --scope=user" in read_log_lines(stub_log)
    assert "失敗したコマンド:" not in result.stderr
    assert "設定が完了しました。" in result.stdout


@pytest.mark.parametrize("kind", installer_runners())
def test_cleans_stage_directory_on_download_failure(kind: str, tmp_path: pathlib.Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)
    rules_dir = home / ".claude" / "rules" / "agent-toolkit"
    rules_dir.mkdir(parents=True)
    kept_file = rules_dir / "01-agent.md"
    kept_file.write_text("# 既存内容\n", encoding="utf-8")

    result = run_installer(
        kind,
        home,
        "http://127.0.0.1:1/does-not-exist",
        stub_bin=stub_bin,
        stub_log=stub_log,
        check=False,
    )

    assert result.returncode != 0
    assert kept_file.read_text(encoding="utf-8") == "# 既存内容\n"
    stage_root = home / ".claude" / "rules-stage"
    assert not stage_root.exists() or not list(stage_root.iterdir())


def test_bash_json_check_uses_explicit_python_with_real_uv(tmp_path: pathlib.Path, rules_url: str) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path, omit="uv")
    uv_executable = os.environ.get("UV") or shutil.which("uv")
    assert uv_executable is not None
    (stub_bin / "uv").symlink_to(uv_executable)
    working_directory = tmp_path / "invalid-project"
    working_directory.mkdir()
    (working_directory / "pyproject.toml").write_text("invalid", encoding="utf-8")
    (working_directory / ".python-version").write_text("3.99", encoding="utf-8")

    run_installer("sh", home, rules_url, stub_bin=stub_bin, stub_log=stub_log, cwd=working_directory)

    assert not any("claude mcp add" in line or "claude mcp remove" in line for line in read_log_lines(stub_log))
    assert not (working_directory / ".venv").exists()
    assert not (working_directory / "uv.lock").exists()


_MOVE_FAILURE_SH = """#!/bin/bash
# 配置先への移動だけを失敗させる。MOVE_LEAVE_TARGET=1では配置先を残してから失敗する。
# MOVE_FAIL_ONCEに記録ファイルを指定した場合は、最初の1回（新配置）だけを失敗させる。
for last; do :; done
if [ "$last" = "$MOVE_FAIL_TARGET" ] && { [ -z "${MOVE_FAIL_ONCE:-}" ] || [ ! -e "$MOVE_FAIL_ONCE" ]; }; then
    if [ -n "${MOVE_FAIL_ONCE:-}" ]; then
        : > "$MOVE_FAIL_ONCE"
    fi
    if [ "${MOVE_LEAVE_TARGET:-0}" = 1 ]; then
        /bin/mkdir -p "$last"
    fi
    echo "mv: cannot move to '$last': Permission denied" >&2
    exit 1
fi
exec /bin/mv "$@"
"""


_MOVE_FAILURE_PS1 = """
function Move-Item {
    [CmdletBinding()]
    param([string]$LiteralPath, [string]$Destination)
    $once = $env:MOVE_FAIL_ONCE
    if ($Destination -eq $env:MOVE_FAIL_TARGET -and (-not $once -or -not (Test-Path -LiteralPath $once))) {
        if ($once) { New-Item -ItemType File -Path $once -Force | Out-Null }
        if ($env:MOVE_LEAVE_TARGET -eq '1') { New-Item -ItemType Directory -Path $Destination -Force | Out-Null }
        throw "Move-Item: access denied: $Destination"
    }
    Microsoft.PowerShell.Management\\Move-Item -LiteralPath $LiteralPath -Destination $Destination
}
& '{installer}'
exit $LASTEXITCODE
"""


def _run_with_move_failure(
    kind: str, tmp_path: pathlib.Path, rules_url: str, *, leave_target: bool, fail_once: bool = False
) -> tuple[subprocess.CompletedProcess[str], pathlib.Path, pathlib.Path]:
    """既存規範を置いた隔離HOMEで、配置先への移動（新配置と復元）を失敗させてインストーラーを実行する。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)
    rules_dir = home / ".claude" / "rules" / "agent-toolkit"
    rules_dir.mkdir(parents=True)
    kept_file = rules_dir / "01-agent.md"
    kept_file.write_text("# 既存内容\n", encoding="utf-8")
    ps1_script = None
    if kind == "sh":
        mover = stub_bin / "mv"
        mover.write_text(_MOVE_FAILURE_SH, encoding="utf-8")
        mover.chmod(mover.stat().st_mode | stat.S_IXUSR)
    else:
        ps1_script = tmp_path / "move-failure.ps1"
        ps1_script.write_text(_MOVE_FAILURE_PS1.replace("{installer}", str(INSTALL_PS1).replace("'", "''")), encoding="utf-8")
    result = run_installer(
        kind,
        home,
        rules_url,
        stub_bin=stub_bin,
        stub_log=stub_log,
        check=False,
        extra_env={
            "MOVE_FAIL_TARGET": str(rules_dir),
            "MOVE_LEAVE_TARGET": "1" if leave_target else "0",
            "MOVE_FAIL_ONCE": str(tmp_path / "move-failed-once") if fail_once else "",
        },
        ps1_script=ps1_script,
    )
    return result, home, rules_dir


@pytest.mark.parametrize("leave_target", [False, True], ids=["restore-fails", "target-remains"])
@pytest.mark.parametrize("kind", installer_runners())
def test_keeps_old_rules_when_replacement_fails_before_commit(
    kind: str, tmp_path: pathlib.Path, rules_url: str, leave_target: bool
) -> None:
    """新配置の確定前に配置へ失敗し旧規範を戻せない場合も、旧規範の実体を退避先に残して復旧操作を示す。

    配置先が残って復元を試みられない場合（target-remains）も同じく保持する。
    回収の判断が新配置の確定状態へ接続されていないと、配置先と退避先の両方から旧規範が消える。
    """
    result, home, rules_dir = _run_with_move_failure(kind, tmp_path, rules_url, leave_target=leave_target)

    assert result.returncode != 0
    kept = [path for path in (home / ".claude" / "rules-stage").glob("agent-toolkit.old.*") if path.is_dir()]
    assert len(kept) == 1, result.stderr
    assert (kept[0] / "01-agent.md").read_text(encoding="utf-8") == "# 既存内容\n"
    assert f"退避先に保持しました: {kept[0]}" in result.stderr
    # 最後の診断行は、原因を解消した後に旧規範を配置先へ戻すコマンドである。
    assert any(str(kept[0]) in line and str(rules_dir) in line for line in result.stderr.splitlines())
    assert not list((home / ".claude" / "rules-stage").glob("agent-toolkit.stage.*"))


@pytest.mark.parametrize("kind", installer_runners())
def test_restores_old_rules_when_only_replacement_fails(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """新配置だけが失敗し復元が成功した場合は、旧規範を配置先へ戻し退避先とstageを残さない。"""
    result, home, rules_dir = _run_with_move_failure(kind, tmp_path, rules_url, leave_target=False, fail_once=True)

    assert result.returncode != 0
    assert (rules_dir / "01-agent.md").read_text(encoding="utf-8") == "# 既存内容\n"
    stage_root = home / ".claude" / "rules-stage"
    assert not list(stage_root.glob("agent-toolkit.old.*"))
    assert not list(stage_root.glob("agent-toolkit.stage.*"))
    assert "退避先に保持しました" not in result.stderr


@pytest.mark.parametrize("kind", installer_runners())
def test_successful_install_replaces_rules_and_removes_old_and_stage(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """通常の成功では新規範を配置し、退避した旧規範とstageを回収する。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)
    rules_dir = home / ".claude" / "rules" / "agent-toolkit"
    rules_dir.mkdir(parents=True)
    (rules_dir / "01-agent.md").write_text("# 既存内容\n", encoding="utf-8")

    run_installer(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    assert (rules_dir / "01-agent.md").read_text(encoding="utf-8") != "# 既存内容\n"
    stage_root = home / ".claude" / "rules-stage"
    assert not stage_root.exists() or not list(stage_root.iterdir())
