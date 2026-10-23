"""両OSの初回導入入口とモード別mise設定を隔離ホームで確認する。"""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent


@pytest.mark.parametrize("mode", ["full", "minimal"])
@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_install_entry_persists_and_transmits_mode(mode, shell, tmp_path, host_environ):
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell}が未導入")
    home = tmp_path / "home"
    bin_directory = home / ".local/bin"
    bin_directory.mkdir(parents=True)
    script = home / "dotfiles/agent-toolkit/agent_toolkit/_common/dotfiles_mode.py"
    script.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "agent-toolkit/agent_toolkit/_common/dotfiles_mode.py", script)
    uv = bin_directory / "uv"
    uv.write_text(f'#!/bin/sh\nshift 6\nexec "{sys.executable}" "$@"\n')
    uv.chmod(0o755)
    chezmoi = bin_directory / "chezmoi"
    chezmoi.write_text(
        '#!/bin/sh\nif [ "$1" = init ]; then printf "%s\\n" "$AGENT_TOOLKIT_CONFIG_DOTFILES_MODE" > "$HOME/observed-mode"; fi\n'
    )
    chezmoi.chmod(0o755)
    environment = {
        **host_environ(),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(home / "AppData/Local"),
        "XDG_CONFIG_HOME": str(home / ".config"),
    }
    environment["PATH"] = str(bin_directory) + os.pathsep + environment["PATH"]
    environment.pop("AGENT_TOOLKIT_CONFIG_DOTFILES_MODE", None)
    arguments = (
        [executable, str(ROOT / "install.sh"), "--mode", mode]
        if shell == "bash"
        else [executable, "-NoProfile", "-File", str(ROOT / "install.ps1"), "-Mode", mode]
    )
    result = subprocess.run(arguments, env=environment, cwd=tmp_path, capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    path = home / ".config/agent-toolkit/config.json"
    assert json.loads(path.read_text())["dotfiles_mode"] == mode
    assert (home / "observed-mode").read_text().strip() == mode


@pytest.mark.parametrize("mode", ["full", "minimal"])
@pytest.mark.parametrize("config_exists", [False, True])
def test_global_mise_template_contains_mode_target_set(mode, config_exists, tmp_path, host_environ):
    chezmoi = shutil.which("chezmoi")
    if chezmoi is None:
        pytest.skip("chezmoiが未導入")
    if config_exists:
        config = tmp_path / ".config/agent-toolkit/config.json"
        config.parent.mkdir(parents=True)
        config.write_text('{"review_model": "codex:terra/medium"}', encoding="utf-8")
    environment = {
        **host_environ(),
        "HOME": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / ".config"),
        "AGENT_TOOLKIT_CONFIG_DOTFILES_MODE": mode,
    }
    result = subprocess.run(
        [chezmoi, "execute-template", "--file", str(ROOT / ".chezmoi-source/dot_config/mise/config.toml.tmpl")],
        env=environment,
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    tools = tomllib.loads(result.stdout)["tools"]
    assert {"chezmoi", "fzf", "gh", "glab", "glow", "jq", "ripgrep"} == set(tools) - (
        {"dotnet", "gcloud", "node", "npm:aws-cdk", "npm:@google/gemini-cli"} if mode == "full" else set()
    )
    assert ("node" in tools) == (mode == "full")
    development = {
        "actionlint",
        "github:editorconfig-checker/editorconfig-checker",
        "git-cliff",
        "pinact",
        "shellcheck",
        "shfmt",
        "typos",
    }
    assert not development & set(tools)
    project = tomllib.loads((ROOT / "mise.toml").read_text())["tools"]
    locked = tomllib.loads((ROOT / "mise.lock").read_text())["tools"]
    assert development <= project.keys()
    assert development <= locked.keys()
