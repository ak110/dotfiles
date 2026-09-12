#!/usr/bin/env python3
"""session-reviewの一次選別結果から報告を決定的に生成し、構造を検査する。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from typing import Any

PHASES = (
    "抽出",
    "一次選別",
    "完全分析",
    "既存キュー照合",
    "報告生成",
    "構造検査",
)
ANALYSIS_FIELDS = ("direct_cause", "root_cause", "rule_gap", "action")


class ReportError(ValueError):
    """入力又は報告構造が契約を満たさない。"""


def _load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ReportError(f"{path}:{line_no}: JSON objectではない")
        values.append(value)
    return values


def _locator(value: dict[str, Any]) -> str:
    record, line = value.get("record"), value.get("line")
    if not isinstance(record, str) or not isinstance(line, int):
        raise ReportError("候補又は判定にrecordとlineがない")
    return f"{record}:{line}"


def _seconds(value: dict[str, Any], phase: str) -> float:
    try:
        started = dt.datetime.fromisoformat(value["started_at"])
        finished = dt.datetime.fromisoformat(value["finished_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise ReportError(f"{phase}の開始・終了時刻が不正である") from error
    seconds = (finished - started).total_seconds()
    if seconds < 0:
        raise ReportError(f"{phase}の終了時刻が開始時刻より前である")
    return seconds


def render(
    candidates: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    timings: dict[str, dict[str, Any]],
) -> str:
    """全候補を過不足なく含むMarkdown報告を返す。"""
    candidate_by_locator = {_locator(item): item for item in candidates}
    if len(candidate_by_locator) != len(candidates):
        raise ReportError("候補locatorが重複している")
    decision_by_locator = {_locator(item): item for item in decisions}
    if len(decision_by_locator) != len(decisions):
        raise ReportError("判定locatorが重複している")
    if decision_by_locator.keys() != candidate_by_locator.keys():
        missing = sorted(candidate_by_locator.keys() - decision_by_locator.keys())
        extra = sorted(decision_by_locator.keys() - candidate_by_locator.keys())
        raise ReportError(f"候補と判定が一致しない: missing={missing}, extra={extra}")
    if tuple(timings) != PHASES:
        raise ReportError("工程時刻は規定の6工程を順序どおり含める")

    rows: list[str] = []
    for locator, candidate in candidate_by_locator.items():
        decision = decision_by_locator[locator]
        disposition = decision.get("disposition")
        if disposition == "excluded":
            reason = decision.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ReportError(f"{locator}: 一次選別の除外理由がない")
            cells = (reason, "一次選別で除外", "一次選別で除外", "一次選別で除外", "一次選別で除外")
        elif disposition == "analyzed":
            analysis_id = decision.get("analysis_id")
            analysis = analyses.get(analysis_id) if isinstance(analysis_id, str) else None
            if not isinstance(analysis, dict):
                raise ReportError(f"{locator}: 完全分析が見つからない")
            missing_fields = [
                field for field in ANALYSIS_FIELDS if not isinstance(analysis.get(field), str) or not analysis[field].strip()
            ]
            if missing_fields:
                raise ReportError(f"{locator}: 完全分析の必須欄がない: {missing_fields}")
            cells = (
                str(decision.get("defect", "要処置")),
                analysis["direct_cause"],
                analysis["root_cause"],
                analysis["rule_gap"],
                analysis["action"],
            )
        else:
            raise ReportError(f"{locator}: dispositionが不正である")
        summary = str(candidate.get("text", candidate.get("candidate_kind", "候補"))).replace("|", "\\|")
        rows.append("| " + " | ".join((f"{locator} {summary}", *cells)) + " |")

    timing_rows = [f"| {phase} | {_seconds(timings[phase], phase):.3f} |" for phase in PHASES]
    return "\n".join(
        [
            "# セッション振り返り",
            "",
            "## 候補別の判定記録",
            "",
            "| 候補 | 欠陥判定 | 直接的原因 | 根本原因 | 既存規範が適用されなかった理由 | 処置 |",
            "| --- | --- | --- | --- | --- | --- |",
            *rows,
            "",
            "## 工程別所要時間",
            "",
            "| 工程 | 秒 |",
            "| --- | ---: |",
            *timing_rows,
            "",
            f"構造検査: 候補{len(candidates)}件、過不足0件、重複0件",
            "",
        ]
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("generate", "check"))
    parser.add_argument("--candidates", type=pathlib.Path, required=True)
    parser.add_argument("--decisions", type=pathlib.Path, required=True)
    parser.add_argument("--analyses", type=pathlib.Path, required=True)
    parser.add_argument("--timings", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """報告を決定的に生成するか、保存済み報告の構造と内容を検査する。"""
    args = _parser().parse_args(argv)
    try:
        decisions = _load_json(args.decisions)
        analyses = _load_json(args.analyses)
        timings = _load_json(args.timings)
        if not isinstance(decisions, list) or not isinstance(analyses, dict) or not isinstance(timings, dict):
            raise ReportError("判定・分析・工程時刻のJSON型が不正である")
        content = render(_load_jsonl(args.candidates), decisions, analyses, timings)
        if args.mode == "generate":
            args.output.write_text(content, encoding="utf-8")
        elif not args.output.is_file() or args.output.read_text(encoding="utf-8") != content:
            raise ReportError("報告が入力から再生成した構造と一致しない")
    except (OSError, json.JSONDecodeError, ReportError) as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
