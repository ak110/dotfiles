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


def _merge(intervals: list[tuple[datetime.datetime, datetime.datetime]]) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """同じ記録の中で重なる作業区間を1つにまとめ、記録の同時数を二重に数えないようにする。"""
    merged: list[tuple[datetime.datetime, datetime.datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _report_lengths(records: list[_Record]) -> list[int]:
    """`send_to_user`の呼び出しごとの入力の長さを、ClaudeとCodexの両形式から集める。"""
    lengths: list[int] = []
    for record in records:
        message = record.entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and str(block.get("name", "")).endswith("send_to_user")
            ):
                lengths.append(len(json.dumps(block.get("input", ""), ensure_ascii=False)))
        payload = record.entry.get("payload")
        if (
            isinstance(payload, dict)
            and payload.get("type") in {"function_call", "custom_tool_call"}
            and str(payload.get("name", "")).endswith("send_to_user")
        ):
            lengths.append(len(str(payload.get("arguments", payload.get("input", "")))))
    return lengths


def _is_request(entry: dict[str, Any]) -> bool:
    """モデルへ次の応答を求める記録（人の依頼かツールの結果）かを返す。応答本文・通知・usageの記録は除く。"""
    message = entry.get("message")
    if entry.get("type") == "user" or (isinstance(message, dict) and message.get("role") == "user"):
        return True
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return False
    kind = payload.get("type")
    return kind in {"function_call_output", "custom_tool_call_output", "user_message"} or (
        kind == "message" and payload.get("role") == "user"
    )


def unit_cost_event(
    collected: list[_CollectedRecord],
    *,
    call_entries: Callable[[list[_Record], _Runtime], list[dict[str, Any]]],
    response_usages: Callable[[list[_Record], _Runtime], list[dict[str, int]]],
    record_timestamp: Callable[[_Record], datetime.datetime | None],
) -> dict[str, Any]:
    """所要の悪化を基準の時期と比べるため、1手あたりの費用・手数・並行数を全記録から集計する。

    応答1回の所要は、応答のusageを持つ記録と、それより前で最も近い依頼の記録（人の依頼かツールの結果）との時刻差とする。
    応答本文や通知などの中間の記録は区間の始点にしない。人の入力待ちは応答の後から次の依頼までに現れるため含まれない。
    始点の依頼を記録から特定できない応答は`responses_unmeasured`へ数え、0秒として平均へ入れない。
    同時数は、応答とツール実行の区間を記録ごとにまとめた作業区間の重なりの最大とする。
    WI 1件あたりの所要はセッション記録に無く、計画ファイルの進捗ログから求めるため`unmeasured`へ示す。
    """
    call_seconds: list[float] = []
    usages: list[dict[str, int]] = []
    response_seconds: list[float] = []
    unmeasured_responses = 0
    timeouts = 0
    reports: list[int] = []
    active: list[tuple[datetime.datetime, datetime.datetime]] = []
    for item in collected:
        if item.runtime is None:
            continue
        stamps = {record.line: stamp for record in item.records if (stamp := record_timestamp(record)) is not None}
        requests = sorted(record.line for record in item.records if record.line in stamps and _is_request(record.entry))
        work: list[tuple[datetime.datetime, datetime.datetime]] = []
        for call in call_entries(item.records, item.runtime):
            call_seconds.append(call["seconds"])
            start = stamps.get(call["line"])
            if start is not None:
                work.append((start, start + datetime.timedelta(seconds=call["seconds"])))
        record_usages = response_usages(item.records, item.runtime)
        usages.extend(record_usages)
        for usage in record_usages:
            line = usage.get("line", -1)
            end = stamps.get(line)
            earlier = [value for value in requests if value < line]
            if end is None or not earlier:
                unmeasured_responses += 1
                continue
            start = stamps[earlier[-1]]
            response_seconds.append(max((end - start).total_seconds(), 0.0))
            work.append((start, end))
        active.extend(_merge(work))
        timeouts += _timeout_result_count(item.records)
        reports.extend(_report_lengths(item.records))
    event: dict[str, Any] = {
        "kind": "stats-unit-costs",
        "tool_calls": len(call_seconds),
        "responses": len(usages),
        "responses_unmeasured": unmeasured_responses,
        "reports": len(reports),
        "max_concurrent_records": _max_concurrency(active),
        "timeout_failures": timeouts,
        "unmeasured": ["wi_minutes: 計画ファイルの進捗ログから求める"],
    }
    if call_seconds:
        event["tool_call_median_seconds"] = round(statistics.median(call_seconds), 2)
    if response_seconds:
        event["response_mean_seconds"] = round(sum(response_seconds) / len(response_seconds), 2)
    if usages:
        event["response_input_tokens_median"] = int(statistics.median(usage["input"] for usage in usages))
        event["response_output_tokens_mean"] = round(sum(usage["output"] for usage in usages) / len(usages), 1)
        event["response_reasoning_tokens_mean"] = round(sum(usage["reasoning"] for usage in usages) / len(usages), 1)
    if reports:
        event["report_mean_chars"] = round(sum(reports) / len(reports), 1)
    return event
