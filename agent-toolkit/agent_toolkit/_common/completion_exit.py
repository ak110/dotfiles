from __future__ import annotations

import json
import os
import pathlib
import re
from collections.abc import Iterator, Mapping

from agent_toolkit._common import host_homes, session_record_paths, transcript
from agent_toolkit._common.agents_server_tools import is_send_to_user_tool
from agent_toolkit._common.send_to_user import COMPLETION_KIND, normalize_completion_message

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


def _receipt_path(session: str, identifier: object, root: pathlib.Path | None) -> pathlib.Path | None:
    if re.fullmatch(r"[A-Za-z0-9_-]+", session) is None:
        return None
    if not isinstance(identifier, str) or re.fullmatch(r"[a-f0-9]{32}", identifier) is None:
        return None
    directory = root if root is not None else host_homes.claude_config_dir()
    return directory / "agent-toolkit-function-hooks" / "completion-receipts" / session / f"{identifier}.json"


def save_receipt(session: str, identifier: object, body: str, *, root: pathlib.Path | None = None) -> bool:
    path = _receipt_path(session, identifier, root)
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 送達IDは再利用せず、古い写しを後から差し替えない。 pyfltr: allow-comment
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    except OSError, UnicodeError:
        return False
    return True


def keep_delivered_report(session: str | None, result: Mapping[str, object], body: str) -> str:
    if not session or result.get("ok") is not True or save_receipt(session, result.get("id"), body):
        return ""
    return "\n本文は表示済みだが、再表示の写しを保存できなかった。本文は再送しない。"


def read_receipt(session: str, identifier: object, *, root: pathlib.Path | None = None) -> str | None:
    path = _receipt_path(session, identifier, root)
    if path is None:
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError, RecursionError:
        return None
    return body if isinstance(body, str) else None


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


def reports(entries: list[dict], *, session: str = "", root: pathlib.Path | None = None) -> dict[str, str]:
    calls: dict[str, str | None] = {}
    delivered: dict[str, str] = {}
    for entry, block in conversation_tool_blocks(entries):
        if block.get("type") == "tool_use" and is_send_to_user_tool(block.get("name")):
            args = block.get("input")
            identifier = block.get("id")
            if isinstance(args, dict) and args.get("kind") == COMPLETION_KIND and isinstance(identifier, str):
                message, reference = args.get("message"), args.get("body_ref")
                if isinstance(message, str) and reference is None:
                    calls[identifier] = message
                elif message is None and isinstance(reference, str):
                    calls[identifier] = None
        if (
            block.get("type") == "tool_result"
            and isinstance(identifier := block.get("tool_use_id"), str)
            and identifier in calls
            and (receipt := ui_receipt(entry, block)) is not None
        ):
            message = calls[identifier]
            if message is not None:
                delivered[identifier] = normalize_completion_message(message)
            elif (body := read_receipt(session, receipt.get("id"), root=root)) is not None:
                delivered[identifier] = body
    return delivered


def current_reports(entries: list[dict], *, session: str = "") -> dict[str, str]:
    for index in range(len(entries) - 1, -1, -1):
        entry = entries[index]
        if entry.get("isSidechain"):
            continue
        kind = entry.get("type")
        if entry.get("isCompactSummary") or (kind == "system" and entry.get("subtype") == "compact_boundary"):
            return reports(entries[index + 1 :], session=session)
        attachment = entry.get("attachment")
        if (
            kind == "attachment"
            and isinstance(attachment, dict)
            and (attachment.get("type") == "queued_command" or attachment.get("commandMode") == "task-notification")
        ):
            return reports(entries[index + 1 :], session=session)
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
            return reports(entries[index + 1 :], session=session)
    return reports(entries, session=session)


def report_text(state: dict, *, root: pathlib.Path | None = None) -> str | None:
    session, call = state.get("session"), state.get("call")
    if state.get("status") != "exiting" or not isinstance(session, str) or not isinstance(call, str):
        return None
    path = record_path(session, root=root)
    return (
        reports(transcript.read_transcript_entries(str(path)), session=session, root=root).get(call)
        if path is not None
        else None
    )
