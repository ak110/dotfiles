"""テンプレート化後の配布mise lock更新経路を模擬する。"""

import os
import subprocess
from pathlib import Path

import pytest
import update_global_mise


def test_lock_update_renders_full_and_preserves_host_mode(tmp_path, monkeypatch):
    """一時展開のdotnet版をテンプレートへ戻し、既存モードを保持する。"""
    source = tmp_path / ".chezmoi-source/dot_config/mise"
    source.mkdir(parents=True)
    (source / "config.toml.tmpl").write_text('[tools]\ndotnet = "10.0.1"\n')
    (source / "mise.lock").write_text("old lock")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_DOTFILES_MODE", "minimal")
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        assert kwargs["env"]["AGENT_TOOLKIT_CONFIG_DOTFILES_MODE"] == "full"
        if arguments[0] == "chezmoi":
            return subprocess.CompletedProcess(arguments, 0, '[tools]\ndotnet = "10.0.1"\n', "")
        directory = Path(kwargs["cwd"])
        if arguments[1] == "upgrade":
            (directory / "config.toml").write_text('[tools]\ndotnet = "10.0.2"\n')
        else:
            (directory / "mise.lock").write_text("new lock")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(update_global_mise.process_tree, "run_with_timeout", run)
    update_global_mise.update(tmp_path)
    assert (source / "config.toml.tmpl").read_text() == '[tools]\ndotnet = "10.0.2"\n'
    assert (source / "mise.lock").read_text() == "new lock"
    assert os.environ["AGENT_TOOLKIT_CONFIG_DOTFILES_MODE"] == "minimal"
    assert len(calls) == 3
    assert [options["timeout"] for _, options in calls] == [60, 1200, 1200]
    assert all(options["check"] for _, options in calls)


def test_failed_lock_update_preserves_sources(tmp_path, monkeypatch):
    """取得失敗時は配布元のlockとテンプレートを保持する。"""
    source = tmp_path / ".chezmoi-source/dot_config/mise"
    source.mkdir(parents=True)
    (source / "config.toml.tmpl").write_text('[tools]\ndotnet = "10.0.1"\n')
    (source / "mise.lock").write_text("old lock")

    def run(arguments, **_kwargs):
        if arguments[0] == "chezmoi":
            return subprocess.CompletedProcess(arguments, 0, '[tools]\ndotnet = "10.0.1"\n', "")
        raise subprocess.CalledProcessError(1, arguments)

    monkeypatch.setattr(update_global_mise.process_tree, "run_with_timeout", run)
    with pytest.raises(subprocess.CalledProcessError):
        update_global_mise.update(tmp_path)
    assert (source / "config.toml.tmpl").read_text() == '[tools]\ndotnet = "10.0.1"\n'
    assert (source / "mise.lock").read_text() == "old lock"
