"""保存したレーン計画・Codex rolloutを、指定したファイル集合だけで再集計する。"""

from __future__ import annotations

import datetime
import glob
import json
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_PHASES = (
    ("観測", r"観測|実機|実描画|tmux|隔離"),
    ("レビュー", r"レビュー|指摘|review|読者|読み手|再発"),
    ("統合", r"統合|merge|rebase|追随"),
    ("公開CI", r"公開|CI|push|リリース"),
    ("検証", r"検証|pytest|pyfltr|テスト"),
    ("計画", r"計画|選定|起草"),
    ("実装", r"実装|commit|修正|是正"),
)
_CALL_TYPES = (
    ("test/lint", r"pytest|pyfltr|make (test|check)|uv run --frozen pyfltr"),
    ("wait/agents", r"agents (wait|notify)|write_stdin"),
    ("atk-ops", r"atk (wi|lane|plans|review-table|run-script)"),
    ("read-norms", r"\b(cat|sed -n|nl|head|tail)\b[^|]*\.md|(SKILL|references|share)/[^ ]*\.md"),
    ("search", r"\b(rg|grep|git grep|find)\b"),
    ("edit", r"apply_patch|\*\*\* Begin Patch"),
    ("git", r"\bgit\b"),
)
_LANE = re.compile(r"process-wi-lane|/lane-0[0-9]")


def _files(patterns: list[str]) -> tuple[list[Path], list[str]]:
    paths: set[Path] = set()
    unmatched = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        if not matches:
            unmatched.append(pattern)
        paths.update(Path(name).resolve() for name in matches)
    return sorted(paths), unmatched


def _classify(value: str, patterns: tuple[tuple[str, str], ...], other: str) -> str:
    return next((name for name, pattern in patterns if re.search(pattern, value)), other)


def _plan_costs(paths: list[Path]) -> dict[str, Any]:
    per_kind: dict[str, list[float]] = defaultdict(list)
    wi_counts: Counter[str] = Counter()
    phases: dict[str, float] = defaultdict(float)
    capped = excluded = sidecars = 0
    for path in paths:
        if path.name.endswith(".bugs.md"):
            sidecars += 1
            continue
        text = path.read_text(encoding="utf-8")
        wis = set(re.findall(r"^\s+- (\d{8}-\d{6}-\d{3})\.md", text, re.M))
        rows = sorted(
            (datetime.datetime.fromisoformat(stamp), label)
            for stamp, label in re.findall(r"^\| (\d{4}-\d\d-\d\d[T ]\d\d:\d\d)[^|]*\|([^|]*)\|", text, re.M)
        )
        if not wis or len(rows) < 2:
            excluded += 1
            continue
        kind_match = re.search(r"^- 作業種別: (\S+)", text, re.M)
        kind = kind_match[1] if kind_match else "?"
        minutes = 0.0
        for (start, _), (end, label) in zip(rows, rows[1:], strict=False):
            duration = (end - start).total_seconds() / 60
            capped += duration > 120
            minutes += min(duration, 120)
            phase = "工程間" if label.strip().startswith("開始:") else _classify(label, _PHASES, "その他")
            phases[phase] += min(duration, 120)
        per_kind[kind].append(minutes / len(wis))
        wi_counts[kind] += len(wis)
    total = wi_counts.total()
    return {
        "files": len(paths),
        "skipped_bug_files": sidecars,
        "excluded_without_wis_or_intervals": excluded,
        "interval_cap_minutes": 120,
        "capped_intervals": capped,
        "plan_wi_count": total,
        "by_kind": {
            kind: {"plans": len(values), "wis": wi_counts[kind], "median_minutes_per_wi": statistics.median(values)}
            for kind, values in per_kind.items()
        },
        "phase_minutes_per_wi": {phase: minutes / total for phase, minutes in phases.items()},
    }


def _call_type(arguments: Any) -> str:
    try:
        obj = json.loads(arguments) if isinstance(arguments, str) else arguments
    except ValueError:
        obj = None
    if arguments is None:
        return "unknown"
    command = (obj.get("cmd") or obj.get("command")) if isinstance(obj, dict) else None
    return _classify(str(command or arguments)[:400], _CALL_TYPES, "other")


def _entries(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as stream:
        for line in stream:
            try:
                obj = json.loads(line)
            except UnicodeError, ValueError:
                obj = None
            yield obj if isinstance(obj, dict) and isinstance(obj.get("payload"), dict) else {}


def _timestamp(entry: dict[str, Any]) -> datetime.datetime | None:
    try:
        stamp = datetime.datetime.fromisoformat(entry["timestamp"])
    except KeyError, TypeError, ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _seconds(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    count = len(ordered)
    return {
        "samples": count,
        "total_seconds": sum(ordered) if count else None,
        "mean_seconds": statistics.mean(ordered) if count else None,
        "p10_seconds": ordered[count // 10] if count else None,
        "median_seconds": statistics.median(ordered) if count else None,
        "p90_seconds": ordered[count * 9 // 10] if count else None,
    }


@dataclass
class _LaneCalls:
    durations: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    thinking: dict[tuple[str | None, str | None], list[float]] = field(default_factory=lambda: defaultdict(list))
    gaps: Counter[str] = field(default_factory=Counter)
    read_chars: int = 0
    read_bytes: int = 0
    read_outputs: int = 0
    lane_files: int = 0

    def collect(self, path: Path) -> None:
        cwd = next(
            (
                entry["payload"]["cwd"]
                for entry in _entries(path)
                if entry.get("type") in {"session_meta", "turn_context"} and entry["payload"].get("cwd")
            ),
            None,
        )
        if not isinstance(cwd, str):
            self.gaps["missing_cwd_files"] += 1
            return
        if not _LANE.search(cwd):
            return
        self.lane_files += 1
        pending: dict[str, tuple[datetime.datetime | None, str]] = {}
        last_output = model = effort = None
        for entry in _entries(path):
            if not entry:
                self.gaps["invalid_rows"] += 1
                last_output = None
                continue
            payload = entry["payload"]
            kind = payload.get("type")
            if entry.get("type") in {"turn_context", "session_meta"}:
                model = payload.get("model") or model
                effort = payload.get("effort") or payload.get("reasoning_effort") or effort
            if entry.get("type") == "turn_context" or kind in {"turn_aborted", "task_complete"}:
                last_output = None
            call_id = payload.get("call_id")
            if kind in {"function_call", "custom_tool_call"}:
                stamp = _timestamp(entry)
                if last_output is not None and stamp is not None:
                    gap = (stamp - last_output).total_seconds()
                    if 0 <= gap < 600:
                        self.thinking[model, effort].append(gap)
                    else:
                        self.gaps["excluded_thinking_intervals"] += 1
                last_output = None
                if not isinstance(call_id, str):
                    self.gaps["calls_without_id"] += 1
                    continue
                if call_id in pending:
                    self.gaps["unmatched_calls"] += 1
                pending[call_id] = stamp, _call_type(payload.get("arguments") or payload.get("input"))
            elif kind in {"function_call_output", "custom_tool_call_output"}:
                last_output = _timestamp(entry)
                pair = pending.pop(call_id, None) if isinstance(call_id, str) else None
                if pair is None:
                    self.gaps["unmatched_outputs"] += 1
                    continue
                self.record_result(pair, last_output, payload.get("output"))
        self.gaps["unmatched_calls"] += len(pending)

    def record_result(self, pair: tuple[datetime.datetime | None, str], end: datetime.datetime | None, output: Any) -> None:
        start, category = pair
        if start is None or end is None or end < start:
            self.gaps["invalid_call_intervals"] += 1
            return
        self.durations[category].append((end - start).total_seconds())
        if category == "read-norms":
            if output is None:
                self.gaps["missing_read_outputs"] += 1
            else:
                result = str(output)
                self.read_outputs += 1
                self.read_chars += len(result)
                self.read_bytes += len(result.encode("utf-8", errors="replace"))


def lane_costs(plan_patterns: list[str], rollout_patterns: list[str]) -> dict[str, Any]:
    """明示した期間の統計を返す。1件あたりの呼出数は同じ範囲の計画WI数を分母とする推定。"""
    plans, missing_plans = _files(plan_patterns)
    rollouts, missing_rollouts = _files(rollout_patterns)
    work = _plan_costs(plans)
    calls = _LaneCalls()
    for path in rollouts:
        calls.collect(path)
    all_seconds = [value for group in calls.durations.values() for value in group]
    reads = len(calls.durations.get("read-norms", []))
    wis = work["plan_wi_count"]
    missing = missing_plans + missing_rollouts
    known_reads = bool(all_seconds) and not calls.durations.get("unknown")
    complete_output = calls.read_outputs == reads and known_reads
    call_gaps = calls.gaps.keys() - {"excluded_thinking_intervals", "missing_read_outputs"}
    incomplete = any(calls.gaps[key] for key in call_gaps)
    denominator = wis if wis and all_seconds and not missing and not incomplete else None
    thinking_seconds = [value for group in calls.thinking.values() for value in group]
    return {
        "kind": "stats-lane-costs",
        "plan_patterns": plan_patterns,
        "rollout_patterns": rollout_patterns,
        "unmatched_patterns": missing,
        "plans": work,
        "rollout_files": len(rollouts),
        "lane_files": calls.lane_files,
        "gaps": dict(+calls.gaps),
        "matched_calls": _seconds(all_seconds),
        "by_call_type": {name: _seconds(values) for name, values in calls.durations.items()},
        "thinking_proxy": [
            {"model": model, "effort": effort, **_seconds(values)} for (model, effort), values in calls.thinking.items()
        ],
        "thinking_exclusive_limit_seconds": 600,
        "read_calls": reads if known_reads else None,
        "read_outputs": calls.read_outputs,
        "read_output_chars": calls.read_chars if complete_output else None,
        "read_output_utf8_bytes": calls.read_bytes if complete_output else None,
        "estimate_plan_wis": denominator,
        "estimated_calls_per_wi": len(all_seconds) / denominator if denominator else None,
        "estimated_tool_seconds_per_wi": sum(all_seconds) / denominator if denominator else None,
        "estimated_thinking_seconds_per_wi": sum(thinking_seconds) / denominator if denominator and thinking_seconds else None,
        "estimated_reads_per_wi": reads / denominator if denominator and known_reads else None,
        "estimated_read_chars_per_wi": calls.read_chars / denominator if denominator and complete_output else None,
        "estimated_read_utf8_bytes_per_wi": calls.read_bytes / denominator if denominator and complete_output else None,
    }
