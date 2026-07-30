"""`_tracked_subagent_types.py`のテスト。"""

from __future__ import annotations

from _tracked_subagent_types import TRACKED_SUBAGENT_TYPES


def test_tracked_subagent_types_are_orchestrators() -> None:
    """追跡対象が2つの委譲窓口の短縮名と完全修飾名だけであることを検証する。"""
    assert (
        frozenset(
            {
                "plan-file-finalizer",
                "agent-toolkit:plan-file-finalizer",
                "plan-impl-executor",
                "agent-toolkit:plan-impl-executor",
            }
        )
        == TRACKED_SUBAGENT_TYPES
    )
