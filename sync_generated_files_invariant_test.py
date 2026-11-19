"""Codex投影へ同期する原本の配置が生成同期の対象に含まれることを確かめる。"""

import pathlib
import tomllib


def test_sync_targets_cover_agent_toolkit_skills_and_share() -> None:
    """skillsとshareの各実在階層と、Codex向け`AGENTS.md`の生成元を生成同期の起動対象に含める。"""
    root = pathlib.Path(__file__).resolve().parent
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    targets = config["tool"]["pyfltr"]["custom-commands"]["sync-generated-files"]["targets"]
    representatives = (
        pathlib.Path("agent-toolkit/share/agents-server-base.md"),
        pathlib.Path(".chezmoi-source/dot_claude/rules/myprojects-common.md"),
        pathlib.Path("agent-toolkit/skills/atk-mode/playbooks/process-wi.md"),
        pathlib.Path("agent-toolkit/skills/atk-mode/references/plan-format.md"),
        pathlib.Path("agent-toolkit/delegation_contract_invariant_test.py"),
        pathlib.Path("agent-toolkit/skills/skill_interfaces_invariant_test.py"),
        pathlib.Path("agent-toolkit/skills/atk-mode/references/session_review_contract_invariant_test.py"),
        pathlib.Path("agent-toolkit/skills/atk-mode/references/history_rewrite_invariant_test.py"),
        pathlib.Path("agent-toolkit/share/task_documents_invariant_test.py"),
        pathlib.Path("agent-toolkit/hooks/rules_context_registration_invariant_test.py"),
    )

    assert all(any(path.match(pattern) for pattern in targets) for path in representatives)
