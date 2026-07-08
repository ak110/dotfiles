"""計画ファイルの構造検査の共通モジュール。

PreToolUseのWrite/Edit/MultiEditブロック判定と、PostToolUseの構造検査の両方で使う。
SSOTは`agent-toolkit/skills/plan-mode/references/plan-file-guidelines.md`の
「セクション構成と記述要件」節。
"""

import pathlib
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


# `## 変更内容 > ### 対象ファイル一覧` 配下のチェックボックス箇条書きから相対パスを抽出するパターン。
# `- [ ] path` および `- [x] path` 形式（大文字`X`も許容）を対象とする。
_CHECKBOX_PATTERN = re.compile(r"^\s*-\s+\[[ xX]\]\s+(.+)")

# チェックボックス項目本文の先頭がバッククォート囲みのパスである場合に、
# 後続の`（現行N行, 見込みM行）`等の付随メタ情報を除いてパス部分のみを取り出すパターン。
_LEADING_BACKTICK_PATH_PATTERN = re.compile(r"^`([^`]+)`")


def extract_h2_section_body(content: str, h2_heading: str) -> list[tuple[int, str]]:
    """指定したH2見出し配下の本文行を、ファイル先頭基準1始まりの行番号付きで返す。

    除外領域の定義は`iter_markdown_body_lines`に従う。
    対象H2見出しが存在しない場合は空リストを返す。
    対象H2見出し行自体は本文行に含めず、次のH2見出し行に達した時点で収集を終える。
    H3見出し行・箇条書き行を含む全ての非除外行を本文行として収集する。
    pretooluse / posttooluse の双方からimportして使うSSOT実装。
    """
    body: list[tuple[int, str]] = []
    in_target_h2 = False
    for lineno, line in iter_markdown_body_lines(content):
        if line.startswith("## "):
            in_target_h2 = line[3:].strip() == h2_heading
            continue
        if in_target_h2:
            body.append((lineno, line))
    return body


def extract_h3_headings_under_h2(content: str, h2_heading: str) -> list[str]:
    """指定したH2見出し配下に出現するH3見出しのテキストをリストで返す。

    除外領域の定義は`iter_markdown_body_lines`に従う。
    指定したH2が存在しない場合は空リストを返す。
    pretooluse / posttooluse の双方からimportして使うSSOT実装。
    """
    headings: list[str] = []
    in_target_h2 = False
    for _, line in iter_markdown_body_lines(content):
        if line.startswith("## "):
            in_target_h2 = line[3:].strip() == h2_heading
            continue
        if in_target_h2 and line.startswith("### "):
            headings.append(line[4:].strip())
    return headings


def iter_h3_sections_under_h2(content: str, h2_heading: str) -> Iterator[tuple[str, list[tuple[int, str]]]]:
    """指定したH2見出し配下のH3見出しごとに、(H3見出しテキスト, body行リスト)を生成する。

    body行はH3見出しの直後行から次のH3見出し行の直前までを、
    ファイル先頭基準1始まりの行番号付きで収集する。
    素朴に全行走査する（`## 変更内容`H2はフロントマターより後方の慣例のため十分）。
    コードフェンス内の行はスキップせず生body行として返す
    （呼び出し側でコードフェンス出現を判定できるようにするため）。
    指定H2の直下にH3が現れる前の本文行は無視する。
    pretooluse / posttooluse の双方からimportして使うSSOT実装。
    """
    lines = content.splitlines()
    in_target_h2 = False
    current_h3: str | None = None
    current_body: list[tuple[int, str]] = []
    for lineno, line in enumerate(lines, start=1):
        if line.startswith("## "):
            if current_h3 is not None:
                yield current_h3, current_body
                current_h3 = None
                current_body = []
            in_target_h2 = line[3:].strip() == h2_heading
            continue
        if not in_target_h2:
            continue
        if line.startswith("### "):
            if current_h3 is not None:
                yield current_h3, current_body
            current_h3 = line[4:].strip()
            current_body = []
            continue
        if current_h3 is not None:
            current_body.append((lineno, line))
    if current_h3 is not None:
        yield current_h3, current_body


def extract_target_files_from_changes(content: str) -> list[str]:
    """`## 変更内容 > ### 対象ファイル一覧`配下のチェックボックス箇条書きから相対パスを抽出する。

    パス記述の慣例（`` `path`（現行N行, 見込みM行） ``形式）に合わせ、
    前後の全角空白を含む空白除去後、先頭のバッククォート囲み区間のみをパスとして取り出す
    （後続の`（現行N行, 見込みM行）`等の付随メタ情報は除く）。
    バッククォート囲みでない場合は従来どおり前後のバッククォートのみを除去する。
    pretooluse / posttooluse の双方からimportして使うSSOT実装。
    """
    body = extract_h2_section_body(content, "変更内容")
    paths: list[str] = []
    in_target_h3 = False
    for _, line in body:
        if line.startswith("### "):
            in_target_h3 = line[4:].strip() == "対象ファイル一覧"
            continue
        if in_target_h3:
            m = _CHECKBOX_PATTERN.match(line)
            if not m:
                continue
            item = m.group(1).strip()
            path_match = _LEADING_BACKTICK_PATH_PATTERN.match(item)
            paths.append(path_match.group(1) if path_match else item.strip("`"))
    return paths


def is_agent_facing_md(rel_path: str) -> bool:
    """パス文字列がコーディングエージェント向けMarkdownの対象種別かを判定する。

    対象は拡張子`.md`のファイルのうち、次のいずれかに該当するもの。
    ルートの`AGENTS.md`・`CLAUDE.md`。パス部品に`rules`を含むもの
    （`agent-toolkit/rules/`・`.claude/rules/`・`.chezmoi-source/dot_claude/rules/`等）。
    末尾から3番目のパス部品が`skills`かつファイル名が`SKILL.md`のもの
    （`agent-toolkit/skills/<name>/SKILL.md`・`.claude/skills/<name>/SKILL.md`・
    `.chezmoi-source/dot_claude/skills/<name>/SKILL.md`等）。
    パス部品に`references`と`skills`の両方を含むもの。パス部品に`agents`を含むもの。
    パス部品の完全一致で判定し、部分文字列一致は行わない。
    pretooluse / posttooluse の双方からimportして使うSSOT実装。
    """
    p = pathlib.PurePosixPath(rel_path.replace("\\", "/"))
    parts = p.parts
    name = p.name
    if not name.endswith(".md"):
        return False
    if len(parts) == 1 and name in ("AGENTS.md", "CLAUDE.md"):
        return True
    if "rules" in parts[:-1]:
        return True
    if len(parts) >= 3 and parts[-3] == "skills" and name == "SKILL.md":
        return True
    if "references" in parts[:-1] and "skills" in parts[:-1]:
        return True
    return "agents" in parts[:-1]
