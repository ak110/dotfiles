from __future__ import annotations

import hashlib
import pathlib

from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._hooks.full_reads import full_reads, skill_entry

_STATE = "external_source_guidance_read"


def _matches(path: pathlib.Path) -> bool:
    return path.parts[-2:] == ("references", "design-heuristics.md") and skill_entry(path.parent.parent / "SKILL.md") == (
        "agent-basics",
        "writing-standards",
    )


def observe(payload: dict, session_id: str, tool_name: str, tool_input: dict, cwd: str) -> None:
    record = None
    for path in full_reads(tool_name, tool_input, payload.get("tool_response"), cwd, _matches):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        record = [str(path), digest]
    if record is not None:
        update_state(session_id, lambda state: {**state, _STATE: record})


def ready(session_id: str) -> bool:
    record = read_state(session_id).get(_STATE)
    if not isinstance(record, list) or len(record) != 2 or not all(isinstance(value, str) for value in record):
        return False
    path = pathlib.Path(record[0])
    try:
        return _matches(path) and hashlib.sha256(path.read_bytes()).hexdigest() == record[1]
    except OSError:
        return False
