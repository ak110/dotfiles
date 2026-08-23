"""process-loopの所要時間観測対象とするサブエージェント種別。"""

from __future__ import annotations

TRACKED_SUBAGENT_TYPES: frozenset[str] = frozenset(
    {
        "plan-impl-executor",
        "agent-toolkit:plan-impl-executor",
        "feedbacks-planner",
        "agent-toolkit:feedbacks-planner",
        "plan-review-executor",
        "agent-toolkit:plan-review-executor",
    }
)
