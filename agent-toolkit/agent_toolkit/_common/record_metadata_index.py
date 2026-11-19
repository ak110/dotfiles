"""記録（JSONL）ごとの本文を持たない値を、変わらない記録について解析し直さずに返す。

振り返りのカタログ走査（review-sessionの`session_evidence_catalog`）は、窓の外の記録も含む走査rootの全記録から
親子関係と期間を決めるため、全記録の値が要る。全記録（Codexの履歴で約1万件・20GB超）を照会のたびに解析し直すと、
仕事量が記録の総量に比例する。値は`derived_index`で走査rootごとの索引へ保存し、全記録のstatは照会のたびに行う。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, NamedTuple

from agent_toolkit._common import derived_index
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.session_family import _RecordLoadFailure, _Runtime, _scan_record_metadata


class RecordSummary(NamedTuple):
    """`session_family._scan_record_metadata`の値のうち、カタログ走査が使うもの。"""

    runtime: _Runtime | None
    record_id: str
    thread_id: str | None
    start_value: Any
    end_value: Any
    child_references: tuple[tuple[str, int], ...]
    failure: _RecordLoadFailure | None


def summaries(root: Path, runtime: str, paths: list[Path]) -> dict[Path, RecordSummary]:
    """`root`配下を`runtime`の記録として走査する`paths`の値を返す。

    識別（inode・更新時刻・大きさ）と値を計算するコードの版が保存時と同じ記録は解析しない。
    """
    name = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:32]
    index = _state_paths.state_dir() / "session-review" / "record-index" / f"{runtime}-{name}.json"
    listed = []
    vanished = {}
    for path in paths:
        try:
            listed.append((str(path), path, derived_index.identity(path.stat())))
        except OSError:
            # 列挙の後に消えた記録も、解析して読込失敗として返す。
            vanished[path] = _summary(path)
    values = derived_index.refreshed(
        index, derived_index.code_version(__name__), listed, lambda _key, path: _summary(path), _encode, _decode
    )
    return {path: vanished[path] if path in vanished else values[str(path)] for path in paths}


def _summary(path: Path) -> RecordSummary:
    meta = _scan_record_metadata(path)
    return RecordSummary(
        meta.runtime, meta.record_id, meta.thread_id, meta.start_value, meta.end_value, meta.child_references, meta.failure
    )


def _encode(summary: RecordSummary) -> list[Any]:
    return [
        *summary[:5],
        [list(reference) for reference in summary.child_references],
        None if summary.failure is None else list(summary.failure),
    ]


def _decode(fields: list[Any]) -> RecordSummary:
    runtime, record_id, thread_id, start_value, end_value, references, failure = fields
    return RecordSummary(
        runtime,
        record_id,
        thread_id,
        start_value,
        end_value,
        tuple((child, line) for child, line in references),
        None if failure is None else _RecordLoadFailure(*failure),
    )
