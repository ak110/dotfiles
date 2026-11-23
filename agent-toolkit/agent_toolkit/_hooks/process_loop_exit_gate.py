"""報告用UWIを保存した巡回統括の終了操作を確かめる。

委譲先での実行可否: 委譲先は統括を終了しないため、メイン以外では遮断しない。
pyfltr: allow-comment
"""

import json
import os
import re
from pathlib import Path

from agent_toolkit._hooks.notice import block_formatter
lazy from agent_toolkit._atk.session_records import exit_session_in_entries
lazy from agent_toolkit._atk.wi import process_loop_control, process_loop_log
lazy from agent_toolkit._common import session_state, transcript
lazy from agent_toolkit._common.process_loop_session import is_identified_process_loop_session
lazy from agent_toolkit._common.shell_segments import extract_execution_segments
lazy from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS, is_atk_command
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context
lazy from agent_toolkit._hooks.background_tasks import is_pending_async_work_from_payload
lazy from agent_toolkit._hooks.stop_session import parse_stop_session

_block = block_formatter("process_loop_exit_gate")


_REPORT_END = "以上で、このセッションの作業は全て終わった。"
_REPORT_QUESTION = re.compile(r"[0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日の.+の作業結果について、この対応で問題ありませんか？")


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
        for key, value in (("--type", "uwi"), ("--question-type", "post-approval"))
    ):
        return ()
    return args[3:]


def _has_compared_report_opening(args: tuple[str, ...]) -> bool:
    for index, arg in enumerate(args):
        if arg == "--compare-opening-file" and index + 1 < len(args):
            value = args[index + 1]
        elif arg.startswith("--compare-opening-file="):
            value = arg.partition("=")[2]
        else:
            continue
        path = Path(value)
        try:
            if path.is_absolute() and path.is_file():
                with path.open(encoding="utf-8") as source:
                    line = source.readline(4096)
                    if len(line) < 4096 and _REPORT_QUESTION.fullmatch(line.rstrip()):
                        return True
        except OSError, UnicodeError:
            continue
    return False


def _saved_report_name(entry: dict) -> str | None:
    response = entry.get("toolUseResult")
    if not isinstance(response, dict) or response.get("interrupted") is not False:
        return None
    stdout = response.get("stdout")
    if not isinstance(stdout, str):
        return None
    match = re.match(r"成功: 1件をinboxへ投入した\n +[^\r\n]*[/\\]([0-9]{8}-[0-9]{6}-[0-9]{3}\.md)(?:\r?\n|$)", stdout)
    return match[1] if match else None


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


def _report_requires_exit(entries: list[dict]) -> bool:
    pending: dict[str, tuple[str, ...]] = {}
    saved: set[str] = set()
    reported = False
    for entry in entries:
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        if entry.get("type") == "assistant":
            reported |= _matches_report(transcript.visible_assistant_text(message), saved)
        if not isinstance(content := message.get("content"), list):
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
                elif (
                    isinstance(name, str)
                    and name.endswith(transcript.SEND_TO_USER_TOOL_SUFFIX)
                    and isinstance(report := tool_input.get("message"), str)
                ):
                    reported |= _matches_report(report, saved)
            elif entry.get("type") == "user" and item.get("type") == "tool_result":
                if not isinstance(identifier := item.get("tool_use_id"), str) or item.get("is_error"):
                    continue
                args = pending.pop(identifier, None)
                if args and (filename := _saved_report_name(entry)) is not None:
                    saved.add(filename)
                    reported |= _has_compared_report_opening(args)
                elif args == () and _accepted_request(entry.get("toolUseResult")):
                    saved.clear()
                    reported = False
    return reported


def _matches_report(text: str, saved: set[str]) -> bool:
    return text.rstrip().endswith(_REPORT_END) and any(filename in text for filename in saved)


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
    entries = _current_turn(transcript.read_transcript_entries(path))
    if not _report_requires_exit(entries):
        return "approve", ""
    for message in transcript.iter_latest_assistant_text_messages(path):
        if any(line.startswith("待機中:") for line in transcript.visible_assistant_text(message).splitlines()):
            return "approve", ""
    if is_pending_async_work_from_payload(payload, session_id, session_state=session_state.read_state(session_id)):
        return "approve", ""
    if process_loop_log.abort_path().exists():
        return "approve", ""
    request = process_loop_control.read_resume_request()
    if request is not None and request.session_id == session_id:
        return "approve", ""
    if exit_session_in_entries(entries, "claude") is not False:
        return "approve", ""
    return "block", _block(
        "報告用の`UWI`は保存済みだが、巡回統括の終了操作を確認できない。",
        fix="未送達の報告があれば送達し、送達済みなら再送せず、`atk agents-exit-session`で終了せよ。",
    )
