#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Claude CodeとCodexのtranscriptから振り返り用の時系列証拠を抽出する。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

_MAX_TEXT_LENGTH = 2000
STOP_ADVISOR_PREFIX = "[auto-generated: agent-toolkit/stop_advisor]"
SESSION_REVIEW_STARTED_MARKER = "[auto-generated: agent-toolkit/session-review-started]"
_FALLBACK_TEXT = (
    "transcript_pathを読み取れないため抽出証拠を生成できない。"
    "継承した会話履歴を評価し、取得できない範囲を未検証と明記すること。"
)
_Runtime = Literal["claude", "codex"]
_MANUAL_REVIEW_COMMANDS: dict[_Runtime, tuple[str, ...]] = {
    "claude": ("/session-review", "/agent-toolkit:session-review"),
    "codex": ("$session-review", "$agent-toolkit:session-review"),
}


def _clip(text: str) -> str:
    """証拠の意味を保ったまま巨大な本文を制限する。"""
    normalized = text.strip()
    if len(normalized) <= _MAX_TEXT_LENGTH:
        return normalized
    return normalized[:_MAX_TEXT_LENGTH] + "…[省略]"


def _text_blocks(content: Any, *, include_tool_results: bool = False) -> list[str]:
    """Message contentから可視テキストを取得する。"""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    result: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            result.append(block["text"])
        elif include_tool_results and block_type == "tool_result":
            result.extend(_text_blocks(block.get("content"), include_tool_results=False))
    return result


def _codex_text_blocks(content: Any) -> list[str]:
    """Codex message contentから可視テキストを取得する。"""
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    result: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"input_text", "output_text", "text"} and isinstance(block.get("text"), str):
            result.append(block["text"])
    return result


def _event(kind: str, text: str, *, tool: str | None = None) -> dict[str, Any] | None:
    clipped = _clip(text)
    if not clipped:
        return None
    event: dict[str, Any] = {"kind": kind, "text": clipped}
    if tool:
        event["tool"] = tool
    return event


def _failed_tool_events(entry: dict[str, Any]) -> list[dict[str, Any]]:
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    events: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result" or block.get("is_error") is not True:
            continue
        text = "\n".join(_text_blocks(block.get("content")))
        event = _event("failed-tool", text, tool=str(block.get("tool_use_id", "")))
        if event:
            events.append(event)
    return events


def _completion_event(entry: dict[str, Any]) -> dict[str, Any] | None:
    result = entry.get("toolUseResult")
    if isinstance(result, dict) and result.get("status") == "completed":
        identity = result.get("agentId") or result.get("taskId") or "unknown"
        summary = result.get("summary") or result.get("result") or "completed"
        return _event("agent-completion", f"{identity}: {summary}")

    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    for text in _text_blocks(message.get("content")):
        if "<task-notification>" in text and "<status>completed</status>" in text:
            return _event("agent-completion", text)
    return None


def _extract_claude(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claude Code形式を共通イベントへ変換する。"""
    events: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("isSidechain") is True:
            completion = _completion_event(entry)
            if completion:
                events.append(completion)
            continue

        is_interrupt = (
            entry.get("isInterrupt") is True or entry.get("type") == "interrupt" or entry.get("subtype") == "interrupt"
        )
        if is_interrupt:
            interrupt = _event("interrupt", json.dumps(entry, ensure_ascii=False))
            events.append(interrupt or {"kind": "interrupt", "text": "interrupt"})

        entry_type = entry.get("type")
        message = entry.get("message")
        if isinstance(message, dict):
            role = message.get("role")
            if entry_type == "user" and role == "user":
                for text in _text_blocks(message.get("content")):
                    event = _event("user", text)
                    if event and not event["text"].startswith("<task-notification>"):
                        events.append(event)
                events.extend(_failed_tool_events(entry))
            elif entry_type == "assistant" and role == "assistant":
                for text in _text_blocks(message.get("content")):
                    event = _event("assistant", text)
                    if event:
                        events.append(event)

        completion = _completion_event(entry)
        if completion:
            events.append(completion)
    return events


def _codex_agent_message(payload: dict[str, Any]) -> str:
    for key in ("message", "text", "content"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _codex_command_output(item: dict[str, Any]) -> str:
    """Codexコマンド実行項目から標準出力相当の本文を取得する。"""
    for key in ("aggregated_output", "output", "stdout"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_codex(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Codex rollout形式を共通イベントへ変換する。"""
    events: list[dict[str, Any]] = []
    for entry in entries:
        entry_type = entry.get("type")
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")
        if entry_type == "response_item" and payload_type == "message":
            role = payload.get("role")
            kind = "user" if role == "user" else "assistant" if role == "assistant" else None
            if kind is not None:
                for text in _codex_text_blocks(payload.get("content")):
                    event = _event(kind, text)
                    if event:
                        events.append(event)
        elif entry_type == "response_item" and payload_type == "agent_message":
            text = _codex_agent_message(payload)
            if text.lstrip().startswith("Message Type: FINAL_ANSWER"):
                event = _event("agent-completion", text)
                if event:
                    events.append(event)
        elif entry_type == "event_msg" and payload_type == "turn_aborted":
            event = _event("interrupt", json.dumps(payload, ensure_ascii=False))
            events.append(event or {"kind": "interrupt", "text": "turn_aborted"})
        elif entry_type == "event_msg" and payload_type == "item_completed":
            item = payload.get("item")
            if not isinstance(item, dict) or item.get("type") != "CommandExecution":
                continue
            status = item.get("status")
            output = _codex_command_output(item)
            if status == "completed" and SESSION_REVIEW_STARTED_MARKER in output:
                event = _event("session-review-started", SESSION_REVIEW_STARTED_MARKER)
            elif status == "failed":
                error = item.get("error")
                text = output or (error if isinstance(error, str) and error.strip() else json.dumps(item, ensure_ascii=False))
                event = _event("failed-tool", text, tool="CommandExecution")
            else:
                event = None
            if event:
                events.append(event)
    return events


def _is_manual_review_invocation(text: str, runtime: _Runtime) -> bool:
    stripped = text.strip()
    commands = _MANUAL_REVIEW_COMMANDS[runtime]
    if any(stripped == command or stripped.startswith(f"{command} ") for command in commands):
        return True
    return any(f"<command-name>{command}</command-name>" in stripped for command in commands)


def _finalize(events: list[dict[str, Any]], runtime: _Runtime) -> list[dict[str, Any]]:
    """手動・自動振り返り境界を適用し、最終結果と連番を確定する。"""
    manual_boundary = next(
        (
            index
            for index, event in enumerate(events)
            if event["kind"] == "user" and _is_manual_review_invocation(event["text"], runtime)
        ),
        len(events),
    )
    started_index = next(
        (index for index, event in enumerate(events) if event["kind"] == "session-review-started"),
        len(events),
    )
    automatic_boundary = len(events)
    if started_index < len(events):
        automatic_boundary = next(
            (
                index
                for index in range(started_index - 1, -1, -1)
                if events[index]["kind"] == "user" and events[index]["text"].startswith(STOP_ADVISOR_PREFIX)
            ),
            len(events),
        )
    boundary = min(manual_boundary, automatic_boundary)
    events = [event for event in events[:boundary] if event["kind"] != "session-review-started"]

    for event in reversed(events):
        if event["kind"] == "assistant":
            event["kind"] = "final-result"
            break
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
    return events


def extract(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """transcript形式を判定し、対象イベントを順序どおり抽出する。"""
    if not entries:
        return []
    runtime = _detect_runtime(entries)
    if runtime is None:
        return _fallback()
    return _finalize(_extract_for_runtime(entries, runtime), runtime)


def _detect_runtime(entries: list[dict[str, Any]]) -> _Runtime | None:
    """transcriptのentry形式から手動構文を解釈するruntimeを返す。"""
    entry_types = {entry.get("type") for entry in entries}
    if entry_types & {"response_item", "event_msg"}:
        return "codex"
    if entry_types & {"user", "assistant", "interrupt"} or any(
        entry.get("isSidechain") is True or "toolUseResult" in entry for entry in entries
    ):
        return "claude"
    return None


def _extract_for_runtime(entries: list[dict[str, Any]], runtime: _Runtime) -> list[dict[str, Any]]:
    """確定したruntimeに対応する共通イベントへ変換する。"""
    return _extract_codex(entries) if runtime == "codex" else _extract_claude(entries)


def _fallback() -> list[dict[str, Any]]:
    return [{"sequence": 1, "kind": "fallback", "text": _FALLBACK_TEXT}]


def _load_entries(raw_path: str | None) -> list[dict[str, Any]] | None:
    """絶対パスのJSONLを読み、失敗時は`None`を返す。"""
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    entries: list[dict[str, Any]] = []
    try:
        for line in text.splitlines():
            if not line.strip():
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                entries.append(parsed)
    except (json.JSONDecodeError, ValueError):
        return None
    return entries


def load_and_extract(raw_path: str | None) -> list[dict[str, Any]]:
    """絶対パスのJSONLを一度読み、抽出結果またはfallbackを返す。"""
    entries = _load_entries(raw_path)
    if entries is None:
        return _fallback()
    return extract(entries)


def has_session_review_started(raw_path: str | None) -> bool:
    """対応するtranscriptに振り返りの手動起動または起動確定標識があれば真を返す。"""
    entries = _load_entries(raw_path)
    if entries is None:
        return False
    runtime = _detect_runtime(entries)
    if runtime is None:
        return False
    events = _extract_for_runtime(entries, runtime)
    return any(
        event["kind"] == "session-review-started"
        or (event["kind"] == "user" and _is_manual_review_invocation(event["text"], runtime))
        for event in events
    )


def main(argv: list[str] | None = None) -> int:
    """証拠を1イベント1 JSONのJSONLとして標準出力へ書く。"""
    args = sys.argv[1:] if argv is None else argv
    events = load_and_extract(args[0] if len(args) == 1 else None)
    for event in events:
        print(json.dumps(event, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
