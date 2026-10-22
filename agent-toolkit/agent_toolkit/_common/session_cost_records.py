"""保存済み記録の同じ家族を使い、ローカルとSSH先の画面用推定費用を求める。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from agent_toolkit._common import session_cost
from agent_toolkit._common import session_record_paths as record_paths
from agent_toolkit._common.session_family import _collect_records, _Record, _Runtime


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

    def resolve(session_id: str) -> tuple[Path, _Runtime] | None:
        found = record_paths.find_session_record(
            session_id, claude_home=claude_home, codex_home=codex_home, state_root=state_root
        )
        if found is None or len(found.paths) != 1:
            return None
        return found.paths[0], cast(_Runtime, found.engine)

    family, unresolved = _collect_records(
        str(path),
        [_Record(line, "", entry) for line, entry in enumerate(entries, 1)],
        main_only=not include_descendants,
        session_resolver=resolve,
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
    return session_cost.estimate_family(records, incomplete=bool(unresolved) or broken_lines > 0)
