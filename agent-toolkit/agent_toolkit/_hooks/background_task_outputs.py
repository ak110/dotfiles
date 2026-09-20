"""背景BashのタスクID・出力先・完了状態を対応付ける。"""

from __future__ import annotations

import pathlib
import re
import shlex
from collections.abc import Iterator

from agent_toolkit._hooks import stop_gate
from agent_toolkit._hooks.bash_command_parser import split_bash_segments
from agent_toolkit._hooks.session_state import update_state

_TASK_ID_RE = re.compile(r"running in background with ID:\s*([\w-]+)", re.IGNORECASE)
_OUTPUT_PATH_RE = re.compile(r"Output is being written to:\s*(/\S+)", re.IGNORECASE)
_READ_COMMANDS = frozenset({"cat", "head", "less", "more", "sed", "tail", "wc", "grep", "rg"})


def task_output_from_response(value: object) -> tuple[str, str] | None:
    """背景移行応答からタスクIDと絶対出力パスを返す。"""
    texts = list(_iter_text(value))
    task_id = stop_gate.background_task_id_from_notice(value)
    if task_id is None:
        task_id = next((match.group(1) for text in texts if (match := _TASK_ID_RE.search(text))), None)
    raw_path = next((match.group(1) for text in texts if (match := _OUTPUT_PATH_RE.search(text))), None)
    if task_id is None or raw_path is None:
        return None
    path = raw_path.rstrip(".,;:)")
    return (task_id, path) if pathlib.PurePath(path).is_absolute() else None


def command_reads_path(command: str, paths: set[str]) -> bool:
    """既知の読取コマンドが対象パスをオペランドとして持つ場合に真を返す。"""
    if not paths:
        return False
    for segment in split_bash_segments(command):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        if not tokens or pathlib.PurePath(tokens[0]).name not in _READ_COMMANDS:
            continue
        if any(token in paths for token in tokens[1:]):
            return True
    return False


def pending_bash_task_ids(transcript_path: str, session_id: str) -> set[str]:
    """stop_gateと同じ起動・完了解析から未完了の背景BashタスクIDを返す。"""
    entries = stop_gate.read_transcript_entries_cached(transcript_path)
    launched, completed, _host_reported = stop_gate._describe_pending_background_entries(  # pylint: disable=protected-access
        entries,
        session_id,
        kinds=("bash",),
        transcript_path=transcript_path,
    )
    pending_tool_use_ids = launched - completed
    task_map = stop_gate._collect_background_task_id_tool_use_ids(entries)  # pylint: disable=protected-access
    return {task_id for task_id, tool_use_ids in task_map.items() if tool_use_ids & pending_tool_use_ids}


def consume_completed_task_outputs(session_id: str, notice: object) -> bool:
    """完了通知が示すタスクIDを出力先対応表から除去する。"""
    completed_ids = {
        task_id
        for text in _iter_text(notice)
        for notification in stop_gate._TASK_NOTIFICATION_RE.findall(text)  # pylint: disable=protected-access
        for task_id in stop_gate._TASK_ID_RE.findall(notification)  # pylint: disable=protected-access
    }
    if not completed_ids:
        return False

    def _consume(state: dict) -> dict | None:
        raw = state.get("background_task_output_paths")
        if not isinstance(raw, dict):
            return None
        remaining = {task_id: path for task_id, path in raw.items() if task_id not in completed_ids}
        if remaining == raw:
            return None
        if remaining:
            state["background_task_output_paths"] = remaining
        else:
            state.pop("background_task_output_paths", None)
        return state

    return update_state(session_id, _consume)


def _iter_text(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _iter_text(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_text(nested)
