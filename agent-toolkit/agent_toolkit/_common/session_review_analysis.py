from __future__ import annotations

import json
import pathlib
from collections import Counter

from agent_toolkit._common import atomic_file, markdown_headings, session_review


def _analysis_ids(targets: list[session_review.PendingGeneration]) -> list[str]:
    counts = Counter(target.session_id for target in targets)
    return [f"{target.host}:{target.session_id}" if counts[target.session_id] > 1 else target.session_id for target in targets]


def ensure(writer: session_review.BatchWriter) -> pathlib.Path:
    path = session_review.batch_analysis_path(writer.path)
    with session_review.locked():
        if path.exists():
            return path
        batch = writer.load()
        manifest_fields = batch.model_extra or {}
        prepared = {(item.get("host"), item.get("session_id")): item for item in batch.prepared}
        lines = [
            f"# 振り返り分析記録: {batch.run_id}",
            "",
            f"- 開始時刻: {manifest_fields.get('started_at') or '不明'}",
            f"- 報告先: {manifest_fields.get('target_repo') or '不明'}",
            "",
            "## 対象ごとの分析",
            "",
            "| 対象ID | 状態 | conversation_path | candidates_path | stats_path | reference_document |"
            " partial_analysis | mandatory_candidates | 欠測・回復した範囲 | 結論 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for target, analysis_id in zip(batch.targets, _analysis_ids(batch.targets), strict=True):
            item = prepared.get((target.host, target.session_id), {})
            fields = (
                analysis_id,
                "未着手",
                item.get("conversation_path"),
                item.get("candidates_path"),
                item.get("stats_path"),
                item.get("reference_document"),
                str(bool(item.get("partial_analysis"))).lower(),
                json.dumps(item.get("mandatory_candidates", []), ensure_ascii=False),
                json.dumps(
                    {key: item[key] for key in ("unconfirmed_scope", "recovered_record_lines") if item.get(key)},
                    ensure_ascii=False,
                )
                if item.get("partial_analysis")
                else None,
                None,
            )
            lines.append("| " + " | ".join(str(field or "なし").replace("|", "&#124;") for field in fields) + " |")
        lines.extend(
            (
                "",
                "## 横断の集計",
                "",
                "- 採否集計: 未着手",
                "- 2回目の合格率: 未着手",
                "- hookの所要: 未着手",
                "- CI履歴: 未着手",
                "",
            )
        )
        atomic_file.atomic_write(path, "\n".join(lines))
    return path


def unfinished(manifest: pathlib.Path, batch: session_review.ReviewBatch) -> tuple[str, ...]:
    path = session_review.batch_analysis_path(manifest)
    states: dict[str, list[str]] = {}
    text = markdown_headings.without_literal_blocks(path.read_text(encoding="utf-8"))
    for line in text.partition("## 対象ごとの分析\n")[2].splitlines():
        if line.startswith("## "):
            break
        if line.startswith("|") and line.count("|") >= 3:
            _, session_id, state, _ = line.split("|", 3)
            states.setdefault(session_id.strip(), []).append(state.strip())
    return tuple(analysis_id for analysis_id in _analysis_ids(batch.targets) if states.get(analysis_id) != ["完了"])
