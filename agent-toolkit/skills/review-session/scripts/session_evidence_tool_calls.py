"""証拠抽出の`--tool-calls`照会。ツール呼び出しと入力・結果の対応を返す。"""

from __future__ import annotations

import collections
import datetime
import pathlib
import re
from typing import Any

from session_evidence_extract import (
    _CollectedRecord,
    _json_object,
    _parse_timestamp,
    _role_document,
    _unresolved_events,
    _UnresolvedRecord,
)
from session_evidence_tool_calls_definitions import definition_body_read_events
from session_evidence_tool_calls_records import (
    _conversation_tool_calls,
    _persisted_result_metrics,
    _record_tool_calls,
    _recorded_input,
    _recorded_result,
)
from session_evidence_tool_calls_resources import resource_usage_events

from agent_toolkit._agents_server import tool_names as _agents_server_tool_names


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
            explicit_read = name in {"Read", "read_file", "Skill", "read", "skill"}
            opaque = name in {"Bash", "bash", "shell", "exec", "exec_command", "functions.exec", "functions.exec_command"}
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
                if isinstance(value, str)
                and key != "subagent_md_path"
                and (key.lower().endswith("file") or key.lower().endswith("path"))
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
                "recorded_result_utf8_bytes": len(result.encode("utf-8")) if result is not None else None,
                **(_persisted_result_metrics(entries[call.result_line], call.call_id) if call.result_line is not None else {}),
                "read_range": {key: inputs[key] for key in ("offset", "limit", "start", "max_bytes") if key in inputs},
                "summary": call.text,
            }
            events.append(event)
    return events


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


__all__ = ["_conversation_tool_calls", "definition_body_read_events", "resource_usage_events"]
