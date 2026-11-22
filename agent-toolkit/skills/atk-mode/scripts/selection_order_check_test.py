"""選定生成が処理可能な候補の脱落とurgentの根拠の欠落を検出することを確かめる。"""

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


def _required(facts: dict[str, dict[str, Any]]) -> set[str]:
    return facts.keys() - {"00-wait.md"}


def test_all_ready_and_processing_candidates_are_accepted() -> None:
    facts = _facts()
    selection_contract._check_ready_selection(facts, _required(facts), set())  # pylint: disable=protected-access


def test_selecting_only_the_prefix_is_rejected() -> None:
    """先頭の一部しか選ばなければ、後続の処理可能な候補を示す。"""
    with pytest.raises(ValueError, match="02-old.md.*03-old.md"):
        selection_contract._check_ready_selection(  # pylint: disable=protected-access
            _facts(), {"05-answered.md", "04-processing.md", "01-old.md", "08-urgent.md"}, set()
        )


def test_excluded_candidate_is_not_required() -> None:
    """既存の認可・処理可否で除外された候補は全件判定の対象外である。"""
    facts = _facts()
    excluded = {"01-old.md"}
    selection_contract._check_ready_selection(facts, _required(facts) - excluded, excluded)  # pylint: disable=protected-access


def test_urgent_without_reason_is_rejected() -> None:
    """根拠の無いurgentは順序の根拠にしない。"""
    facts = _facts()
    facts["08-urgent.md"]["priority_reason"] = {**_REASON, "priority_release": ""}
    with pytest.raises(ValueError, match="08-urgent.md"):
        selection_contract._check_ready_selection(facts, {"01-old.md"}, set())  # pylint: disable=protected-access
