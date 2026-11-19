"""全文の送達本文と地の文を比較し、再掲の候補と期間・日別の集計を返す。"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from session_evidence_catalog import _catalog_read_item, _catalog_scan
from session_evidence_extract import (
    _TEXT_LIMIT,
    _CollectedRecord,
    _error_event,
    _extract_records,
    _generated_user_event,
    _parse_timestamp,
    _Runtime,
    _unresolved_events,
)


def post_delivery_events(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """既存時系列の由来と全文文字数から、送達後の長い終了本文だけを選ぶ。

    本文は通常表示の長さのまま扱う。分析用の全文は既存の個別証拠保存が元記録から取得する。
    """
    latest: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    for event in timeline:
        if event.get("is_sidechain") is True:
            continue
        record = event.get("record")
        if not isinstance(record, str):
            continue
        if event["kind"] == "user" and not _generated_user_event(event):
            latest.pop(record, None)
        elif event.get("delivery") == "send_to_user":
            latest[record] = event
        elif event.get("delivery") == "text" and event.get("stop_reason") == "end_turn":
            delivered = latest.pop(record, None)
            if delivered is not None and event["text_length"] > 200:
                events.append(
                    {
                        **event,
                        "kind": "post-delivery-long-text",
                        "delivery_line": delivered["line"],
                        "delivery_timestamp": delivered.get("timestamp"),
                    }
                )
    return events


def delivery_events(
    collected: list[_CollectedRecord],
    *,
    since: datetime.datetime | None = None,
    boundary: datetime.datetime | None = None,
) -> list[dict[str, Any]]:
    """送達ごとの最初の類似本文と集計対象の送達を返す。

    集計の送達は60文字以上、比較する地の文は30文字以上とする。
    空白を除いた4-gramの少ない側が20要素以上で、共通部分が半分以上なら候補とする。
    送達成功の確認は行わず、呼び出しを数える。候補は確認済みの再掲ではない。
    """
    events: list[dict[str, Any]] = []
    for item in collected:
        if item.runtime != "claude":
            continue
        records = [row for row in item.records if row.entry.get("isSidechain") is not True]
        # 通常表示の短縮本文では一致の割合が変わるため、同じ変換の文字数制限だけを外す。
        token = _TEXT_LIMIT.set(None)
        try:
            timeline = _extract_records(records)
        finally:
            _TEXT_LIMIT.reset(token)
        pending: list[dict[str, Any]] = []
        for event in timeline:
            stamp = _timestamp(event)
            if boundary is not None and stamp is not None and stamp > boundary:
                continue
            if event["kind"] == "user" and not _generated_user_event(event):
                pending.clear()
                continue
            if event.get("delivery") == "send_to_user":
                selected = (since is None and boundary is None) or (
                    stamp is not None and (since is None or since < stamp) and (boundary is None or stamp <= boundary)
                )
                if selected and event["text_length"] >= 60:
                    delivery = {
                        "kind": "delivery",
                        "record": item.record_id,
                        "line": event["line"],
                        "timestamp": event.get("timestamp"),
                        "text_length": event["text_length"],
                        "day": stamp.astimezone(datetime.UTC).date().isoformat() if stamp is not None else "unknown",
                    }
                    events.append(delivery)
                    pending.append({"event": event, "delivery": delivery, "grams": _grams(event["text"])})
                continue
            if event.get("delivery") != "text":
                continue
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
    deliveries = sum(row["delivery_count"] for row in by_day.values())
    candidates = sum(row["candidate_count"] for row in by_day.values())
    return {
        "kind": "delivery-repost-summary",
        "delivery_count": deliveries,
        "candidate_count": candidates,
        "candidate_ratio": candidates / deliveries if deliveries else None,
        "by_day": dict(sorted(by_day.items())),
        "day_timezone": "UTC",
        "since": since.isoformat() if since is not None else None,
        "observation_boundary": boundary.isoformat() if boundary is not None else None,
    }


def catalog_delivery_events(
    root: Path, runtime: _Runtime, since: datetime.datetime, boundary: datetime.datetime
) -> tuple[list[dict[str, Any]], int]:
    """既存カタログのメイン記録だけを走査し、期間全体を同じ定義で集計する。"""
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
        *(event for event in events if event["kind"] == "delivery-repost-candidate"),
        *_unresolved_events(scan.unresolved),
        summary,
    ], 0


def _timestamp(event: dict[str, Any]) -> datetime.datetime | None:
    value = event.get("timestamp")
    try:
        return _parse_timestamp(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _grams(text: str) -> set[str]:
    normalized = "".join(text.split())
    return {normalized[index : index + 4] for index in range(len(normalized) - 3)}
