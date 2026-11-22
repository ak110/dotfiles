from __future__ import annotations

from collections.abc import Iterable, Mapping

PRIORITY_KEY = "priority"
URGENT = "urgent"
REASON_KEYS = ("priority_impact", "priority_evidence", "priority_release")
"""根拠の各欄。観測した影響、観測条件と根拠の所在、優先を解除する条件の順。"""


def priority_of(metadata: Mapping[str, object]) -> str:
    return URGENT if metadata.get(PRIORITY_KEY) == URGENT else "normal"


def reason_of(metadata: Mapping[str, object]) -> dict[str, str] | None:
    if priority_of(metadata) != URGENT:
        return None
    return {key: str(metadata.get(key, "")) for key in REASON_KEYS}


def missing_reason_keys(metadata: Mapping[str, object]) -> list[str]:
    reason = reason_of(metadata)
    if reason is None:
        return []
    return [key for key, value in reason.items() if not value.strip()]


def selection_order(awis: Iterable[tuple[str, str]]) -> list[str]:
    ordered = sorted(awis)
    oldest = [name for name, _ in ordered]
    urgent = [name for name, priority in ordered if priority == URGENT]
    chosen: list[str] = []
    taken: set[str] = set()
    take_oldest = True
    while len(chosen) < len(oldest):
        source = oldest if take_oldest or not any(name not in taken for name in urgent) else urgent
        name = next(name for name in source if name not in taken)
        chosen.append(name)
        taken.add(name)
        take_oldest = not take_oldest
    return chosen
