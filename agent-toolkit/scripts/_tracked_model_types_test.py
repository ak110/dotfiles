"""`_tracked_model_types.py`のテスト。"""

from __future__ import annotations

from _tracked_model_types import TRACKED_MODEL_TYPES


def test_tracked_model_types_cover_plan_execution_stages() -> None:
    """観測対象が計画実行系の5工程だけで構成され、探索起動と全体選定を含まないことを検証する。"""
    assert frozenset({"plan", "plan_review", "execute_fast", "execute", "execute_review"}) == TRACKED_MODEL_TYPES
    assert not TRACKED_MODEL_TYPES & {"explore", "explore_fast", "pick_feedbacks"}
