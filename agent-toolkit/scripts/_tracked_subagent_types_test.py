"""`_tracked_subagent_types.py`のテスト。"""

from __future__ import annotations

from _tracked_subagent_types import TRACKED_SUBAGENT_TYPES


def test_tracked_subagent_types_are_orchestrators() -> None:
    """追跡対象が委譲調整役の短縮名と完全修飾名だけであることを検証する。"""
    assert (
        frozenset(
            {
                "plan-impl-executor",
                "agent-toolkit:plan-impl-executor",
                "feedbacks-planner",
                "agent-toolkit:feedbacks-planner",
            }
        )
        == TRACKED_SUBAGENT_TYPES
    )
