from __future__ import annotations

import hashlib
import json
import pathlib

from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._common.shell_segments import extract_execution_segments
from agent_toolkit._common.shell_tokens import is_agents_wait_command, is_atk_command
from agent_toolkit._hooks.agent_id import resolve_hook_agent_id
from agent_toolkit._hooks.full_reads import full_reads, response_text, skill_entry

_STATE = "exec_review_receipt_sources"
_GUIDANCE = {
    "agent-basics:review-standards/SKILL.md": "SKILL.md",
    "agent-basics:review-standards/references/reviewee.md": "reviewee.md",
}


def _review_document(path: pathlib.Path) -> bool:
    entry = path if path.name == "SKILL.md" else path.parent.parent / "SKILL.md"
    return (path.name == "SKILL.md" or path.parts[-2:] == ("references", "reviewee.md")) and skill_entry(entry) == (
        "agent-basics",
        "review-standards",
    )


def _requirements(text: str) -> set[str]:
    result: set[str] = set()
    in_body = False
    for line in text.splitlines():
        if line.startswith("本文開始:"):
            in_body = True
        elif line.startswith("本文終了:"):
            in_body = False
        elif not in_body and line.startswith("終了行:") and " status=completed " in line:
            _, marker, value = line.partition(" read_requirements=")
            if marker:
                try:
                    names, _ = json.JSONDecoder().raw_decode(value)
                except json.JSONDecodeError:
                    continue
                if isinstance(names, list):
                    result.update(name for name in names if isinstance(name, str) and name in _GUIDANCE)
    return result


def observe(payload: dict, session_id: str, tool_name: str, tool_input: dict, cwd: str) -> list[str]:
    response = payload.get("tool_response")
    command = tool_input.get("command")
    required: set[str] = set()
    if isinstance(command, str) and any(
        item.resolved and is_agents_wait_command(item.tokens) for item in extract_execution_segments(command)
    ):
        required = _requirements(response_text(response))
    read: dict[str, list[str] | str] = {}
    if tool_name == "Skill" and tool_input.get("skill") in ("agent-basics:review-standards", "review-standards"):
        read["SKILL.md"] = "activated"
    for path in full_reads(tool_name, tool_input, response, cwd, _review_document):
        try:
            read[path.name] = [str(path), hashlib.sha256(path.read_bytes()).hexdigest()]
        except OSError:
            continue
    if not required and not read:
        return []
    actor = resolve_hook_agent_id(payload)
    tool_use_id = payload.get("tool_use_id")

    def remember(state: dict) -> dict:
        subjects = state.get(_STATE)
        subjects = dict(subjects) if isinstance(subjects, dict) else {}
        prior = subjects.get(actor, {})
        subjects[actor] = {
            "required": sorted(set(prior.get("required", [])) | required),
            "read": {**prior.get("read", {}), **read},
            "read_calls": {**prior.get("read_calls", {}), **dict.fromkeys(read, tool_use_id)},
        }
        return {**state, _STATE: subjects}

    update_state(session_id, remember)
    return _missing(_receipt(payload, session_id)) if required else []


def _still_current(record: object) -> bool:
    if record == "activated":
        return True
    if not isinstance(record, list) or len(record) != 2 or not all(isinstance(value, str) for value in record):
        return False
    path, expected = record
    try:
        return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest() == expected
    except OSError:
        return False


def _receipt(payload: dict, session_id: str) -> dict:
    subjects = read_state(session_id).get(_STATE)
    return subjects.get(resolve_hook_agent_id(payload), {}) if isinstance(subjects, dict) else {}


def _missing(prior: dict) -> list[str]:
    seen = prior.get("read", {})
    return [name for name in prior.get("required", []) if name in _GUIDANCE and not _still_current(seen.get(_GUIDANCE[name]))]


def current_read_calls(payload: dict, session_id: str) -> set[str]:
    prior = _receipt(payload, session_id)
    if _missing(prior):
        return set()
    reads = prior.get("read_calls", {})
    calls = [reads.get(_GUIDANCE[name]) for name in prior.get("required", []) if name in _GUIDANCE]
    if not all(isinstance(call, str) and call for call in calls):
        return set()
    return set(calls)


def missing_before_response(payload: dict, session_id: str, tool_input: dict) -> list[str]:
    command = tool_input.get("command")
    if (
        not session_id
        or not isinstance(command, str)
        or not any(
            item.resolved and is_atk_command(item.tokens) and item.tokens[1:3] == ("review-table", "respond")
            for item in extract_execution_segments(command)
        )
    ):
        return []
    return _missing(_receipt(payload, session_id))


def forget_reads(session_id: str, actor: str, *, retain_requirements: bool) -> None:
    def forget(state: dict) -> dict | None:
        subjects = state.get(_STATE)
        if not isinstance(subjects, dict) or actor not in subjects:
            return None
        active = dict(subjects)
        prior = active.pop(actor)
        if retain_requirements:
            active[actor] = {**prior, "read": {}, "read_calls": {}}
        return {**state, _STATE: active}

    update_state(session_id, forget)
