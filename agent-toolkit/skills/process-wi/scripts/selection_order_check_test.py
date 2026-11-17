"""選定生成が、新しく着手するAWIを交互の選定順の先頭から選んだかと、urgentの根拠の欠落を検出することを確かめる。"""

from __future__ import annotations

from typing import Any

import pytest
import selection_contract

_REASON = {"priority_impact": "担当を起動できない", "priority_evidence": "ログ", "priority_release": "修正の配布"}


def _facts() -> dict[str, dict[str, Any]]:
    """古い通常項目、新しいurgent、回答済みUWI、依存待ち、処理中の項目が混在する候補。"""
    rows: list[dict[str, Any]] = [
        {"filename": "01-old.md", "type": "awi", "state": "inbox", "ready": True},
        {"filename": "02-old.md", "type": "awi", "state": "inbox", "ready": True},
        {"filename": "03-old.md", "type": "awi", "state": "inbox", "ready": True},
        {
            "filename": "08-urgent.md",
            "type": "awi",
            "state": "inbox",
            "ready": True,
            "priority": "urgent",
            "priority_reason": _REASON,
        },
        {
            "filename": "09-urgent.md",
            "type": "awi",
            "state": "inbox",
            "ready": True,
            "priority": "urgent",
            "priority_reason": _REASON,
        },
        {
            "filename": "00-wait.md",
            "type": "awi",
            "state": "inbox",
            "ready": False,
            "priority": "urgent",
            "priority_reason": _REASON,
        },
        {"filename": "05-answered.md", "type": "uwi", "state": "inbox", "ready": True},
        {"filename": "04-processing.md", "type": "awi", "state": "processing", "ready": False},
    ]
    return {row["filename"]: row for row in rows}


@pytest.mark.parametrize(
    "selected",
    [
        {"05-answered.md", "04-processing.md", "01-old.md", "08-urgent.md"},
        {"01-old.md", "08-urgent.md", "02-old.md", "09-urgent.md", "03-old.md"},
        set(),
    ],
)
def test_prefix_of_alternating_order_is_accepted(selected: set[str]) -> None:
    """UWI・処理中は順序の対象外で、新規AWIは最古・urgent最古の交互の先頭から選ぶ。依存待ちは選ばれない。"""
    selection_contract._check_selection_order(_facts(), selected, set())  # pylint: disable=protected-access


def test_skipping_oldest_for_urgent_items_is_rejected() -> None:
    """urgentだけを先に全件入れて古い項目を押し出す選定は拒否し、期待の先頭を示す。"""
    with pytest.raises(ValueError, match="01-old.md, 08-urgent.md"):
        selection_contract._check_selection_order(_facts(), {"08-urgent.md", "09-urgent.md"}, set())  # pylint: disable=protected-access


def test_excluded_candidate_is_removed_from_order() -> None:
    """除外候補として理由を記録した項目は順から除き、次の項目で埋める。"""
    selection_contract._check_selection_order(_facts(), {"02-old.md", "08-urgent.md"}, {"01-old.md"})  # pylint: disable=protected-access


def test_urgent_without_reason_is_rejected() -> None:
    """根拠の無いurgentは順序の根拠にしない。"""
    facts = _facts()
    facts["08-urgent.md"]["priority_reason"] = {**_REASON, "priority_release": ""}
    with pytest.raises(ValueError, match="08-urgent.md"):
        selection_contract._check_selection_order(facts, {"01-old.md"}, set())  # pylint: disable=protected-access
