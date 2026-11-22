from __future__ import annotations

import pytest

from agent_toolkit._atk.wi import priority


def test_alternates_oldest_and_oldest_urgent_without_duplicates() -> None:
    awis = [
        ("a-old.md", "normal"),
        ("b.md", "normal"),
        ("c.md", "normal"),
        ("x-urgent.md", "urgent"),
        ("y-urgent.md", "urgent"),
    ]
    assert priority.selection_order(awis) == ["a-old.md", "x-urgent.md", "b.md", "y-urgent.md", "c.md"]


def test_oldest_urgent_is_not_counted_twice() -> None:
    awis = [("a.md", "urgent"), ("b.md", "normal"), ("c.md", "urgent")]
    assert priority.selection_order(awis) == ["a.md", "c.md", "b.md"]


@pytest.mark.parametrize("count", range(1, 8))
def test_at_least_half_of_any_prefix_comes_from_oldest_order(count: int) -> None:
    awis = [(f"{index:02d}.md", "urgent" if index >= 3 else "normal") for index in range(10)]
    prefix = priority.selection_order(awis)[:count]
    oldest = [name for name, _ in sorted(awis)][: (count + 1) // 2]
    assert set(oldest) <= set(prefix)


def test_without_urgent_falls_back_to_name_order() -> None:
    assert priority.selection_order([("b.md", "normal"), ("a.md", "normal")]) == ["a.md", "b.md"]
    assert priority.priority_of({}) == "normal"
    assert priority.priority_of({"priority": "high"}) == "normal"


def test_reason_requires_all_fields() -> None:
    metadata = {"priority": "urgent", "priority_impact": "停止", "priority_evidence": "", "priority_release": "配布"}
    assert priority.missing_reason_keys(metadata) == ["priority_evidence"]
    assert priority.reason_of({"priority_impact": "x"}) is None
