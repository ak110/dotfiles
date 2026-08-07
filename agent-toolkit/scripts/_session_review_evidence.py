#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Claude Code transcriptから振り返りに必要な時系列証拠だけを抽出する。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_MAX_TEXT_LENGTH = 2000
_FALLBACK_TEXT = (
    "transcript_pathを読み取れないため抽出証拠を生成できない。"
    "継承した会話履歴を評価し、取得できない範囲を未検証と明記すること。"
)


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


def extract(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """transcriptエントリから対象イベントを順序どおり抽出する。"""
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

    for event in reversed(events):
        if event["kind"] == "assistant":
            event["kind"] = "final-result"
            break
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
    return events


def _fallback() -> list[dict[str, Any]]:
    return [{"sequence": 1, "kind": "fallback", "text": _FALLBACK_TEXT}]


def load_and_extract(raw_path: str | None) -> list[dict[str, Any]]:
    """絶対パスのJSONLを一度読み、抽出結果またはfallbackを返す。"""
    if not raw_path:
        return _fallback()
    path = Path(raw_path)
    if not path.is_absolute():
        return _fallback()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return _fallback()

    entries: list[dict[str, Any]] = []
    try:
        for line in text.splitlines():
            if not line.strip():
                continue
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                entries.append(parsed)
    except (json.JSONDecodeError, ValueError):
        return _fallback()
    return extract(entries)


def main(argv: list[str] | None = None) -> int:
    """証拠を1イベント1 JSONのJSONLとして標準出力へ書く。"""
    args = sys.argv[1:] if argv is None else argv
    events = load_and_extract(args[0] if len(args) == 1 else None)
    for event in events:
        print(json.dumps(event, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
