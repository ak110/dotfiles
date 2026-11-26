from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Iterator

from agent_toolkit._common import host_homes, session_record_paths, transcript
from agent_toolkit._common.agents_server_tools import is_send_to_user_tool

LAUNCH_ENV = "AGENT_TOOLKIT_COMPLETION_LAUNCH_ID"
DISABLE_ENV = "AGENT_TOOLKIT_KEEP_SESSION"


def state_path(launch: str, *, root: pathlib.Path | None = None) -> pathlib.Path | None:
    if re.fullmatch(r"[0-9a-f]{32}", launch) is None:
        return None
    directory = root if root is not None else host_homes.claude_config_dir()
    return directory / "agent-toolkit-function-hooks" / f"completion-exit-{launch}.txt"


def read_state(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError, RecursionError:
        return {}
    return value if isinstance(value, dict) else {}


def write_state(path: pathlib.Path, state: dict) -> None:
    pending = path.with_suffix(".pending.txt")
    pending.write_text(json.dumps(state), encoding="utf-8")
    pending.replace(path)


def record_path(session: str, *, root: pathlib.Path | None = None) -> pathlib.Path | None:
    paths = session_record_paths.find_claude_records(session, claude_home=root)
    return paths[0] if len(paths) == 1 else None


def conversation_tool_blocks(entries: list[dict]) -> Iterator[tuple[dict, dict]]:
    for entry in entries:
        if entry.get("isSidechain") or entry.get("type") not in ("assistant", "user"):
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            yield from ((entry, item) for item in content if isinstance(item, dict))


def ui_receipt(entry: dict, block: dict) -> dict | None:
    if block.get("is_error") is True:
        return None
    content = block.get("content")
    items = content if isinstance(content, list) else [{"text": content}]
    # REPL内のMCP呼び出しは同じIDの仮想tool_resultの本文が空で、応答はtoolUseResultに残る。pyfltr: allow-comment
    if entry.get("isVirtual") is True:
        items = [*items, {"text": entry.get("toolUseResult")}]
    for item in items:
        body = item.get("text") if isinstance(item, dict) else None
        if not isinstance(body, str):
            continue
        try:
            value = json.loads(body)
        except ValueError, RecursionError:
            continue
        if isinstance(value, dict) and value.get("ok") is True:
            return value
    return None


def reports(entries: list[dict]) -> dict[str, str]:
    calls: dict[str, str] = {}
    delivered: dict[str, str] = {}
    for entry, block in conversation_tool_blocks(entries):
        if block.get("type") == "tool_use" and is_send_to_user_tool(block.get("name")):
            args = block.get("input")
            identifier = block.get("id")
            if (
                isinstance(args, dict)
                and args.get("kind") == "completion"
                and isinstance(args.get("message"), str)
                and isinstance(identifier, str)
            ):
                calls[identifier] = args["message"]
        if (
            block.get("type") == "tool_result"
            and isinstance(identifier := block.get("tool_use_id"), str)
            and identifier in calls
            and ui_receipt(entry, block) is not None
        ):
            delivered[identifier] = calls[identifier]
    return delivered


def current_reports(entries: list[dict]) -> dict[str, str]:
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if entry.get("isSidechain"):
            continue
        kind = entry.get("type")
        if entry.get("isCompactSummary") or (kind == "system" and entry.get("subtype") == "compact_boundary"):
            return reports(entries[index + 1 :])
        attachment = entry.get("attachment")
        if (
            kind == "attachment"
            and isinstance(attachment, dict)
            and (attachment.get("type") == "queued_command" or attachment.get("commandMode") == "task-notification")
        ):
            return reports(entries[index + 1 :])
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if (
            kind == "user"
            and not entry.get("isMeta")
            and (
                isinstance(content, str)
                or isinstance(content, list)
                and any(isinstance(item, dict) and item.get("type") == "text" for item in content)
            )
        ):
            return reports(entries[index + 1 :])
    return reports(entries)


def report_text(state: dict, *, root: pathlib.Path | None = None) -> str | None:
    session, call = state.get("session"), state.get("call")
    if state.get("status") != "exiting" or not isinstance(session, str) or not isinstance(call, str):
        return None
    path = record_path(session, root=root)
    return reports(transcript.read_transcript_entries(str(path))).get(call) if path is not None else None
