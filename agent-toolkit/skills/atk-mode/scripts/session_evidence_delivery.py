from __future__ import annotations

import datetime
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from session_evidence_catalog import _catalog_read_item, _catalog_scan
from session_evidence_extract import (
    _TEXT_LIMIT,
    _CollectedRecord,
    _error_event,
    _generated_user_event,
    _parse_timestamp,
    _Record,
    _Runtime,
    _unresolved_events,
)
from session_evidence_extract_claude import _extract_claude

_LONG_TEXT_THRESHOLD = 200
RESPONSE_CANDIDATE = "post-delivery-response-candidate"
DELIVERY_CANDIDATE_KINDS = frozenset({"delivery-repost-candidate", "post-delivery-long-text-candidate", RESPONSE_CANDIDATE})


def post_delivery_events(timeline: list[dict[str, Any]], collected: list[_CollectedRecord]) -> list[dict[str, Any]]:
    with_delivery = {
        record
        for event in timeline
        if event.get("delivery") == "send_to_user"
        and isinstance(record := event.get("record"), str)
        and isinstance(event.get("delivery_tool_use_id"), str)
    }
    results = {
        item.record_id: _result_index(item.records, None)
        for item in collected
        if item.runtime == "claude" and item.record_id in with_delivery
    }
    return _response_candidates(timeline, results)


def _result_index(records: list[_Record], boundary: datetime.datetime | None) -> dict[str, tuple[int, str]]:
    results: dict[str, tuple[int, str]] = {}
    for record in records:
        if record.entry.get("type") != "user" or record.entry.get("isSidechain") is True:
            continue
        stamp = _timestamp(record.entry)
        if boundary is not None and stamp is not None and stamp > boundary:
            continue
        message = record.entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_result"
                and isinstance(call_id := block.get("tool_use_id"), str)
                and call_id
            ):
                status = "failed" if block.get("is_error") is True else "succeeded"
                results.setdefault(call_id, (record.line, status))
    return results


def _in_period(stamp: datetime.datetime | None, since: datetime.datetime | None, boundary: datetime.datetime | None) -> bool:
    return (since is None and boundary is None) or (
        stamp is not None and (since is None or since < stamp) and (boundary is None or stamp <= boundary)
    )


def _response_candidates(
    timeline: list[dict[str, Any]],
    results: dict[str, dict[str, tuple[int, str]]],
    *,
    default_record: str | None = None,
    since: datetime.datetime | None = None,
    boundary: datetime.datetime | None = None,
) -> list[dict[str, Any]]:
    sent: dict[str, list[tuple[int, int]]] = defaultdict(list)
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for event in timeline:
        record = event.get("record", default_record)
        line = event.get("line")
        if (
            not isinstance(record, str)
            or record not in results
            or not isinstance(line, int)
            or event.get("is_sidechain") is True
        ):
            continue
        stamp = _timestamp(event)
        if boundary is not None and stamp is not None and stamp > boundary:
            continue
        if event["kind"] == "user" and not _generated_user_event(event):
            sent.pop(record, None)
            continue
        if event.get("delivery") == "send_to_user" and _in_period(stamp, since, boundary):
            call_id = event.get("delivery_tool_use_id")
            if not isinstance(call_id, str):
                continue
            result_line, status = results[record].get(call_id, (-1, "missing"))
            if status == "succeeded" and line < result_line:
                sent[record].append((line, result_line))
        elif event.get("delivery") == "text" and event["text"].strip() not in ("", "…") and (record, line) not in seen:
            deliveries = [(start, result) for start, result in sent.get(record, ()) if result < line]
            if deliveries:
                seen.add((record, line))
                candidates.append(
                    {
                        **event,
                        "kind": RESPONSE_CANDIDATE,
                        "record": record,
                        "delivery_lines": sorted({start for start, _ in deliveries}),
                        "delivery_result_lines": sorted({result for _, result in deliveries}),
                        "review_status": "unreviewed",
                    }
                )
    return candidates


def delivery_events(
    collected: list[_CollectedRecord],
    *,
    since: datetime.datetime | None = None,
    boundary: datetime.datetime | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in collected:
        if item.runtime != "claude":
            continue
        records = [row for row in item.records if row.entry.get("isSidechain") is not True]

        token = _TEXT_LIMIT.set(None)
        try:
            timeline = _extract_claude([row.entry for row in records], [row.line for row in records], include_delivery_id=True)
        finally:
            _TEXT_LIMIT.reset(token)
        pending: list[dict[str, Any]] = []
        awaiting_long_text: list[tuple[int, dict[str, Any]]] = []
        results = _result_index(records, boundary)
        for event in timeline:
            stamp = _timestamp(event)
            if boundary is not None and stamp is not None and stamp > boundary:
                continue
            if event["kind"] == "user" and not _generated_user_event(event):
                pending.clear()
                awaiting_long_text.clear()
                continue
            if event.get("delivery") == "send_to_user":
                selected = _in_period(stamp, since, boundary)
                if selected and event["text_length"] >= 60:
                    delivery = {
                        "kind": "delivery",
                        "record": item.record_id,
                        "line": event["line"],
                        "timestamp": event.get("timestamp"),
                        "text_length": event["text_length"],
                        "day": stamp.astimezone(datetime.UTC).date().isoformat() if stamp is not None else "unknown",
                    }
                    call_id = event.get("delivery_tool_use_id")
                    result_line, status = results.get(call_id, (-1, "missing")) if isinstance(call_id, str) else (-1, "missing")
                    if result_line <= event["line"]:
                        status = "missing"
                    delivery["result"] = status
                    events.append(delivery)
                    if status == "succeeded":
                        awaiting_long_text.append((result_line, delivery))
                    pending.append({"event": event, "delivery": delivery, "grams": _grams(event["text"])})
                continue
            if event.get("delivery") != "text":
                continue
            if event.get("stop_reason") == "end_turn" and event["text_length"] > _LONG_TEXT_THRESHOLD:
                still_awaiting: list[tuple[int, dict[str, Any]]] = []
                for result_line, delivery in awaiting_long_text:
                    if event["line"] <= result_line:
                        still_awaiting.append((result_line, delivery))
                    else:
                        events.append(
                            {
                                "kind": "post-delivery-long-text-candidate",
                                "record": item.record_id,
                                "delivery_line": delivery["line"],
                                "line": event["line"],
                                "text_length": event["text_length"],
                            }
                        )
                awaiting_long_text = still_awaiting
            if event["text_length"] < 30:
                continue
            grams = _grams(event["text"])
            remaining = []
            for delivery in pending:
                smaller = min(len(grams), len(delivery["grams"]))
                common = len(grams & delivery["grams"])
                if 20 <= smaller <= common * 2:
                    events.append(
                        {
                            **event,
                            "kind": "delivery-repost-candidate",
                            "record": item.record_id,
                            "delivery_line": delivery["event"]["line"],
                            "delivery_timestamp": delivery["event"].get("timestamp"),
                            "day": delivery["delivery"]["day"],
                            "similarity": common / smaller,
                        }
                    )
                else:
                    remaining.append(delivery)
            pending = remaining
        events.extend(
            _response_candidates(
                timeline,
                {item.record_id: results},
                default_record=item.record_id,
                since=since,
                boundary=boundary,
            )
        )
    return events


def delivery_summary(
    events: list[dict[str, Any]],
    *,
    since: datetime.datetime | None = None,
    boundary: datetime.datetime | None = None,
) -> dict[str, Any]:
    """送達日をUTCの日付へそろえ、送達ごとの候補件数を合算する。"""
    by_day: dict[str, dict[str, int]] = {}
    for event in events:
        field = {"delivery": "delivery_count", "delivery-repost-candidate": "candidate_count"}.get(event["kind"])
        if field is not None:
            day = by_day.setdefault(event["day"], {"delivery_count": 0, "candidate_count": 0})
            day[field] += 1
    results = Counter(event.get("result", "missing") for event in events if event["kind"] == "delivery")
    long_text_candidates = sum(event["kind"] == "post-delivery-long-text-candidate" for event in events)
    deliveries = sum(row["delivery_count"] for row in by_day.values())
    candidates = sum(row["candidate_count"] for row in by_day.values())
    response_candidates = sum(event["kind"] == RESPONSE_CANDIDATE for event in events)
    return {
        "kind": "delivery-repost-summary",
        "delivery_count": deliveries,
        "candidate_count": candidates,
        "candidate_ratio": candidates / deliveries if deliveries else None,
        "by_day": dict(sorted(by_day.items())),
        "post_delivery_long_text": {
            "description": (
                f"成功した送達後、次のユーザー入力までの終端本文が{_LONG_TEXT_THRESHOLD}文字を超えた候補。"
                "翻訳・言い換えの実件数ではない。"
            ),
            "successful_delivery_count": results["succeeded"],
            "candidate_count": long_text_candidates,
            "candidate_ratio": long_text_candidates / results["succeeded"] if results["succeeded"] else None,
            "excluded_failed_count": results["failed"],
            "excluded_missing_result_count": results["missing"],
        },
        "post_delivery_response": {
            "description": (
                "成功した送達後、次のユーザー入力までの通常本文を記録行ごとに数える。"
                "字面・長さ・言語は条件としない。終了の「…」を除く。内容は未照合。"
            ),
            "candidate_count": response_candidates,
            "confirmed_repost_count": 0,
            "unconfirmed_count": response_candidates,
        },
        "day_timezone": "UTC",
        "since": since.isoformat() if since is not None else None,
        "observation_boundary": boundary.isoformat() if boundary is not None else None,
    }


def catalog_delivery_events(
    root: Path, runtime: _Runtime, since: datetime.datetime, boundary: datetime.datetime
) -> tuple[list[dict[str, Any]], int]:
    if runtime != "claude":
        return [
            _error_event(
                "送達再掲の集計はClaude Codeの記録だけを対象にする",
                next_action="`--catalog-claude-project <プロジェクトの記録ディレクトリ>`で再実行する",
            )
        ], 2
    scan = _catalog_scan(root, runtime, since, boundary)
    if isinstance(scan, dict):
        return [scan], 2
    events: list[dict[str, Any]] = []
    try:
        for parent in scan.parents:
            events.extend(delivery_events([_catalog_read_item(parent.item)], since=since, boundary=boundary))
    except ValueError as error:
        return [_error_event(str(error), next_action="対象の記録の存在と読取権限を確認して再実行する")], 2
    summary = delivery_summary(events, since=since, boundary=boundary)
    summary.update(
        {
            "scan_root": str(scan.root),
            "parent_record_count": len(scan.parents),
            "unresolved_record_count": scan.unresolved_count,
        }
    )
    return [
        *comparison_output(events),
        *_unresolved_events(scan.unresolved),
        summary,
    ], 0


def comparison_output(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in event.items() if key != "text"} if event["kind"] == RESPONSE_CANDIDATE else event
        for event in events
        if event["kind"] in DELIVERY_CANDIDATE_KINDS
    ]


def _timestamp(event: dict[str, Any]) -> datetime.datetime | None:
    value = event.get("timestamp")
    try:
        return _parse_timestamp(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _grams(text: str) -> set[str]:
    normalized = "".join(text.split())
    return {normalized[index : index + 4] for index in range(len(normalized) - 3)}
