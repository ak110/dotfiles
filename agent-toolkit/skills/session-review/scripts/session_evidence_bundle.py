"""証拠抽出の`--bundle`集約実行。1回の記録の読み込みで各走査を行い、全量をファイルへ書いて要約を返す。"""

from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any

from session_evidence_candidates import (
    _BUNDLE_BODY_LENGTH,
    _adhoc_processing_events,
    _candidate_events,
    _candidate_evidence_events,
    _wi_candidate_events,
    _write_candidate_evidence_files,
)
from session_evidence_extract import (
    _clip,
    _CollectedRecord,
    _collection_events,
    _default_events,
    _error_event,
    _unresolved_events,
    _UnresolvedRecord,
)
from session_evidence_hook_notices import (
    _hook_notice_candidate_events,
    _hook_notice_events,
)
from session_evidence_stats import (
    _stats_events,
)
from session_evidence_tool_calls import comparison_material_events
from session_evidence_user_events import (
    _conversation_events,
)
from session_evidence_warn import (
    _warning_collection_events,
)

_BUNDLE_SCAN_FILENAMES = (
    "timeline.jsonl",
    "warnings.jsonl",
    "stats.jsonl",
    "hook-notices.jsonl",
    "candidates.jsonl",
    "candidate-evidence.jsonl",
    "conversation.jsonl",
)


_BUNDLE_BODY_KINDS = frozenset({"failed-tool", "agent-completion", "final-result"})


_BUNDLE_LOCATOR_ONLY_KINDS = frozenset({"user"})


_BUNDLE_WARNING_GROUP_LENGTH = 120


_BUNDLE_WARNING_SAMPLE_COUNT = 3


def _bundle_events(
    collected: list[_CollectedRecord],
    unresolved: list[_UnresolvedRecord],
    directory: Path,
    compaction_record_dir: Path,
) -> tuple[list[dict[str, Any]], int]:
    """4走査を1回の記録読み込みで行い、走査ごとの全量をファイルへ書いて要約だけを返す。

    標準出力へ返す要約の項目は、抽出担当が走査ごとの全量をファイルへ保存し、自作の集計コマンドで
    再加工していた工程を代替する目的で設けた。項目を減らすとその工程が抽出担当側へ戻るため、
    取捨は代替対象の集計を確認してから判断する。
    保存先のファイルと同じ内容になる集計と通知の走査は標準出力へ返さない。呼び出し元が同じ内容を
    ファイルと標準出力の双方から受け取ると、標準出力の分量が実行環境の切り詰めに達するためである。
    未解決記録のイベントはどのファイルにも保存しないため、標準出力へ1回だけ書く。各ファイルの内容は、
    その走査を単独で実行した出力から未解決記録のイベントを除いたものと一致する。
    """
    if not directory.is_dir():
        return [
            _error_event(
                f"出力先が実在するディレクトリでない: {directory}",
                next_action="`--bundle`へ作成済みのディレクトリ（managed-tempの中など）の絶対パスを渡して再実行する",
            )
        ], 2
    resolved = directory.resolve()
    timeline = _default_events(collected, [])
    metadata = _collection_events(collected, [])
    warnings = [*metadata, *_warning_collection_events(collected, [])]
    stats = [*metadata, *_stats_events(collected, compaction_record_dir)]
    hook_notices = [*metadata, *_hook_notice_events([record for item in collected for record in item.records])]
    main_record = next(item for item in collected if item.role == "main")
    candidates = _candidate_events(
        timeline,
        warnings,
        _hook_notice_candidate_events(collected),
        main_record_id=main_record.record_id,
        adhoc_processing=_adhoc_processing_events(collected),
        wi_events=_wi_candidate_events(collected, main_record.path.stem),
    )

    candidate_evidence = _write_candidate_evidence_files(
        resolved, _candidate_evidence_events(collected, candidates, timeline, warnings, hook_notices)
    )
    events: list[dict[str, Any]] = []
    scans = (timeline, warnings, stats, hook_notices, candidates, candidate_evidence, _conversation_events(collected))
    for filename, scan_events in zip(_BUNDLE_SCAN_FILENAMES, scans, strict=True):
        path = resolved / filename
        path.write_text(
            "".join(f"{json.dumps(event, ensure_ascii=False)}\n" for event in scan_events),
            encoding="utf-8",
        )
        events.append({"kind": "bundle-file", "path": str(path), "count": len(scan_events)})
    comparison_path = resolved / "comparison-materials.jsonl"
    comparison = comparison_material_events(collected)
    comparison_path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in comparison), encoding="utf-8")
    events.append({"kind": "bundle-file", "path": str(comparison_path), "count": len(comparison)})
    candidate_items = [item for item in candidates if item.get("kind") == "candidate"]
    events.append(
        {
            "kind": "bundle-evidence-metrics",
            "full_scan_lines": len(timeline) + len(warnings) + len(hook_notices),
            "full_scan_bytes": sum(
                (resolved / filename).stat().st_size for filename in ("timeline.jsonl", "warnings.jsonl", "hook-notices.jsonl")
            ),
            "candidate_evidence_lines": len(candidate_evidence),
            "candidate_evidence_bytes": (resolved / "candidate-evidence.jsonl").stat().st_size,
            "decision_count": len(candidate_items),
            "analysis_group_count": len(
                {json.dumps(item["analysis_group_hint"], ensure_ascii=False) for item in candidate_items}
            ),
        }
    )
    events.extend(_bundle_timeline_events(timeline))
    events.extend(_bundle_warning_events(warnings))
    events.extend(_unresolved_events(unresolved))
    return events, 0


def _bundle_timeline_events(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """通常表示を、イベント種別ごとの件数と問題候補の位置へ要約する。

    `assistant`と`skill-invocation`は件数だけで候補を確定できるため、本文を標準出力へ含めない。
    位置を伴う種別の本文も冒頭に限り、全体は`--detail`で取得する。
    位置を伴うイベントへは、そのエントリの時刻を`timestamp`として載せる。
    時刻を持たないエントリでは空文字列とし、所要時間の区間の境界を`--detail`の追加照会なしで確定できるようにする。
    """
    counts = collections.Counter(str(event["kind"]) for event in timeline)
    events: list[dict[str, Any]] = [
        {"kind": "bundle-kind-count", "event_kind": event_kind, "count": count}
        for event_kind, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    for event in timeline:
        event_kind = event["kind"]
        timestamp = event.get("timestamp")
        if event_kind in _BUNDLE_BODY_KINDS:
            events.append(
                {
                    "kind": "bundle-locator",
                    "event_kind": event_kind,
                    "record": event["record"],
                    "line": event["line"],
                    "timestamp": timestamp,
                    "text": _clip(str(event.get("text", "")), _BUNDLE_BODY_LENGTH),
                }
            )
        elif event_kind in _BUNDLE_LOCATOR_ONLY_KINDS:
            events.append(
                {
                    "kind": "bundle-locator",
                    "event_kind": event_kind,
                    "record": event["record"],
                    "line": event["line"],
                    "timestamp": timestamp,
                }
            )
    return events


def _bundle_warning_events(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """警告を本文の冒頭で分類し、分類ごとの件数と先頭の代表位置へ要約する。

    一致が無い場合に`_warning_collection_events`が返す位置を持たないイベントは分類の対象外とする。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in warnings:
        if event.get("kind") != "warning" or "line" not in event:
            continue
        groups.setdefault(str(event.get("text", ""))[:_BUNDLE_WARNING_GROUP_LENGTH], []).append(event)
    return [
        {
            "kind": "bundle-warning-group",
            "text": text,
            "count": len(items),
            "samples": [{"record": item["record"], "line": item["line"]} for item in items[:_BUNDLE_WARNING_SAMPLE_COUNT]],
        }
        for text, items in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    ]
