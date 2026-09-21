#!/usr/bin/env python3
"""session-reviewの一次選別結果から報告を決定的に生成し、構造を検査する。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys
from typing import Any

TIMING_CATEGORIES = (
    "preparation",
    "rule-stop",
    "delegate-runtime",
    "parent-review",
)
ANALYSIS_FIELDS = ("direct_cause", "root_cause", "rule_gap", "action")
DURATION_TARGET_SECONDS = 180.0
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
FREE_SECTION_HEADINGS = (
    "対象セッション",
    "メイン由来の改善点",
    "規範適用による停止",
    "登録したキュー項目",
    "未確認範囲",
)
"""自由記述の本文を入力から受け取る節。

表を機械生成する2節を除く全てとする。
本文を入力として渡せる節を限ると、残る節は生成後の部分編集で埋めることになり、
報告1件ごとに編集の往復が生じる。
"""
_INPUT_STRUCTURE_HELP = f"""入力JSONの構造:

--decisions: 候補ごとの判定を並べたJSON配列。各要素は次のキーを持つJSON object。
  candidate_id: candidates.jsonlの候補を参照する識別子
  disposition: 判定の区分。`excluded`（一次選別で除外）又は`analyzed`（完全分析へ送る）の2つだけを受理する
  reason: `excluded`で必須。欠陥でないと判定した根拠の文字列
  analysis_id: `analyzed`で必須。参照する分析の識別子の文字列
  defect: `analyzed`で任意。判定結果として表へ書く値。省略時は`要処置`とする

--analyses: 分析の識別子をキーとするJSON object。値は次のキーを持つJSON object。
  {ANALYSIS_FIELDS[0]}: 直接的原因
  {ANALYSIS_FIELDS[1]}: 根本原因
  {ANALYSIS_FIELDS[2]}: 規範の欠落
  {ANALYSIS_FIELDS[3]}: 確定した処置

--timings: 区間IDをキーとするJSON object。値は`category`（{"、".join(TIMING_CATEGORIES)}のいずれか）と
           `source`（典拠）を持ち、観測できた区間では`started_at`と`finished_at`をISO 8601の文字列で、
           観測できない区間では`unknown_reason`を非空文字列で持つ。

--duration-analysis: 次のキーを持つJSON object。
  bottleneck: `interval`（非空文字列）と`seconds`（0以上の有限な数値）を持つJSON object
  reduction: `seconds`（0以上の有限な数値）と`basis`（非空文字列）を持つJSON object
  non_reducible_reason: 削減できない区間又は理由を示す非空文字列
  unmeasured_intervals: `interval`と`reason`を非空文字列で持つJSON objectの配列
  extractor_event: `kind`と`value`を非空文字列で持つJSON object
  comparison_intervals: 短縮前後の比較へ用いる観測済み区間IDの配列

--sections: 節名をキーとするJSON object。値はその節へ置くMarkdown本文の文字列。
            受理する節名は{"、".join(FREE_SECTION_HEADINGS)}の5つとする。
            省略した節と空文字列の節は本文を持たない節として生成する。
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


def _nonempty_string(value: Any, field: str) -> str:
    """非空文字列を返し、型又は空値を拒否する。"""
    if not isinstance(value, str) or not value.strip():
        raise ReportError(f"所要時間分析の{field}は非空文字列で指定する")
    return value.strip()


def _nonnegative_number(value: Any, field: str) -> float:
    """boolを除く0以上の有限な数値をfloatで返す。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReportError(f"所要時間分析の{field}は0以上の有限な数値で指定する")
    try:
        number = float(value)
    except OverflowError as error:
        raise ReportError(f"所要時間分析の{field}は0以上の有限な数値で指定する") from error
    if not math.isfinite(number) or number < 0:
        raise ReportError(f"所要時間分析の{field}は0以上の有限な数値で指定する")
    return number


def _duration_analysis_lines(value: dict[str, Any], measured: dict[str, float]) -> list[str]:
    """構造化された所要時間分析を検証し、固定順のMarkdown行へ変換する。"""
    expected = {
        "bottleneck",
        "reduction",
        "non_reducible_reason",
        "unmeasured_intervals",
        "extractor_event",
        "comparison_intervals",
    }
    if set(value) != expected:
        raise ReportError(f"所要時間分析のキーが不正である: {sorted(set(value) ^ expected)}")
    bottleneck = value["bottleneck"]
    reduction = value["reduction"]
    extractor_event = value["extractor_event"]
    unmeasured = value["unmeasured_intervals"]
    comparison_intervals = value["comparison_intervals"]
    if not isinstance(bottleneck, dict) or set(bottleneck) != {"interval", "seconds"}:
        raise ReportError("所要時間分析のbottleneckが不正である")
    if not isinstance(reduction, dict) or set(reduction) != {"seconds", "basis"}:
        raise ReportError("所要時間分析のreductionが不正である")
    if not isinstance(extractor_event, dict) or set(extractor_event) != {"kind", "value"}:
        raise ReportError("所要時間分析のextractor_eventが不正である")
    if not isinstance(unmeasured, list):
        raise ReportError("所要時間分析のunmeasured_intervalsは配列で指定する")
    if (
        not isinstance(comparison_intervals, list)
        or not comparison_intervals
        or any(not isinstance(interval, str) or not interval for interval in comparison_intervals)
        or len(set(comparison_intervals)) != len(comparison_intervals)
    ):
        raise ReportError("所要時間分析のcomparison_intervalsは重複のない非空文字列配列で指定する")
    bottleneck_seconds = _nonnegative_number(bottleneck["seconds"], "bottleneck.seconds")
    reduction_seconds = _nonnegative_number(reduction["seconds"], "reduction.seconds")
    unmeasured_texts: list[str] = []
    for index, interval in enumerate(unmeasured):
        if not isinstance(interval, dict) or set(interval) != {"interval", "reason"}:
            raise ReportError(f"所要時間分析のunmeasured_intervals[{index}]が不正である")
        unmeasured_texts.append(
            f"{_nonempty_string(interval['interval'], f'unmeasured_intervals[{index}].interval')}"
            f"（{_nonempty_string(interval['reason'], f'unmeasured_intervals[{index}].reason')}）"
        )
    missing_comparison = [interval for interval in comparison_intervals if interval not in measured]
    if missing_comparison:
        comparison_line = f"- 180秒目標との比較: 未確定（未観測区間: {'、'.join(missing_comparison)}）"
        return [
            f"- ボトルネック: {_nonempty_string(bottleneck['interval'], 'bottleneck.interval')}（{bottleneck_seconds:.3f}秒）",
            f"- 削減見込み: {reduction_seconds:.3f}秒（{_nonempty_string(reduction['basis'], 'reduction.basis')}）",
            f"- 削減不能部分: {_nonempty_string(value['non_reducible_reason'], 'non_reducible_reason')}",
            f"- 未計測区間: {'、'.join(unmeasured_texts) if unmeasured_texts else 'なし'}",
            f"- 抽出器イベント: {_nonempty_string(extractor_event['kind'], 'extractor_event.kind')}="
            f"{_nonempty_string(extractor_event['value'], 'extractor_event.value')}",
            comparison_line,
        ]
    measured_seconds = sum(measured[interval] for interval in comparison_intervals)
    if reduction_seconds > measured_seconds:
        raise ReportError("所要時間分析のreduction.secondsが比較対象区間の合計を超える")
    estimated_seconds = measured_seconds - reduction_seconds
    difference = estimated_seconds - DURATION_TARGET_SECONDS
    comparison = (
        f"目標を{abs(difference):.3f}秒下回る"
        if difference < 0
        else f"目標を{difference:.3f}秒上回る"
        if difference > 0
        else "目標と一致する"
    )
    return [
        f"- ボトルネック: {_nonempty_string(bottleneck['interval'], 'bottleneck.interval')}（{bottleneck_seconds:.3f}秒）",
        f"- 削減見込み: {reduction_seconds:.3f}秒（{_nonempty_string(reduction['basis'], 'reduction.basis')}）",
        f"- 削減不能部分: {_nonempty_string(value['non_reducible_reason'], 'non_reducible_reason')}",
        f"- 未計測区間: {'、'.join(unmeasured_texts) if unmeasured_texts else 'なし'}",
        f"- 抽出器イベント: {_nonempty_string(extractor_event['kind'], 'extractor_event.kind')}="
        f"{_nonempty_string(extractor_event['value'], 'extractor_event.value')}",
        f"- 比較対象区間: {'、'.join(comparison_intervals)}",
        f"- 180秒目標との比較: 同一区間集合の改善後見込み{estimated_seconds:.3f}秒、{comparison}",
    ]


def _timing_rows(timings: dict[str, dict[str, Any]]) -> tuple[list[str], dict[str, float]]:
    """任意個の観測区間と未観測区間を検証して表へ変換する。"""
    if not timings:
        raise ReportError("工程時刻は1区間以上を含める")
    rows: list[str] = []
    measured: dict[str, float] = {}
    for interval_id, value in timings.items():
        if not isinstance(interval_id, str) or not interval_id or not isinstance(value, dict):
            raise ReportError("工程時刻の区間ID又は値が不正である")
        category = value.get("category")
        source = value.get("source")
        if category not in TIMING_CATEGORIES:
            raise ReportError(f"{interval_id}のcategoryが不正である")
        source_text = _nonempty_string(source, f"timings.{interval_id}.source")
        timestamp_keys = {"started_at", "finished_at"}
        has_timestamps = timestamp_keys <= set(value)
        has_unknown = "unknown_reason" in value
        expected_keys = {"category", "source"} | (timestamp_keys if has_timestamps else {"unknown_reason"})
        if has_timestamps == has_unknown or set(value) != expected_keys:
            raise ReportError(f"{interval_id}は時刻の組又はunknown_reasonの一方だけを持つ")
        if has_timestamps:
            seconds = _seconds(value, interval_id)
            measured[interval_id] = seconds
            rows.append(f"| {interval_id} | {category} | {seconds:.3f} | {source_text} |")
        else:
            reason = _nonempty_string(value["unknown_reason"], f"timings.{interval_id}.unknown_reason")
            rows.append(f"| {interval_id} | {category} | 不明 | {source_text}: {reason} |")
    return rows, measured


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


def _section_lines(sections: dict[str, str], heading: str) -> list[str]:
    """節ごとの自由記述本文を、本文と後続の空行の並びで返す。"""
    body = sections.get(heading, "").strip()
    return [body, ""] if body else []


def render(
    candidates: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    analyses: dict[str, dict[str, Any]],
    timings: dict[str, dict[str, Any]],
    duration_analysis: dict[str, Any],
    sections: dict[str, str] | None = None,
) -> str:
    """全候補を過不足なく含むMarkdown報告を返す。"""
    sections = sections or {}
    unknown = sorted(set(sections) - set(FREE_SECTION_HEADINGS))
    if unknown:
        raise ReportError(f"受理しない節名がある: {unknown}")
    if any(not isinstance(body, str) for body in sections.values()):
        raise ReportError("節の本文が文字列ではない")
    summaries = [item for item in candidates if item.get("kind") == "candidate-summary"]
    candidate_items = [item for item in candidates if item.get("kind") == "candidate"]
    if len(summaries) != 1 or len(candidate_items) + 1 != len(candidates):
        raise ReportError("候補入力はcandidateと末尾のcandidate-summaryだけを含める")
    candidate_by_id: dict[str, dict[str, Any]] = {}
    for candidate in candidate_items:
        candidate_id = candidate.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in candidate_by_id:
            raise ReportError("候補candidate_idがないか重複している")
        candidate_by_id[candidate_id] = candidate
    for candidate in candidate_items:
        locators = _locators(candidate)
        if candidate.get("count") != len(locators):
            raise ReportError("候補countがlocatorsの件数と一致しない")
    decision_by_id: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        candidate_id = decision.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in decision_by_id:
            raise ReportError("判定candidate_idがないか重複している")
        decision_by_id[candidate_id] = decision
    if decision_by_id.keys() != candidate_by_id.keys():
        missing = sorted(candidate_by_id.keys() - decision_by_id.keys())
        extra = sorted(decision_by_id.keys() - candidate_by_id.keys())
        raise ReportError(f"候補と判定が一致しない: missing={missing}, extra={extra}")
    flattened = sorted({locator for candidate in candidate_items for locator in _locators(candidate)})
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
    candidate_rows: list[str] = []
    used_analysis_ids: set[str] = set()
    for candidate in candidate_items:
        candidate_id = str(candidate["candidate_id"])
        locators = _locators(candidate)
        locator_text = ", ".join(f"{record}:{line}" for record, line in locators)
        decision = decision_by_id[candidate_id]
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

    timing_rows, measured = _timing_rows(timings)
    duration_lines = _duration_analysis_lines(duration_analysis, measured)
    return "\n".join(
        [
            "# セッション振り返り",
            "",
            "## 対象セッション",
            "",
            *_section_lines(sections, "対象セッション"),
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
            *_section_lines(sections, "メイン由来の改善点"),
            "## 規範適用による停止",
            "",
            *_section_lines(sections, "規範適用による停止"),
            "## 所要時間の内訳と改善提案",
            "",
            "| 区間 | 区分 | 秒 | 典拠・未観測理由 |",
            "| --- | --- | ---: | --- |",
            *timing_rows,
            "",
            *duration_lines,
            "",
            "## 登録したキュー項目",
            "",
            *_section_lines(sections, "登録したキュー項目"),
            "## 未確認範囲",
            "",
            *_section_lines(sections, "未確認範囲"),
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
    parser.add_argument(
        "--duration-analysis",
        type=pathlib.Path,
        required=True,
        help="所要時間分析の構造化JSONの絶対パス",
    )
    parser.add_argument("--sections", type=pathlib.Path, help="節ごとの自由記述本文を並べたJSONの絶対パス")
    parser.add_argument("--output", type=pathlib.Path, required=True, help="生成する報告の絶対パス")
    return parser


def main(argv: list[str] | None = None) -> int:
    """報告を決定的に生成するか、保存済み報告の構造と内容を検査する。"""
    args = _parser().parse_args(argv)
    try:
        decisions = _load_json(args.decisions)
        analyses = _load_json(args.analyses)
        timings = _load_json(args.timings)
        duration_analysis = _load_json(args.duration_analysis)
        sections = _load_json(args.sections) if args.sections is not None else {}
        if (
            not isinstance(decisions, list)
            or not isinstance(analyses, dict)
            or not isinstance(timings, dict)
            or not isinstance(duration_analysis, dict)
        ):
            raise ReportError("判定・分析・工程時刻・所要時間分析のJSON型が不正である")
        if not isinstance(sections, dict):
            raise ReportError("節の本文のJSON型が不正である")
        content = render(_load_jsonl(args.candidates), decisions, analyses, timings, duration_analysis, sections)
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
