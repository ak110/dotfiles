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
        stripped = line.lstrip()
        fence_match = _FENCE_PATTERN.match(stripped)
        if fence_match:
            candidate = fence_match.group(1)
            if fence_marker is None:
                # 開きフェンス: infoストリング許容
                fence_marker = candidate
                continue
            if stripped.startswith(fence_marker) and not stripped[len(fence_marker) :].strip():
                # 閉じフェンス: 開きと同じ字種・同等以上の長さ・後続は空白のみ
                fence_marker = None
                continue
            # fence_markerと異なる字種のフェンスはフェンス内テキスト扱い
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
