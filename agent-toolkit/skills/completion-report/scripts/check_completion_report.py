"""completion-reportが公開するMarkdownの構造を検証する。"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

REVIEW_STATES = ("success", "not-run", "failed")
ALLOWED_SKIP_REASONS = {
    "成果を再利用したため起動省略",
    "分析失敗のためUWIへ記録",
}


def _headings(text: str, level: int) -> list[str]:
    marker = "#" * level
    return re.findall(rf"(?m)^{marker} (.+)$", text)


def validate_report(text: str, review_state: str) -> list[str]:
    """報告本文を検証し、違反理由を返す。"""
    errors: list[str] = []
    h2 = _headings(text, 2)
    expected_h2 = ["作業完了報告", "振り返り結果報告"] if review_state == "success" else ["作業完了報告"]
    if h2 != expected_h2:
        errors.append(f"H2は次の順序にする: {', '.join(expected_h2)}")

    h3 = _headings(text, 3)
    expected_h3 = ["成果", "投入したWI"]
    if review_state == "success":
        expected_h3.append("対策として投入したWI")
    else:
        expected_h3.append("振り返り")
    if h3 != expected_h3:
        errors.append(f"H3は次の順序にする: {', '.join(expected_h3)}")

    if "### 投入したWI\n\n- " not in text:
        errors.append("### 投入したWIに1件以上の箇条書きを置く")
    if review_state == "success" and "### 対策として投入したWI\n\n- " not in text:
        errors.append("### 対策として投入したWIに1件以上の箇条書きを置く")

    skip_matches = re.findall(r"(?m)^- session-review未実施: (.+)$", text)
    if review_state == "success" and skip_matches:
        errors.append("振り返り成功時はsession-review未実施理由を書かない")
    if review_state != "success":
        if len(skip_matches) != 1:
            errors.append("振り返り未実施時はsession-review未実施理由を1件書く")
        elif not any(skip_matches[0].startswith(reason) for reason in ALLOWED_SKIP_REASONS):
            errors.append("session-review未実施理由が許可された理由ではない")

    if "成果ファイル:" in text:
        errors.append("回収済みの成果ファイルpathを最終報告へ書かない")
    return errors


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_file", type=pathlib.Path)
    parser.add_argument("--review-state", required=True, choices=REVIEW_STATES)
    args = parser.parse_args(argv)
    try:
        text = args.report_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"完了報告を読み取れません: {exc}", file=sys.stderr)
        return 2
    errors = validate_report(text, args.review_state)
    if errors:
        for error in errors:
            print(f"完了報告の構造違反: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
