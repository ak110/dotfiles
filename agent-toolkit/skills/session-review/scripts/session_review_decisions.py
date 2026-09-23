"""証拠bundleから全候補を含む未判定の一次判定入力を生成する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any


def _read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if any(not isinstance(value, dict) for value in values):
        raise ValueError(f"JSON object以外の行がある: {path}")
    return values


def build_decisions(bundle: pathlib.Path) -> list[dict[str, Any]]:
    """候補、証拠索引及び重複集計を候補単位へ結合する。"""
    candidates = _read_jsonl(bundle / "candidates.jsonl")
    indexes = _read_jsonl(bundle / "candidate-evidence.jsonl")
    items = [item for item in candidates if item.get("kind") == "candidate"]
    summaries = [item for item in candidates if item.get("kind") == "candidate-summary"]
    if len(summaries) != 1 or summaries[0].get("count") != len(items):
        raise ValueError("候補件数とcandidate-summaryが一致しない")
    by_id = {item.get("candidate_id"): item for item in items}
    index_by_id = {item.get("candidate_id"): item for item in indexes}
    if len(by_id) != len(items) or len(index_by_id) != len(indexes) or by_id.keys() != index_by_id.keys():
        raise ValueError("候補と証拠索引のIDが一致しない")
    groups: dict[str, list[str]] = {}
    for item in items:
        key = json.dumps(item.get("analysis_group_hint"), ensure_ascii=False, sort_keys=True)
        groups.setdefault(key, []).append(item["candidate_id"])
    decisions: list[dict[str, Any]] = []
    for item in items:
        candidate_id = item["candidate_id"]
        index = index_by_id[candidate_id]
        key = json.dumps(item.get("analysis_group_hint"), ensure_ascii=False, sort_keys=True)
        if index.get("locators") != item.get("locators"):
            raise ValueError(f"候補と証拠索引の位置が一致しない: {candidate_id}")
        decisions.append(
            {
                "candidate_id": candidate_id,
                "candidate_kind": item["candidate_kind"],
                "locators": item["locators"],
                "analysis_group_hint": item["analysis_group_hint"],
                "related_candidate_ids": [other for other in groups[key] if other != candidate_id],
                "occurrence_count": item.get("occurrence_count", item["count"]),
                "omitted_locator_count": item.get("omitted_locator_count", 0),
                "evidence_path": index["path"],
                "evidence_count": index["evidence_count"],
                "disposition": "pending",
            }
        )
    return decisions


def main(argv: list[str] | None = None) -> int:
    """一次判定JSONを保存し、候補と未判定の件数を表示する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=pathlib.Path, required=True, help="証拠bundleの絶対ディレクトリ")
    parser.add_argument("--output", type=pathlib.Path, required=True, help="一次判定JSONの絶対パス")
    args = parser.parse_args(argv)
    try:
        decisions = build_decisions(args.bundle)
        args.output.write_text(json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"一次判定入力を生成できない: {error}", file=sys.stderr)
        return 2
    print(f"候補{len(decisions)}件、未判定{len(decisions)}件: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
