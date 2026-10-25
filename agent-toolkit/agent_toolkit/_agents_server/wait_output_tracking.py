"""委譲先が実行した`atk agents wait`とagents_serverのツールの結果から、回収済みと未観測の子sessionを追跡する。"""

from __future__ import annotations

import contextlib
import json
import logging
import pathlib
from collections.abc import Callable, Mapping
from typing import Any

from agent_toolkit._agents_server import tool_names, wait_summary
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.state import TERMINAL_STATUSES, SessionState
from agent_toolkit._common import background_output
from agent_toolkit._common.shell_segments import extract_execution_segments
from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS, is_agents_wait_command

# 子を稼働させる操作と、その終端・回収を観測する操作の対応。
# CLI待機はBashとCodexのcommandExecutionの両方から同じ回収処理へ入る。
_AGENTS_WAIT_TOOL_USE = "atk agents wait"
_CHILD_SESSION_OPERATIONS = {
    **dict.fromkeys(tool_names.RECORDED_START_OPERATIONS, "activate"),
    "send_message": "resume",
    "kill": "terminal",
    _AGENTS_WAIT_TOOL_USE: "collect",
}


def consume_claude_agents_server_message(
    session: SessionState, message: Any, *, collected_output: Callable[[str], None] | None = None
) -> None:
    """Claude SDKのツール利用と結果から、孫sessionと未完了のツール呼び出しの状態を更新する。

    未完了のツール呼び出しは`agents_server`のツールに限らず記録する。
    記録の対象を`agents_server`のツールへ限ると、委譲元は委譲先が何で止まっているかを
    `show`の応答から判定できない。
    """
    for block in _content_blocks(message):
        tool_use_id = _block_value(block, "id")
        tool_name = _block_value(block, "name")
        tool_input = _block_value(block, "input")
        if isinstance(tool_use_id, str) and isinstance(tool_name, str):
            session.record_tool_use_start(tool_use_id, tool_name, tool_input)
            normalized = _agents_server_tool_name(tool_name)
            if normalized is not None:
                arguments = dict(tool_input) if isinstance(tool_input, Mapping) else {}
                session.child_tool_uses[tool_use_id] = (normalized, arguments)
            elif tool_name in ATK_SHELL_TOOLS and _is_agents_wait_command(tool_input):
                session.child_tool_uses[tool_use_id] = (_AGENTS_WAIT_TOOL_USE, {})
            continue

        tool_use_id = _block_value(block, "tool_use_id")
        if not isinstance(tool_use_id, str):
            continue
        session.record_tool_use_end(tool_use_id)
        tool_use = session.child_tool_uses.pop(tool_use_id, None)
        if tool_use is None:
            continue
        if _CHILD_SESSION_OPERATIONS.get(tool_use[0]) == "collect":
            text = _tool_result_text(_block_value(block, "content"))
            if collected_output is None:
                consume_agents_wait_output(session, text)
            else:
                collected_output(text)
            continue
        result = _structured_tool_result(_block_value(block, "content"))
        if result is not None:
            consume_agents_server_tool_result(session, tool_use[0], tool_use[1], result)


async def consume_claude_agents_server_message_async(session: SessionState, message: Any) -> None:
    """ツール記録をloopで反映し、待機の保存出力の読取をworkerで待つ。"""
    outputs: list[str] = []
    consume_claude_agents_server_message(session, message, collected_output=outputs.append)
    for text in outputs:
        await consume_agents_wait_output_async(session, text)


async def consume_agents_wait_output_async(session: SessionState, text: str) -> None:
    """ファイル読取の後、回収した識別子だけをloopで反映する。"""
    collected = await run_file_operation(_collected_from_wait_output, text)
    session.agents_wait_background_outputs.update(background_output.output_paths(text))
    _discard_collected(session, collected)


async def consume_agents_wait_background_outputs_async(session: SessionState) -> None:
    """背景出力のsnapshotを読み、sessionの追跡集合をloopで更新する。"""
    paths = tuple(session.agents_wait_background_outputs)
    if not paths:
        return
    collected = await run_file_operation(_read_background_outputs, paths)
    _discard_collected(session, collected)


def _read_background_outputs(paths: tuple[str, ...]) -> set[str]:
    collected: set[str] = set()
    for path in paths:
        with contextlib.suppress(OSError, UnicodeError):
            collected |= _collected_from_wait_output(pathlib.Path(path).read_text(encoding="utf-8"))
    return collected


def consume_agents_server_tool_result(
    session: SessionState,
    tool_name: str,
    arguments: Mapping[str, Any],
    result: Mapping[str, Any],
) -> None:
    """agents_serverツールの結果を孫session集合へ反映する。"""
    normalized = _agents_server_tool_name(tool_name)
    operation = _CHILD_SESSION_OPERATIONS.get(normalized or "")
    if operation in {"activate", "resume"}:
        if operation == "resume" and result.get("delivery") == "reply_failed":
            return
        session_id = arguments.get("session_id") if operation == "resume" else result.get("session_id")
        if isinstance(session_id, str) and session_id:
            if session_id not in session.live_child_session_ids:
                # 新しい委譲は前の完了通知と別の待機単位であり、その終端でも一度だけ再開できる。
                session.auto_resume_consumed = False
            session.live_child_session_ids.add(session_id)
            session.terminal_child_session_ids.discard(session_id)
        return
    if operation != "terminal":
        return
    session_id = arguments.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return
    if result.get("status") in TERMINAL_STATUSES | {"expired"}:
        # このレコードは孫sessionを所有する別プロセスが持つため、観測側は削除しない。
        session.live_child_session_ids.discard(session_id)
        session.terminal_child_session_ids.add(session_id)


# `atk`の共通の自動保存が、要約が長い場合に標準出力へ書く保存先の行。
_AGENTS_WAIT_SAVED_PREFIX = "保存先: "
WAIT_BODY_START_PREFIX = wait_summary.WAIT_BODY_START_PREFIX
WAIT_BODY_END_PREFIX = wait_summary.WAIT_BODY_END_PREFIX


def _is_agents_wait_command(tool_input: Any) -> bool:
    command = tool_input.get("command") if isinstance(tool_input, Mapping) else None
    return isinstance(command, str) and any(
        segment.resolved and is_agents_wait_command(segment.tokens) for segment in extract_execution_segments(command)
    )


def _tool_result_text(value: Any) -> str:
    """ツール結果の本文を文字列として連結する。"""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        text = value.get("text")
        if isinstance(text, str):
            return text
        return _tool_result_text(value.get("content"))
    if isinstance(value, list | tuple):
        return "\n".join(_tool_result_text(item) for item in value)
    return ""


def _collected_session_ids(text: str) -> set[str]:
    """`atk agents wait`のJSON Lines出力とエージェント環境の要約の`終端行:`から、終端結果を回収したsession識別子を返す。"""
    collected = wait_summary.terminal_session_ids(text)
    for line in text.splitlines():
        try:
            payload: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("session_id"), str)
            and payload.get("status") in TERMINAL_STATUSES
        ):
            collected.add(payload["session_id"])
    return collected


def consume_agents_wait_output(session: SessionState, text: str) -> None:
    """委譲先が`atk agents wait`で終端結果を回収した孫sessionを自動再開の追跡から外す。

    回収済みの結果は再配送されないため、そのsessionの終端を理由に委譲先を再開させると、
    委譲先は受け取り済みの結果について同じ報告を返し直すだけのturnを費やす。
    回収の根拠は待機コマンドが返したJSON Linesかエージェント環境の要約の`終端行:`とする。
    要約が長く保存された場合は標準出力が示す保存先を読む。
    結果ファイルの不在は公開前の状態と区別できないため、回収の根拠に用いない。
    ホストが待機を背景実行へ移した場合は結果本文が出力ファイルへ書かれるため、そのパスを記録し、
    `consume_agents_wait_background_outputs`が判定の直前に読む。
    """
    collected = _collected_from_wait_output(text)
    session.agents_wait_background_outputs.update(background_output.output_paths(text))
    _discard_collected(session, collected)


def consume_agents_wait_background_outputs(session: SessionState) -> None:
    """背景実行の`atk agents wait`が出力ファイルへ書いた終端結果の孫sessionを追跡から外す。

    孫sessionの終端判定と未観測の記録の直前に呼ぶ。出力ファイルが無い、読めない、終端statusの行が無い
    （待機が未完了、または`status: running`だけ）場合は追跡に残す。
    """
    collected: set[str] = set()
    for path in session.agents_wait_background_outputs:
        with contextlib.suppress(OSError, UnicodeError):
            collected |= _collected_from_wait_output(pathlib.Path(path).read_text(encoding="utf-8"))
    _discard_collected(session, collected)


def _collected_from_wait_output(text: str) -> set[str]:
    """`atk agents wait`の標準出力から、終端結果を回収したsession識別子を返す。

    標準出力はJSON Linesか、エージェント環境の要約（長い場合は共通の自動保存による保存先の行）を持つ。
    ツール結果の本文と背景実行の出力ファイルはどちらも標準出力そのものであるため、同じ規則で読む。
    要約が表示する委譲先の本文は待機の出力を引用して終端行や保存先の行と同じ形の行を含み得るため、
    本文の範囲の行は回収の根拠から外す。
    """
    lines = _lines_outside_wait_bodies(text)
    collected = _collected_session_ids("\n".join(lines))
    for line in lines:
        if not line.startswith(_AGENTS_WAIT_SAVED_PREFIX):
            continue
        with contextlib.suppress(OSError, UnicodeError):
            collected |= _collected_session_ids(
                pathlib.Path(line.removeprefix(_AGENTS_WAIT_SAVED_PREFIX).strip()).read_text(encoding="utf-8")
            )
    return collected


def _lines_outside_wait_bodies(text: str) -> list[str]:
    """`本文開始:`の行から同じsession識別子の`本文終了:`の行までを除いた行を返す。

    終了の行が無い範囲は末尾まで本文として扱い、本文の行を回収の根拠へ混ぜない。
    """
    lines: list[str] = []
    end_line: str | None = None
    for line in text.splitlines():
        if end_line is not None:
            if line == end_line:
                end_line = None
            continue
        if line.startswith(WAIT_BODY_START_PREFIX):
            end_line = WAIT_BODY_END_PREFIX + line.removeprefix(WAIT_BODY_START_PREFIX)
            continue
        lines.append(line)
    return lines


def _discard_collected(session: SessionState, collected: set[str]) -> None:
    session.live_child_session_ids.difference_update(collected)
    session.terminal_child_session_ids.difference_update(collected)


def _agents_server_tool_name(tool_name: str) -> str | None:
    for prefix in tool_names.MCP_NAMESPACES:
        if tool_name.startswith(prefix):
            return tool_name.removeprefix(prefix)
    if tool_name in _CHILD_SESSION_OPERATIONS:
        return tool_name
    return None


def _content_blocks(message: Any) -> tuple[Any, ...]:
    content = _block_value(message, "content")
    return tuple(content) if isinstance(content, list | tuple) else ()


def _block_value(block: Any, name: str) -> Any:
    return block.get(name) if isinstance(block, Mapping) else getattr(block, name, None)


def _structured_tool_result(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        structured = value.get("structuredContent")
        if isinstance(structured, Mapping):
            return dict(structured)
        if isinstance(value.get("session_id"), str) or "status" in value or "delivery" in value:
            return dict(value)
        for key in ("content", "result"):
            nested = _structured_tool_result(value.get(key))
            if nested is not None:
                return nested
        text = value.get("text")
        if isinstance(text, str):
            return _structured_tool_result(text)
        return None
    if isinstance(value, list | tuple):
        for item in value:
            result = _structured_tool_result(item)
            if result is not None:
                return result
        return None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return _structured_tool_result(decoded)
    return None


_LOG = logging.getLogger("agent-toolkit.agents-server.state")
