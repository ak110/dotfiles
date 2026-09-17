"""pyfltrへ登録したリポジトリ固有linterの契約テスト。"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

import pyfltr.command.targets
import pyfltr.config.config

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_custom_linter_paths_and_filename_contracts() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    commands = config["tool"]["pyfltr"]["custom-commands"]

    expected = {
        "check-cmd-encoding": False,
        "check-templates": True,
        "require-ps1-bom": True,
        "powershell-analyzer": True,
        "claude-plugin-validate": True,
        "agent-doc-tone": True,
    }
    for name, pass_filenames in expected.items():
        definition = commands[name]
        assert definition["type"] == "linter"
        assert definition.get("pass-filenames", True) is pass_filenames
        executable = definition["path"]
        if executable.startswith("scripts/"):
            path = REPO_ROOT / executable
            assert path.is_file()
            assert os.access(path, os.X_OK)


# 各rootについて、母集団に実在する全ての深さの代表を1件ずつ挙げる。
# agent-doc-toneの対象は静かに欠けやすい。pyfltrはtargetsをpathlib.Path.matchで照合して
# `**`を任意階層として扱わず、サブプロジェクト配下を別設定で実行し、symlink越しに同一実体へ
# 到達する2つのパスのうち片方だけを残す。いずれが崩れてもlinterは登録されたまま成功し続ける。
AGENT_DOC_TONE_REPRESENTATIVES = (
    "AGENTS.md",
    "agent-toolkit/rules/01-agent.md",
    "agent-toolkit/share/add-wi.parent.md",
    "agent-toolkit/skills/add-awi-by-user/SKILL.md",
    "agent-toolkit/skills/bugfix/references/ci-failure-handling.md",
    ".chezmoi-source/dot_claude/docs/session-review-dotfiles.md",
    ".chezmoi-source/dot_claude/skills/ak110-projects-operations/SKILL.md",
    ".chezmoi-source/dot_claude/skills/ak110-projects-operations/references/doc-structure.md",
    ".claude/skills/agent-toolkit-edit/SKILL.md",
    ".claude/skills/agent-toolkit-edit/references/agents-server-investigation.md",
)


def test_agent_doc_tone_covers_every_population_root() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    globs = pyproject["tool"]["pyfltr"]["custom-commands"]["agent-doc-tone"]["targets"]
    config = pyfltr.config.config.load_config(config_dir=REPO_ROOT)
    all_files = pyfltr.command.targets.expand_all_files([Path(".")], config=config, start_cwd=REPO_ROOT)
    reached = {(REPO_ROOT / path).resolve() for path in pyfltr.command.targets.filter_by_globs(all_files, globs)}

    for relative in AGENT_DOC_TONE_REPRESENTATIVES:
        path = REPO_ROOT / relative
        assert path.is_file(), relative
        assert path.resolve() in reached, relative


def test_validate_claude_plugins_processes_each_file(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    stub = bin_dir / "claude"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n', encoding="utf-8")
    stub.chmod(0o755)
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "CALL_LOG": str(log)}

    result = subprocess.run(
        [str(REPO_ROOT / "scripts/validate-claude-plugins.sh"), "first.json", "second.json"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert log.read_text(encoding="utf-8").splitlines() == [
        "plugin validate --strict first.json",
        "plugin validate --strict second.json",
    ]
