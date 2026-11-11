"""証拠抽出のツール呼び出し（session_evidence_tool_calls_records）。"""

from __future__ import annotations

import json
import re
from typing import Any, NamedTuple

import session_evidence_execution
from session_evidence_detail import _tool_result_body
from session_evidence_extract import _Record, _text_blocks
from session_evidence_stats import _claude_call_hint, _codex_call_hint

from agent_toolkit._common import agy_records
from agent_toolkit._common.persisted_output_reference import PERSISTED_OUTPUT_PREFIX

_APPLY_PATCH_FILE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)


def _conversation_tool_calls(records: list[_Record]) -> list[dict[str, Any]]:
    """記録上の全呼び出しと結果を、入力全文・識別子・位置の対応を保って返す。"""
    entries = {record.line: record.entry for record in records}
    events = []
    for record in records:
        entry = record.entry
        step = entry.get("step_update")
        if entry.get("event") != "step_update" or not isinstance(step, dict):
            continue
        info = step.get("tool_info")
        if step.get("step_type") != "tool" or step.get("state") == "ACTIVE" or not isinstance(info, dict):
            continue
        common = {
            "record": "main",
            "line": record.line,
            "timestamp": entry.get("timestamp"),
            "call_id": None,
            "tool": str(step.get("tool_name", "")),
        }
        events.extend(
            [
                {**common, "kind": "tool-call", "text": json.dumps(info.get("parameters"), ensure_ascii=False)},
                {**common, "kind": "tool-result", "text": json.dumps(info, ensure_ascii=False)},
            ]
        )
    for call in _record_tool_calls(records):
        inputs = _recorded_input(entries[call.line], call.call_id)
        payload = entries[call.line].get("payload")
        raw_input = payload.get("input") if isinstance(payload, dict) else None
        events.append(
            {
                "kind": "tool-call",
                "record": "main",
                "line": call.line,
                "timestamp": call.timestamp,
                "tool": call.tool,
                "call_id": call.call_id,
                "text": json.dumps(inputs, ensure_ascii=False)
                if inputs
                else raw_input
                if isinstance(raw_input, str)
                else call.text,
                "result_line": call.result_line,
            }
        )
        if call.result_line is not None:
            result = _restored_result(entries[call.result_line], call.call_id)
            if result is not None:
                events.append(
                    {
                        "kind": "tool-result",
                        "record": "main",
                        "line": call.result_line,
                        "timestamp": entries[call.result_line].get("timestamp"),
                        "call_id": call.call_id,
                        "tool": call.tool,
                        "text": result,
                        "input_line": call.line,
                    }
                )
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
        item = payload.get("item")
        if raw is None and isinstance(item, dict) and item.get("type") == "McpToolCall":
            raw = item.get("arguments")
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
    """エージェントが受け取った記録本文を返す。退避先は量の集計へ展開しない。"""
    message = entry.get("message", {})
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") == call_id:
                return "\n".join(_text_blocks(block.get("content")))
    payload = entry.get("payload", {})
    item = payload.get("item") if isinstance(payload, dict) else None
    if isinstance(item, dict) and item.get("type") in {"McpToolCall", "CommandExecution"}:
        return json.dumps(item.get("result", item), ensure_ascii=False)
    value = payload.get("output") if isinstance(payload, dict) else None
    return value if isinstance(value, str) else None


def _restored_result(entry: dict[str, Any], call_id: str | None) -> str | None:
    """中身を表示する照会用に、Claudeの退避された結果を復元する。"""
    message = entry.get("message", {})
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") == call_id:
                return _tool_result_body(block, entry)
    return _recorded_result(entry, call_id)


def _persisted_result_metrics(entry: dict[str, Any], call_id: str | None) -> dict[str, Any]:
    """受領本文が退避案内の場合だけ、復元できた退避前の量を別の軸で返す。"""
    received = _recorded_result(entry, call_id)
    if received is None or not received.lstrip().startswith(PERSISTED_OUTPUT_PREFIX):
        return {}
    original = _restored_result(entry, call_id)
    original = original if original != received else None
    return {
        "persisted": True,
        "original_characters": len(original) if original is not None else None,
        "original_utf8_bytes": len(original.encode("utf-8")) if original is not None else None,
    }


class _ToolCall(NamedTuple):
    """記録にある1件のツール呼び出し。"""

    line: int
    timestamp: str | None
    tool: str
    call_id: str | None
    turn_id: str | None
    text: str
    """代表入力。`_claude_call_input`・`_codex_call_input`が返す値を切り詰めずに持つ。"""
    result_line: int | None
    """同じ呼び出し識別子を持つ最初の結果の記録行。結果の記録が無い場合は`None`。"""


def _record_turn_id(entry: dict[str, Any]) -> str | None:
    """Codexの外側セルと内側実行記録を比較するため、記録済みturnだけを返す。"""
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return None
    value = payload.get("turn_id")
    if not isinstance(value, str):
        metadata = payload.get("internal_chat_message_metadata_passthrough")
        value = metadata.get("turn_id") if isinstance(metadata, dict) else None
    return value if isinstance(value, str) else None


def _record_tool_calls(records: list[_Record]) -> list[_ToolCall]:
    """1つの記録のツール呼び出しを、記録位置、時刻、ツール名、呼び出し識別子、代表入力および結果の記録行で返す。

    会話の流れ、`adhoc-processing`候補および`--tool-calls`が共有する抽出であり、
    Claude Code形式の`tool_use`要素とCodex形式の`function_call`・`custom_tool_call`のpayloadを1件の呼び出しとする。
    結果はClaude Code形式の`tool_result`の`tool_use_id`と、Codex形式の`function_call_output`・
    `custom_tool_call_output`の`call_id`で対応付ける。
    """
    records = [
        record._replace(entry=entry)
        for record, entry in zip(records, agy_records.normalize_entries([record.entry for record in records]), strict=True)
    ]
    pending: list[tuple[int, str | None, str, str | None, str | None, str]] = []
    result_lines: dict[str, list[int]] = {}
    active_turn_id: str | None = None
    for record in records:
        raw_timestamp = record.entry.get("timestamp")
        timestamp = raw_timestamp if isinstance(raw_timestamp, str) else None
        recorded_turn_id = _record_turn_id(record.entry)
        payload = record.entry.get("payload")
        payload_type = payload.get("type") if isinstance(payload, dict) else None
        if payload_type == "task_started":
            # 新しいタスクの開始時点で前タスクの識別子を必ず破棄する。
            active_turn_id = recorded_turn_id
        turn_id = recorded_turn_id or active_turn_id
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
                            turn_id,
                            _claude_call_input(name, block.get("input")),
                        )
                    )
                elif block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
                    result_lines.setdefault(block["tool_use_id"], []).append(record.line)
            continue
        if not isinstance(payload, dict):
            continue
        call_id = payload.get("call_id")
        item = payload.get("item")
        if payload_type == "item_completed" and isinstance(item, dict) and item.get("type") == "CommandExecution":
            identity = item.get("id")
            if isinstance(identity, str) and any(call[3] == identity for call in pending):
                continue
            command = item.get("command", "")
            text = session_evidence_execution.command_execution_input(command)
            pending.append(
                (record.line, timestamp, "CommandExecution", str(identity) if identity is not None else None, turn_id, text)
            )
            if isinstance(identity, str):
                result_lines.setdefault(identity, []).append(record.line)
            continue
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
                    turn_id,
                    json.dumps(item.get("arguments", {}), ensure_ascii=False),
                )
            )
            if isinstance(identity, str):
                result_lines.setdefault(identity, []).append(record.line)
            continue
        if payload_type in {"function_call", "custom_tool_call"}:
            name = str(payload.get("name", ""))
            pending.append(
                (
                    record.line,
                    timestamp,
                    name,
                    call_id if isinstance(call_id, str) else None,
                    turn_id,
                    _codex_call_input(name, payload),
                )
            )
        elif payload_type in {"function_call_output", "custom_tool_call_output"} and isinstance(call_id, str):
            result_lines.setdefault(call_id, []).append(record.line)
        if payload_type in {"task_complete", "turn_aborted"}:
            active_turn_id = None
    calls: list[_ToolCall] = []
    completed_lines = {
        record.line
        for record in records
        if isinstance(record.entry.get("payload"), dict) and record.entry["payload"].get("type") == "item_completed"
    }
    for line, timestamp, name, call_id, turn_id, text in pending:
        result_line = (
            next((value for value in result_lines.get(call_id, ()) if value >= line), None) if call_id is not None else None
        )
        if result_line is None and line in completed_lines:
            result_line = line
        calls.append(_ToolCall(line, timestamp, name, call_id, turn_id, text, result_line))
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
