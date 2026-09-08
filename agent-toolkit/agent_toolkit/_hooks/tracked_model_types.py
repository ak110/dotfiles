"""process-loopの所要時間観測対象とする`agents_server`の`model_type`。"""

from __future__ import annotations

TRACKED_MODEL_TYPES: frozenset[str] = frozenset(
    {
        "plan",
        "plan_review",
        "execute_fast",
        "execute",
        "execute_review",
    }
)
