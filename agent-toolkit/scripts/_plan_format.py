"""計画ファイルのH2節順検査の共通モジュール。

PreToolUseのWriteブロック判定とPostToolUseの警告出力で共有する。
SSOTは`agent-toolkit/skills/plan-mode/references/plan-file-guidelines.md`の
「セクション構成と記述要件」節。
"""

import re

PLAN_REQUIRED_H2: tuple[str, ...] = (
    "変更履歴",
    "背景",
    "対応方針",
    "調査結果",
    "変更内容",
    "実行方法",
    "進捗ログ",
    "計画ファイル（本ファイル）のパス",
)

_H2_PATTERN = re.compile(r"^## (.+?)\s*$", re.MULTILINE)


def extract_h2_sections(content: str) -> list[str]:
    """本文からH2見出しの一覧を抽出する。"""
    return _H2_PATTERN.findall(content)


def check_h2_order(content: str) -> list[str]:
    """H2節順違反を検査して違反メッセージの一覧を返す。"""
    headings = extract_h2_sections(content)
    allowed = set(PLAN_REQUIRED_H2)
    violations: list[str] = []

    unexpected = [h for h in headings if h not in allowed]
    if unexpected:
        violations.append(f"unexpected H2 sections: {unexpected}. Allowed: {list(PLAN_REQUIRED_H2)}.")

    missing = [h for h in PLAN_REQUIRED_H2 if h not in headings]
    if missing:
        violations.append(f"missing required H2 sections: {missing}.")

    present_required = [h for h in headings if h in allowed]
    expected_order = [h for h in PLAN_REQUIRED_H2 if h in headings]
    if present_required != expected_order:
        violations.append(f"required H2 sections are out of order. Expected: {expected_order}, but found: {present_required}.")

    return violations
