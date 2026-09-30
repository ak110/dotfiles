"""completion-reportが公開するMarkdownの構造を検証する。"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.markdown_headings import top_level_atx_headings

STAGES = ("work-complete", "review-result", "review-submission")
REVIEW_STATES = ("success", "not-run", "failed")
REVIEW_SUMMARY_PREFIXES = ("- 候補: ", "- 所要時間: ", "- 改善見込み: ")
"""振り返りが正常完了した報告が持つ要約行の接頭辞。値は準備スクリプトの出力から転記する。"""
REVIEW_SUCCESS_SECTIONS = ("確定した問題と対策", "対策を見送った問題")
"""振り返りが正常完了した報告のH3。確定した問題ごとの対策と、対策を見送った問題とその理由を読めるようにする。"""
_WI_FILENAME = re.compile(r"\b\d{8}-\d{6}-\d{3}\.md\b")
_IMPLEMENTED_EVIDENCE = re.compile(r"（同一セッションで実装済み:\s*[^）\s][^）]*）")
_SCHEDULED_MARKER = "投入予定:"
_SCHEDULED_EVIDENCE = re.compile(r"（投入予定:\s*[^）\s][^）]*）")
SUBMISSION_SECTIONS = ("投入したAWI", "予告との差分")
"""投入後の最終報告のH3。予告どおりなら`投入したAWI`だけを置き、予告と異なる結果があれば差分を続ける。"""
_UNRESEARCHED = ("照会していない", "未照会", "未調査", "確定できない")
SKIP_REASONS = {
    "not-run": "成果を再利用したため起動省略",
    "failed": "分析失敗のため欠陥AWIへ記録",
}


def _headings(text: str, level: int) -> list[str]:
    return [title for _, title in top_level_atx_headings(text, level)]


def _section_items(text: str, title: str) -> list[str]:
    """指定したH3の直下にある箇条書きの行を返す。"""
    items: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("#"):
            inside = line == f"### {title}"
            continue
        if inside and line.startswith("- "):
            items.append(line)
    return items


def _has_measure_evidence(item: str) -> bool:
    if "同一セッションで実装済み:" in item:
        return _IMPLEMENTED_EVIDENCE.search(item) is not None
    if _SCHEDULED_MARKER in item:
        return _SCHEDULED_EVIDENCE.search(item) is not None
    return _WI_FILENAME.search(item) is not None


def _validate_submission(text: str) -> list[str]:
    """投入後の最終報告（`## AWI投入結果報告`）の構造を検証する。"""
    errors: list[str] = []
    submitted = _section_items(text, SUBMISSION_SECTIONS[0])
    if not submitted:
        errors.append(f"### {SUBMISSION_SECTIONS[0]}に1件以上の箇条書きを置く（新たな投入が無い場合は`- なし`）")
    elif "- なし" in submitted and len(submitted) != 1:
        errors.append(f"### {SUBMISSION_SECTIONS[0]}の`- なし`は他の行と併記しない")
    else:
        errors.extend(
            f"### {SUBMISSION_SECTIONS[0]}の各行へWIのファイル名を書く: {item}"
            for item in submitted
            if item != "- なし" and _WI_FILENAME.search(item) is None
        )
    if SUBMISSION_SECTIONS[1] in _headings(text, 3):
        differences = [item for item in _section_items(text, SUBMISSION_SECTIONS[1]) if item != "- なし"]
        if not differences:
            errors.append(f"### {SUBMISSION_SECTIONS[1]}は予告と異なる結果がある場合だけ置き、その結果を箇条書きで書く")
    if _SCHEDULED_MARKER in text:
        errors.append("最終報告には予告の`投入予定`を残さず、投入したファイル名で書く")
    return errors


def validate_report(text: str, stage: str, review_state: str | None = None) -> list[str]:
    """報告本文を検証し、違反理由を返す。"""
    errors: list[str] = []
    if stage not in STAGES:
        return [f"検査段階が不正である: {stage}（受理する値: {', '.join(STAGES)}）"]
    if stage in ("work-complete", "review-submission") and review_state is not None:
        errors.append(f"{stage}段階ではreview-stateを指定しない")
    if stage == "review-result" and review_state not in REVIEW_STATES:
        errors.append("振り返り結果段階ではreview-stateを指定する")

    h2 = _headings(text, 2)
    expected_h2 = {"work-complete": ["作業完了報告"], "review-submission": ["AWI投入結果報告"]}.get(stage, ["振り返り結果報告"])
    if h2 != expected_h2:
        errors.append(f"H2は次の順序にする: {', '.join(expected_h2)}")

    h3 = _headings(text, 3)
    if stage == "review-submission":
        allowed_h3 = [list(SUBMISSION_SECTIONS[:1]), list(SUBMISSION_SECTIONS)]
    elif stage == "work-complete":
        allowed_h3 = [["成果", "投入したWI"]]
    elif review_state == "success":
        allowed_h3 = [list(REVIEW_SUCCESS_SECTIONS)]
    else:
        allowed_h3 = [["振り返り"]]
    if h3 not in allowed_h3:
        errors.append("H3は次の順序にする: " + "、または".join(", ".join(order) for order in allowed_h3))
    if stage == "review-submission":
        errors.extend(_validate_submission(text))

    if stage == "work-complete" and "### 投入したWI\n\n- " not in text:
        errors.append("### 投入したWIに1件以上の箇条書きを置く")
    if stage == "review-result" and review_state == "success":
        for title in REVIEW_SUCCESS_SECTIONS:
            items = _section_items(text, title)
            if not items:
                errors.append(f"### {title}に1件以上の箇条書きを置く（該当なしは`- なし`）")
            elif title == REVIEW_SUCCESS_SECTIONS[0]:
                errors.extend(
                    f"### {title}の各行へ対策として投入したAWIのファイル名、投入予定のH1タイトル"
                    f"または同一セッションで実装済みの根拠を書く: {item}"
                    for item in items
                    if item != "- なし" and not _has_measure_evidence(item)
                )
            else:
                if "- なし" in items and len(items) != 1:
                    errors.append("### 対策を見送った問題の`- なし`は他の行と併記しない")
                for item in items:
                    if item == "- なし":
                        continue
                    if item.startswith("- 判定済み: "):
                        parts = item.removeprefix("- 判定済み: ").split("; 根拠: ", maxsplit=1)
                        if len(parts) != 2 or not all(part.strip() for part in parts):
                            errors.append(f"判定済み行に問題と根拠を書く: {item}")
                        elif any(word in parts[1] for word in _UNRESEARCHED):
                            errors.append(f"未照会や未確定を判定済みの根拠にしない: {item}")
                    elif item.startswith("- 未確定: "):
                        fields = re.fullmatch(r"- 未確定: (.+?); 照会: (.+?); 再現: (.+?); 残る理由: (.+)", item)
                        if fields is None or not all(value.strip() for value in fields.groups()):
                            errors.append(f"未確定行に照会、再現、残る理由を書く: {item}")
                    else:
                        errors.append(f"対策を見送った問題の行形式が不正である: {item}")

    if stage == "review-result" and review_state == "success":
        lines = text.splitlines()
        errors.extend(
            f"振り返り成功時は`{prefix.strip()}`で始まる要約行を1件置く"
            for prefix in REVIEW_SUMMARY_PREFIXES
            if sum(1 for line in lines if line.startswith(prefix)) != 1
        )

    skip_matches = re.findall(r"(?m)^- session-review未実施: (.+)$", text)
    if stage in ("work-complete", "review-submission") and skip_matches:
        errors.append(f"{stage}段階にはsession-review未実施理由を書かない")
    elif review_state == "success" and skip_matches:
        errors.append("振り返り成功時はsession-review未実施理由を書かない")
    elif stage == "review-result" and review_state in SKIP_REASONS:
        if len(skip_matches) != 1:
            errors.append("振り返り未実施時はsession-review未実施理由を1件書く")
        elif not skip_matches[0].startswith(SKIP_REASONS[review_state]):
            errors.append(
                "session-review未実施理由がreview-stateに対応していない"
                f"（`{review_state}`では`- session-review未実施: {SKIP_REASONS[review_state]}`で始める）"
            )

    if "成果ファイル:" in text:
        errors.append("回収済みの成果ファイルpathを最終報告へ書かない")
    return errors


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_file", type=pathlib.Path)
    parser.add_argument("--stage", required=True, choices=STAGES)
    parser.add_argument("--review-state", choices=REVIEW_STATES)
    args = parser.parse_args(argv)
    try:
        text = args.report_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _next_action.report(
            f"完了報告を読み取れません: {exc}", next_action="UTF-8で保存した完了報告ファイルのパスを渡して再実行する"
        )
        return 2
    errors = validate_report(text, args.stage, args.review_state)
    if errors:
        for error in errors:
            print(f"完了報告の構造違反: {error}", file=sys.stderr)
        print(
            _next_action.next_action_line("各行が示す形へ完了報告を直し、同じコマンドで再検査する"),
            file=sys.stderr,
        )
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
