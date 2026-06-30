"""計画ファイルの構造検査の共通モジュール。

PreToolUseのWrite/Edit/MultiEditブロック判定と、PostToolUseの構造検査の両方で使う。
SSOTは`agent-toolkit/skills/plan-mode/references/plan-file-guidelines.md`の
「セクション構成と記述要件」節。
"""

import re
from collections.abc import Iterator

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
    """本文からH2見出しの一覧を抽出する（コードフェンス内は除外する）。

    フェンス閉じ判定は同字種かつ開始長以上で閉じる方式（CommonMark準拠）に揃え、
    `iter_markdown_body_lines`と同一仕様で動作する。
    """
    headings: list[str] = []
    fence_marker: str | None = None
    for line in content.splitlines():
        stripped = line.strip()
        fence_match = _FENCE_PATTERN.match(stripped)
        if fence_match:
            candidate = fence_match.group(1)
            if fence_marker is None:
                # 開きフェンス: infoストリング許容
                fence_marker = candidate
                continue
            if (
                stripped
                and stripped[0] == fence_marker[0]
                and len(stripped) >= len(fence_marker)
                and set(stripped) == {fence_marker[0]}
            ):
                # 閉じフェンス: 同字種・開始長以上・他字種を含まない
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


def iter_markdown_body_lines(content: str) -> Iterator[tuple[int, str]]:
    """Markdown本文の有効行を、ファイル先頭基準1始まりの行番号付きで順に生成する。

    以下の領域内の行は生成対象外とする（行番号もスキップされる）。

    - ファイル先頭のYAMLフロントマター（`---`または`...`で閉じる）
    - コードフェンス（開きフェンスと同字種・同長以上の閉じフェンスで抜ける）。
      開始・終了行自体も生成対象外
    - 複数行にまたがるHTMLコメント（`<!--`から`-->`まで）

    H2見出し・H3見出し・箇条書き行を含む全ての非除外行を生成する。
    H2/H3抽出や本文収集など、上記領域を共通除外する各種スキャン処理の基盤として使う。
    pretooluse / posttooluse の双方からimportして使うSSOT実装。
    """
    lines = content.splitlines()
    i = 0
    # フロントマター: 1 行目が `---` のときのみ検出対象とする（途中の `---` は区切り線）
    if lines and lines[0].rstrip() == "---":
        i = 1
        while i < len(lines):
            if lines[i].rstrip() in ("---", "..."):
                i += 1
                break
            i += 1

    fence_marker: str | None = None  # 開きフェンスのマーカー文字列（同字種・同長以上で閉じる）
    in_html_comment = False
    while i < len(lines):
        lineno = i + 1
        line = lines[i]
        i += 1
        if in_html_comment:
            # 閉じタグ到達行は `-->` 以降を解析せず丸ごとスキップする（素朴な実装）
            if "-->" in line:
                in_html_comment = False
            continue
        if fence_marker is not None:
            stripped = line.strip()
            if (
                stripped
                and stripped[0] == fence_marker[0]
                and len(stripped) >= len(fence_marker)
                and set(stripped) == {fence_marker[0]}
            ):
                fence_marker = None
            continue
        fence_match = _FENCE_PATTERN.match(line.lstrip())
        if fence_match:
            fence_marker = fence_match.group(1)
            continue
        if "<!--" in line and "-->" not in line.split("<!--", 1)[1]:
            in_html_comment = True
            continue
        yield lineno, line
