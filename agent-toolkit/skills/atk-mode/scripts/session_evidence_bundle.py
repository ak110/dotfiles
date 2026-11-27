from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any

import session_evidence_execution
from agent_toolkit._common import session_review
from session_conversation import compact_assistant_reports
from session_evidence_candidates import (
    _BUNDLE_BODY_LENGTH,
    _adhoc_processing_events,
    _candidate_events,
    _candidate_evidence_events,
    _delegate_notice_events,
    _wi_candidate_events,
    _write_candidate_evidence_files,
)
from session_evidence_delivery import post_delivery_events
from session_evidence_extract import (
    _clip,
    _CollectedRecord,
    _collection_events,
    _default_events,
    _error_event,
    _recovery_events,
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
from session_evidence_tool_calls import comparison_material_events, definition_body_read_events, resource_usage_events
from session_evidence_tool_calls_records import _conversation_body
from session_evidence_user_events import (
    _conversation_events,
)
from session_evidence_warn import (
    _warning_collection_events,
)
from session_quality_metrics import quality_metrics

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


@session_evidence_execution.shell_parse_scope()
def _bundle_events(
    collected: list[_CollectedRecord],
    unresolved: list[_UnresolvedRecord],
    directory: Path,
    compaction_record_dir: Path,
    *,
    after: Path | None = None,
    conversation_output: list[dict[str, Any]] | None = None,
    review_session_id: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """4走査を1回の記録読み込みで行い、走査ごとの全量をファイルへ書いて要約だけを返す。

    標準出力へ返す要約の項目は、抽出担当が走査ごとの全量をファイルへ保存し、自作の集計コマンドで
    再加工していた工程を代替する目的で設けた。項目を減らすと抽出担当がその集計を再び自ら行うことになるため、
    取捨は代替対象の集計を確認してから判断する。
    保存先のファイルと同じ内容になる集計と通知の走査は標準出力へ返さない。呼び出し元が同じ内容を
    ファイルと標準出力の双方から受け取ると、標準出力の分量が実行環境の切り詰めの上限に達するためである。
    未解決記録と回復した記録行のイベントはどのファイルにも保存しないため、標準出力へ1回だけ書く。各ファイルの内容は、
    その走査を単独で実行した出力から未解決記録のイベントと`record-provenance`を除き、全収集記録の
    `record-provenance`を加えたものと一致する。単独実行の標準出力は結果が参照する記録の由来だけを返すが、
    `session_review_prepare.py`は`timeline.jsonl`から候補の記録の由来を取得するため、ファイルは全記録の由来を保つ。
    conversationのツール本文は原文量と抜粋だけを保存し、表示用の入力は呼出側へメモリー上で渡す。
    結果全文の取得はdetailと原本位置へ分離する。
    """
    if not directory.is_dir():
        return [
            _error_event(
                f"出力先が実在するディレクトリでない: {directory}",
                next_action="`--bundle`へ作成済みのディレクトリ（managed-tempの中など）の絶対パスを渡して再実行する",
            )
        ], 2
    resolved = directory.resolve()
    detached: set[str] = set()
    lanes: dict[str, tuple[str, str]] = {}
    try:
        if review_session_id is not None and collected:
            lanes, inherited = session_review.lane_review_targets(review_session_id, record_path=collected[0].path.resolve())
        else:
            inherited = None
        if lanes:
            detached = {item.record_id for item in collected if str(item.path) in lanes and item.role != "main"}
            while descendants := {
                item.record_id for item in collected if item.source_record in detached and item.record_id not in detached
            }:
                detached.update(descendants)
            collected = [item for item in collected if item.record_id not in detached]
            unresolved = [item for item in unresolved if item.record_id not in detached and item.source_record not in detached]
        scope = session_review.scope_records(collected, after=after, inherited=inherited, detached=detached)
    except (OSError, ValueError, KeyError) as error:
        return [_error_event(str(error), next_action="処理済みscopeと元記録を確認し、範囲を復元して再実行する")], 2
    session_review.write(resolved / "scope.json", scope)
    incremental = (
        [
            item._replace(records=[row for row in item.records if row.line > scope["records"][item.record_id]["after_line"]])
            for item in collected
        ]
        if after is not None or inherited is not None
        else collected
    )
    timeline = _default_events(collected, [], include_delivery_id=True)
    post_delivery = post_delivery_events(timeline, collected)
    for event in timeline:
        event.pop("delivery_tool_use_id", None)
    if after is not None or inherited is not None:
        timeline = [event for event in timeline if _in_scope(event, scope)]
    if detached:
        timeline.extend(
            {"kind": "lane-review-boundary", "session_id": session, "result_summary": summary}
            for session, summary in sorted(set(lanes.values()))
        )
    metadata = _collection_events(collected, [], recoveries=False)
    warnings = [*metadata, *_warning_collection_events(collected, [])]
    if after is not None or inherited is not None:
        warnings = [event for event in warnings if _in_scope(event, scope)]
    stats = [*metadata, *_stats_events(incremental, compaction_record_dir), *quality_metrics(collected)]
    hook_notices = [*metadata, *_hook_notice_events([record for item in incremental for record in item.records])]
    main_record = next(item for item in collected if item.role == "main")
    candidates = _candidate_events(
        timeline,
        warnings,
        _hook_notice_candidate_events(incremental),
        main_record_id=main_record.record_id,
        adhoc_processing=_adhoc_processing_events(incremental),
        wi_events=_wi_candidate_events(
            incremental, main_record.record_id.split(":", 1)[1] if main_record.runtime == "opencode" else main_record.path.stem
        ),
        definition_reads=definition_body_read_events(incremental),
        delegate_notices=_delegate_notice_events(incremental),
        post_delivery=[event for event in post_delivery if _in_scope(event, scope)],
    )

    candidate_evidence = _write_candidate_evidence_files(
        resolved, _candidate_evidence_events(collected, candidates, timeline, warnings, hook_notices)
    )
    events: list[dict[str, Any]] = []
    conversation = _conversation_events(incremental, compact=True)
    if conversation_output is not None:
        conversation_output.extend([*metadata, *conversation])
    scans = (
        timeline,
        warnings,
        stats,
        hook_notices,
        candidates,
        candidate_evidence,
        conversation,
    )
    for filename, scan_events in zip(_BUNDLE_SCAN_FILENAMES, scans, strict=True):
        if filename == "conversation.jsonl":
            scan_events, _ = compact_assistant_reports(scan_events)
            scan_events = [
                {**event, **_conversation_body(event["text"], True)} if event.get("kind") == "tool-call" else event
                for event in scan_events
            ]
        path = resolved / filename
        path.write_text(
            "".join(f"{json.dumps(event, ensure_ascii=False)}\n" for event in scan_events),
            encoding="utf-8",
        )
        events.append({"kind": "bundle-file", "path": str(path), "count": len(scan_events)})
    comparison_path = resolved / "comparison-materials.jsonl"
    comparison = comparison_material_events(collected)
    if after is not None or inherited is not None:
        comparison = [event for event in comparison if _in_scope(event, scope)]
    comparison_path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in comparison), encoding="utf-8")
    events.append({"kind": "bundle-file", "path": str(comparison_path), "count": len(comparison)})
    resource_path = resolved / "resource-usage.jsonl"
    resources = resource_usage_events(incremental)
    resource_path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in resources), encoding="utf-8")
    events.append({"kind": "bundle-file", "path": str(resource_path), "count": len(resources)})
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
    events.extend(_recovery_events(collected))
    gaps = [event for event in _collection_events(collected, []) if event["kind"] == "record-gap"]
    if gaps:
        events = [{**event, "partial": True} for event in [*events, *gaps]]
    return events, 0


def _in_scope(event: dict[str, Any], scope: dict[str, Any]) -> bool:
    record = event.get("record")
    entry = scope["records"].get(record) if isinstance(record, str) else None
    if entry is not None:
        positions = [event.get(name) for name in ("line", "result_line")]
        return any(isinstance(line, int) and line > entry["after_line"] for line in positions)
    if event.get("kind") == "record-provenance":
        return True
    return any(
        _in_scope(item, scope) for name in ("locators", "calls") for item in event.get(name, []) if isinstance(item, dict)
    )


def _conversation_bundle(
    collected: list[_CollectedRecord],
    unresolved: list[_UnresolvedRecord],
    directory: Path,
    output: list[dict[str, Any]],
    *,
    after: Path | None,
    saved_scope: Path | None,
) -> tuple[list[dict[str, Any]], int]:
    try:
        if saved_scope is None:
            scope = session_review.scope_records(collected, after=after)
        else:
            scope = session_review.read(saved_scope)
            by_id = {item.record_id: item for item in collected}
            collected = [
                by_id[record_id]._replace(
                    records=[row for row in by_id[record_id].records if row.line <= entry["through_line"]]
                )
                for record_id, entry in scope["records"].items()
            ]
            session_review.scope_records(collected, after=saved_scope)
        session_review.write(directory / "scope.json", scope)
    except (OSError, ValueError, KeyError) as error:
        return [_error_event(str(error), next_action="元記録と保存scopeを確認し、初回取得からやり直す")], 2
    incremental = [
        item._replace(records=[row for row in item.records if row.line > scope["records"][item.record_id]["after_line"]])
        for item in collected
    ]
    output.extend(_collection_events(collected, [], recoveries=False))
    output.extend(_conversation_events(incremental, compact=True))
    return [*_unresolved_events(unresolved), *_recovery_events(collected)], 0


def _bundle_timeline_events(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
