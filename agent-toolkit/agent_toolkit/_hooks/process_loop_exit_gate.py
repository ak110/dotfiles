"""報告用UWIを保存した巡回統括の終了操作を確かめる。

委譲先での実行可否: 委譲先は統括を終了しないため、メイン以外では遮断しない。
pyfltr: allow-comment
"""

import datetime
import json
import os
import re
from pathlib import Path

from agent_toolkit._hooks.notice import block_formatter
lazy from agent_toolkit._atk.session_records import codex_shell_command, codex_shell_output, exit_session_in_entries
lazy from agent_toolkit._atk.wi import process_loop_control, process_loop_log
lazy from agent_toolkit._atk.wi.frontmatter import parse_frontmatter
lazy from agent_toolkit._common import session_state, transcript
lazy from agent_toolkit._common.process_loop_session import (
    REPORT_SAVED_AT_KEY,
    is_identified_process_loop_session,
)
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS, is_atk_command
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context
lazy from agent_toolkit._hooks.background_tasks import is_pending_async_work_from_payload
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.stop_session import parse_stop_session

_block = block_formatter("process_loop_exit_gate")


_SAVE_SUCCESS = re.compile(
    r"^成功: 1件を報告として保存した\r?\n +(?P<path>[^\r\n]*[/\\][0-9]{8}-[0-9]{6}-[0-9]{3}\.md)\r?$", re.M
)


def _save_args(command: object) -> tuple[str, ...]:
    if not isinstance(command, str):
        return ()
    segments = extract_execution_segments(command)
    if len(segments) == 2 and segments[0].resolved and segments[0].tokens[:1] == ("cd",):
        segments = segments[1:]
    if len(segments) != 1 or not segments[0].resolved:
        return ()
    args = segments[0].tokens
    if not is_atk_command(args) or args[1:3] != ("wi", "add"):
        return ()
    if "--dry-run" in args or not all(
        f"{key}={value}" in args or (key, value) in zip(args[3:], args[4:], strict=False)
        for key, value in (("--type", "uwi"), ("--question-type", "report"))
    ):
        return ()
    return args[3:]


def _save_succeeded(stdout: object) -> bool:
    """保存成功の出力が示す保存済みのWIに、巡回の完了報告の印（`completion_report: true`）があるかを返す。

    原稿でなく保存結果を読むため、原稿の移動や消失で判定が変わらない。
    公開後の案内など、同じ回答形式の報告を途中で保存しても終了判定に使わない。
    pyfltr: allow-comment
    """
    if not isinstance(stdout, str) or (match := _SAVE_SUCCESS.search(stdout)) is None:
        return False
    try:
        parsed = parse_frontmatter(Path(match["path"].strip()).expanduser().read_text(encoding="utf-8"))
    except OSError, UnicodeError:
        return False
    return parsed is not None and str(parsed[0].get("completion_report")).lower() == "true"


def _current_turn(entries: list[dict]) -> list[dict]:
    start = 0
    for offset, entry in enumerate(entries):
        if entry.get("isSidechain") or entry.get("isMeta") is True or entry.get("type") != "user":
            continue
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) or (
            isinstance(content, list) and any(isinstance(item, dict) and item.get("type") == "text" for item in content)
        ):
            start = offset + 1
    return [entry for entry in entries[start:] if not entry.get("isSidechain")]


def _claude_report_saved(entries: list[dict]) -> bool:
    pending: dict[str, tuple[str, ...]] = {}
    reported = False
    for entry in entries:
        message = entry.get("message")
        if not isinstance(message, dict) or not isinstance(content := message.get("content"), list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if entry.get("type") == "assistant" and item.get("type") == "tool_use":
                if not isinstance(identifier := item.get("id"), str) or not isinstance(tool_input := item.get("input"), dict):
                    continue
                name = item.get("name")
                if name in ATK_SHELL_TOOLS and (args := _save_args(tool_input.get("command"))):
                    pending[identifier] = args
                elif isinstance(name, str) and name.endswith("__compact_conversation"):
                    pending[identifier] = ()
            elif entry.get("type") == "user" and item.get("type") == "tool_result":
                if not isinstance(identifier := item.get("tool_use_id"), str) or item.get("is_error"):
                    continue
                args = pending.pop(identifier, None)
                response = entry.get("toolUseResult")
                if args and isinstance(response, dict) and response.get("interrupted") is False:
                    reported |= _save_succeeded(response.get("stdout"))
                elif args == () and _accepted_request(response):
                    reported = False
    return reported


def _codex_current_turn(entries: list[dict], turn_id: str) -> tuple[list[dict], object]:
    start = 0
    saw_task_start = False
    latest_turn = None
    for index, entry in enumerate(entries):
        payload = entry.get("payload")
        if entry.get("type") == "compacted":
            start = index + 1
        elif entry.get("type") == "event_msg" and isinstance(payload, dict):
            kind = payload.get("type")
            if kind == "task_started":
                saw_task_start = True
                latest_turn = payload.get("turn_id")
                start = index + 1
            elif kind == "turn_aborted" or (kind == "user_message" and not saw_task_start):
                start = index + 1
    if not start or (saw_task_start and latest_turn != turn_id):
        return [], None
    return entries[start:], entries[start - 1].get("timestamp")


def _codex_report_saved(entries: list[dict]) -> bool:
    pending: dict[str, tuple[str, ...]] = {}
    for entry in entries:
        payload = entry.get("payload")
        if entry.get("type") != "response_item" or not isinstance(payload, dict):
            continue
        call_id = payload.get("call_id")
        if not isinstance(call_id, str):
            continue
        if args := _save_args(codex_shell_command(payload)):
            pending[call_id] = args
        elif (
            payload.get("type") in {"function_call_output", "custom_tool_call_output"}
            and (args := pending.pop(call_id, ()))
            and _save_succeeded(codex_shell_output(payload))
        ):
            return True
    return False


def _saved_in_codex_turn(saved_at: object, boundary: object) -> bool:
    if not isinstance(saved_at, (float, int)) or not isinstance(boundary, str):
        return False
    try:
        start = datetime.datetime.fromisoformat(boundary)
    except ValueError:
        return False
    return start.tzinfo is not None and saved_at >= start.timestamp()


def _accepted_request(response: object) -> bool:
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            return False
    return isinstance(response, dict) and response.get("ok") is True


def evaluate(payload_text: str) -> tuple[str, str]:
    resolved = parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    if not is_identified_process_loop_session(session_id, os.environ) or not is_main_agent_context(payload):
        return "approve", ""
    path = payload.get("transcript_path")
    if not isinstance(path, str):
        return "approve", ""
    codex = is_codex_payload(payload)
    records = transcript.read_transcript_entries(path)
    state = session_state.read_state(session_id)
    if codex:
        turn_id = payload.get("turn_id")
        if not isinstance(turn_id, str) or not turn_id:
            return "approve", ""
        entries, boundary = _codex_current_turn(records, turn_id)
        if not (_saved_in_codex_turn(state.get(REPORT_SAVED_AT_KEY), boundary) or _codex_report_saved(entries)):
            return "approve", ""
    else:
        entries = _current_turn(records)
        if not _claude_report_saved(entries):
            return "approve", ""
    if not codex:
        latest = payload.get("last_assistant_message")
        if isinstance(latest, str) and any(line.startswith("待機中:") for line in latest.splitlines()):
            return "approve", ""
        for message in transcript.iter_latest_assistant_text_messages(path):
            if any(line.startswith("待機中:") for line in transcript.visible_assistant_text(message).splitlines()):
                return "approve", ""
    if is_pending_async_work_from_payload(payload, session_id, session_state=state):
        return "approve", ""
    if process_loop_log.abort_path().exists():
        return "approve", ""
    request = process_loop_control.read_resume_request()
    if request is not None and request.session_id == session_id:
        return "approve", ""
    if exit_session_in_entries(entries, "codex" if codex else "claude") is True:
        return "approve", ""
    return "block", _block(
        "報告用の`UWI`は保存済みだが、巡回統括の終了操作を確認できない。",
        fix="未送達の報告があれば送達し、送達済みなら再送せず、`atk agents-exit-session`で終了してください。",
    )
