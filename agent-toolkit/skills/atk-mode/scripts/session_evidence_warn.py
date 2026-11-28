from __future__ import annotations

import argparse
import bisect
import dataclasses
import datetime
import hashlib
import json
import re
import socket
from pathlib import Path
from typing import Any

from agent_toolkit._agents_server import tool_names as _agents_server_tool_names
from agent_toolkit._common import host_homes, session_record_paths, shell_segments, shell_tokens
from agent_toolkit._common import message_format as _message_format
from agent_toolkit._common.git_invocation import git_subcommand_tokens
from agent_toolkit._common.persisted_output_reference import PERSISTED_OUTPUT_PREFIX
from session_evidence_detail import (
    _persisted_result,
    _tool_hint,
)
from session_evidence_extract import (
    _HOOK_NOTICE_MARKER,
    _LINE_NUMBER_PREFIX,
    _clip,
    _CollectedRecord,
    _events_with_record,
    _load_records_checked,
    _Record,
    _scannable_records,
    _unresolved_events,
    _UnresolvedRecord,
)
from session_evidence_hook_notices import (
    _hook_notice_bodies,
    _hook_notice_parts,
    _hook_occurrence,
    _hook_records,
    _result_hook_records,
)
from session_evidence_stats import _parse_cli_timestamp, _record_timestamp
from session_evidence_tool_calls_records import _record_tool_calls, _restored_result

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


def _is_schema_definition(value: dict[str, Any]) -> bool:
    return isinstance(value.get("properties"), dict) and value.get("type") == "object"


def _runtime_children(value: dict[str, Any]) -> list[Any]:
    return [item for key, item in value.items() if key not in {"inputSchema", "outputSchema", "$schema"}]


def _structured_warning_fields(value: dict[str, Any]) -> tuple[list[Any], bool]:
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
    normalized_keys = {key.casefold() for key in value if isinstance(key, str)}
    return bool(normalized_keys.intersection(_STRUCTURED_WARNING_BODY_KEYS))


def _structured_warning_value_texts(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError, TypeError, ValueError:
            return [value] if value.strip() else []
        if isinstance(parsed, (dict, list)):
            return _structured_warning_value_texts(parsed)
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [text for item in value for text in _structured_warning_value_texts(item)]
    if not isinstance(value, dict):
        return []
    if _is_schema_definition(value):
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
    return [
        text
        for item in _runtime_children(value)
        if isinstance(item, (dict, list))
        for text in _structured_warning_value_texts(item)
    ]


def _is_execution_tool_name(name: str | None) -> bool:
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

    values.extend((hook_record, True, True) for hook_record in _hook_records(entry))
    return values


def _warning_texts(entry: dict[str, Any], tool_names: dict[str, str] | None = None) -> list[str]:
    bodies: list[tuple[str, bool, bool]] = []

    def collect_markers(value: Any, from_hook_record: bool) -> None:
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError, TypeError, ValueError:
                bodies.append((value, True, from_hook_record))
                return
            if isinstance(parsed, (dict, list)):
                collect_markers(parsed, from_hook_record)
            else:
                bodies.append((value, True, from_hook_record))
            return
        if isinstance(value, dict):
            if _is_schema_definition(value):
                return
            for item in _runtime_children(value):
                collect_markers(item, from_hook_record)
        elif isinstance(value, list):
            for item in value:
                collect_markers(item, from_hook_record)

    def collect_structured(value: Any, from_hook_record: bool) -> None:
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError, TypeError, ValueError:
                return
            if isinstance(parsed, (dict, list)):
                collect_structured(parsed, from_hook_record)
            return
        if isinstance(value, dict):
            if _is_schema_definition(value):
                return
            warning_values, direct_warning = _structured_warning_fields(value)
            for warning_value in warning_values:
                for text in _structured_warning_value_texts(warning_value):
                    bodies.append((text, False, from_hook_record))
            if direct_warning and not warning_values and _has_structured_warning_body(value):
                for text in _structured_warning_value_texts(value):
                    bodies.append((text, False, from_hook_record))
            for item in _runtime_children(value):
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
        if from_hook_record and marker_only and _HOOK_NOTICE_MARKER.search(text):
            continue

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


def _warning_hook_identities(entry: dict[str, Any], line: int) -> dict[str, list[str]]:
    identities: dict[str, list[str]] = {}
    for hook_record in _hook_records(entry):
        occurrence = _hook_occurrence(hook_record, line)
        for line_text in _warning_texts(hook_record):
            tool_use_ids = identities.setdefault(line_text, [])
            if occurrence not in tool_use_ids:
                tool_use_ids.append(occurrence)
    return identities


def _marked_hook_warnings(
    entry: dict[str, Any], line: int, seen: set[tuple[str, str | None, str, str]]
) -> list[dict[str, Any]]:
    events = []
    for hook_record in _hook_records(entry):
        hook_name = hook_record.get("hookName")
        for body in _hook_notice_bodies(hook_record):
            for key, full_text, display in _hook_notice_parts(body, hook_name if isinstance(hook_name, str) else None):
                if key.tag not in {"warn", "warning", "block"}:
                    continue
                compact_text = " ".join(full_text.split())
                if not compact_text or _WARNING_ABSENCE_PATTERN.fullmatch(compact_text):
                    continue
                identity = (_hook_occurrence(hook_record, line), key.hook, key.tag, compact_text)
                if identity in seen:
                    continue
                seen.add(identity)
                events.append(
                    {
                        "kind": "warning",
                        "line": line,
                        "text": _clip(display),
                        "full_text": full_text,
                        "source": key.hook or key.hook_name,
                        **({"tool": hint} if (hint := _tool_hint(entry)) else {}),
                    }
                )
    return events


def _warning_events(records: list[_Record]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen_hook_warnings: set[tuple[str, str]] = set()
    seen_marked: set[tuple[str, str | None, str, str]] = set()
    scannable = _scannable_records(records)
    tool_names = _warning_tool_names(scannable)
    for record in scannable:
        events.extend(_marked_hook_warnings(record.entry, record.line, seen_marked))
        matched_lines = _warning_texts(record.entry, tool_names)
        if not matched_lines:
            continue
        hook_identities = _warning_hook_identities(record.entry, record.line)
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


def _warning_issuer(command: str) -> str | None:
    commands = shell_segments.extract_execution_segments(command)
    if not commands or any(
        not segment.resolved
        or not (
            segment.tokens in {("set", "-o", "pipefail"), ("set", "-euo", "pipefail")}
            or len(segment.tokens) == 2
            and segment.tokens[0] == "cd"
            and not segment.tokens[1].startswith("-")
        )
        for segment in commands[:-1]
    ):
        return None
    segment = commands[-1]
    if not segment.resolved or not (tokens := segment.tokens):
        return None
    if shell_tokens.is_atk_command(tokens) and tokens[1:3] in {("wi", "add"), ("wi", "edit")}:
        return "wi"
    if tokens[0].replace("\\", "/").rsplit("/", 1)[-1] in {"git", "git.exe"}:
        segment = dataclasses.replace(segment, tokens=("git", *tokens[1:]))
    parsed = git_subcommand_tokens(segment)
    return "git" if parsed is not None and parsed[0] == "commit" else None


_DRAFT_OUTPUT_BOUNDARY = re.compile(
    r"(?m)^(?:原稿: [^\r\n]*\r?\n冒頭保存先: [^\r\n]*\r?\n"
    r"冒頭文字数: (?P<size>[0-9]{1,10})\r?\n冒頭本文:(?P<transport_eol>\r?\n)"
    r"|失敗: (?:HEADコミットの取得|remote同期)に失敗した。確定済みの本文を以下に再表示する\r?$)"
)
_DRAFT_PREVIEW_END = re.compile(r"\r?\n冒頭本文終了(?=\r?\n|$)")
_DRAFT_CANDIDATE_LINE = re.compile(r"(?m)^情報: 先送りの候補 [0-9]+行目: [^\r\n]*")


def _warning_output(body: str, issuer: str) -> str:
    if issuer != "wi":
        return body
    parts = []
    start = 0
    while (preview := _DRAFT_OUTPUT_BOUNDARY.search(body, start)) is not None:
        parts.append(body[start : preview.start()])
        if (size := preview["size"]) is None:
            break
        opening_start = preview.end()
        if preview["transport_eol"] == "\r\n":
            body = body[:opening_start] + body[opening_start:].replace("\r\n", "\n")
        end = opening_start + int(size)
        if (closing := _DRAFT_PREVIEW_END.match(body, end)) is None:
            break
        start = closing.end()
    else:
        parts.append(body[start:])
    return _DRAFT_CANDIDATE_LINE.sub("", "".join(parts))


def _result_warning_body(entry: dict[str, Any], call_id: str) -> str:
    payload = entry.get("payload")
    item = payload.get("item") if isinstance(payload, dict) else None
    if isinstance(item, dict) and item.get("type") == "CommandExecution":
        for key in ("aggregated_output", "output"):
            if isinstance(value := item.get(key), str) and value:
                return value
        streams = (item.get("stdout"), item.get("stderr"))
        return "\n".join(value for value in streams if isinstance(value, str))
    return _restored_result(entry, call_id) or ""


def _assistant_speech(entry: dict[str, Any]) -> str:
    message = entry.get("message") if entry.get("type") == "assistant" else entry.get("payload")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return _clip(content)
    if isinstance(content, list):
        return _clip(
            " ".join(
                block["text"]
                for block in content
                if isinstance(block, dict)
                and block.get("type") in {"text", "output_text"}
                and isinstance(block.get("text"), str)
            )
        )
    return ""


def _countable_warnings(
    records: list[_Record], sources: set[str], since: datetime.datetime, until: datetime.datetime
) -> tuple[list[dict[str, Any]], int]:
    events = {}
    undated = set()
    records = _scannable_records(records)
    by_line = {record.line: record for record in records}
    calls = _record_tool_calls(records)
    operations = [(call.line, call.text or call.tool) for call in calls]
    operation_lines = [line for line, _ in operations]
    speeches = [(record.line, spoken) for record in records if (spoken := _assistant_speech(record.entry))]
    speech_lines = [line for line, _ in speeches]

    def collect(bodies: list[str], record: _Record, channel: str, occurrence: str) -> None:
        indexes: dict[tuple[str, str], int] = {}
        timestamp = _record_timestamp(record)
        for part, text, _ in (notice for body in bodies for notice in _hook_notice_parts(body, None)):
            if part.hook not in sources or part.tag not in {"warn", "warning", "block"} or not text:
                continue
            kind = "warn" if part.tag == "warning" else part.tag
            source_tag = part.hook, kind
            position = indexes.get(source_tag, 0)
            indexes[source_tag] = position + 1
            key = (channel, occurrence, *source_tag, position)
            if timestamp is None:
                undated.add(key)
            elif since <= timestamp < until and key not in events:
                speech = bisect.bisect_left(speech_lines, record.line) - 1
                action = bisect.bisect_right(operation_lines, record.line)
                events[key] = {
                    "source": part.hook,
                    "kind": kind,
                    "line": record.line,
                    "text": _clip(text),
                    "previous_speech": speeches[speech][1] if speech >= 0 else None,
                    "next_operation": _clip(operations[action][1]) if action < len(operations) else None,
                }

    blocked: set[tuple[int, str]] = set()
    for record in records:
        for hook in _result_hook_records(record.entry):
            if isinstance(call_id := hook.get("toolUseID"), str):
                blocked.add((record.line, call_id))
        hook_bodies: dict[str, list[str]] = {}
        for hook in _hook_records(record.entry):
            occurrence = _hook_occurrence(hook, record.line)
            hook_bodies.setdefault(occurrence, []).extend(_hook_notice_bodies(hook))
        for occurrence, bodies in hook_bodies.items():
            collect(bodies, record, "hook", occurrence)
    for call in calls:
        if (
            call.call_id is not None
            and call.result_line is not None
            and (call.result_line, call.call_id) not in blocked
            and _is_execution_tool_name(call.tool)
            and (issuer := _warning_issuer(call.text)) is not None
        ):
            record = by_line[call.result_line]
            body = _warning_output(_result_warning_body(record.entry, call.call_id), issuer)
            collect([body], record, "command", call.call_id)
    return [dict(event, occurrence=key[:2] + key[-1:]) for key, event in events.items()], len(undated - events.keys())


def _warning_records() -> list[tuple[str, Path, str]]:
    projects = host_homes.claude_config_dir() / "projects"
    claude = [("claude", path, path.stem) for path in projects.glob("*/*.jsonl") if path.is_file()]
    claude.extend(
        ("claude", path, path.parent.parent.name) for path in projects.glob("*/*/subagents/*.jsonl") if path.is_file()
    )
    codex = [
        ("codex", path, session_record_paths.codex_session_id(path))
        for path in session_record_paths.codex_rollouts(host_homes.codex_home())
    ]
    return sorted(claude + codex)


def _has_warning_candidate(path: Path, needles: tuple[bytes, ...]) -> bool:
    overlap = max(map(len, needles)) - 1
    try:
        with path.open("rb") as stream:
            tail = b""
            while chunk := stream.read(1024 * 1024):
                block = tail + chunk
                if any(needle in block for needle in needles):
                    return True
                tail = block[-overlap:]
    except OSError:
        return True
    return False


_CODEX_STOP_SOURCE = "norm_excuse_gate"
_CODEX_STOP_MARKER = f" decision=codex_stop_warning source={_CODEX_STOP_SOURCE} "
_CODEX_STOP_ID = re.compile(r"occurrence=([0-9a-f]{64})")


def _codex_rollout_start(path: Path) -> datetime.datetime | None:
    try:
        with path.open("rb") as stream:
            metadata = json.loads(stream.readline())
        if isinstance(metadata, dict) and metadata.get("type") == "session_meta":
            timestamp = metadata.get("timestamp")
            if isinstance(timestamp, str):
                return _parse_cli_timestamp(timestamp)
    except OSError, ValueError, OverflowError:
        pass
    return None


def _codex_stop_contexts(rollout: Path, occurrences: set[str]) -> tuple[dict[str, tuple[int, str, str | None]], bool]:
    loaded = _load_records_checked(str(rollout), allow_partial_records=True)
    if loaded.records is None:
        return {}, True
    answers: dict[str, tuple[int, str]] = {}
    repeated: set[str] = set()
    for record in loaded.records:
        payload = record.entry.get("payload")
        if record.entry.get("type") != "event_msg" or not isinstance(payload, dict):
            continue
        turn_id, item = payload.get("turn_id"), payload.get("item")
        if payload.get("type") != "item_completed" or not isinstance(turn_id, str) or not isinstance(item, dict):
            continue
        if item.get("type") != "AgentMessage":
            continue
        blocks = item.get("content")
        if not isinstance(blocks, list):
            continue
        answer = "\n".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "Text" and isinstance(block.get("text"), str)
        )
        if not answer:
            continue
        digest = hashlib.sha256(json.dumps((turn_id, answer)).encode()).hexdigest()
        if digest in occurrences:
            if digest in answers:
                repeated.add(digest)
            answers[digest] = record.line, _clip(answer)
    calls = _record_tool_calls(_scannable_records(loaded.records))
    operation_lines = [call.line for call in calls]
    contexts = {}
    for digest, (line, speech) in answers.items():
        if digest in repeated:
            continue
        following = bisect.bisect_right(operation_lines, line)
        call = calls[following] if following < len(calls) else None
        contexts[digest] = line, speech, _clip(call.text or call.tool) if call is not None else None
    return contexts, bool(loaded.gaps) or len(contexts) != len(occurrences)


def _codex_stop_warnings(
    paths: list[tuple[str, Path, str]], since: datetime.datetime, until: datetime.datetime, found: dict[tuple, dict[str, Any]]
) -> tuple[set[Path], int]:
    """直接Stopの診断ログは会話単位で読み、旧世代と現行に重複する事象を一度だけ数える。  pyfltr: allow-comment"""
    partial: set[Path] = set()
    undated = 0
    seen: set[str] = set()
    for engine, rollout, session in paths:
        if engine != "codex" or session in seen or re.fullmatch(r"[A-Za-z0-9_-]+", session) is None:
            continue
        seen.add(session)
        start = _codex_rollout_start(rollout)
        if start is not None and start >= until:
            continue
        active = session_record_paths.stop_log_path(session)
        rotated = active.with_suffix(".log.1")
        first: datetime.datetime | None = None
        observed_in_window = False
        has_rotated = incomplete = False
        events: dict[str, tuple[datetime.datetime, Path, int]] = {}
        for path in (rotated, active):
            try:
                data = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                incomplete |= path == active
                continue
            except OSError, UnicodeError:
                incomplete = True
                continue
            has_rotated |= path == rotated
            for number, row in enumerate(data.splitlines(keepends=True), 1):
                raw_timestamp, _, _ = row.partition(" ")
                try:
                    timestamp = _parse_cli_timestamp(raw_timestamp)
                except ValueError, OverflowError:
                    undated += _CODEX_STOP_MARKER in row
                    continue
                first = timestamp if first is None else min(first, timestamp)
                in_window = since <= timestamp < until
                observed_in_window |= in_window
                prefix = raw_timestamp + _CODEX_STOP_MARKER
                if not row.startswith(prefix):
                    continue
                digest = _CODEX_STOP_ID.fullmatch(row.removeprefix(prefix).removesuffix("\n"))
                if not row.endswith("\n") or digest is None:
                    incomplete |= in_window
                    continue
                key = digest[1]
                previous = events.get(key)
                if previous is None or previous[0] > timestamp:
                    events[key] = (timestamp, path, number)
        occurrences = {key for key, (timestamp, _, _) in events.items() if since <= timestamp < until}
        contexts, missing_context = _codex_stop_contexts(rollout, occurrences) if occurrences else ({}, False)
        incomplete |= missing_context
        for key in sorted(occurrences, key=events.__getitem__):
            _, diagnostic_path, diagnostic_line = events[key]
            if key in contexts:
                line, speech, operation = contexts[key]
                evidence_path = rollout
            else:
                line, speech, operation = diagnostic_line, None, None
                evidence_path = diagnostic_path
            found[(engine, session, _CODEX_STOP_SOURCE, "warn", "codex_stop", key)] = {
                "source": _CODEX_STOP_SOURCE,
                "kind": "warn",
                "line": line,
                "text": "Codex Stopが送出した再発防止の見送りに関する警告",
                "previous_speech": speech,
                "next_operation": operation,
                "engine": engine,
                "conversation": session,
                "record": f"{evidence_path}:{line}",
            }
        try:
            if not observed_in_window and rollout.stat().st_mtime < since.timestamp():
                continue
        except OSError:
            incomplete = True
        lost_beginning = has_rotated and first is not None and first > max(since, start or since)
        if incomplete or not observed_in_window or lost_beginning:
            partial.add(rollout)
    return partial, undated


def warning_count_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="このホストで記録した警告・遮断を発行元と期間で集計する。")
    parser.add_argument("--source", action="append", required=True, help="発行元。複数回指定できる。")
    parser.add_argument("--since", required=True, help="開始日またはISO 8601日時。開始を含む。")
    parser.add_argument("--until", required=True, help="終了日（その日を含む）またはISO 8601日時（終了を含まない）。")
    args = parser.parse_args(argv)
    try:
        since, until = (_parse_cli_timestamp(value) for value in (args.since, args.until))
        if len(args.until) == 10:
            until = _parse_cli_timestamp((datetime.date.fromisoformat(args.until) + datetime.timedelta(days=1)).isoformat())
        if since >= until:
            raise ValueError("開始は終了より前を指定する")
    except ValueError, OverflowError:
        parser.error("範囲をYYYY-MM-DDかISO 8601日時で指定する。開始は終了より前を指定する。")
    sources = set(args.source)
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", source) for source in sources):
        parser.error("sourceには英数字・ピリオド・アンダースコア・ハイフンを指定する。")
    needles = (*[source.encode("ascii") for source in sources], b"persistedOutputPath", PERSISTED_OUTPUT_PREFIX.encode())
    found: dict[tuple, dict[str, Any]] = {}
    candidates = undated = 0
    unresolved: set[Path] = set()
    paths = _warning_records()
    for engine, path, session in paths:
        if not _has_warning_candidate(path, needles):
            continue
        candidates += 1
        loaded = _load_records_checked(str(path))
        if loaded.records is None and loaded.failure is not None and loaded.failure.line is not None:
            loaded = _load_records_checked(str(path), allow_partial_records=True)
        if loaded.records is None:
            unresolved.add(path)
            continue
        if loaded.gaps:
            unresolved.add(path)
        timestamps = (_record_timestamp(record) for record in loaded.records)
        if not any(since <= timestamp < until for timestamp in timestamps if timestamp is not None):
            continue
        events, skipped = _countable_warnings(loaded.records, sources, since, until)
        undated += skipped
        for event in events:
            channel, call, position = event.pop("occurrence")
            identity = (
                engine,
                session,
                event["source"],
                event["kind"],
                channel,
                path if call.startswith("line:") else None,
                call,
                position,
            )
            found.setdefault(
                identity, {**event, "engine": engine, "conversation": session, "record": f"{path}:{event['line']}"}
            )
    if _CODEX_STOP_SOURCE in sources:
        stop_partial, stop_undated = _codex_stop_warnings(paths, since, until, found)
        unresolved.update(stop_partial)
        undated += stop_undated
    counts = {}
    for source in sorted(sources):
        matching = [event for event in found.values() if event["source"] == source]
        counts[source] = {
            "occurrences": len(matching),
            "conversations": len({(event["engine"], event["conversation"]) for event in matching}),
        }
    print(
        json.dumps(
            {
                "host": socket.gethostname(),
                "since": since.isoformat(),
                "until_exclusive": until.isoformat(),
                "records": len(paths),
                "candidate_records": candidates,
                "partial_records": len(unresolved),
                "undated_occurrences": undated,
                "sources": counts,
                "occurrences": list(found.values()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(warning_count_main())
