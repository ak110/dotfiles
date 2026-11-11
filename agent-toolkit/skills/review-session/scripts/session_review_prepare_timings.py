"""振り返りの入力準備（session_review_prepare_timings）。"""

from __future__ import annotations

import collections
import json
import math
import statistics
from typing import Any

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import file_lock
from agent_toolkit._common.json_lines import split_lines


def _hook_timing_stats(session_id: str) -> dict[str, Any]:
    """有限な診断記録のスナップショットを区間別に集計し、未記録と不正行を区別する。"""
    result: dict[str, Any] = {
        "kind": "stats-hook-timings",
        "session_id": session_id,
        "status": "missing",
        "count": 0,
        "invalid_rows": 0,
        "groups": [],
    }
    directory = managed_temp.find_session_temp(session_id)
    if directory is None:
        return result
    path = directory / "hook-timings.jsonl"
    try:
        path.stat()
        with (directory / "hook-timings.lock").open("a+b") as lock:
            file_lock.acquire_lock(lock)
            try:
                raw = path.read_bytes()
            finally:
                file_lock.release_lock(lock)
    except FileNotFoundError:
        return result
    except OSError as error:
        return {
            **result,
            "status": "unreadable",
            "source": str(path),
            "diagnostic": str(error),
            "next_action": (
                f"`{path}`と親ディレクトリの読取権限、および同じ場所のhook-timings.lockの作成・書込権限を確認せよ。"
                "復旧できる場合は同じ入力引数で`atk run-script session-review-prepare --`を再実行せよ。"
                "復旧できない場合はhookの統計を除いて振り返りを続け、取得できなかった範囲を報告せよ。"
            ),
        }
    grouped: dict[tuple[str, str, str], list[float]] = collections.defaultdict(list)
    lines = split_lines(raw)
    if lines and not raw.endswith(b"\n"):
        result["invalid_rows"] += 1
        lines.pop()
    for line in lines:
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("計測がobjectでない")
            seconds = row["seconds"]
            fields = (row["event"], row["operation"], row["stage"])
            if (
                not all(isinstance(field, str) for field in fields)
                or isinstance(seconds, bool)
                or not isinstance(seconds, (int, float))
                or not math.isfinite(seconds)
                or seconds < 0
            ):
                raise ValueError("計測欄が不正")
        except ValueError, KeyError, TypeError:
            result["invalid_rows"] += 1
            continue
        grouped[fields].append(float(seconds))
    result.update(
        status="available",
        source=str(path),
        retained_bytes=len(raw),
        count=sum(len(values) for values in grouped.values()),
        groups=[
            {
                "event": event,
                "operation": operation,
                "stage": stage,
                "count": len(values),
                "median_seconds": statistics.median(values),
                "total_seconds": sum(values),
            }
            for (event, operation, stage), values in sorted(grouped.items())
        ],
    )
    return result
