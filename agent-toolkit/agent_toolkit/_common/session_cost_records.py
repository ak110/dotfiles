"""保存済み記録の同じ家族を使い、ローカルとSSH先の画面用推定費用を求める。"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from agent_toolkit._common import session_cost
from agent_toolkit._common import session_record_paths as record_paths
from agent_toolkit._common.session_family import _collect_records, _load_records_checked, _Record, _RecordLoad, _Runtime


def estimate_detail_cost(
    engine: str,
    path: Path,
    entries: list[dict[str, Any]],
    *,
    include_descendants: bool,
    claude_home: Path | None = None,
    codex_home: Path | None = None,
    state_root: Path | None = None,
    broken_lines: int = 0,
) -> dict[str, Any]:
    """起点の本文を再取得せず、必要な子孫だけを解決して費用計算へ渡す。"""
    return estimate_detail_family(
        engine,
        path,
        entries,
        include_descendants=include_descendants,
        claude_home=claude_home,
        codex_home=codex_home,
        state_root=state_root,
        broken_lines=broken_lines,
    ).cost


@dataclasses.dataclass(frozen=True)
class DetailCost:
    """推定費用と、算出に使った家族の記録。"""

    cost: dict[str, Any]
    members: tuple[Path, ...]
    complete: bool
    """全ての子孫を解決し、全行を解析できたか。偽なら家族の記録だけでは結果の再利用を判定できない。"""


def estimate_detail_family(
    engine: str,
    path: Path,
    entries: list[dict[str, Any]],
    *,
    include_descendants: bool,
    claude_home: Path | None = None,
    codex_home: Path | None = None,
    state_root: Path | None = None,
    broken_lines: int = 0,
    record_loader: Callable[[str], _RecordLoad] = _load_records_checked,
) -> DetailCost:
    """`estimate_detail_cost`と同じ推定費用を、家族の記録と解決の完全性とともに返す。

    `record_loader`は子孫の記録の読込を差し替える（`session_family._collect_records`）。
    """
    # 子孫の多い記録は数十件の委譲先を解決するため、保存先の一覧を1回だけ読む。
    locator = record_paths.RecordLocator(claude_home=claude_home, codex_home=codex_home, state_root=state_root)

    def resolve(session_id: str) -> tuple[Path, _Runtime] | None:
        found = locator.find(session_id)
        if found is None or len(found.paths) != 1:
            return None
        return found.paths[0], cast(_Runtime, found.engine)

    family, unresolved = _collect_records(
        str(path),
        [_Record(line, "", entry) for line, entry in enumerate(entries, 1)],
        main_only=not include_descendants,
        session_resolver=resolve,
        record_loader=record_loader,
    )
    records = [
        session_cost.CostRecord(
            item.record_id,
            item.runtime or (engine if index == 0 else "unknown"),
            [record.entry for record in item.records],
            item.role,
            item.source_record,
        )
        for index, item in enumerate(family)
    ]
    complete = not unresolved and broken_lines == 0
    return DetailCost(
        session_cost.estimate_family(records, incomplete=not complete),
        tuple(item.path for item in family),
        complete,
    )
