"""導入モードの消費側を公開コマンドとpost-applyの実ステップで検証する。"""

# pylint: disable=protected-access  # 対象分類・削除境界の内部関数を隔離環境で検証する。

import importlib
import os
import subprocess
from pathlib import Path

import pytest
from agent_toolkit._common import dotfiles_mode

from pytools import post_apply
from pytools._internal import post_apply_outcome, setup_mise


@pytest.mark.parametrize(
    "module",
    [
        "setup_claude_cli",
        "setup_codex_cli",
        "setup_agy_cli",
        "setup_opencode_cli",
        "setup_herdr_cli",
        "install_claude_plugins",
        "install_codex_plugins",
        "setup_statusline_binary",
    ],
)
def test_minimal_individual_setup_entry_points_skip(module, monkeypatch):
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, "minimal")
    target = importlib.import_module("pytools._internal." + module)
    assert not target.run().changed


def test_minimal_step_selection_keeps_shell_and_dotfiles_foundation(monkeypatch):
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, "minimal")
    excluded = {
        step.run.__module__.rsplit(".", 1)[-1]
        for step in post_apply._DEFAULT_STEPS
        if post_apply._skip_reason(step, home_substituted=False) == "minimalモードのため導入対象外"
    }
    assert {
        "setup_claude_cli",
        "setup_codex_cli",
        "setup_agy_cli",
        "setup_herdr_cli",
        "setup_opencode_cli",
        "warmup_agents_server",
        "warmup_pyfltr_mcp",
        "install_claude_plugins",
        "install_codex_plugins",
        "setup_statusline_binary",
    } <= excluded
    retained = {step.run.__module__.rsplit(".", 1)[-1] for step in post_apply._DEFAULT_STEPS if not step.full_only}
    assert {
        "setup_mise",
        "setup_tmux_plugins",
        "setup_bin_path",
        "setup_dotfiles_autoupdate",
        "apply_dotfiles_plugins",
    } <= retained
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, "full")
    assert all(
        post_apply._skip_reason(step, home_substituted=False) != "minimalモードのため導入対象外"
        for step in post_apply._DEFAULT_STEPS
    )


def test_minimal_mise_install_ignores_working_tree(monkeypatch, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "mise.toml").write_text('[tools]\nrust="1"\n')
    monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(root))
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, "minimal")
    assert setup_mise._working_tree_with_config() == Path.home()
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, "full")
    assert setup_mise._working_tree_with_config() == root


def test_minimal_mise_install_explicitly_selects_only_lightweight_tools(monkeypatch):
    """古いfull用グローバル設定が残る単独実行でも大型ツールを取得しない。"""
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, "minimal")
    calls = []

    def run(_binary, arguments, **_kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(setup_mise, "_run_mise", run)
    assert setup_mise._ensure_tools_installed(Path("/fixture/mise"))
    assert calls == [["install", "chezmoi", "fzf", "gh", "glab", "glow", "jq", "ripgrep"]]


def test_post_apply_mode_and_prune_flags_are_transmitted(monkeypatch, tmp_path):
    """公開post-applyの引数を保存値と後処理の環境へ反映する。"""
    path = tmp_path / "config.json"
    monkeypatch.setattr(dotfiles_mode, "configuration_path", lambda: path)
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, "full")
    monkeypatch.setenv("DOTFILES_PRUNE", "0")
    with pytest.raises(SystemExit) as result:
        post_apply.main(["--mode", "minimal", "--prune"], runner=lambda: ([], []))
    assert result.value.code == 0
    assert dotfiles_mode.is_minimal()
    assert path.is_file()
    assert os.environ["DOTFILES_PRUNE"] == "1"


def test_prune_runs_after_all_post_apply_steps(monkeypatch):
    """削除対象を使うセットアップが完了してからpruneへ進む。"""
    calls = []

    def setup():
        calls.append("setup")
        return post_apply_outcome.PostApplyOutcome()

    def prune():
        assert calls == ["setup"]
        calls.append("prune")
        return post_apply_outcome.PostApplyOutcome(changed=True)

    monkeypatch.setenv("DOTFILES_PRUNE", "1")
    monkeypatch.setenv(dotfiles_mode.MODE_ENV, "minimal")
    monkeypatch.setattr(post_apply, "_DEFAULT_STEPS", [post_apply._StepSpec("fixture", setup)])
    monkeypatch.setattr(post_apply, "_home_is_substituted", lambda: False)
    monkeypatch.setattr(post_apply.prune_installation, "run", prune)
    results, _ = post_apply.run()
    assert calls == ["setup", "prune"]
    assert all(result.ok for result in results)
