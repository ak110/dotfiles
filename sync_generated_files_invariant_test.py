"""Codex投影へ同期する原本の配置が生成同期の対象に含まれることを確かめる。"""

import pathlib
import tomllib

import pytest

pytestmark = pytest.mark.repo_invariant


def test_sync_targets_cover_agent_toolkit_skills_and_share() -> None:
    """skillsとshareの各実在階層を生成同期の起動対象に含める。"""
    root = pathlib.Path(__file__).resolve().parent
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    targets = config["tool"]["pyfltr"]["custom-commands"]["sync-generated-files"]["targets"]
    representatives = (
        pathlib.Path("agent-toolkit/share/rules-subagent.md"),
        pathlib.Path("agent-toolkit/skills/process-wi/SKILL.md"),
        pathlib.Path("agent-toolkit/skills/plan-mode/references/plan-file-standards.md"),
        pathlib.Path("agent-toolkit/delegation_contract_invariant_test.py"),
        pathlib.Path("agent-toolkit/skills/skill_interfaces_invariant_test.py"),
        pathlib.Path("agent-toolkit/skills/session-review/session_review_contract_invariant_test.py"),
        pathlib.Path("agent-toolkit/skills/plan-mode/references/structure_contract_invariant_test.py"),
        pathlib.Path("agent-toolkit/share/task_documents_invariant_test.py"),
        pathlib.Path("agent-toolkit/rules/rules_invariant_test.py"),
        pathlib.Path("agent-toolkit/hooks/rules_context_registration_invariant_test.py"),
    )

    assert all(any(path.match(pattern) for pattern in targets) for path in representatives)
