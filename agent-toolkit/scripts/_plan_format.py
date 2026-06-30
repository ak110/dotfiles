"""計画ファイルのH2節順検査の共通モジュール。

PreToolUseのWrite/Edit/MultiEditブロック判定で使う。
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

_FENCE_PATTERN = re.compile(r"^(`{3,}|~{3,})")
_H2_PATTERN = re.compile(r"^## (.+?)\s*$")


def extract_h2_sections(content: str) -> list[str]:
    """本文からH2見出しの一覧を抽出する（コードフェンス内は除外する）。"""
    headings: list[str] = []
    fence_marker: str | None = None
    for line in content.splitlines():
        fence_match = _FENCE_PATTERN.match(line.lstrip())
        if fence_match:
            if fence_marker is None:
                fence_marker = fence_match.group(1)
            elif line.lstrip().startswith(fence_marker):
                fence_marker = None
            continue
        if fence_marker is not None:
            continue
        m = _H2_PATTERN.match(line)
        if m:
            headings.append(m.group(1))
    return headings


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
