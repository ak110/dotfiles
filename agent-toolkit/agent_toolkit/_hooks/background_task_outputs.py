"""未完了の背景Bashが書き込む出力ファイルを、transcriptの起動記録から求める。"""

from __future__ import annotations

import pathlib
import shlex

from agent_toolkit._common import background_output
from agent_toolkit._hooks import stop_gate
from agent_toolkit._hooks.bash_command_parser import split_bash_segments

_READ_COMMANDS = frozenset({"cat", "head", "less", "more", "sed", "tail", "wc", "grep", "rg"})


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


def pending_task_output_paths(transcript_path: str, session_id: str) -> set[str]:
    """未完了の背景Bashが書き込む出力ファイルの絶対パスを返す。

    出力パスは、タスクIDを返した起動の`tool_result`本文（`Output is being written to: <パス>`）から得る。
    PostToolUseの`tool_response`は構造化した`backgroundTaskId`だけを持ち出力パスを含まないため、入力に使えない。
    起動の`tool_use_id`に対応する`tool_result`だけを読み、前景Bashや他ツールの結果本文に同じ文言が現れても対象にしない。
    """
    pending_ids = pending_bash_task_ids(transcript_path, session_id)
    if not pending_ids:
        return set()
    entries = stop_gate.read_transcript_entries_cached(transcript_path)
    task_map = stop_gate._collect_background_task_id_tool_use_ids(entries)  # pylint: disable=protected-access
    launch_tool_use_ids = {tool_use_id for task_id in pending_ids for tool_use_id in task_map.get(task_id, set())}
    paths: set[str] = set()
    for entry in entries:
        message = entry.get("message") if entry.get("type") == "user" else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                not isinstance(block, dict)
                or block.get("type") != "tool_result"
                or block.get("tool_use_id") not in launch_tool_use_ids
            ):
                continue
            for text in stop_gate._tool_result_text_blocks(block.get("content")):  # pylint: disable=protected-access
                paths.update(background_output.output_paths(text))
    return paths
