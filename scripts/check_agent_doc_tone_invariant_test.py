import pathlib

import check_agent_doc_tone
import pytest


@pytest.mark.parametrize("name", ["tone-examples.md", "textlint-violations.md"])
def test_norm_examples_satisfy_term_contract(name: str) -> None:
    """実際に配布する例と説明に、失敗対象の語が戻っていないことを確認する。"""
    references = pathlib.Path(__file__).resolve().parents[1] / "agent-basics/skills/writing-standards/references"
    path = references / name
    assert not check_agent_doc_tone.term_violations(path, path.read_text(encoding="utf-8"))
