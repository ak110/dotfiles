"""証拠抽出の`--tool-calls`照会。ツール呼び出しと入力・結果の対応を返す。"""

from __future__ import annotations

import collections
import datetime
import json
import re
from typing import Any, NamedTuple

from session_evidence_extract import (
    _clip,
    _CollectedRecord,
    _parse_timestamp,
    _Record,
    _unresolved_events,
    _UnresolvedRecord,
)
from session_evidence_stats import (
    _claude_call_hint,
    _codex_call_hint,
)

_CONVERSATION_INPUT_LIMIT = 1000
"""会話の流れのツール呼び出しへ保存する代表入力の上限。表示側がさらに1行へ切り詰める。"""


_APPLY_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)


def _conversation_tool_calls(records: list[_Record]) -> list[dict[str, Any]]:
    """メイン記録のツール呼び出しを、ツール名、代表入力、呼び出し識別子および記録位置で返す。"""
    return [
        {
            "kind": "tool-call",
            "record": "main",
            "line": call.line,
            "timestamp": call.timestamp,
            "tool": call.tool,
            "call_id": call.call_id,
            "text": _clip(call.text, _CONVERSATION_INPUT_LIMIT),
        }
        for call in _record_tool_calls(records)
    ]


class _ToolCall(NamedTuple):
    """記録にある1件のツール呼び出し。"""

    line: int
    timestamp: str | None
    tool: str
    call_id: str | None
    text: str
    """代表入力。`_claude_call_input`・`_codex_call_input`が返す値を切り詰めずに持つ。"""
    result_line: int | None
    """同じ呼び出し識別子を持つ最初の結果の記録行。結果の記録が無い場合は`None`。"""


def _record_tool_calls(records: list[_Record]) -> list[_ToolCall]:
    """1つの記録のツール呼び出しを、記録位置、時刻、ツール名、呼び出し識別子、代表入力および結果の記録行で返す。

    会話の流れ、`adhoc-processing`候補および`--tool-calls`が共有する抽出であり、
    Claude Code形式の`tool_use`要素とCodex形式の`function_call`・`custom_tool_call`のpayloadを1件の呼び出しとする。
    結果はClaude Code形式の`tool_result`の`tool_use_id`と、Codex形式の`function_call_output`・
    `custom_tool_call_output`の`call_id`で対応付ける。
    """
    pending: list[tuple[int, str | None, str, str | None, str]] = []
    result_lines: dict[str, list[int]] = {}
    for record in records:
        raw_timestamp = record.entry.get("timestamp")
        timestamp = raw_timestamp if isinstance(raw_timestamp, str) else None
        message = record.entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    name = str(block.get("name", ""))
                    call_id = block.get("id")
                    pending.append(
                        (
                            record.line,
                            timestamp,
                            name,
                            call_id if isinstance(call_id, str) else None,
                            _claude_call_input(name, block.get("input")),
                        )
                    )
                elif block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                    result_lines.setdefault(block["tool_use_id"], []).append(record.line)
            continue
        payload = record.entry.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")
        call_id = payload.get("call_id")
        if payload_type in {"function_call", "custom_tool_call"}:
            name = str(payload.get("name", ""))
            pending.append(
                (record.line, timestamp, name, call_id if isinstance(call_id, str) else None, _codex_call_input(name, payload))
            )
        elif payload_type in {"function_call_output", "custom_tool_call_output"} and isinstance(call_id, str):
            result_lines.setdefault(call_id, []).append(record.line)
    calls: list[_ToolCall] = []
    for line, timestamp, name, call_id, text in pending:
        result_line = (
            next((value for value in result_lines.get(call_id, ()) if value >= line), None) if call_id is not None else None
        )
        calls.append(_ToolCall(line, timestamp, name, call_id, text, result_line))
    return calls


def _claude_call_input(name: str, block_input: Any) -> str:
    """Claude Codeのツール呼び出しの代表入力を返す。`Write`・`Edit`は対象ファイルだけとし、本文を含めない。"""
    hint = _claude_call_hint(block_input)
    if hint is not None:
        return hint
    if not isinstance(block_input, dict):
        return ""
    keys = {"Agent": ("description", "prompt"), "Task": ("description", "prompt"), "Skill": ("skill", "args")}.get(name)
    if keys is not None:
        return " ".join(str(block_input[key]) for key in keys if isinstance(block_input.get(key), str) and block_input[key])
    return json.dumps(block_input, ensure_ascii=False, sort_keys=True)


def _codex_call_input(name: str, payload: dict[str, Any]) -> str:
    """Codexのツール呼び出しの代表入力を返す。パッチの適用は対象ファイルだけとし、差分の本文を含めない。"""
    raw = payload.get("input")
    if name == "apply_patch" and isinstance(raw, str):
        return " ".join(_APPLY_PATCH_FILE.findall(raw)) or name
    hint = _codex_call_hint(payload)
    if hint is not None:
        return hint
    arguments = payload.get("arguments")
    return arguments if isinstance(arguments, str) else ""


def _tool_call_events(item: _CollectedRecord, tools: list[str] | None, pattern: re.Pattern[str] | None) -> list[dict[str, Any]]:
    """1つの記録のツール呼び出しのうち、ツール名と代表入力の条件に一致するものを`tool-call`イベントで返す。

    本スクリプト自身の呼び出しも含める。`--grep`のように自己呼び出しを除くと、本スクリプトの呼び出し回数を数えられない。
    """
    return [
        {
            "kind": "tool-call",
            "record": item.record_id,
            "line": call.line,
            "timestamp": call.timestamp,
            "tool": call.tool,
            "call_id": call.call_id,
            "text": call.text,
            "result_line": call.result_line,
        }
        for call in _record_tool_calls(item.records)
        if (tools is None or call.tool in tools) and (pattern is None or pattern.search(call.text))
    ]


def _sorted_tool_call_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`tool-call`イベントを時刻の昇順に並べる。同時刻と時刻を持たないイベントは収集順と行番号の順を保つ。

    時刻を持たないイベントは最後に置く。入力のイベント列は記録の収集順と行番号の順に並んでいる前提とする。
    """
    timed: list[tuple[datetime.datetime, int, dict[str, Any]]] = []
    untimed: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        try:
            timestamp = _parse_timestamp(event["timestamp"]) if isinstance(event.get("timestamp"), str) else None
        except ValueError:
            timestamp = None
        if timestamp is None:
            untimed.append(event)
        else:
            timed.append((timestamp, index, event))
    timed.sort(key=lambda item: (item[0], item[1]))
    return [*(event for _, _, event in timed), *untimed]


def _tool_call_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    """`tool-call`イベントの件数と、ツール名ごと・記録ごとの件数を返す。"""
    return {
        "kind": "tool-call-summary",
        "count": len(events),
        "by_tool": dict(sorted(collections.Counter(str(event["tool"]) for event in events).items())),
        "by_record": dict(sorted(collections.Counter(str(event["record"]) for event in events).items())),
    }


def _tool_call_collection_events(
    collected: list[_CollectedRecord],
    unresolved: list[_UnresolvedRecord],
    tools: list[str] | None,
    pattern: re.Pattern[str] | None,
) -> list[dict[str, Any]]:
    """単一transcriptのメイン記録と全ての委譲先の記録のツール呼び出しを時刻順に返し、末尾へ要約を置く。"""
    events = _sorted_tool_call_events([event for item in collected for event in _tool_call_events(item, tools, pattern)])
    return [*events, *_unresolved_events(unresolved), _tool_call_summary(events)]
