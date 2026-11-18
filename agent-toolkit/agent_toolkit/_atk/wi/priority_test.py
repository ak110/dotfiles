"""選定順が最古枠とurgent枠の交互になり、古い項目が毎回前進することを確かめる。"""

from __future__ import annotations

import pytest

from agent_toolkit._atk.wi import priority


def test_alternates_oldest_and_oldest_urgent_without_duplicates() -> None:
    """古い通常項目と新しいurgent項目が混在すると、最古・urgent最古を交互に選ぶ。"""
    awis = [
        ("a-old.md", "normal"),
        ("b.md", "normal"),
        ("c.md", "normal"),
        ("x-urgent.md", "urgent"),
        ("y-urgent.md", "urgent"),
    ]
    assert priority.selection_order(awis) == ["a-old.md", "x-urgent.md", "b.md", "y-urgent.md", "c.md"]


def test_oldest_urgent_is_not_counted_twice() -> None:
    """最古がurgentでも重複させず、次のurgentへ進む。"""
    awis = [("a.md", "urgent"), ("b.md", "normal"), ("c.md", "urgent")]
    assert priority.selection_order(awis) == ["a.md", "c.md", "b.md"]


@pytest.mark.parametrize("count", range(1, 8))
def test_at_least_half_of_any_prefix_comes_from_oldest_order(count: int) -> None:
    """urgentが多数でも、先頭N件の少なくとも半数（切上げ）は全AWIの最古順から選ばれる。"""
    awis = [(f"{index:02d}.md", "urgent" if index >= 3 else "normal") for index in range(10)]
    prefix = priority.selection_order(awis)[:count]
    oldest = [name for name, _ in sorted(awis)][: (count + 1) // 2]
    assert set(oldest) <= set(prefix)


def test_without_urgent_falls_back_to_name_order() -> None:
    """区分の無い旧項目と解除後の項目だけなら、従来どおりファイル名の昇順になる。"""
    assert priority.selection_order([("b.md", "normal"), ("a.md", "normal")]) == ["a.md", "b.md"]
    assert priority.priority_of({}) == "normal"
    assert priority.priority_of({"priority": "high"}) == "normal"


def test_reason_requires_all_fields() -> None:
    """urgentの根拠は影響・根拠の所在・解除条件の3欄で、空の欄を検出する。"""
    metadata = {"priority": "urgent", "priority_impact": "停止", "priority_evidence": "", "priority_release": "配布"}
    assert priority.missing_reason_keys(metadata) == ["priority_evidence"]
    assert priority.reason_of({"priority_impact": "x"}) is None
