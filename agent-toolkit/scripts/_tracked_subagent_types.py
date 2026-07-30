"""process-loopの所要時間観測対象とするサブエージェント種別。"""

from __future__ import annotations

TRACKED_SUBAGENT_TYPES: frozenset[str] = frozenset(
    {
        "plan-file-finalizer",
        "agent-toolkit:plan-file-finalizer",
        "plan-impl-executor",
        "agent-toolkit:plan-impl-executor",
    }
)
