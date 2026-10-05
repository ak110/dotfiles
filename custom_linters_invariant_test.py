"""pyfltrのカスタムコマンドと対象範囲を確かめる。"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pyfltr.command.targets
import pyfltr.config.config

REPO_ROOT = Path(__file__).resolve().parent


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
        "statusline-version": False,
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


def test_custom_commands_declare_fast() -> None:
    # pyfltrは`fast`の省略を許し、省略した定義は何も表示せずにcommit時の`pyfltr fast`から外れる。
    # 登録時にcommit時の実行可否を判断させるため、全定義へ真偽値の明示を求める。
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    commands = config["tool"]["pyfltr"]["custom-commands"]

    undecided = sorted(name for name, definition in commands.items() if not isinstance(definition.get("fast"), bool))
    assert not undecided, (
        f"pyproject.tomlのカスタムコマンド{undecided}が`fast`を真偽値で明示していない。"
        "変更したファイルだけで判定でき数秒以内で終わるチェックは`fast = true`とし、commit時のpre-commitで実行する。"
        "全体を走査するチェックや、途中のcommitを止めてはならない事情があるチェックは`fast = false`とし、"
        "その理由をコメントに書く。"
    )


# 各rootについて、母集団に実在する全ての深さの代表を1件ずつ挙げる。
# agent-doc-toneの対象は欠けても失敗として現れにくい。pyfltrはtargetsとパスの一致をpathlib.Path.matchで判定して
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
    "agent-toolkit/delegation_contract_invariant_test.py",
    "agent-toolkit/skills/skill_interfaces_invariant_test.py",
    "agent-toolkit/skills/session-review/session_review_contract_invariant_test.py",
    "agent-toolkit/skills/plan-mode/references/structure_contract_invariant_test.py",
    "agent-toolkit/share/task_documents_invariant_test.py",
    "agent-toolkit/rules/rules_invariant_test.py",
    "agent-toolkit/hooks/rules_context_registration_invariant_test.py",
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
