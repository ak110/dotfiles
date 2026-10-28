"""保存済みセッション記録（Claude Code・Codex・Antigravity）からprocess-wiの起動と終了工程の到達を判定する。

記録の形式（保存先の規約、行の解析）は`session_record_format`が解釈し、本モジュールはその上の判定を持つ。
"""

from __future__ import annotations

import contextlib
import datetime
import io
import json
import pathlib
from collections.abc import Iterator
from typing import Any

from agent_toolkit._atk import session_record_format
from agent_toolkit._atk.wi.constants import PROCESS_WI_GOAL_BODY
from agent_toolkit._atk.wi.repo import resolve_repo_id
from agent_toolkit._common import host_homes, session_state
from agent_toolkit._common.agy_records import normalize_entries
from agent_toolkit._common.shell_segments import extract_execution_segments
from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS, is_agents_exit_session_command

# Claude Codeのハーネスが、`Skill`ツールの起動の`tool_result`として記録する起動確認文言。
_CLAUDE_PROCESS_WI_MARKERS = (
    "Launching skill: agent-toolkit:process-wi",
    "Launching skill: agent-toolkit:process-wi-all-repos",
)
_CLAUDE_EXIT_SESSION_MARKER = "Launching skill: agent-toolkit:exit-session"

# 記録にはホストの前置きや/goalの境界が含まれるため、目的文本体の包含で判定する。
# 登録全体の統括へ移行した後も、手動process-wiと過去の記録を検出対象に残す。
_PROCESS_WI_GOAL_BODIES = (PROCESS_WI_GOAL_BODY, "agent-toolkit:process-wi-all-reposを完遂する")

_TEXT_EXCERPT_LIMIT = 200


def candidate_paths() -> Iterator[tuple[pathlib.Path, str, str]]:
    """対応ホストの本体セッション候補を列挙する。"""
    for path in session_record_format.claude_session_records(host_homes.claude_config_dir()):
        yield path, "claude", path.stem
    for path in session_record_format.codex_session_records(host_homes.codex_home()):
        yield path, "codex", session_record_format.codex_session_id(path)
    for path in session_record_format.agy_session_records(pathlib.Path.home() / ".gemini"):
        yield path, "agy", session_record_format.agy_session_id(path)


def session_cwd(path: pathlib.Path, engine: str) -> str | None:
    """本体セッションなら判定に用いるcwdを返す。"""
    if engine == "agy":
        try:
            for record in session_record_format.parsed_records(path):
                cwd = record.get("cwd")
                if isinstance(cwd, str) and cwd:
                    return cwd
            state = json.loads(session_state.state_path(session_record_format.agy_session_id(path)).read_text(encoding="utf-8"))
            cwd = state.get("agy_cwd") if isinstance(state, dict) else None
            return cwd if isinstance(cwd, str) and cwd else None
        except OSError, ValueError:
            return None
    try:
        for record in session_record_format.parsed_records(path):
            if engine == "claude" and record.get("type") == "user":
                cwd = record.get("cwd")
                return cwd if record.get("entrypoint") == "cli" and isinstance(cwd, str) else None
            if engine == "codex" and record.get("type") == "session_meta":
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    return None
                cwd = payload.get("cwd")
                return cwd if payload.get("originator") == "codex-tui" and isinstance(cwd, str) else None
    except OSError:
        return None
    return None


def resolved_repo(cwd: str, cache: dict[str, str | None]) -> str | None:
    """cwdごとにリポジトリ識別子を1回だけ解決する。"""
    if cwd not in cache:
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                cache[cwd] = resolve_repo_id(None, cwd=pathlib.Path(cwd))
            except OSError, SystemExit, ValueError:
                cache[cwd] = None
    return cache[cwd]


def _contains_process_wi_marker(record: dict[str, Any], engine: str) -> bool:
    """実行系固有の保存形式にprocess-wiの起動標識があれば真を返す。

    CodexとAntigravityでは、ホストの前置きを許容する目的文本体の包含で判定する。
    """
    if engine == "agy":
        return any(
            event.kind in {"user", "injected"} and event.text and any(body in event.text for body in _PROCESS_WI_GOAL_BODIES)
            for event in session_record_format.agy_events([record])
        )
    if engine == "claude":
        message = record.get("message")
        if record.get("type") != "user" or not isinstance(message, dict):
            return False
        content = message.get("content")
        return isinstance(content, list) and any(
            isinstance(item, dict) and item.get("type") == "tool_result" and item.get("content") in _CLAUDE_PROCESS_WI_MARKERS
            for item in content
        )

    payload = record.get("payload")
    if record.get("type") != "response_item" or not isinstance(payload, dict):
        return False
    content = payload.get("content")
    return (
        payload.get("type") == "message"
        and payload.get("role") == "user"
        and isinstance(content, list)
        and any(
            isinstance(item, dict)
            and item.get("type") == "input_text"
            and isinstance(item.get("text"), str)
            and any(body in item["text"] for body in _PROCESS_WI_GOAL_BODIES)
            for item in content
        )
    )


def invoked_process_wi(path: pathlib.Path, engine: str) -> bool:
    """保存済み記録にprocess-wiの起動標識があれば真を返す。"""
    try:
        return any(_contains_process_wi_marker(record, engine) for record in session_record_format.parsed_records(path))
    except OSError:
        return False


def exit_session_reached(path: pathlib.Path, engine: str) -> bool | None:
    """終了CLIの応答または過去のClaudeスキル起動標識の有無を返す。"""

    def _is_exit_command(command: object) -> bool:
        if not isinstance(command, str):
            return False
        return any(
            segment.resolved and is_agents_exit_session_command(segment.tokens)
            for segment in extract_execution_segments(command)
        )

    def _has_invocation_record(value: object) -> bool:
        if isinstance(value, dict):
            if value.get("exit_session_invoked") is True:
                return True
            return any(_has_invocation_record(nested) for nested in value.values())
        if isinstance(value, list):
            return any(_has_invocation_record(nested) for nested in value)
        if not isinstance(value, str):
            return False
        for line in value.splitlines():
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("exit_session_invoked") is True:
                return True
        return False

    if engine not in {"claude", "codex", "agy"}:
        return None
    try:
        pending: set[str] = set()
        observed_shape = engine in {"claude", "agy"}
        entries = list(session_record_format.parsed_records(path))
        if engine == "agy":
            entries = normalize_entries(entries)
        for record in entries:
            if engine == "codex":
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                if record.get("type") == "response_item" and payload.get("type") == "function_call":
                    observed_shape = True
                    arguments = payload.get("arguments")
                    try:
                        arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                    except json.JSONDecodeError:
                        arguments = None
                    command = arguments.get("cmd") if isinstance(arguments, dict) else None
                    call_id = payload.get("call_id")
                    if _is_exit_command(command) and isinstance(call_id, str):
                        pending.add(call_id)
                elif record.get("type") == "response_item" and payload.get("type") == "function_call_output":
                    call_id = payload.get("call_id")
                    if call_id in pending and _has_invocation_record(payload.get("output")):
                        return True
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "tool_use" and item.get("name") in {*ATK_SHELL_TOOLS, "run_command"}:
                    tool_input = item.get("input")
                    command = tool_input.get("command", tool_input.get("CommandLine")) if isinstance(tool_input, dict) else None
                    tool_id = item.get("id")
                    if _is_exit_command(command) and isinstance(tool_id, str):
                        pending.add(tool_id)
                elif item.get("type") == "tool_result":
                    if item.get("content") == _CLAUDE_EXIT_SESSION_MARKER:
                        return True
                    if item.get("tool_use_id") in pending and _has_invocation_record(item.get("content")):
                        return True
    except OSError:
        return False
    return False if observed_shape else None


def _excerpt(text: str) -> str:
    """改行を半角空白へ置換し、先頭200文字までへ切り詰める。"""
    return text.replace("\n", " ")[:_TEXT_EXCERPT_LIMIT]


def _claude_text(record: dict[str, Any], *, role_type: str) -> str | None:
    """Claude Codeのuser・assistantレコードからテキスト本文を抽出する。"""
    if record.get("type") != role_type:
        return None
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content or None
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"]:
            return block["text"]
    return None


def _codex_text(record: dict[str, Any], *, role: str, item_type: str) -> str | None:
    """Codexの`response_item`レコードからテキスト本文を抽出する。"""
    if record.get("type") != "response_item":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "message" or payload.get("role") != role:
        return None
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    for item in content:
        if isinstance(item, dict) and item.get("type") == item_type and isinstance(item.get("text"), str) and item["text"]:
            return item["text"]
    return None


def first_user_input(path: pathlib.Path, engine: str) -> str:
    """最初のユーザー入力テキストの抜粋を返す。取得できない場合は空文字列。"""
    try:
        if engine == "agy":
            return next(
                (
                    _excerpt(event.text)
                    for event in session_record_format.agy_events(session_record_format.parsed_records(path))
                    if event.kind == "user" and event.text
                ),
                "",
            )
        for record in session_record_format.parsed_records(path):
            text = (
                _claude_text(record, role_type="user")
                if engine == "claude"
                else _codex_text(record, role="user", item_type="input_text")
            )
            if text:
                return _excerpt(text)
    except OSError:
        return ""
    return ""


def last_agent_message(path: pathlib.Path, engine: str) -> str:
    """最後のエージェント発言テキストの抜粋を返す。取得できない場合は空文字列。"""
    result = ""
    try:
        if engine == "agy":
            messages = [
                event.text
                for event in session_record_format.agy_events(session_record_format.parsed_records(path))
                if event.kind == "assistant" and event.text
            ]
            return _excerpt(messages[-1]) if messages else ""
        for record in session_record_format.parsed_records(path):
            text = (
                _claude_text(record, role_type="assistant")
                if engine == "claude"
                else _codex_text(record, role="assistant", item_type="output_text")
            )
            if text:
                result = _excerpt(text)
    except OSError:
        return ""
    return result


def format_modified_at(path: pathlib.Path) -> str:
    """`path`のmtimeをローカル時刻のISO 8601表記へ整形する。"""
    return datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
