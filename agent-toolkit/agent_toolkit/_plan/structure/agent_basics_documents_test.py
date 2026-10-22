"""品質プラグインの改訂もエージェント文書の検収対象へ届くことを確認する。"""

import pytest

from agent_toolkit._plan import structure


@pytest.mark.parametrize(
    "path",
    [
        "agent-basics/skills/writing-standards/SKILL.md",
        "agent-basics/skills/writing-standards/references/python.md",
        "agent-basics/skills/review-standards/references/reviewer.md",
    ],
)
def test_quality_documents_are_agent_doc_targets(path: str) -> None:
    """品質規範の変更が計画・統合時のエージェント文書判定へ含まれる。"""
    assert structure.is_agent_doc_target_file(path)


def test_plugin_readme_keeps_human_document_scope() -> None:
    """導入案内はエージェント規範の変更対象と区別する。"""
    assert not structure.is_agent_doc_target_file("agent-basics/README.md")
