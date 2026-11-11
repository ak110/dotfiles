"""記録からの証拠イベント抽出（session_evidence_extract_runtimes）。"""

from __future__ import annotations

import json
from typing import Any

from session_evidence_extract_claude import _extract_claude, _set_entry_timestamp
from session_evidence_extract_common import _event

from agent_toolkit._atk import session_record_format as _session_record_format
from agent_toolkit._common import agy_records as _agy_records
from agent_toolkit._common.session_family import _Record

# Antigravity（agy）の委譲先ログは、agents_serverが公開JSONイベントを1行ずつ保存したものである。


def _extract_agy(entries: list[dict[str, Any]], lines: list[int]) -> list[dict[str, Any]]:
    """対話トランスクリプトと委譲先ログを、同じ会話・失敗イベントへ変換する。

    時刻・使用量・ツール対応は共通の解析写しを使い、公開ストリーム固有のエラーも残す。
    """
    events = _extract_claude(_agy_records.normalize_entries(entries), lines)
    for line, entry in zip(lines, entries, strict=True):
        original = entry.get("_agy_original", entry)
        for event in _agy_entry_events(original):
            if event.get("kind") == "assistant":
                continue
            event.setdefault("line", line)
            _set_entry_timestamp(event, entry)
            events.append(event)
    return events


def _agy_entry_events(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """agyの1イベントから、ツール失敗・エラー報告・失敗終了・返却本文の共通イベントを取得する。"""
    event_type = entry.get("event")
    body = entry.get(event_type) if isinstance(event_type, str) else None
    if not isinstance(body, dict):
        return []
    events: list[dict[str, Any]] = []
    if event_type == "step_update":
        step_type = body.get("step_type")
        if step_type == "error_message":
            # agyはエラー報告のステップへ本文を持たせないため、ステップの位置だけを示す。
            event = _event(
                "failed-tool", f"agyの委譲先がエラーを報告した（step_index: {body.get('step_index')}）", tool="error_message"
            )
            if event:
                event["tool_name"] = "error_message"
                events.append(event)
        elif step_type == "tool" and body.get("state") != "ACTIVE":
            tool_info = body.get("tool_info")
            error = tool_info.get("error") if isinstance(tool_info, dict) else None
            # 終了したツールステップは、`ERROR`状態に加えて`DONE`状態でも`tool_info.error`で失敗を表す。
            if body.get("state") == "ERROR" or error is not None:
                message = error.get("message") if isinstance(error, dict) else None
                if isinstance(message, str) and message.strip():
                    text = message
                elif error is not None:
                    text = json.dumps(error, ensure_ascii=False)
                else:
                    text = "tool step failed"
                tool_name = str(body.get("tool_name") or "")
                event = _event("failed-tool", text, tool=tool_name)
                if event:
                    event["tool_name"] = tool_name
                    parameters = tool_info.get("parameters") if isinstance(tool_info, dict) else None
                    event["operation"] = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
                    events.append(event)
    elif event_type == "result":
        status = body.get("status")
        if status != "SUCCESS":
            error = body.get("error")
            detail = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False) if error is not None else ""
            event = _event("failed-tool", f"status: {status}\n{detail}".strip(), tool="result")
            if event:
                event["tool_name"] = "result"
                events.append(event)
        response = body.get("response")
        if isinstance(response, str):
            event = _event("assistant", response)
            if event:
                events.append(event)
    return events


def _opencode_records(records: list[_Record]) -> list[_Record]:
    """OpenCodeの共通表示イベントを既存の発話・ツール・使用量の抽出契約へ接続する。"""
    converted: list[_Record] = []
    for record in records:
        if record.entry.get("_opencode_sqlite") is True:
            converted.append(record)
            continue
        for index, event in enumerate(_session_record_format.opencode_events([record.entry])):
            entry: dict[str, Any] = {"timestamp": event.timestamp}
            content: list[dict[str, Any]] = []
            role = "assistant"
            if event.kind == "tool_call":
                content = [
                    {"type": "tool_use", "id": event.call_id, "name": event.name, "input": (event.detail or {}).get("input")}
                ]
            elif event.kind == "tool_result":
                role = "user"
                content = [
                    {
                        "type": "tool_result",
                        "tool_use_id": event.call_id,
                        "content": event.text or "",
                        "is_error": (event.detail or {}).get("is_error", False),
                    }
                ]
            elif event.kind == "error":
                role = "user"
                content = [
                    {"type": "tool_result", "tool_use_id": "opencode-error", "content": event.text or "", "is_error": True}
                ]
            elif event.kind == "thinking":
                content = [{"type": "thinking", "thinking": event.text or ""}]
            elif event.kind in {"user", "assistant"}:
                role = event.kind
                content = [{"type": "text", "text": event.text or ""}]
            entry.update(type=role, message={"id": f"opencode-{record.line}-{index}", "role": role, "content": content})
            if event.usage is not None:
                entry["message"]["usage"] = event.usage
            converted.append(_Record(record.line, record.text, entry))
    return converted


def _extract_opencode(entries: list[dict[str, Any]], lines: list[int]) -> list[dict[str, Any]]:
    """元の行番号を保持して、OpenCodeへ既存の証拠抽出を適用する。"""
    records = _opencode_records([_Record(line, "", entry) for line, entry in zip(lines, entries, strict=True)])
    return _extract_claude([record.entry for record in records], [record.line for record in records])
