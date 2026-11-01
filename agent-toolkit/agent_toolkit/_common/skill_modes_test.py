"""起動モードの完全一致と自由記述の保持を検査する。"""

import pytest

from agent_toolkit._common import skill_modes


@pytest.mark.parametrize(
    "skill,arguments,mode,remaining",
    [
        ("process-wi", None, None, None),
        ("review-session", "", None, ""),
        ("process-wi", "single-lane 追記  内容\n", "single-lane", "追記  内容\n"),
        ("process-wi", " \tall-repos\n追加指示", "all-repos", "追加指示"),
        ("review-session", "batch auto", "batch", "auto"),
        ("review-session", "batch", "batch", ""),
        ("review-session", "batchx", None, "batchx"),
        ("review-session", "batching 注意", None, "batching 注意"),
        ("review-session", "auto", None, "auto"),
        ("process-wi", "unknown all-repos", None, "unknown all-repos"),
        ("process-wi", "single-lanex", None, "single-lanex"),
        ("other", "batch", None, "batch"),
    ],
)
def test_split_mode(skill, arguments, mode, remaining):
    assert skill_modes.split_mode(f"agent-toolkit:{skill}", arguments) == (mode, remaining)


def test_scope_ignores_free_text_and_separates_modes():
    scope = skill_modes.invocation_scope
    assert scope("agent-toolkit:process-wi", "single-lane A") == scope("agent-toolkit:process-wi", "single-lane B")
    assert scope("agent-toolkit:process-wi", "single-lane") != scope("agent-toolkit:process-wi", "all-repos")
    assert scope("agent-toolkit:review-session", "batch auto") != scope("agent-toolkit:review-session", "auto")
