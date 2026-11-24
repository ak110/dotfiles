from __future__ import annotations

from collections.abc import Iterable, Mapping

from agent_toolkit._atk.wi import headings

PRIORITY_KEY = "priority"
URGENT = "urgent"
REASON_HEADING = "優先の根拠"
LEGACY_REASON_KEYS = ("priority_impact", "priority_evidence", "priority_release")


def priority_of(metadata: Mapping[str, object]) -> str:
    return URGENT if metadata.get(PRIORITY_KEY) == URGENT else "normal"


def reason_of(metadata: Mapping[str, object], body: str) -> str | None:
    if priority_of(metadata) != URGENT:
        return None
    lines = headings.last_h2_section_lines(body, REASON_HEADING)
    if lines is not None:
        return "\n".join(lines).strip() or None
    legacy = [metadata.get(key) for key in LEGACY_REASON_KEYS]
    if all(isinstance(value, str) and value.strip() for value in legacy):
        return "\n".join(str(value).strip() for value in legacy)
    return None


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
