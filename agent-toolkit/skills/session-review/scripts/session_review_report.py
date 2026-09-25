#!/usr/bin/env python3
"""振り返り素材AWIの判定結果から、項目専用の事後承認型UWIの本文を決定的に生成し、入力を検査する。

素材AWIを処理したレーンは、候補ごとの問題、原因、対策、再発防止策及び見送りの理由を
ユーザーが読んで可否を判断できる形でまとめて届ける。本スクリプトはその本文の骨格を生成し、
候補集合の過不足、未判定の残存、除外と非欠陥判定の根拠、再発防止策の実体を機械的に検査する。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

import session_review_decisions  # pylint: disable=import-error

ANALYSIS_TEXT_FIELDS = ("observation", "root_cause", "measures")
"""分析が必ず持つ文字列の欄。問題の観測事象、確定した原因、実施した対策の順とする。"""

PREVENTION_KINDS = {
    "implemented": "実装済み",
    "active-awi": "未完了のAWI",
    "uwi": "確認中のUWI",
}
"""再発防止策の実体の種別と、UWI本文での表示名。"""

USER_INTERVENTION_KIND = "user-intervention"

USER_INTERVENTION_EXCLUSIONS = {
    "single-inquiry": "単発の照会",
    "non-changing-request": "判断を変えない追加要求",
    "unexplained-refusal": "理由の無い拒否・中断",
}
"""ユーザー介入の候補を除外できる区分と表示名。

ユーザー介入は従来の判断を是正した事象であり、原則として再発防止策を要する。
除外できるのは是正を含まないことを介入本文又は直後の発話の有無で示せる区分に限る。
"""

SECTION_KEYS = ("メイン由来の改善点", "所要時間", "規範適用による目的逸脱")
"""`--sections`で受理する節名。いずれも省略できる。"""

CHOICES = ("その対応で問題無い", "問題がある")
"""事後承認型UWIの選択肢。`atk wi add --choices`へ同じ順で渡す。"""

_INPUT_STRUCTURE_HELP = f"""入力JSONの構造:

--decisions: 候補ごとの判定を並べたJSON配列。`session-review-decisions`の出力を起点に、各要素の`disposition`を確定する。
  candidate_id: 素材AWIの候補ID
  candidate_kind: 素材AWIの候補種別（生成された値をそのまま保持する）
  disposition: `excluded`（一次選別で除外）又は`analyzed`（分析した）。`pending`が残る入力は受理しない
  reason: `excluded`で必須。除外の根拠
  exclusion_category: 候補種別`{USER_INTERVENTION_KIND}`の`excluded`で必須。
                      {"、".join(f"`{key}`（{label}）" for key, label in USER_INTERVENTION_EXCLUSIONS.items())}のいずれか。
                      `reason`には介入本文の該当箇所、又は直後の発話の本文とその不在を根拠として書く
  analysis_id: `analyzed`で必須。参照する分析の識別子
  defect: `analyzed`で必須。`欠陥`又は`非欠陥`。候補種別`{USER_INTERVENTION_KIND}`は`欠陥`だけを受理する

--analyses: 分析の識別子をキーとするJSON object。値は次のキーを持つJSON object。
  {ANALYSIS_TEXT_FIELDS[0]}: 問題の観測事象
  {ANALYSIS_TEXT_FIELDS[1]}: 確定した原因
  {ANALYSIS_TEXT_FIELDS[2]}: 実施した対策。欠陥でない場合はその判断の根拠
  prevention: 再発防止策の実体の配列。各要素は
              `kind`（{"、".join(f"`{key}`（{label}）" for key, label in PREVENTION_KINDS.items())}）、
              `ref`（成果物の識別子、AWI又はUWIのファイル名）、`summary`（再発防止策の内容）を持つ。
              `欠陥`の候補又は候補種別`{USER_INTERVENTION_KIND}`の候補を含む分析では1件以上を必須とする
  artifacts: 変更した成果物の識別子の配列。`欠陥`の候補を含む分析では1件以上を必須とする

--sections: 節名をキーとするJSON object。値はその節の本文の文字列。{"、".join(SECTION_KEYS)}を受理し、いずれも省略できる。
"""
"""`--help`へ示す入力JSONの構造。消費側が構造を確定するために実装を読む往復を除く。"""


class ReportError(ValueError):
    """入力が契約を満たさない。検査で見つかった不足の全件を保持する。"""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


def _one_line(text: str) -> str:
    """改行と連続空白を1つの空白へ畳み、箇条書きの1項目へ収める。"""
    return " ".join(text.split())


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _material_title(material: str) -> str:
    for line in material.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    raise ReportError(["素材AWIにH1が無い"])


def validate(
    candidates: list[dict[str, str]], decisions: list[Any], analyses: dict[str, Any], sections: dict[str, Any]
) -> list[str]:
    """入力の不足を全件返す。空のリストは入力が契約を満たすことを示す。"""
    problems: list[str] = []
    kinds = {item["candidate_id"]: item["candidate_kind"] for item in candidates}
    seen: list[str] = []
    analysis_members: dict[str, list[tuple[str, str]]] = {}
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            problems.append(f"判定{index + 1}件目がJSON objectではない")
            continue
        candidate_id = decision.get("candidate_id")
        if candidate_id not in kinds:
            problems.append(f"素材AWIに無い候補IDの判定がある: {candidate_id}")
            continue
        if candidate_id in seen:
            problems.append(f"{candidate_id}: 判定が重複している")
            continue
        seen.append(candidate_id)
        kind = kinds[candidate_id]
        disposition = decision.get("disposition")
        if disposition == "excluded":
            if not _nonempty(decision.get("reason")):
                problems.append(f"{candidate_id}: 除外の根拠`reason`が無い")
            if kind == USER_INTERVENTION_KIND and decision.get("exclusion_category") not in USER_INTERVENTION_EXCLUSIONS:
                problems.append(
                    f"{candidate_id}: ユーザー介入の除外区分`exclusion_category`が"
                    f"{'、'.join(USER_INTERVENTION_EXCLUSIONS)}のいずれでもない"
                    "（「ユーザーの選好」「追加要件」「既存規範で扱える」は除外の根拠にならない）"
                )
        elif disposition == "analyzed":
            analysis_id = decision.get("analysis_id")
            defect = decision.get("defect")
            if not _nonempty(analysis_id):
                problems.append(f"{candidate_id}: 分析の識別子`analysis_id`が無い")
            elif analysis_id not in analyses:
                problems.append(f"{candidate_id}: 分析`{analysis_id}`が`--analyses`に無い")
            if defect not in {"欠陥", "非欠陥"}:
                problems.append(f"{candidate_id}: `defect`が`欠陥`又は`非欠陥`ではない")
            elif kind == USER_INTERVENTION_KIND and defect != "欠陥":
                problems.append(f"{candidate_id}: ユーザー介入を分析した候補は`欠陥`として再発防止策を対応付ける")
            if _nonempty(analysis_id) and isinstance(defect, str):
                analysis_members.setdefault(str(analysis_id), []).append((kind, defect))
        elif disposition == "pending":
            problems.append(f"{candidate_id}: 未判定のまま残っている")
        else:
            problems.append(f"{candidate_id}: `disposition`が`excluded`又は`analyzed`ではない")
    problems.extend(f"{candidate_id}: 判定が無い" for candidate_id in kinds if candidate_id not in seen)
    for analysis_id, members in analysis_members.items():
        analysis = analyses.get(analysis_id)
        if not isinstance(analysis, dict):
            continue
        problems.extend(
            f"分析{analysis_id}: `{field}`が無い" for field in ANALYSIS_TEXT_FIELDS if not _nonempty(analysis.get(field))
        )
        needs_prevention = any(defect == "欠陥" or kind == USER_INTERVENTION_KIND for kind, defect in members)
        problems.extend(_prevention_problems(analysis_id, analysis.get("prevention"), required=needs_prevention))
        artifacts = analysis.get("artifacts", [])
        if not isinstance(artifacts, list) or not all(_nonempty(item) for item in artifacts):
            problems.append(f"分析{analysis_id}: `artifacts`が文字列の配列ではない")
        elif any(defect == "欠陥" for _, defect in members) and not artifacts:
            problems.append(f"分析{analysis_id}: 欠陥の候補へ対応する変更した成果物`artifacts`が無い")
    unknown_sections = sorted(set(sections) - set(SECTION_KEYS))
    if unknown_sections:
        problems.append(f"受理しない節名がある: {', '.join(unknown_sections)}")
    problems.extend(f"節{key}: 本文が文字列ではない" for key, value in sections.items() if not isinstance(value, str))
    return problems


def _prevention_problems(analysis_id: str, prevention: Any, *, required: bool) -> list[str]:
    if prevention is None:
        prevention = []
    if not isinstance(prevention, list):
        return [f"分析{analysis_id}: `prevention`が配列ではない"]
    problems = [
        f"分析{analysis_id}: 再発防止策{index + 1}件目の`kind`・`ref`・`summary`が不正"
        for index, item in enumerate(prevention)
        if not isinstance(item, dict)
        or item.get("kind") not in PREVENTION_KINDS
        or not _nonempty(item.get("ref"))
        or not _nonempty(item.get("summary"))
    ]
    if required and not prevention:
        problems.append(
            f"分析{analysis_id}: 再発防止策の実体`prevention`が無い"
            f"（{'、'.join(PREVENTION_KINDS.values())}のいずれかを対応付ける）"
        )
    return problems


def render(material: str, decisions: list[dict[str, Any]], analyses: dict[str, Any], sections: dict[str, str]) -> str:
    """検査済みの入力から事後承認型UWIの質問本文を生成する。"""
    title = _material_title(material)
    by_analysis: dict[str, list[dict[str, Any]]] = {}
    skipped: list[dict[str, Any]] = []
    for decision in decisions:
        if decision["disposition"] == "analyzed" and decision["defect"] == "欠陥":
            by_analysis.setdefault(decision["analysis_id"], []).append(decision)
        else:
            skipped.append(decision)
    lines = [
        f"振り返り素材「{title}」の候補に対して実施した対策と、見送った候補の判断は、この内容で問題ありませんか？",
        "",
        "## 選択肢と帰結",
        "",
        f"- {CHOICES[0]}: 実施した対策と見送りの判断をそのまま維持し、この確認を終えます。",
        f"- {CHOICES[1]}: 回答欄へ、是正を望む候補と望む対応を書いてください。後続の処理で是正します。",
        "",
        "## 判断材料",
        "",
        f"候補{len(decisions)}件のうち、対策を実施した候補は{len(decisions) - len(skipped)}件、"
        f"見送った候補は{len(skipped)}件です。",
    ]
    for number, (analysis_id, members) in enumerate(by_analysis.items(), start=1):
        analysis = analyses[analysis_id]
        candidate_ids = "、".join(f"{item['candidate_id']}（{item['candidate_kind']}）" for item in members)
        lines.extend(
            [
                "",
                f"- 対策{number}: 候補{candidate_ids}",
                f"  - 問題: {_one_line(analysis['observation'])}",
                f"  - 原因: {_one_line(analysis['root_cause'])}",
                f"  - 対策: {_one_line(analysis['measures'])}",
            ]
        )
        lines.extend(
            f"  - 再発防止策: {_one_line(item['summary'])}（{PREVENTION_KINDS[item['kind']]}: {item['ref']}）"
            for item in analysis.get("prevention", [])
        )
        lines.append(f"  - 変更した成果物: {'、'.join(analysis.get('artifacts', []))}")
    if skipped:
        lines.extend(["", "見送った候補とその理由は次のとおりです。", ""])
        for decision in skipped:
            label = f"{decision['candidate_id']}（{decision['candidate_kind']}）"
            if decision["disposition"] == "excluded":
                category = USER_INTERVENTION_EXCLUSIONS.get(decision.get("exclusion_category", ""))
                prefix = f"{category}として除外" if category else "除外"
                lines.append(f"- {label}: {prefix}。{_one_line(decision['reason'])}")
            else:
                reason = _one_line(analyses[decision["analysis_id"]]["measures"])
                lines.append(f"- {label}: 欠陥ではないと判断。{reason}")
    for key in SECTION_KEYS:
        body = sections.get(key, "")
        if body.strip():
            lines.extend(["", f"{key}: {_one_line(body)}"])
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.epilog = _INPUT_STRUCTURE_HELP
    parser.add_argument("mode", choices=("generate", "check"))
    parser.add_argument("--material", type=pathlib.Path, required=True, help="振り返り素材AWIの本文ファイルの絶対パス")
    parser.add_argument("--decisions", type=pathlib.Path, required=True, help="候補ごとの判定を並べたJSONの絶対パス")
    parser.add_argument("--analyses", type=pathlib.Path, required=True, help="分析IDごとの原因と対策を並べたJSONの絶対パス")
    parser.add_argument("--sections", type=pathlib.Path, help="節ごとの本文を並べたJSONの絶対パス")
    parser.add_argument("--output", type=pathlib.Path, required=True, help="生成するUWI本文ファイルの絶対パス")
    return parser


def main(argv: list[str] | None = None) -> int:
    """UWI本文を生成するか、保存済み本文を同じ入力から照合する。

    入力の不足は全件を標準エラーへ示して終了コード1、入力ファイルの読み込み又はJSONの解析に失敗した場合は終了コード2とする。
    """
    args = _parser().parse_args(argv)
    try:
        material = args.material.read_text(encoding="utf-8")
        candidates = session_review_decisions.parse_material_candidates(material)
        decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
        analyses = json.loads(args.analyses.read_text(encoding="utf-8"))
        sections = json.loads(args.sections.read_text(encoding="utf-8")) if args.sections is not None else {}
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"入力の読み込みに失敗した: {error}", file=sys.stderr)
        return 2
    if not isinstance(decisions, list) or not isinstance(analyses, dict) or not isinstance(sections, dict):
        print("入力の型が不正: 判定は配列、分析と節はJSON objectとする", file=sys.stderr)
        return 2
    problems = validate(candidates, decisions, analyses, sections)
    if problems:
        print(f"入力が契約を満たさない（{len(problems)}件）:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    try:
        content = render(material, decisions, analyses, sections)
    except ReportError as error:
        print(error, file=sys.stderr)
        return 1
    if args.mode == "generate":
        args.output.write_text(content, encoding="utf-8")
        print(f"生成した: {args.output}")
        return 0
    if not args.output.is_file() or args.output.read_text(encoding="utf-8") != content:
        print(f"保存済み本文が同じ入力から生成した本文と一致しない: {args.output}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
