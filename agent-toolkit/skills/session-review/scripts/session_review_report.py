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
SUMMARY_MAX_CHARS = 200
REPORT_H2_HEADINGS = (
    "対象セッション",
    "問題候補の判定記録",
    "メイン由来の改善点",
    "規範適用による停止",
    "所要時間の内訳と改善提案",
    "登録したキュー項目",
    "未確認範囲",
)
_GENERATED_SECTION_HEADINGS = ("問題候補の判定記録", "所要時間の内訳と改善提案")
_INPUT_STRUCTURE_HELP = f"""入力JSONの構造:

--decisions: 候補ごとの判定を並べた配列。
  locators: 候補の記録位置の配列（`<記録名>:<行番号>`）
  disposition: 判定の区分
  analysis_id: 欠陥と判定した候補が参照する分析の識別子
  reason: 欠陥でないと判定した候補の根拠
  defect: 欠陥かどうかの真偽値

--analyses: 分析の識別子ごとの原因分析を並べた配列。
  analysis_id: 分析の識別子
  {ANALYSIS_FIELDS[0]}: 直接的原因
  {ANALYSIS_FIELDS[1]}: 根本原因
  {ANALYSIS_FIELDS[2]}: 規範の欠落
  {ANALYSIS_FIELDS[3]}: 確定した処置

--timings: 工程ごとの区間を並べた配列。工程名は{"、".join(PHASES)}の6つとする。
  phase: 工程名
  started_at: 開始時刻（ISO 8601）
  finished_at: 終了時刻（ISO 8601）
"""
"""`--help`へ示す入力JSONの構造。

消費側が構造を確定するために実装を読む往復を除く。
"""


class ReportError(ValueError):
    """入力又は報告構造が契約を満たさない。"""


def _cell_text(text: str) -> str:
    """表のセルへ収まる1行の要約を返す。"""
    collapsed = " ".join(text.split()).replace("|", "\\|")
    if len(collapsed) > SUMMARY_MAX_CHARS:
        collapsed = collapsed[:SUMMARY_MAX_CHARS] + "…"
    return collapsed


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


def _locators(value: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    raw_locators = value.get("locators")
    if not isinstance(raw_locators, list) or not raw_locators:
        raise ReportError("候補又は判定にlocatorsがない")
    locators: list[tuple[str, int]] = []
    for locator in raw_locators:
        if not isinstance(locator, dict):
            raise ReportError("locatorがJSON objectではない")
        record, line = locator.get("record"), locator.get("line")
        if not isinstance(record, str) or not isinstance(line, int):
            raise ReportError("locatorにrecordとlineがない")
        locators.append((record, line))
    if locators != sorted(set(locators)):
        raise ReportError("locatorsが安定順でないか重複している")
    return tuple(locators)


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


def _section_body(content: str, heading: str) -> str:
    """指定したH2の本文を次のH2直前まで返す。"""
    lines = content.splitlines()
    marker = f"## {heading}"
    try:
        start = lines.index(marker) + 1
    except ValueError as error:
        raise ReportError(f"報告に見出しがない: {marker}") from error
    end = next((index for index in range(start, len(lines)) if lines[index].startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).strip()


def _check_rendered_report(content: str, expected: str) -> None:
    """6節の順序と機械生成部分を検査し、各節への担当記述を許容する。"""
    lines = content.splitlines()
    if not lines or lines[0] != "# セッション振り返り":
        raise ReportError("報告のH1が不正である")
    headings = tuple(line.removeprefix("## ") for line in lines if line.startswith("## "))
    if headings != REPORT_H2_HEADINGS:
        raise ReportError(f"報告のH2が規定の順序と一致しない: {headings!r}")
    for heading in _GENERATED_SECTION_HEADINGS:
        expected_body = _section_body(expected, heading)
        if expected_body not in _section_body(content, heading):
            raise ReportError(f"機械生成部分が入力と一致しない: ## {heading}")


def render(
    candidates: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    timings: dict[str, dict[str, Any]],
) -> str:
    """全候補を過不足なく含むMarkdown報告を返す。"""
    summaries = [item for item in candidates if item.get("kind") == "candidate-summary"]
    candidate_items = [item for item in candidates if item.get("kind") == "candidate"]
    if len(summaries) != 1 or len(candidate_items) + 1 != len(candidates):
        raise ReportError("候補入力はcandidateと末尾のcandidate-summaryだけを含める")
    candidate_by_locators = {_locators(item): item for item in candidate_items}
    if len(candidate_by_locators) != len(candidate_items):
        raise ReportError("候補locatorsが重複している")
    for locators, candidate in candidate_by_locators.items():
        if candidate.get("count") != len(locators):
            raise ReportError("候補countがlocatorsの件数と一致しない")
    decision_by_locators = {_locators(item): item for item in decisions}
    if len(decision_by_locators) != len(decisions):
        raise ReportError("判定locatorsが重複している")
    if decision_by_locators.keys() != candidate_by_locators.keys():
        missing = sorted(candidate_by_locators.keys() - decision_by_locators.keys())
        extra = sorted(decision_by_locators.keys() - candidate_by_locators.keys())
        raise ReportError(f"候補と判定が一致しない: missing={missing}, extra={extra}")
    flattened = sorted(locator for locators in candidate_by_locators for locator in locators)
    if flattened:
        summary_locators = _locators({"locators": summaries[0].get("included_locators")})
    else:
        included_locators = summaries[0].get("included_locators")
        if not isinstance(included_locators, list) or included_locators:
            raise ReportError("候補0件のincluded_locatorsは空listでなければならない")
        summary_locators = ()
    if tuple(flattened) != summary_locators:
        raise ReportError("集約候補の全locatorが一次選別集合と一致しない")
    if summaries[0].get("count") != len(candidate_items):
        raise ReportError("candidate-summaryのcountが候補件数と一致しない")
    if summaries[0].get("included_locator_count") != len(flattened):
        raise ReportError("candidate-summaryのlocator件数が一致しない")
    if tuple(timings) != PHASES:
        raise ReportError("工程時刻は規定の6工程を順序どおり含める")

    candidate_rows: list[str] = []
    used_analysis_ids: set[str] = set()
    for locators, candidate in candidate_by_locators.items():
        locator_text = ", ".join(f"{record}:{line}" for record, line in locators)
        decision = decision_by_locators[locators]
        disposition = decision.get("disposition")
        if disposition == "excluded":
            reason = decision.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ReportError(f"{locator_text}: 一次選別の除外理由がない")
            outcome = f"一次選別で除外: {reason}"
            analysis_id_text = "-"
        elif disposition == "analyzed":
            analysis_id = decision.get("analysis_id")
            analysis = analyses.get(analysis_id) if isinstance(analysis_id, str) else None
            if not isinstance(analysis, dict):
                raise ReportError(f"{locator_text}: 完全分析が見つからない")
            missing_fields = [
                field for field in ANALYSIS_FIELDS if not isinstance(analysis.get(field), str) or not analysis[field].strip()
            ]
            if missing_fields:
                raise ReportError(f"{locator_text}: 完全分析の必須欄がない: {missing_fields}")
            assert isinstance(analysis_id, str)
            used_analysis_ids.add(analysis_id)
            outcome = str(decision.get("defect", "要処置"))
            analysis_id_text = analysis_id
        else:
            raise ReportError(f"{locator_text}: dispositionが不正である")
        summary = _cell_text(str(candidate.get("text", candidate.get("candidate_kind", "候補"))))
        occurrence_count = candidate.get("occurrence_count")
        omitted_locator_count = candidate.get("omitted_locator_count")
        if isinstance(occurrence_count, int) and isinstance(omitted_locator_count, int):
            summary += f"（発生{occurrence_count}件、代表位置{len(locators)}件、省略{omitted_locator_count}件）"
        candidate_rows.append("| " + " | ".join((f"{locator_text} {summary}", outcome, analysis_id_text)) + " |")

    analysis_rows = [
        "| "
        + " | ".join(
            (
                analysis_id,
                _cell_text(analyses[analysis_id]["direct_cause"]),
                _cell_text(analyses[analysis_id]["root_cause"]),
                _cell_text(analyses[analysis_id]["rule_gap"]),
                _cell_text(analyses[analysis_id]["action"]),
            )
        )
        + " |"
        for analysis_id in sorted(used_analysis_ids)
    ]

    timing_rows = [f"| {phase} | {_seconds(timings[phase], phase):.3f} |" for phase in PHASES]
    return "\n".join(
        [
            "# セッション振り返り",
            "",
            "## 対象セッション",
            "",
            "## 問題候補の判定記録",
            "",
            "| 候補 | 判定 | 分析ID |",
            "| --- | --- | --- |",
            *candidate_rows,
            "",
            "| 分析ID | 直接的原因 | 根本原因 | 既存規範が適用されなかった理由 | 処置 |",
            "| --- | --- | --- | --- | --- |",
            *analysis_rows,
            "",
            f"構造検査: 候補{len(candidate_items)}件、locator{len(flattened)}件、過不足0件、重複0件",
            "",
            "## メイン由来の改善点",
            "",
            "## 規範適用による停止",
            "",
            "## 所要時間の内訳と改善提案",
            "",
            "| 工程 | 秒 |",
            "| --- | ---: |",
            *timing_rows,
            "",
            "## 登録したキュー項目",
            "",
            "## 未確認範囲",
            "",
        ]
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.epilog = _INPUT_STRUCTURE_HELP
    parser.add_argument("mode", choices=("generate", "check"))
    parser.add_argument("--candidates", type=pathlib.Path, required=True, help="抽出器が出力したcandidates.jsonlの絶対パス")
    parser.add_argument("--decisions", type=pathlib.Path, required=True, help="候補ごとの判定を並べたJSONの絶対パス")
    parser.add_argument("--analyses", type=pathlib.Path, required=True, help="分析IDごとの原因分析を並べたJSONの絶対パス")
    parser.add_argument("--timings", type=pathlib.Path, required=True, help="工程ごとの開始と終了を並べたJSONの絶対パス")
    parser.add_argument("--output", type=pathlib.Path, required=True, help="生成する報告の絶対パス")
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
        elif not args.output.is_file():
            raise ReportError("報告ファイルが存在しない")
        else:
            _check_rendered_report(args.output.read_text(encoding="utf-8"), content)
    except (OSError, json.JSONDecodeError, ReportError) as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
