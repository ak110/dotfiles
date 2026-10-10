"""証拠抽出の`--tool-calls`照会。ツール呼び出しと入力・結果の対応を返す。"""

from __future__ import annotations

import collections
import datetime
import json
import pathlib
import re
from typing import Any, NamedTuple

from session_evidence_extract import (
    _clip,
    _CollectedRecord,
    _json_object,
    _parse_timestamp,
    _Record,
    _role_document,
    _unresolved_events,
    _UnresolvedRecord,
)
from session_evidence_stats import (
    _claude_call_hint,
    _codex_call_hint,
)

from agent_toolkit._agents_server import tool_names as _agents_server_tool_names

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


def comparison_material_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """正常な委譲・規範読込も比較できる事実と全文位置を、欠陥の採点をせず返す。"""
    events: list[dict[str, Any]] = []
    for item in collected:
        entries = {record.line: record.entry for record in item.records}
        for call in _record_tool_calls(item.records):
            name = call.tool.rsplit("__", 1)[-1]
            delegate = name in {"Agent", "Task", "spawn_agent", "followup_task", "send_message"} or (
                "agents_server" in call.tool and name in {"start", "send_message"}
            )
            explicit_read = name in {"Read", "read_file", "Skill"}
            opaque = name in {"Bash", "exec", "exec_command", "functions.exec", "functions.exec_command"}
            if not (delegate or explicit_read or opaque):
                continue
            inputs = _recorded_input(entries[call.line], call.call_id)
            body = next(
                (inputs[key] for key in ("prompt", "message", "instructions") if isinstance(inputs.get(key), str)),
                None,
            )
            paths = {
                key: value
                for key, value in inputs.items()
                if isinstance(value, str) and key != "subagent_md_path" and (key.endswith("file") or key.endswith("path"))
            }
            named_inputs = inputs.get("extra_params")
            if isinstance(named_inputs, dict):
                paths.update(
                    (f"extra_params.{key}", value)
                    for key, value in named_inputs.items()
                    if isinstance(value, str)
                    and (pathlib.PurePosixPath(value).is_absolute() or pathlib.PureWindowsPath(value).is_absolute())
                )
            result = _recorded_result(entries[call.result_line], call.call_id) if call.result_line is not None else None
            event = {
                "kind": "comparison-material",
                "record": item.record_id,
                "role": item.role,
                "line": call.line,
                "result_line": call.result_line,
                "timestamp": call.timestamp,
                "tool": call.tool,
                "category": "delegation" if delegate else "explicit-read" if explicit_read else "opaque-command",
                "target": paths or ({"skill": inputs["skill"]} if isinstance(inputs.get("skill"), str) else {}),
                "input_form": "file-reference" if delegate and paths else "direct-body" if body is not None else "command",
                "observed_body_characters": len(body) if body is not None else None,
                "recorded_result_characters": len(result) if result is not None else None,
                "summary": _clip(call.text, _CONVERSATION_INPUT_LIMIT),
            }
            events.append(event)
    return events


def _recorded_input(entry: dict[str, Any], call_id: str | None) -> dict[str, Any]:
    """明示されたツール入力だけを取得し、shellやJavaScriptの命令を読了の事実へ変換しない。"""
    message = entry.get("message", {})
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") == call_id:
                value = block.get("input")
                return value if isinstance(value, dict) else {}
    payload = entry.get("payload", {})
    if isinstance(payload, dict):
        raw = payload.get("arguments", payload.get("input"))
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                value = json.loads(raw)
            except ValueError:
                return {}
            return value if isinstance(value, dict) else {}
    return {}


def _recorded_result(entry: dict[str, Any], call_id: str | None) -> str | None:
    """対応する結果本文の文字列を返し、取得量を現在のファイルサイズから推定しない。"""
    message = entry.get("message", {})
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") == call_id:
                content = block.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return "\n".join(
                        part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
                    )
    payload = entry.get("payload", {})
    value = payload.get("output") if isinstance(payload, dict) else None
    return value if isinstance(value, str) else None


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
        item = payload.get("item")
        if payload_type == "item_completed" and isinstance(item, dict) and item.get("type") == "McpToolCall":
            identity = item.get("id")
            if isinstance(identity, str) and any(call[3] == identity for call in pending):
                continue
            tool = f"mcp__{item.get('server')}__{item.get('tool')}"
            pending.append(
                (
                    record.line,
                    timestamp,
                    tool,
                    identity if isinstance(identity, str) else None,
                    json.dumps(item.get("arguments", {}), ensure_ascii=False),
                )
            )
            if isinstance(identity, str):
                result_lines.setdefault(identity, []).append(record.line)
            continue
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
    return arguments if isinstance(arguments, str) else raw if isinstance(raw, str) else ""


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


def _tool_call_summary(events: list[dict[str, Any]], collected: list[_CollectedRecord] | None = None) -> dict[str, Any]:
    """`tool-call`イベントの件数と、ツール名ごと・記録ごとの件数を返す。"""
    summary = {
        "kind": "tool-call-summary",
        "count": len(events),
        "by_tool": dict(sorted(collections.Counter(str(event["tool"]) for event in events).items())),
        "by_record": dict(sorted(collections.Counter(str(event["record"]) for event in events).items())),
    }
    if collected is not None:
        groups: dict[str, dict[str, Any]] = {}
        owners = {item.record_id: item for item in collected}
        for event in events:
            owner = owners[str(event["record"])]
            document = _role_document(owner)
            key = f"document:{document}" if document else f"record:{owner.role}"
            group = groups.setdefault(key, {"count": 0, "by_tool": {}, "by_mode": {}})
            group["count"] += 1
            tool = str(event["tool"])
            group["by_tool"][tool] = group["by_tool"].get(tool, 0) + 1
            leaf = tool.rsplit("__", 1)[-1]
            if leaf in _agents_server_tool_names.RECORDED_START_OPERATIONS:
                inputs = _json_object(event["text"])
                if inputs is not None:
                    mode = _agents_server_tool_names.start_mode(leaf, inputs)
                    if mode is not None:
                        group["by_mode"][mode] = group["by_mode"].get(mode, 0) + 1
        summary["by_role"] = groups
    return summary


def _tool_call_collection_events(
    collected: list[_CollectedRecord],
    unresolved: list[_UnresolvedRecord],
    tools: list[str] | None,
    pattern: re.Pattern[str] | None,
) -> list[dict[str, Any]]:
    """単一transcriptのメイン記録と全ての委譲先の記録のツール呼び出しを時刻順に返し、末尾へ要約を置く。"""
    events = _sorted_tool_call_events([event for item in collected for event in _tool_call_events(item, tools, pattern)])
    return [*events, *_unresolved_events(unresolved), _tool_call_summary(events, collected)]
