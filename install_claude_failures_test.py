"""統合インストーラーの主題別公開契約を検証する。"""

import os
import pathlib
import shutil

import pytest

from _test_helpers import installer_runners, make_command_stubs, read_log_lines, run_installer
from _test_helpers import raw_base_url_fixture as _raw_base_url_fixture  # noqa: F401  # pytestがfixtureとして収集する。


@pytest.mark.parametrize("kind", installer_runners())
@pytest.mark.parametrize("missing_command", ["claude", "codex", "uv"])
def test_exits_before_writing_when_required_command_is_missing(
    kind: str,
    missing_command: str,
    tmp_path: pathlib.Path,
    raw_base_url: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path, omit=missing_command)
    retired = home / ".claude" / "rules" / "agent-toolkit" / "01-agent.md"
    retired.parent.mkdir(parents=True)
    retired.write_text("# 旧い常時規範\n", encoding="utf-8")

    result = run_installer(kind, home, raw_base_url, stub_bin=stub_bin, stub_log=stub_log, check=False)

    assert result.returncode != 0
    # 前提の確認より前には何も変更しない。
    assert retired.read_text(encoding="utf-8") == "# 旧い常時規範\n"


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
    raw_base_url: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(
        kind, home, raw_base_url, stub_bin=stub_bin, stub_log=stub_log, fail_pattern=fail_pattern, check=False
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
    raw_base_url: str,
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
        raw_base_url,
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
    raw_base_url: str,
) -> None:
    """後続の更新成功で目的を達成できるClaude呼び出しの失敗では、導入を続け、失敗の案内を出さない。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)

    result = run_installer(kind, home, raw_base_url, stub_bin=stub_bin, stub_log=stub_log, fail_pattern=fail_pattern)

    assert "claude plugin update agent-toolkit@ak110-dotfiles --scope=user" in read_log_lines(stub_log)
    assert "失敗したコマンド:" not in result.stderr
    assert "設定が完了しました。" in result.stdout


def test_bash_json_check_uses_explicit_python_with_real_uv(tmp_path: pathlib.Path, raw_base_url: str) -> None:
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

    run_installer("sh", home, raw_base_url, stub_bin=stub_bin, stub_log=stub_log, cwd=working_directory)

    assert not any("claude mcp add" in line or "claude mcp remove" in line for line in read_log_lines(stub_log))
    assert not (working_directory / ".venv").exists()
    assert not (working_directory / "uv.lock").exists()


@pytest.mark.parametrize("kind", installer_runners())
def test_successful_install_removes_retired_rules_and_keeps_user_rules(
    kind: str, tmp_path: pathlib.Path, raw_base_url: str
) -> None:
    """旧版が配置した常時規範と退避先を削除し、ユーザーの規範とリンク先の実体を残す。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = make_command_stubs(tmp_path)
    claude = home / ".claude"
    retired_files = [
        claude / "rules" / "agent-basics" / "01-agent.md",
        claude / "rules-stage" / "agent-toolkit.old.123" / "01-agent.md",
    ]
    kept_files = [claude / "rules" / "myprojects.md", claude / "CLAUDE.md"]
    # 旧版の配置先がリンクの場合は、リンク先を辿らずリンク自体を削除する。
    link_target = tmp_path / "outside" / "01-agent.md"
    for path in (*retired_files, *kept_files, link_target):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 既存内容\n", encoding="utf-8")
    (claude / "rules" / "agent-toolkit").symlink_to(link_target.parent, target_is_directory=True)

    result = run_installer(kind, home, raw_base_url, stub_bin=stub_bin, stub_log=stub_log)

    for retired in ("rules/agent-toolkit", "rules/agent-basics", "rules-stage"):
        path = claude / retired
        assert not path.exists() and not path.is_symlink(), retired
        assert f"削除（旧い常時規範）: {path}" in result.stdout
    for path in (*kept_files, link_target):
        assert path.read_text(encoding="utf-8") == "# 既存内容\n"
