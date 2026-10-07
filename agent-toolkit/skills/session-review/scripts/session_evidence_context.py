"""証拠抽出の`--record-schema`・`--context-at`照会。記録の形の一覧と、指定した行の時点の文脈を返す。"""

from __future__ import annotations

import re
from typing import Any

from session_evidence_detail import (
    _entry_timestamp,
    _matched_lines,
    _resolve_record_alias,
    _resolve_record_locator,
)
from session_evidence_extract import (
    _RECORD_LOCATOR_NEXT_ACTION,
    _clip,
    _CollectedRecord,
    _error_event,
    _Record,
    _scannable_records,
)
from session_evidence_stats import (
    _claude_call_hint,
    _codex_call_hint,
    _compaction_event,
)

from agent_toolkit._common.runtime_identity import latest_identity as _latest_identity


def _json_type(value: Any) -> str:
    """JSON値の型名を値そのものを含めずに返す。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    return "object"


def _schema_paths(value: Any, path: str, paths: dict[str, set[str]]) -> None:
    """値を含めず、JSON pathごとの観測型を集約する。"""
    paths.setdefault(path, set()).add(_json_type(value))
    if isinstance(value, dict):
        for key in sorted(value):
            _schema_paths(value[key], f"{path}.{key}", paths)
    elif isinstance(value, list):
        for item in value:
            _schema_paths(item, f"{path}[]", paths)


def _record_schema_collection_events(
    collected: list[_CollectedRecord], locators: list[str]
) -> tuple[list[dict[str, Any]], int]:
    """locator先の元JSON entryからkey pathとJSON型だけを返す。"""
    events: list[dict[str, Any]] = []
    for locator in locators:
        selected, line, error_events = _resolve_record_locator(collected, locator, label="構造照会")
        if error_events is not None:
            return error_events, 2
        assert selected is not None and line is not None
        entry = next(record.entry for record in selected.records if record.line == line)
        paths: dict[str, set[str]] = {}
        _schema_paths(entry, "$", paths)
        events.extend(
            {
                "kind": "record-schema",
                "record": selected.record_id,
                "line": line,
                "path": path,
                "types": sorted(types),
            }
            for path, types in sorted(paths.items())
        )
    return events, 0


def _context_at_events(collected: list[_CollectedRecord], locator: str, phrases: list[str]) -> tuple[list[dict[str, Any]], int]:
    """指定した記録位置の時点で、各phraseがその記録の文脈にあったかを判定する。

    文脈はエージェントごとに独立するため、母集団は指定した記録の指定行より前のレコードだけとする。
    指定行より前で最後の圧縮境界（有効境界）の行自身とそれより後の一致は、圧縮後も文脈にある一致として扱う。
    Codexは`compacted`レコードの`replacement_history`が圧縮後の文脈を保持し、
    Claude Codeは境界の直後にルールファイルと起動済みスキル本文を再注入するため、
    境界以後に現れた一致だけで圧縮後の文脈を判定できる。
    """
    if ":" in locator:
        record_id, raw_line = locator.rsplit(":", 1)
    else:
        record_id, raw_line = "main", locator
    if not record_id or not raw_line.isdecimal():
        return [_error_event(f"記録位置が不正: {locator}", next_action=_RECORD_LOCATOR_NEXT_ACTION)], 2
    selected, ambiguous = _resolve_record_alias(collected, record_id)
    if ambiguous:
        return [_error_event(f"記録別名が曖昧: {record_id}", next_action=_RECORD_LOCATOR_NEXT_ACTION)], 2
    if selected is None:
        return [_error_event(f"記録が不明: {record_id}", next_action=_RECORD_LOCATOR_NEXT_ACTION)], 2
    record_id = selected.record_id
    target_line = int(raw_line)
    if not selected.records or not 1 <= target_line <= max(record.line for record in selected.records):
        return [_error_event(f"行番号が記録の範囲外: {locator}", next_action=_RECORD_LOCATOR_NEXT_ACTION)], 2
    if not phrases or any(not phrase for phrase in phrases):
        return [
            _error_event(
                "--phraseへ空でない固定文字列を1件以上指定する",
                next_action="`--phrase <固定文字列>`を1件以上付けて再実行する",
            )
        ], 2

    preceding = [record for record in selected.records if record.line < target_line]
    boundary = next(
        (record for record in reversed(preceding) if _compaction_event(record, record_id) is not None),
        None,
    )
    channels = _context_channels(preceding)
    latest = _latest_identity(selected.records, selected.runtime, before_line=target_line - 1) if selected.runtime else None
    events: list[dict[str, Any]] = []
    for phrase in phrases:
        pattern = re.compile(re.escape(phrase))
        matches: list[dict[str, Any]] = []
        for record in _scannable_records(preceding):
            for line_text in _matched_lines(record.entry, pattern):
                match: dict[str, Any] = {
                    "kind": "context-match",
                    "record": record_id,
                    "phrase": phrase,
                    "line": record.line,
                    "timestamp": _entry_timestamp(record.entry),
                    "text": _clip(line_text),
                    **channels[record.line],
                    "before_boundary": boundary is not None and record.line < boundary.line,
                }
                matches.append(match)
        retained = [match for match in matches if not match["before_boundary"]]
        verdict = "present" if retained else "dropped-by-compaction" if matches else "absent"
        events.append(
            {
                "kind": "context-verdict",
                "record": record_id,
                "line": target_line,
                "phrase": phrase,
                "verdict": verdict,
                "boundary_line": boundary.line if boundary is not None else None,
                "boundary_timestamp": _entry_timestamp(boundary.entry) if boundary is not None else None,
                "match_count": len(matches),
                "last_match_line": matches[-1]["line"] if matches else None,
                "last_match_timestamp": matches[-1]["timestamp"] if matches else None,
                "observed_identity": latest[1].public() if latest is not None else None,
                "identity_locator": {"record": record_id, "line": latest[0]} if latest is not None else None,
            }
        )
        events.extend(matches)
    return events, 0


def _context_channels(records: list[_Record]) -> dict[int, dict[str, Any]]:
    """各レコードの本文が文脈へどのように入ったかを表す`channel`を、レコードの構造から行番号ごとに返す。

    ツール結果には対応する呼び出しのツール名と入力の要点を添える。条文の本文が
    ルールファイル、Skill起動、Readの結果、hook通知のどれで入ったかによって、
    圧縮後の再注入の有無と原因の区分が変わるためである。
    """
    calls: dict[str, tuple[str, str | None]] = {}
    channels: dict[int, dict[str, Any]] = {}
    for record in records:
        entry = record.entry
        entry_type = entry.get("type")
        channel: dict[str, Any] = {"channel": "other"}
        message = entry.get("message")
        payload = entry.get("payload")
        if entry_type == "attachment":
            attachment = entry.get("attachment")
            attachment_type = attachment.get("type") if isinstance(attachment, dict) else None
            if isinstance(attachment_type, str) and attachment_type:
                channel = {"channel": f"attachment:{attachment_type}"}
        elif entry_type in {"user", "assistant"} and isinstance(message, dict):
            content = message.get("content")
            blocks = [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []
            for block in blocks:
                if block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                    calls[block["id"]] = (str(block.get("name", "")), _claude_call_hint(block.get("input")))
            result = next(
                (block for block in blocks if block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str)),
                None,
            )
            if entry_type == "assistant":
                channel = {"channel": "assistant"}
            elif entry.get("isCompactSummary"):
                channel = {"channel": "compact-summary"}
            elif entry.get("isMeta"):
                channel = {"channel": "meta"}
            elif result is not None:
                channel = _tool_result_channel(calls.get(result["tool_use_id"]))
            else:
                channel = {"channel": "user"}
        elif entry_type == "compacted":
            channel = {"channel": "compaction-retained"}
        elif entry_type == "response_item" and isinstance(payload, dict):
            payload_type = payload.get("type")
            call_id = payload.get("call_id")
            if payload_type == "message" and isinstance(payload.get("role"), str):
                channel = {"channel": payload["role"]}
            elif payload_type in {"custom_tool_call", "function_call"}:
                if isinstance(call_id, str):
                    calls[call_id] = (str(payload.get("name", "")), _codex_call_hint(payload))
                channel = {"channel": "assistant"}
            elif payload_type in {"custom_tool_call_output", "function_call_output"}:
                channel = _tool_result_channel(calls.get(call_id) if isinstance(call_id, str) else None)
        channels[record.line] = channel
    return channels


_CONTEXT_TOOL_INPUT_LENGTH = 200
"""`channel`へ添える呼び出し入力の上限。用途はどのファイルやコマンドの結果かの識別に限られるため、先頭行を短く切り詰める。"""


def _tool_result_channel(call: tuple[str, str | None] | None) -> dict[str, Any]:
    """ツール結果の`channel`へ、対応する呼び出しのツール名と入力の要点を添える。"""
    channel: dict[str, Any] = {"channel": "tool-result"}
    if call is not None:
        name, hint = call
        channel["tool"] = name
        if hint:
            channel["tool_input"] = _clip(hint.splitlines()[0], _CONTEXT_TOOL_INPUT_LENGTH)
    return channel
