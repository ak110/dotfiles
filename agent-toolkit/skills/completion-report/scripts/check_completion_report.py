"""completion-reportが公開するMarkdownの構造を検証する。"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

from agent_toolkit._common.markdown_headings import top_level_atx_headings

STAGES = ("work-complete", "review-result")
REVIEW_STATES = ("success", "not-run", "failed")
REVIEW_SUMMARY_PREFIXES = ("- 候補: ", "- 所要時間: ")
"""振り返りが正常完了した報告が持つ要約行の接頭辞。値は準備スクリプトの出力から転記する。"""
REVIEW_SUCCESS_SECTIONS = ("確定した問題と対策", "対策を見送った問題")
"""振り返りが正常完了した報告のH3。確定した問題ごとの対策と、対策を見送った問題とその理由を読めるようにする。"""
_WI_FILENAME = re.compile(r"\b\d{8}-\d{6}-\d{3}\.md\b")
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


def validate_report(text: str, stage: str, review_state: str | None = None) -> list[str]:
    """報告本文を検証し、違反理由を返す。"""
    errors: list[str] = []
    if stage not in STAGES:
        return [f"検査段階が不正である: {stage}"]
    if stage == "work-complete" and review_state is not None:
        errors.append("作業完了段階ではreview-stateを指定しない")
    if stage == "review-result" and review_state not in REVIEW_STATES:
        errors.append("振り返り結果段階ではreview-stateを指定する")

    h2 = _headings(text, 2)
    expected_h2 = ["作業完了報告"] if stage == "work-complete" else ["振り返り結果報告"]
    if h2 != expected_h2:
        errors.append(f"H2は次の順序にする: {', '.join(expected_h2)}")

    h3 = _headings(text, 3)
    if stage == "work-complete":
        expected_h3 = ["成果", "投入したWI"]
    elif review_state == "success":
        expected_h3 = list(REVIEW_SUCCESS_SECTIONS)
    else:
        expected_h3 = ["振り返り"]
    if h3 != expected_h3:
        errors.append(f"H3は次の順序にする: {', '.join(expected_h3)}")

    if stage == "work-complete" and "### 投入したWI\n\n- " not in text:
        errors.append("### 投入したWIに1件以上の箇条書きを置く")
    if stage == "review-result" and review_state == "success":
        for title in REVIEW_SUCCESS_SECTIONS:
            items = _section_items(text, title)
            if not items:
                errors.append(f"### {title}に1件以上の箇条書きを置く（該当なしは`- なし`）")
            elif title == REVIEW_SUCCESS_SECTIONS[0]:
                errors.extend(
                    f"### {title}の各行へ対策として投入したAWIのファイル名を書く: {item}"
                    for item in items
                    if item != "- なし" and not _WI_FILENAME.search(item)
                )

    if stage == "review-result" and review_state == "success":
        lines = text.splitlines()
        errors.extend(
            f"振り返り成功時は`{prefix.strip()}`で始まる要約行を1件置く"
            for prefix in REVIEW_SUMMARY_PREFIXES
            if sum(1 for line in lines if line.startswith(prefix)) != 1
        )

    skip_matches = re.findall(r"(?m)^- session-review未実施: (.+)$", text)
    if stage == "work-complete" and skip_matches:
        errors.append("作業完了段階にはsession-review未実施理由を書かない")
    elif review_state == "success" and skip_matches:
        errors.append("振り返り成功時はsession-review未実施理由を書かない")
    elif stage == "review-result" and review_state in SKIP_REASONS:
        if len(skip_matches) != 1:
            errors.append("振り返り未実施時はsession-review未実施理由を1件書く")
        elif not skip_matches[0].startswith(SKIP_REASONS[review_state]):
            errors.append("session-review未実施理由がreview-stateに対応していない")

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
        print(f"完了報告を読み取れません: {exc}", file=sys.stderr)
        return 2
    errors = validate_report(text, args.stage, args.review_state)
    if errors:
        for error in errors:
            print(f"完了報告の構造違反: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
