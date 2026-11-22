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
