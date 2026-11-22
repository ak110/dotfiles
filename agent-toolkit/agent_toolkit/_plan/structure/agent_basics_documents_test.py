import pytest

from agent_toolkit._plan import structure

pytestmark = pytest.mark.windows


@pytest.mark.parametrize(
    "path",
    [
        "agent-basics/skills/writing-standards/SKILL.md",
        "agent-basics/skills/writing-standards/references/python.md",
        "agent-basics/skills/review-standards/references/reviewer.md",
    ],
)
def test_quality_documents_are_agent_doc_targets(path: str) -> None:
    assert structure.is_agent_doc_target_file(path)


def test_plugin_readme_keeps_human_document_scope() -> None:
    assert not structure.is_agent_doc_target_file("agent-basics/README.md")
