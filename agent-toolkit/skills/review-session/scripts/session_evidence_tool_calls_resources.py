"""証拠抽出のツール呼び出し（session_evidence_tool_calls_resources）。"""

from __future__ import annotations

import json
import re
from typing import Any

from session_evidence_extract import _HOOK_NOTICE_MARKER, _codex_text_blocks, _CollectedRecord, _launch_mode, _text_blocks
from session_evidence_stats import _stats_summary_data
from session_evidence_tool_calls_definitions import _expected_body_read
from session_evidence_tool_calls_records import (
    _conversation_tool_calls,
    _persisted_result_metrics,
    _record_tool_calls,
    _recorded_input,
    _recorded_result,
)

from agent_toolkit._common import requirement_units

_SHELL_ABSOLUTE_PATH = re.compile(
    r"""(?P<quote>['"])(?P<quoted>(?:/|[A-Za-z]:[\\/]|\\\\)[^'"]*)(?P=quote)|"""
    r"""(?<![\w:/\\])(?P<bare>(?:/|[A-Za-z]:[\\/]|\\\\)[^\s"'`;&|()<>{}\[\]]+)"""
)


_LARGE_OUTPUT_MIN_BYTES = 8_192


def _string_values(value: Any) -> list[str]:
    """JSONの値にある本文を取り出し、識別子・数値の置換は行わない。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _string_values(child)]
    if isinstance(value, list):
        return [text for child in value for text in _string_values(child)]
    return []


def _input_component(name: str, inputs: dict[str, Any]) -> str:
    """生成した入力の用途を、公開ツール名と入力フィールドから分類する。"""
    if name in {"Write", "Edit", "MultiEdit", "apply_patch", "write", "edit", "patch"}:
        return "editing"
    if any(key in inputs for key in ("prompt", "message", "instructions")):
        return "delegation"
    if any(key in inputs for key in ("command", "cmd", "CommandLine")):
        return "command"
    return "other"


def _partial_repetitions(item: _CollectedRecord) -> list[dict[str, Any]]:
    """同じ記録へ新たに追加された文の一致位置を示す。APIの履歴再送量は数えない。

    既存の要求単位抽出を使い、短い定型句も意味判断用の候補として残す。
    圧縮・別担当への必要な配送と不要な再取込の区別は読者が前後関係から判断する。
    """
    units: dict[str, list[dict[str, Any]]] = {}
    events = _conversation_tool_calls(item.records)
    for record in item.records:
        message = record.entry.get("message", record.entry.get("payload"))
        if isinstance(message, dict):
            extractor = _codex_text_blocks if item.runtime == "codex" else _text_blocks
            for text in extractor(message.get("content")):
                events.append({"text": text, "line": record.line, "kind": "message"})
    for event in sorted(events, key=lambda event: event["line"]):
        text = event["text"]
        try:
            value = json.loads(text)
        except ValueError, TypeError:
            value = text
        for body_number, body in enumerate(_string_values(value), 1):
            # フェンス内の引用・JSON文字列も比較へ含める。分割自体は共通実装を使う。
            body = re.sub(r"^\s*```[^\n]*$", "", body, flags=re.MULTILINE)
            for unit_number, unit in enumerate(requirement_units.requirement_units(body.splitlines()), 1):
                row = {
                    "location": f"{item.record_id}:{event['line']}",
                    "component": event["kind"],
                    "body": body_number,
                    "unit": unit_number,
                }
                locations = units.setdefault(unit, [])
                if row not in locations:
                    locations.append(row)
    return [
        {"excerpt": unit[:120], "characters": len(unit), "locations": locations, "count": len(locations)}
        for unit, locations in units.items()
        if len(locations) > 1
    ]


def resource_usage_events(collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    """担当ごとの読込・Skill・反復・結果量を、欠陥の判定を加えず集計する。

    shellとJavaScriptの入力は命令文字列のまま残し、ファイルの読了件数へ換算しない。
    結果上位は各担当から選ぶため、非律速の担当の大量出力にも到達できる。
    """
    events: list[dict[str, Any]] = []
    for item in collected:
        entries = {record.line: record.entry for record in item.records}
        calls = _record_tool_calls(item.records)
        reads: list[dict[str, Any]] = []
        skills: list[dict[str, Any]] = []
        shell_paths: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        large_reviews: list[dict[str, Any]] = []
        persisted_results: list[dict[str, Any]] = []
        generated_inputs: list[dict[str, Any]] = []
        hook_notices: dict[tuple[str, str, str], dict[str, Any]] = {}
        expected_large_reads = 0
        repeats: dict[tuple[str, str], list[str]] = {}
        for call in calls:
            name = call.tool.rsplit("__", 1)[-1]
            inputs = _recorded_input(entries[call.line], call.call_id)
            locator = f"{item.record_id}:{call.line}"
            input_text = json.dumps(inputs, ensure_ascii=False) if inputs else call.text
            generated_inputs.append(
                {
                    "location": locator,
                    "component": _input_component(name, inputs),
                    "utf8_bytes": len(input_text.encode("utf-8")),
                    "managed_temp_paths": [
                        match["quoted"] or match["bare"]
                        for match in _SHELL_ABSOLUTE_PATH.finditer(input_text)
                        if "managed-temp/" in (match["quoted"] or match["bare"])
                    ],
                }
            )
            result = _recorded_result(entries[call.result_line], call.call_id) if call.result_line is not None else None
            result_metrics = {
                "result_location": f"{item.record_id}:{call.result_line}" if call.result_line is not None else None,
                "characters": len(result) if result is not None else None,
                "utf8_bytes": len(result.encode("utf-8")) if result is not None else None,
                **(_persisted_result_metrics(entries[call.result_line], call.call_id) if call.result_line is not None else {}),
            }
            if call.text:
                repeats.setdefault((call.tool, call.text), []).append(locator)
            if name in {"Read", "read_file", "Skill", "read", "skill"}:
                row = {
                    "location": locator,
                    "tool": call.tool,
                    "target": inputs.get("skill", inputs.get("name"))
                    if name in {"Skill", "skill"}
                    else inputs.get("file_path", inputs.get("filePath", inputs.get("path", inputs.get("Path")))),
                    "range": {key: inputs[key] for key in ("offset", "limit", "start", "max_bytes") if key in inputs},
                    **result_metrics,
                }
                (skills if name in {"Skill", "skill"} else reads).append(row)
            if name in {"Bash", "bash", "shell", "exec_command", "functions.exec_command", "CommandExecution"}:
                command = inputs.get("command", inputs.get("cmd", call.text))
                if isinstance(command, str):
                    shell_paths.extend(
                        {"location": locator, "tool": call.tool, "target": match["quoted"] or match["bare"]}
                        for match in _SHELL_ABSOLUTE_PATH.finditer(command)
                    )
            if result is not None:
                for match in _HOOK_NOTICE_MARKER.finditer(result):
                    source = match["hook_xml"] or match["hook_legacy"] or "unknown"
                    kind = match["tag_xml"] or match["tag_legacy"] or "unknown"
                    end = result.find("</atk-auto>", match.end())
                    body = result[match.end() : end if end >= 0 else len(result)]
                    notice = hook_notices.setdefault(
                        (source, kind, body),
                        {"source": source, "notice_kind": kind, "excerpt": body[:120], "locations": []},
                    )
                    notice["locations"].append(f"{item.record_id}:{call.result_line}")
                result_row: dict[str, Any] = {
                    "location": locator,
                    "result_location": f"{item.record_id}:{call.result_line}",
                    "tool": call.tool,
                    "characters": len(result),
                    "utf8_bytes": len(result.encode("utf-8")),
                    **{
                        key: value
                        for key, value in result_metrics.items()
                        if key.startswith("persisted") or key.startswith("original_")
                    },
                }
                results.append(result_row)
                if result_row.get("persisted") and (result_row.get("original_utf8_bytes") or 0) >= _LARGE_OUTPUT_MIN_BYTES:
                    persisted_results.append({**result_row, "input_summary": " ".join(call.text.split())[:300]})
                if result_row["utf8_bytes"] >= _LARGE_OUTPUT_MIN_BYTES:
                    if _expected_body_read(call.tool, inputs):
                        expected_large_reads += 1
                    else:
                        large_reviews.append({**result_row, "input_summary": " ".join(call.text.split())[:300]})
        events.append(
            {
                "kind": "stats-resource-usage",
                "record": item.record_id,
                "role": item.role,
                "mode": _launch_mode(item, collected),
                "observed_identities": _stats_summary_data(item.records, item.runtime).get("observed_identities", [])
                if item.runtime is not None
                else [],
                "tool_calls": len(calls),
                "generated_inputs": generated_inputs,
                "generated_input_utf8_bytes": sum(row["utf8_bytes"] for row in generated_inputs),
                "hook_notices": list(hook_notices.values()),
                "partial_repetitions": _partial_repetitions(item),
                "reads": reads,
                "skills": skills,
                "shell_paths": shell_paths,
                "recorded_result_characters": sum(row["characters"] for row in results),
                "recorded_result_utf8_bytes": sum(row["utf8_bytes"] for row in results),
                "largest_results": sorted(results, key=lambda row: (-row["utf8_bytes"], row["location"]))[:10],
                "omitted_largest_result_count": max(0, len(results) - 10),
                "large_output_reviews": sorted(large_reviews, key=lambda row: (-row["utf8_bytes"], row["location"]))[:10],
                "large_output_review_count": len(large_reviews),
                "expected_large_read_count": expected_large_reads,
                "persisted_result_count": len(persisted_results),
                "persisted_results": sorted(persisted_results, key=lambda row: (-row["original_utf8_bytes"], row["location"]))[
                    :10
                ],
                "repeats": [
                    {"tool": tool, "input": text, "locations": locations, "count": len(locations)}
                    for (tool, text), locations in sorted(repeats.items(), key=lambda row: (-len(row[1]), row[0]))
                    if len(locations) > 1
                ][:10],
                "omitted_repeat_count": max(0, sum(len(locations) > 1 for locations in repeats.values()) - 10),
            }
        )
    return [
        *events,
        {
            "kind": "stats-resource-summary",
            "reads_by_target": _resource_target_groups(events, "reads"),
            "skills_by_name": _resource_target_groups(events, "skills"),
            "shell_paths_by_target": _resource_target_groups(events, "shell_paths"),
        },
    ]


def _resource_target_groups(events: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    """同じ明示対象を担当横断でまとめ、担当内の回数と観測できた結果量を保持する。"""
    groups: dict[str, dict[str, Any]] = {}
    for event in events:
        for row in event[field]:
            target = row["target"]
            if not isinstance(target, str):
                continue
            group = groups.setdefault(
                target,
                {"target": target, "count": 0, "observed_result_count": 0, "characters": 0, "utf8_bytes": 0, "by_record": {}},
            )
            record = group["by_record"].setdefault(
                event["record"],
                {"count": 0, "observed_result_count": 0, "characters": 0, "utf8_bytes": 0, "locations": []},
            )
            group["count"] += 1
            record["count"] += 1
            record["locations"].append(row["location"])
            if row.get("characters") is not None:
                group["observed_result_count"] += 1
                group["characters"] += row["characters"]
                group["utf8_bytes"] += row["utf8_bytes"]
                record["observed_result_count"] += 1
                record["characters"] += row["characters"]
                record["utf8_bytes"] += row["utf8_bytes"]
    return sorted(groups.values(), key=lambda group: (-group["count"], group["target"]))
