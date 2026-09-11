"""保存済みセッション記録（Claude Code・Codex）の走査・復号・判定を提供する共通モジュール。"""

from __future__ import annotations

import contextlib
import datetime
import io
import json
import pathlib
from collections.abc import Iterator
from typing import Any

from agent_toolkit._atk.serve.sessions import (
    CODEX_ROLLOUT_PREFIX,
    RECORD_SUFFIX,
    codex_session_id,
    default_claude_home,
    default_codex_home,
)
from agent_toolkit._atk.wi.repo import resolve_repo_id

# Claude Codeのハーネスが、Skillツール起動の`tool_result`として記録する起動確認文言。
_CLAUDE_PROCESS_WI_MARKER = "Launching skill: agent-toolkit:process-wi"
_CLAUDE_EXIT_SESSION_MARKER = "Launching skill: agent-toolkit:exit-session"

# `atk wi process-loop`が`_build_process_loop_prompt`でCodexへ渡す起動プロンプト本文。
_CODEX_PROCESS_WI_PROMPT = "/goal `agent-toolkit:process-wi`を完遂してください。"

_TEXT_EXCERPT_LIMIT = 200


def candidate_paths() -> Iterator[tuple[pathlib.Path, str, str]]:
    """Claude CodeとCodexの本体セッション候補を列挙する。"""
    projects = default_claude_home() / "projects"
    if projects.is_dir():
        for project_dir in projects.iterdir():
            if project_dir.is_dir():
                for path in project_dir.glob(f"*{RECORD_SUFFIX}"):
                    if path.is_file():
                        yield path, "claude", path.stem

    sessions = default_codex_home() / "sessions"
    if sessions.is_dir():
        for path in sessions.glob(f"*/*/*/{CODEX_ROLLOUT_PREFIX}*{RECORD_SUFFIX}"):
            if path.is_file():
                yield path, "codex", codex_session_id(path)


def parsed_records(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    """JSON Linesから解釈できる辞書レコードだけを返す。"""
    with path.open(encoding="utf-8") as record_file:
        for line in record_file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def session_cwd(path: pathlib.Path, engine: str) -> str | None:
    """本体セッションなら判定に用いるcwdを返す。"""
    try:
        for record in parsed_records(path):
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
            except (OSError, SystemExit, ValueError):
                cache[cwd] = None
    return cache[cwd]


def _contains_process_wi_marker(record: dict[str, Any], engine: str) -> bool:
    """実行系固有の保存形式にprocess-wiの起動標識があれば真を返す。"""
    if engine == "claude":
        message = record.get("message")
        if record.get("type") != "user" or not isinstance(message, dict):
            return False
        content = message.get("content")
        return isinstance(content, list) and any(
            isinstance(item, dict) and item.get("type") == "tool_result" and item.get("content") == _CLAUDE_PROCESS_WI_MARKER
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
            isinstance(item, dict) and item.get("type") == "input_text" and item.get("text") == _CODEX_PROCESS_WI_PROMPT
            for item in content
        )
    )


def invoked_process_wi(path: pathlib.Path, engine: str) -> bool:
    """保存済み記録にprocess-wiの起動標識があれば真を返す。"""
    try:
        return any(_contains_process_wi_marker(record, engine) for record in parsed_records(path))
    except OSError:
        return False


def exit_session_reached(path: pathlib.Path, engine: str) -> bool | None:
    """`agent-toolkit:exit-session`の起動標識の有無を返す。

    Claude Codeは`tool_result`の起動確認文言で判定する。Codexのセッション記録には
    スキル起動を一意に示すレコードが無いため、常に`None`（判定不能）を返す。
    """
    if engine != "claude":
        return None
    try:
        for record in parsed_records(path):
            message = record.get("message")
            if record.get("type") != "user" or not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            if any(
                isinstance(item, dict)
                and item.get("type") == "tool_result"
                and item.get("content") == _CLAUDE_EXIT_SESSION_MARKER
                for item in content
            ):
                return True
    except OSError:
        return False
    return False


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
        for record in parsed_records(path):
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
        for record in parsed_records(path):
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
