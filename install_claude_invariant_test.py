"""導入処理と配布元ルールの横断契約を検証する。"""

import pathlib
import stat
import typing

import pytest

from install_claude_test import (
    REPO_ROOT,
    RULES_SRC,
    _log_lines,
    _make_command_stubs,
    _run,
    _runners,
    _serve_rules,
)


@pytest.fixture(name="rules_url", scope="module")
def local_rules_url() -> typing.Iterator[str]:
    """テスト用のルール配布先URLを返す。"""
    yield from _serve_rules()


@pytest.mark.parametrize("kind", _runners())
def test_deploys_rules_and_configures_both_agents(kind: str, tmp_path: pathlib.Path, rules_url: str) -> None:
    """配布元ルールと起動スクリプトを導入し、両agentを順序どおり設定する。"""
    home = tmp_path / "home"
    home.mkdir()
    stub_bin, stub_log = _make_command_stubs(tmp_path)
    legacy_dir = home / ".claude" / "rules" / "agent-basics"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "01-agent.md").write_text("# 旧配布\n", encoding="utf-8")
    rules_dir = home / ".claude" / "rules" / "agent-toolkit"
    rules_dir.mkdir(parents=True)
    (rules_dir / "obsolete.md").write_text("# 旧ファイル\n", encoding="utf-8")

    result = _run(kind, home, rules_url, stub_bin=stub_bin, stub_log=stub_log)

    assert (rules_dir / "01-agent.md").read_text(encoding="utf-8") == (RULES_SRC / "01-agent.md").read_text(encoding="utf-8")
    if kind == "sh":
        hook_wrapper = home / ".local" / "bin" / "atk-hook"
        assert hook_wrapper.read_bytes() == (REPO_ROOT / "agent-toolkit" / "bin" / "atk-hook").read_bytes()
        assert hook_wrapper.stat().st_mode & stat.S_IXUSR
    assert not legacy_dir.exists()
    assert not (rules_dir / "obsolete.md").exists()
    joined = "\n".join(_log_lines(stub_log))
    expected = [
        "claude plugin marketplace add ak110/dotfiles --scope=user",
        "claude plugin marketplace update ak110-dotfiles",
        "claude plugin uninstall edit-guardrails@ak110-dotfiles",
        "claude plugin install agent-toolkit@ak110-dotfiles --scope=user",
        "claude plugin update agent-toolkit@ak110-dotfiles --scope=user",
        "codex plugin marketplace add ak110/dotfiles --json",
        "codex plugin marketplace upgrade ak110-dotfiles --json",
        "codex plugin add agent-toolkit@ak110-dotfiles --json",
        "uv run --project",
        "agents_server_mcp.py --check-dependencies",
    ]
    last_index = -1
    for command in expected:
        index = joined.find(command, last_index + 1)
        assert index > last_index, f"未呼び出しまたは順序違反: {command!r}\nlog={joined}"
        last_index = index
    assert not any("claude mcp add" in line or "claude mcp remove" in line for line in _log_lines(stub_log))
    assert result.stderr.splitlines()[-1] == "codex app-server daemon restart"
    assert result.stderr.count("Codex pluginを更新しました。") == 1
