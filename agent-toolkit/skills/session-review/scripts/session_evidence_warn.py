"""証拠抽出の`--warn`照会。ツールの結果とhookの記録から警告の行を抽出する。"""

from __future__ import annotations

import json
import re
from typing import Any

from session_evidence_detail import (
    _persisted_result,
    _tool_hint,
)
from session_evidence_extract import (
    _BODY_KEYS,
    _HOOK_NOTICE_MARKER,
    _LINE_NUMBER_PREFIX,
    _clip,
    _CollectedRecord,
    _events_with_record,
    _is_hook_record,
    _Record,
    _scannable_records,
    _unresolved_events,
    _UnresolvedRecord,
)

from agent_toolkit._agents_server import tool_names as _agents_server_tool_names
from agent_toolkit._common import message_format as _message_format

_WARNING_LINE_PATTERN = re.compile(
    r"^(?:"
    r"\s*(?:\d+\t)?(?:"
    rf"<(?:{_message_format.AUTO_ELEMENT_NAME_PATTERN})"
    r'(?=[^>]*\ssource="[^"]+")(?=[^>]*\skind="(?:warn|warning)")[^>]*>|'
    r"(?:\[auto-generated:[^\]]+\]\s*)?\[(?:warn|warning)\](?:\s|$)|"
    r"⚠(?:\s+|\s*[:：])"
    r")|"
    r"(?:warning|warn|警告)\s*[:：]"
    r")",
    re.IGNORECASE,
)


_ZERO_COUNT_ANNOTATION = r"(?:[\s]*[(（](?:warnings?|エラー|警告)?[\s:：]*0(?:件)?[)）])?"
"""不在を表す語の後に続く、件数が0であることを示す注記。

`警告: なし(warning: 0)`のように、自動チェックの正常終了に件数の注記を添える形で書かれる。
注記を不在判定の対象外にすると、正常終了の本文が警告候補として上がる。
一致の条件を件数が0の場合へ限り、0でない件数が続く本文を除外しない。
"""


_WARNING_ABSENCE_PATTERN = re.compile(
    r"(?:なし|無し|ありません|検出なし|0件|none|no|n/a|-)" + _ZERO_COUNT_ANNOTATION + r"[\s。.]*\Z",
    re.IGNORECASE,
)


_STRUCTURED_WARNING_VALUES = frozenset({"warn", "warning", "警告"})


_STRUCTURED_WARNING_KEYS = frozenset({"warning", "warnings", "warning_message", "warningmessage", "is_warning"})


_STRUCTURED_SEVERITY_KEYS = frozenset({"severity", "level"})


_STRUCTURED_WARNING_BODY_KEYS = ("text", "message", "detail", "description", "output", "content")


_STRUCTURED_WARNING_STREAM_KEYS = ("stdout", "stderr")


_HOOK_XML_END_TAGS = tuple(f"</{element}>" for element in _message_format.AUTO_ELEMENTS)


def _structured_warning_fields(value: dict[str, Any]) -> tuple[list[Any], bool]:
    """辞書から警告キーの値と直接警告を表す標識を取り出す。"""
    warning_values: list[Any] = []
    direct_warning = False
    for key, item in value.items():
        normalized_key = key.casefold() if isinstance(key, str) else ""
        if normalized_key in _STRUCTURED_WARNING_KEYS:
            if normalized_key == "is_warning":
                direct_warning |= item is True
            elif item is True:
                direct_warning = True
            elif item not in (None, "", [], {}):
                warning_values.append(item)
            continue
        if (
            normalized_key in _STRUCTURED_SEVERITY_KEYS
            and isinstance(item, str)
            and item.casefold() in _STRUCTURED_WARNING_VALUES
        ):
            direct_warning = True
        if normalized_key in {"type", "kind"} and isinstance(item, str) and item.casefold() in _STRUCTURED_WARNING_VALUES:
            direct_warning = True
    return warning_values, direct_warning


def _has_structured_warning_body(value: dict[str, Any]) -> bool:
    """警告本文として扱える明示フィールドが辞書に存在するかを返す。"""
    normalized_keys = {key.casefold() for key in value if isinstance(key, str)}
    return bool(normalized_keys.intersection(_STRUCTURED_WARNING_BODY_KEYS))


def _structured_warning_value_texts(value: Any) -> list[str]:
    """構造化警告の値または直接警告辞書から本文だけを取り出す。"""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            return [value] if value.strip() else []
        if isinstance(parsed, (dict, list)):
            return _structured_warning_value_texts(parsed)
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in _structured_warning_value_texts(item)]
    if not isinstance(value, dict):
        return []

    warning_values, _ = _structured_warning_fields(value)
    if warning_values:
        return [text for item in warning_values for text in _structured_warning_value_texts(item)]
    normalized = {key.casefold(): item for key, item in value.items() if isinstance(key, str)}
    for key in _STRUCTURED_WARNING_BODY_KEYS:
        if key in normalized:
            return _structured_warning_value_texts(normalized[key])
    stream_values = [normalized[key] for key in _STRUCTURED_WARNING_STREAM_KEYS if key in normalized]
    if stream_values:
        return [text for item in stream_values for text in _structured_warning_value_texts(item)]
    return [text for item in value.values() if isinstance(item, (dict, list)) for text in _structured_warning_value_texts(item)]


def _warning_hook_records(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """入力本文を除外してhook通知の記録だけを集める。"""
    found: list[dict[str, Any]] = []

    def collect(value: Any, *, in_body: bool = False) -> None:
        if isinstance(value, dict):
            if not in_body and _is_hook_record(value):
                found.append(value)
                return
            for key, item in value.items():
                collect(item, in_body=in_body or key in _BODY_KEYS)
        elif isinstance(value, list):
            for item in value:
                collect(item, in_body=in_body)

    collect(entry)
    return found


def _is_execution_tool_name(name: str | None) -> bool:
    """非構造化の実行時警告を返し得るツール名かを判定する。"""
    if not isinstance(name, str):
        return False
    leaf = name.casefold().rsplit("__", maxsplit=1)[-1].rsplit(".", maxsplit=1)[-1]
    return leaf in {"bash", "commandexecution", "exec_command", "start_batch", "start_shell"}


def _execution_kind_name(name: str, arguments: Any) -> str:
    """`agents_server`の`start`のうちshellのmodeを、コマンド実行のツール名`start_shell`へ揃えて返す。

    統合前の記録はツール名`start_shell`でコマンド実行を表し、統合後は`start`の`mode`で表す。
    """
    leaf = name.rsplit("__", maxsplit=1)[-1]
    if leaf in _agents_server_tool_names.START_OPERATIONS and _agents_server_tool_names.start_mode(leaf, arguments) == "shell":
        return "start_shell"
    return name


def _warning_tool_names(records: list[_Record]) -> dict[str, str]:
    """ツール結果の識別子を、先行する呼び出しのツール名へ対応付ける。"""
    names: dict[str, str] = {}
    for record in records:
        entry = record.entry
        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                tool_id = block.get("id")
                name = block.get("name")
                if isinstance(tool_id, str) and isinstance(name, str):
                    names[tool_id] = _execution_kind_name(name, block.get("input"))
        payload = entry.get("payload")
        if not isinstance(payload, dict) or payload.get("type") not in {"function_call", "custom_tool_call"}:
            continue
        call_id = payload.get("call_id")
        name = payload.get("name")
        if isinstance(call_id, str) and isinstance(name, str):
            names[call_id] = name
    return names


def _warning_result_values(entry: dict[str, Any], tool_names: dict[str, str]) -> list[tuple[Any, bool, bool]]:
    """警告を抽出できる結果値、hook由来および非構造化本文の走査可否を返す。"""
    values: list[tuple[Any, bool, bool]] = []
    tool_use_result = _persisted_result(entry.get("toolUseResult"))
    is_read_result = isinstance(tool_use_result, dict) and "file" in tool_use_result

    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    result_blocks = (
        [block for block in content if isinstance(block, dict) and block.get("type") == "tool_result"]
        if isinstance(content, list)
        else []
    )
    result_ids = [block.get("tool_use_id") for block in result_blocks if isinstance(block.get("tool_use_id"), str)]
    known_result_names = [tool_names[tool_id] for tool_id in result_ids if tool_id in tool_names]
    stream_result = isinstance(tool_use_result, dict) and any(key in tool_use_result for key in ("stdout", "stderr"))
    allow_tool_use_result_markers = (
        any(_is_execution_tool_name(name) for name in known_result_names) if known_result_names else stream_result
    )

    if "toolUseResult" in entry and not is_read_result:
        values.append((tool_use_result, False, allow_tool_use_result_markers))

    if isinstance(content, list) and not is_read_result:
        for block in result_blocks:
            tool_id = block.get("tool_use_id")
            tool_name = tool_names.get(tool_id) if isinstance(tool_id, str) else None
            values.append((block, False, tool_name is not None and _is_execution_tool_name(tool_name)))

    payload = entry.get("payload")
    if isinstance(payload, dict) and payload.get("type") == "function_call_output":
        call_id = payload.get("call_id")
        tool_name = tool_names.get(call_id) if isinstance(call_id, str) else None
        values.append((payload.get("output"), False, tool_name is not None and _is_execution_tool_name(tool_name)))
    if isinstance(payload, dict) and payload.get("type") == "custom_tool_call_output":
        call_id = payload.get("call_id")
        tool_name = tool_names.get(call_id) if isinstance(call_id, str) else None
        values.append((payload.get("output"), False, tool_name is not None and _is_execution_tool_name(tool_name)))
    if entry.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "item_completed":
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") == "CommandExecution":
            values.extend((item.get(key), False, True) for key in ("aggregated_output", "output", "stdout", "stderr"))

    values.extend((hook_record, True, True) for hook_record in _warning_hook_records(entry))
    return values


def _warning_texts(entry: dict[str, Any], tool_names: dict[str, str] | None = None) -> list[str]:
    """本文の由来に基づき、実行結果領域から実行時警告の本文行を返す。

    フック通知標識はhook実行の記録に由来する場合だけ採用する。コマンド出力に由来する通常の
    実行時警告は検出対象として維持し、問題の不在を述べる本文は除外する。
    """
    bodies: list[tuple[str, bool, bool]] = []

    def collect_markers(value: Any, from_hook_record: bool) -> None:
        if isinstance(value, str):
            bodies.append((value, True, from_hook_record))
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return
            if isinstance(parsed, (dict, list)):
                collect_markers(parsed, from_hook_record)
            return
        if isinstance(value, dict):
            for item in value.values():
                collect_markers(item, from_hook_record)
        elif isinstance(value, list):
            for item in value:
                collect_markers(item, from_hook_record)

    def collect_structured(value: Any, from_hook_record: bool) -> None:
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError, ValueError):
                return
            if isinstance(parsed, (dict, list)):
                collect_structured(parsed, from_hook_record)
            return
        if isinstance(value, dict):
            warning_values, direct_warning = _structured_warning_fields(value)
            for warning_value in warning_values:
                for text in _structured_warning_value_texts(warning_value):
                    bodies.append((text, False, from_hook_record))
            if direct_warning and not warning_values and _has_structured_warning_body(value):
                for text in _structured_warning_value_texts(value):
                    bodies.append((text, False, from_hook_record))
            for item in value.values():
                collect_structured(item, from_hook_record)
        elif isinstance(value, list):
            for item in value:
                collect_structured(item, from_hook_record)

    for result_value, from_hook_record, allow_markers in _warning_result_values(entry, tool_names or {}):
        if allow_markers:
            collect_markers(result_value, from_hook_record)
        collect_structured(result_value, from_hook_record)
    unnumbered_by_body = [
        {line.strip() for line in text.splitlines() if _LINE_NUMBER_PREFIX.match(line) is None} for text, _, _ in bodies
    ]
    seen: set[str] = set()
    result: list[str] = []
    for body_index, (text, marker_only, from_hook_record) in enumerate(bodies):
        normalized_text = " ".join(text.split())
        xml_marker = _HOOK_NOTICE_MARKER.match(normalized_text)
        if (
            marker_only
            and from_hook_record
            and xml_marker is not None
            and xml_marker.group("tag_xml") in _STRUCTURED_WARNING_VALUES
        ):
            warning_body = normalized_text[xml_marker.end() :].strip()
            for end_tag in _HOOK_XML_END_TAGS:
                if warning_body.endswith(end_tag):
                    warning_body = warning_body[: -len(end_tag)].rstrip()
                    break
            if warning_body and not _WARNING_ABSENCE_PATTERN.fullmatch(warning_body) and warning_body not in seen:
                seen.add(warning_body)
                result.append(warning_body)
            continue
        # コマンドが表示した文書のコードフェンス内は過去の出力の引用であり、実行時の警告ではない。
        # hook記録と構造化された警告値は文書の表示を含まないため、この判定の対象から外す。
        skip_fenced = marker_only and not from_hook_record
        in_fence = False
        for line in text.splitlines():
            stripped = line.strip()
            if skip_fenced:
                numbered_line = _LINE_NUMBER_PREFIX.match(line)
                unnumbered = (numbered_line.group(1) if numbered_line else line).strip()
                if unnumbered.startswith(("```", "~~~")):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
            if not stripped or not (not marker_only or _WARNING_LINE_PATTERN.search(line)):
                continue
            if _HOOK_NOTICE_MARKER.search(line) and not from_hook_record:
                continue
            warning_body = stripped
            if numbered_body := _LINE_NUMBER_PREFIX.match(warning_body):
                warning_body = numbered_body.group(1).strip()
            if warning_marker := _WARNING_LINE_PATTERN.search(warning_body):
                warning_body = warning_body[warning_marker.end() :].strip()
            if not warning_body or _WARNING_ABSENCE_PATTERN.fullmatch(warning_body):
                continue
            numbered = _LINE_NUMBER_PREFIX.match(line)
            key = stripped
            if numbered:
                normalized = numbered.group(1).strip()
                if any(
                    other_index != body_index and normalized in other_lines
                    for other_index, other_lines in enumerate(unnumbered_by_body)
                ):
                    key = normalized
            if key in seen:
                continue
            seen.add(key)
            result.append(line)
    return result


def _warning_hook_identities(entry: dict[str, Any]) -> dict[str, list[str]]:
    """hook記録の警告本文ごとにツール呼び出し識別子を出現順で返す。"""
    identities: dict[str, list[str]] = {}
    for hook_record in _warning_hook_records(entry):
        tool_use_id = hook_record.get("toolUseID")
        if not isinstance(tool_use_id, str):
            continue
        for line_text in _warning_texts(hook_record):
            tool_use_ids = identities.setdefault(line_text, [])
            if tool_use_id not in tool_use_ids:
                tool_use_ids.append(tool_use_id)
    return identities


def _warning_events(records: list[_Record]) -> list[dict[str, Any]]:
    """セッション全体の実行時警告を行番号付きで返す。

    同じhook通知は成功記録と追加コンテキストへ重複して格納されるため、
    ツール呼び出し識別子と本文の組で1件として扱う。
    識別子を持たない警告はコマンド出力由来の検出を失わないように個別に保持する。
    一致しない場合はその事実を返す。
    """
    events: list[dict[str, Any]] = []
    seen_hook_warnings: set[tuple[str, str]] = set()
    scannable = _scannable_records(records)
    tool_names = _warning_tool_names(scannable)
    for record in scannable:
        matched_lines = _warning_texts(record.entry, tool_names)
        if not matched_lines:
            continue
        hook_identities = _warning_hook_identities(record.entry)
        hint = _tool_hint(record.entry)
        for line_text in matched_lines:
            tool_use_ids = hook_identities.get(line_text, [])
            if not tool_use_ids:
                event: dict[str, Any] = {"kind": "warning", "line": record.line, "text": _clip(line_text)}
                if hint:
                    event["tool"] = hint
                events.append(event)
                continue
            for tool_use_id in tool_use_ids:
                identity = (tool_use_id, line_text)
                if identity in seen_hook_warnings:
                    continue
                seen_hook_warnings.add(identity)
                event = {"kind": "warning", "line": record.line, "text": _clip(line_text)}
                if hint:
                    event["tool"] = hint
                events.append(event)
    return events


def _warning_collection_events(collected: list[_CollectedRecord], unresolved: list[_UnresolvedRecord]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in collected:
        events.extend(_events_with_record(_warning_events(item.records), item.record_id))
    if not events:
        events.append({"kind": "warning", "text": "一致なし"})
    events.extend(_unresolved_events(unresolved))
    return events
