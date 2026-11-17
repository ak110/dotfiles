"""所要の悪化を基準の時期と比べるための、1手あたりの費用・手数・並行数の集計。

`session_evidence_stats.py`の`--stats`が`stats-unit-costs`イベントとして出力する。
"""

from __future__ import annotations

import datetime
import json
import statistics
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from session_evidence_extract import _CollectedRecord, _Record, _Runtime


_TIMEOUT_MARKERS = ("timed out", "timeout", "タイムアウト", "時間上限")


def _timeout_result_count(records: list[_Record]) -> int:
    """ツール結果のうち、時間上限による失敗を示す文言を含むものを数える。文言による推定であり厳密な分類ではない。"""
    count = 0
    for record in records:
        entry = record.entry
        texts: list[str] = []
        message = entry.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            texts.extend(
                str(block.get("content", ""))
                for block in message["content"]
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error")
            )
        payload = entry.get("payload")
        if isinstance(payload, dict) and payload.get("type") in {"function_call_output", "custom_tool_call_output"}:
            output = payload.get("output")
            text = str(output)[:2000]
            if "exit code" in text.lower() or "error" in text.lower():
                texts.append(text)
        count += sum(1 for text in texts if any(marker in text.lower() for marker in _TIMEOUT_MARKERS))
    return count


def _max_concurrency(intervals: list[tuple[datetime.datetime, datetime.datetime]]) -> int:
    """区間の集合で同時に開いていた区間の最大数を返す。"""
    points = sorted([(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals])
    current = peak = 0
    for _, delta in points:
        current += delta
        peak = max(peak, current)
    return peak


def unit_cost_event(
    collected: list[_CollectedRecord],
    summaries: dict[str, dict[str, Any]],
    *,
    call_entries: Callable[[list[_Record], _Runtime], list[dict[str, Any]]],
    response_usages: Callable[[list[_Record], _Runtime], list[dict[str, int]]],
    parse_timestamp: Callable[[str], datetime.datetime | None],
) -> dict[str, Any]:
    """所要の悪化を基準の時期と比べるため、1手あたりの費用・手数・並行数を全記録から集計する。

    比較の単位は、ツール呼び出し1回の所要（中央値）、応答1回の所要（記録の経過からツールの所要を除いた平均）と
    入力・出力トークン、ユーザー向けの報告の件数と長さ、同時に作業中だった記録の最大数、時間上限による失敗である。
    """
    call_seconds: list[float] = []
    usages: list[dict[str, int]] = []
    response_seconds_total = 0.0
    timeouts = 0
    reports: list[int] = []
    intervals: list[tuple[datetime.datetime, datetime.datetime]] = []
    for item in collected:
        if item.runtime is None:
            continue
        calls = call_entries(item.records, item.runtime)
        call_seconds.extend(call["seconds"] for call in calls)
        record_usages = response_usages(item.records, item.runtime)
        usages.extend(record_usages)
        summary = summaries.get(item.record_id, {})
        elapsed = summary.get("elapsed_seconds")
        if isinstance(elapsed, (int, float)) and record_usages:
            response_seconds_total += max(float(elapsed) - sum(call["seconds"] for call in calls), 0.0)
        timeouts += _timeout_result_count(item.records)
        for record in item.records:
            message = record.entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and str(block.get("name", "")).endswith("send_to_user")
                ):
                    reports.append(len(json.dumps(block.get("input", ""), ensure_ascii=False)))
        start = parse_timestamp(summary["start"]) if isinstance(summary.get("start"), str) else None
        end = parse_timestamp(summary["end"]) if isinstance(summary.get("end"), str) else None
        if start is not None and end is not None and start < end:
            intervals.append((start, end))
    event: dict[str, Any] = {
        "kind": "stats-unit-costs",
        "tool_calls": len(call_seconds),
        "responses": len(usages),
        "reports": len(reports),
        "max_concurrent_records": _max_concurrency(intervals),
        "timeout_failures": timeouts,
    }
    if call_seconds:
        event["tool_call_median_seconds"] = round(statistics.median(call_seconds), 2)
    if usages:
        event["response_mean_seconds"] = round(response_seconds_total / len(usages), 2)
        event["response_input_tokens_median"] = int(statistics.median(usage["input"] for usage in usages))
        event["response_output_tokens_mean"] = round(sum(usage["output"] for usage in usages) / len(usages), 1)
        event["response_reasoning_tokens_mean"] = round(sum(usage["reasoning"] for usage in usages) / len(usages), 1)
    if reports:
        event["report_mean_chars"] = round(sum(reports) / len(reports), 1)
    return event
