"""計画の進捗表から、明示した工程の実働と旧書式の推定を別々に集計する。"""

from __future__ import annotations

import datetime
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_toolkit._plan.structure import parse_plan_metadata, progress_log_rows
from agent_toolkit._plan.structure.constants import PLAN_QUEUE_ID_PATTERN
from session_evidence_lane_plans import SavedPlan

_PHASES = (
    ("観測", r"観測|実機|実描画|tmux|隔離"),
    ("レビュー", r"レビュー|指摘|review|読者|読み手|再発"),
    ("統合", r"統合|merge|rebase|追随"),
    ("公開CI", r"公開|CI|push|リリース"),
    ("検証", r"検証|pytest|pyfltr|テスト"),
    ("計画", r"計画|選定|起草"),
    ("実装", r"実装|commit|修正|是正"),
)


def _timestamp(stamp: str, *, legacy: bool) -> datetime.datetime | None:
    match = re.match(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d", stamp)
    if match:
        try:
            return datetime.datetime.fromisoformat(match[0] if legacy else stamp)
        except ValueError:
            pass
    return None


type Rows = list[tuple[datetime.datetime | None, str]]


def _recorded(rows: Rows) -> tuple[dict[str, float], float] | None:
    phases: dict[str, float] = defaultdict(float)
    between = 0.0
    boundary = None
    active = None
    for stamp, label in rows:
        if label.startswith(("開始:", "終了:")):
            action, phase = label.split(":", 1)
            phase = phase.strip()
        elif end := re.fullmatch(r"(実装|検証|レビュー|統合|観測|公開CI|計画)(?:終了|完了)?", label):
            action, phase = "終了", end[1]
        else:
            continue
        if stamp is None or not phase:
            return None
        if boundary is not None and ((stamp.tzinfo is None) != (boundary.tzinfo is None) or stamp < boundary):
            return None
        minutes = (stamp - boundary).total_seconds() / 60 if boundary is not None else 0.0
        if action == "開始":
            if active is not None:
                return None
            between += minutes
            active = phase
        else:
            if boundary is None or (active is not None and active != phase):
                return None
            phases[phase] += minutes
            active = None
        boundary = stamp
    return (phases, between) if phases and active is None else None


def _legacy(rows: Rows) -> tuple[dict[str, float], int]:
    phases: dict[str, float] = defaultdict(float)
    capped = 0
    dated = sorted((stamp, label) for stamp, label in rows if stamp is not None)
    for (start, _), (end, label) in zip(dated, dated[1:], strict=False):
        duration = (end - start).total_seconds() / 60
        capped += duration > 120
        phase = next((name for name, pattern in _PHASES if re.search(pattern, label)), "その他")
        phases[phase] += min(duration, 120)
    return phases, capped


@dataclass
class _Work:
    per_kind: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    wi_counts: Counter[str] = field(default_factory=Counter)
    phases: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def add(self, kind: str, wis: int, phases: dict[str, float]) -> None:
        self.per_kind[kind].append(sum(phases.values()) / wis)
        self.wi_counts[kind] += wis
        for phase, minutes in phases.items():
            self.phases[phase] += minutes

    def summary(self) -> dict[str, Any]:
        return {
            "plan_wi_count": self.wi_counts.total(),
            "by_kind": {
                kind: {"plans": len(values), "wis": self.wi_counts[kind], "median_minutes_per_wi": statistics.median(values)}
                for kind, values in self.per_kind.items()
            },
            "phase_minutes_per_wi": {phase: minutes / self.wi_counts.total() for phase, minutes in self.phases.items()},
        }


def plan_costs(paths: list[Path]) -> tuple[dict[str, Any], list[SavedPlan]]:
    """各計画は一方の書式だけで数え、不完全な新書式を旧推定で埋めない。"""
    recorded, legacy = _Work(), _Work()
    between = 0.0
    capped = excluded = sidecars = undated_rows = incomplete = 0
    plans: list[SavedPlan] = []
    for path in paths:
        if path.name.endswith(".bugs.md"):
            sidecars += 1
            continue
        text = path.read_text(encoding="utf-8")
        metadata, _ = parse_plan_metadata(text)
        target = metadata.values.get("対象リポジトリ", "") if metadata else ""
        wis = {name for name, _ in metadata.related_wi} if metadata else set()
        if (metadata and metadata.values.get("関連WI")) or any(not PLAN_QUEUE_ID_PATTERN.fullmatch(name) for name in wis):
            wis.clear()
        try:
            progress = progress_log_rows(text)
        except ValueError:
            progress = []
        plans.append(SavedPlan.from_progress(path, target.replace("\\", "/").rstrip("/"), frozenset(wis), progress))
        explicit = any(label.strip().startswith(("開始:", "終了:")) for _, label, _ in progress)
        rows = [(_timestamp(stamp, legacy=not explicit), label.strip()) for stamp, label, _ in progress]
        undated_rows += sum(stamp is None for stamp, _ in rows)
        kind = metadata.values.get("作業種別", "?") if metadata else "?"
        if explicit:
            intervals = _recorded(rows)
            if intervals is None:
                incomplete += 1
            elif wis:
                phases, gaps = intervals
                recorded.add(kind, len(wis), phases)
                between += gaps
                continue
        elif wis:
            phases, cuts = _legacy(rows)
            if phases:
                legacy.add(kind, len(wis), phases)
                capped += cuts
                continue
        excluded += 1
    counts = [len(plan.wis) for plan in plans]
    return {
        "files": len(paths),
        "skipped_bug_files": sidecars,
        "excluded_without_wis_or_intervals": excluded,
        "plans_without_wis": counts.count(0),
        "related_wi_count": sum(counts) if counts and all(counts) else None,
        "progress_rows_without_minute": undated_rows,
        "recorded_work": {
            **recorded.summary(),
            "between_phase_minutes_per_wi": between / recorded.wi_counts.total() if recorded.wi_counts else None,
            "incomplete_plans": incomplete,
        },
        "legacy_estimate": {**legacy.summary(), "interval_cap_minutes": 120, "capped_intervals": capped},
    }, plans
