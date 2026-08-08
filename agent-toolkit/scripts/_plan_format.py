"""計画ファイルの構造検査の共通モジュール。

構造検査（`check_plan_file.py`）、フィードバック登録（`_atk_mq_add.py`）、
2系統のPreToolUse（`pretooluse.py`・`scripts/claude_hook_pretooluse.py`）、
PostToolUse（`posttooluse.py`）が本モジュールから同じ判定結果を得る。
SSOTは`agent-toolkit/skills/plan-mode/SKILL.md`の「計画ファイルの完成条件」節。
"""

import functools
import pathlib
import re
from collections.abc import Iterator
from dataclasses import dataclass

import markdown_it
import markdown_it.common.html_re
import markdown_it.rules_inline
import markdown_it.token

PLAN_H2_HISTORY: str = "変更履歴"
PLAN_H2_PURPOSE: str = "目的"
PLAN_H2_BUG: str = "バグ調査結果"
PLAN_H2_POLICY: str = "対応方針"
PLAN_H2_PROGRESS: str = "進捗ログ"

PLAN_PURPOSE_H3: tuple[str, ...] = ("概要", "計画メタ情報", "提示素材", "ユーザー合意済み事項")
"""`## 目的`直下に固定順で置くH3。"""

PLAN_POLICY_H3: tuple[str, ...] = ("実施内容", "恒久化・リファクタリング内容")
"""`## 対応方針`直下に固定順で置くH3。"""

PLAN_PERMANENCE_H4: tuple[str, ...] = ("恒久化", "リファクタリング", "類似見直し")
"""`### 恒久化・リファクタリング内容`直下に固定順で置くH4。"""

PLAN_METADATA_H3: str = "計画メタ情報"
PLAN_TARGET_LIST_H3: str = "対象ファイル一覧"

PLAN_METADATA_FIELDS: tuple[str, ...] = ("起動経路", "対象リポジトリ", "作業種別", "ベースコミット")
"""計画メタ情報の正規形が持つ項目と順序。"""

PLAN_METADATA_QUOTED_FIELDS: frozenset[str] = frozenset({"起動経路", "対象リポジトリ", "ベースコミット"})
"""値をバッククォートで囲む項目。`作業種別`だけは固定値を裸で書く。"""

PLAN_WORK_TYPES: tuple[str, ...] = ("バグ対応", "通常変更")

PLAN_METADATA_FALLBACK_H2: tuple[str, ...] = ("実装契約", "背景")
"""正規配置を持たない既存計画で計画メタ情報を読み取る旧配置。読み取り専用の互換経路とする。"""

PLAN_HISTORY_TABLE_HEADER: tuple[str, ...] = ("ID", "起点", "採否・現在の結論", "同期先")
PLAN_AGREEMENT_TABLE_HEADER: tuple[str, ...] = ("合意事項", "適用範囲", "原文参照")
PLAN_ACTION_TABLE_HEADER: tuple[str, ...] = ("実施内容", "ユーザー指示との関係", "根拠")
PLAN_ACTION_RELATIONS: tuple[str, ...] = ("指示どおり", "具体化", "エージェント追加")

PLAN_BUG_TABLE_HEADER: tuple[str, ...] = ("項目", "内容")
PLAN_BUG_TABLE_ROWS: tuple[str, ...] = (
    "観測事象",
    "期待する契約",
    "直接的原因",
    "混入要因",
    "動機的要因",
    "見逃し原因",
    "根本原因",
    "原因分析の根拠",
    "類似見直しの観点",
    "類似見直し結果",
    "是正処置",
    "横展開処置",
    "再発防止処置",
    "設計意図の記録",
)
"""バグ調査表の固定14行。行名と順序を`agent-toolkit:bugfix`の原因分析契約と対応させる。"""

PLAN_PERMANENCE_TABLE_ROWS: tuple[str, ...] = ("観測事象", "根本原因", "反映先", "反映内容", "対策強度")
"""通常変更の恒久化表の固定5行。バグ対応はバグ調査表の14行を正本とする。"""

PLAN_REFACTORING_TABLE_ROWS: tuple[str, ...] = ("対象", "現状の問題", "対応", "本計画に含めるか")
PLAN_SIMILAR_REVIEW_TABLE_ROWS: tuple[str, ...] = ("母集団", "点検観点", "該当件数と箇所")
"""通常変更の類似見直し表の固定3行。バグ対応はバグ調査表の14行を正本とする。"""

PLAN_PLACEHOLDER_WORDS: frozenset[str] = frozenset({"なし", "不要", "該当なし", "特になし"})
"""検討結果として成立しない結論語。これだけの記載は検討の省略として拒否する。"""

PLUGIN_MANIFEST_PATH: str = "agent-toolkit/.claude-plugin/plugin.json"
"""`scripts/agent_toolkit_bump.py`が更新するagent-toolkitプラグインmanifestの相対パス。"""

MARKETPLACE_MANIFEST_PATH: str = ".claude-plugin/marketplace.json"
"""`scripts/agent_toolkit_bump.py`が更新するmarketplace manifestの相対パス。"""

BUMP_MANIFEST_PATHS: frozenset[str] = frozenset({PLUGIN_MANIFEST_PATH, MARKETPLACE_MANIFEST_PATH})
"""`scripts/agent_toolkit_bump.py`が更新するmanifestファイルの相対パス集合。

`agent_toolkit_bump.py`側のリテラルとの一致は`scripts/agent_toolkit_bump_test.py`が検証する。
"""


def markdown_body_start_index(content: str) -> int:
    """先頭フロントマターの直後にあるMarkdown本文の0始まり行番号を返す。"""
    lines = content.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return 0
    for index, line in enumerate(lines[1:], start=1):
        if line.rstrip() in ("---", "..."):
            return index + 1
    return len(lines)


def _is_escaped(text: str, index: int) -> bool:
    """指定位置の文字が直前のバックスラッシュでエスケープされているかを返す。"""
    backslashes = 0
    while index > backslashes and text[index - backslashes - 1] == "\\":
        backslashes += 1
    return backslashes % 2 == 1


def _code_span_comment_starts(content: str, inline_tokens: list[markdown_it.token.Token]) -> set[int]:
    """各inline blockのコードスパン内にあるHTMLコメント開始位置を返す。"""
    parser = markdown_it.MarkdownIt("commonmark")
    spans: list[tuple[int, int]] = []

    def record_backtick(state: markdown_it.rules_inline.StateInline, silent: bool) -> bool:
        start = state.pos
        token_count = len(state.tokens)
        matched = markdown_it.rules_inline.backtick(state, silent)
        if matched and not silent and len(state.tokens) > token_count and state.tokens[-1].type == "code_inline":
            spans.append((start, state.pos))
        return matched

    parser.inline.ruler.at("backticks", record_backtick)

    def protected_markers(source: str) -> list[bool]:
        spans.clear()
        parser.parseInline(source)
        markers: list[bool] = []
        cursor = 0
        while (marker := source.find("<!--", cursor)) >= 0:
            markers.append(any(start <= marker < end for start, end in spans))
            cursor = marker + len("<!--")
        return markers

    lines = content.splitlines(keepends=True)
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))

    tokens_by_line_range: dict[tuple[int, int], list[markdown_it.token.Token]] = {}
    for token in inline_tokens:
        assert token.map is not None
        tokens_by_line_range.setdefault((token.map[0], token.map[1]), []).append(token)

    protected: set[int] = set()
    for (start_line, end_line), block_tokens in tokens_by_line_range.items():
        source_start = line_offsets[start_line]
        source_end = line_offsets[end_line]
        source = content[source_start:source_end]
        if len(block_tokens) == 1:
            marker_flags = protected_markers(source)
        else:
            marker_flags = []
            for token in block_tokens:
                marker_flags.extend(protected_markers(token.content))

        cursor = 0
        marker_index = 0
        while (marker := source.find("<!--", cursor)) >= 0:
            if marker_index >= len(marker_flags) or marker_flags[marker_index]:
                protected.add(source_start + marker)
            marker_index += 1
            cursor = marker + len("<!--")
    return protected


def _markdown_excluded_line_indices(content: str) -> set[int]:
    """CommonMarkのフェンスと複数行HTMLコメントに属する0始まり行番号を返す。"""
    excluded: set[int] = set()
    inline_tokens: list[markdown_it.token.Token] = []
    parser = markdown_it.MarkdownIt("commonmark").enable("table")
    for token in parser.parse(content):
        if token.type == "fence" and token.map is not None:
            start, end = token.map
            excluded.update(range(start, end))
        elif token.type == "html_block" and token.map is not None and token.content.lstrip().startswith("<!--"):
            start, end = token.map
            if end - start > 1:
                excluded.update(range(start, end))
        elif token.type == "inline" and token.map is not None:
            inline_tokens.append(token)

    code_span_comment_starts = _code_span_comment_starts(content, inline_tokens)
    cursor = 0
    while (comment_start := content.find("<!--", cursor)) >= 0:
        start_line = content.count("\n", 0, comment_start)
        if start_line in excluded or _is_escaped(content, comment_start) or comment_start in code_span_comment_starts:
            cursor = comment_start + len("<!--")
            continue
        match = markdown_it.common.html_re.HTML_TAG_RE.match(content[comment_start:])
        if match is None or not match.group().startswith("<!--"):
            cursor = comment_start + len("<!--")
            continue
        comment_end = comment_start + len(match.group())
        end_line = content.count("\n", 0, comment_end - 1)
        if end_line > start_line:
            excluded.update(range(start_line, end_line + 1))
        cursor = comment_end
    return excluded


def iter_markdown_body_lines(content: str) -> Iterator[tuple[int, str]]:
    """Markdown本文の有効行を、ファイル先頭基準1始まりの行番号付きで順に生成する。

    以下の領域内の行は生成対象外とする（行番号もスキップされる）。

    - ファイル先頭のYAMLフロントマター（`---`または`...`で閉じる）
    - コードフェンス（開きフェンスと同字種・同長以上の閉じフェンスで抜ける）。
      開始・終了行自体も生成対象外
    - 複数行にまたがるHTMLコメント（`<!--`から`-->`まで）

    H2見出し・H3見出し・箇条書き行を含む全ての非除外行を生成する。
    見出し抽出や本文収集など、上記領域を共通除外する各種スキャン処理の基盤として使う。
    """
    lines = content.splitlines()
    body_start = markdown_body_start_index(content)
    body = "\n".join(lines[body_start:])
    excluded = _markdown_excluded_line_indices(body)
    for body_index, line in enumerate(lines[body_start:]):
        if body_index not in excluded:
            yield body_start + body_index + 1, line


_TARGET_PATTERN = re.compile(r"^- `(?P<path>[^`]+)`(?:（(?P<state>新設|削除)）)?\s*$")
_TARGET_BULLET_PATTERN = re.compile(r"^\s*[-*+]\s+(?P<body>.+?)\s*$")
_TARGET_CHECKBOX_PATTERN = re.compile(r"^\[[ xX]\]\s+")
_TARGET_STATE_PATTERN = re.compile(r"（(?:新設|削除)[^）]*）")


@dataclass(frozen=True)
class PlanTarget:
    """実装者向け領域の`### 対象ファイル一覧`に記載された対象パスと基準コミット上の状態を表す。"""

    path: str
    state: str = "existing"


def extract_h2_section_body(content: str, h2_heading: str) -> list[tuple[int, str]]:
    """指定したH2見出し配下の本文行を、ファイル先頭基準1始まりの行番号付きで返す。

    除外領域の定義は`iter_markdown_body_lines`に従う。
    対象H2見出しが存在しない場合は空リストを返す。
    対象H2見出し行自体は本文行に含めず、次のH2見出し行に達した時点で収集を終える。
    H3見出し行・箇条書き行を含む全ての非除外行を本文行として収集する。
    `extract_implementer_region`が既存計画の`## 実装契約`を読み取る互換経路で使う。
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


@dataclass(frozen=True)
class PlanHeading:
    """Markdown本文で有効な見出し1件を表す。"""

    lineno: int
    level: int
    text: str


_HEADING_PATTERN = re.compile(r"^(#{1,6}) +(.*?)\s*$")


def extract_headings(content: str) -> list[PlanHeading]:
    """本文の有効行から全階層の見出しを出現順に抽出する。

    除外領域の定義は`iter_markdown_body_lines`に従う。
    """
    headings: list[PlanHeading] = []
    for lineno, line in iter_markdown_body_lines(content):
        match = _HEADING_PATTERN.match(line)
        if match is not None:
            headings.append(PlanHeading(lineno, len(match.group(1)), match.group(2)))
    return headings


def heading_subtree_range(headings: list[PlanHeading], index: int) -> tuple[int, int | None]:
    """指定見出しの本文範囲を(見出し行番号, 次の同位以上の見出し行番号)で返す。

    末尾まで続く場合は第2要素を`None`とする。
    """
    level = headings[index].level
    for following in headings[index + 1 :]:
        if following.level <= level:
            return headings[index].lineno, following.lineno
    return headings[index].lineno, None


def lines_within(lines: list[tuple[int, str]], start: int, end: int | None) -> list[tuple[int, str]]:
    """行番号付き行列から`start`超過かつ`end`未満の範囲を切り出す。"""
    return [(lineno, line) for lineno, line in lines if lineno > start and (end is None or lineno < end)]


def child_headings(headings: list[PlanHeading], index: int, level: int) -> list[tuple[int, PlanHeading]]:
    """指定見出しの本文範囲にある指定階層の見出しを(索引, 見出し)で返す。"""
    start, end = heading_subtree_range(headings, index)
    return [
        (position, heading)
        for position, heading in enumerate(headings)
        if heading.level == level and heading.lineno > start and (end is None or heading.lineno < end)
    ]


def find_heading_index(headings: list[PlanHeading], level: int, text: str) -> int | None:
    """指定階層・指定見出し文の最初の索引を返す。存在しない場合は`None`を返す。"""
    return next((index for index, heading in enumerate(headings) if heading.level == level and heading.text == text), None)


@dataclass(frozen=True)
class MarkdownTable:
    """パイプ表1件の見出し行と本文行を表す。"""

    lineno: int
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def row_labels(self) -> tuple[str, ...]:
        """各行の第1列を返す。"""
        return tuple(row[0] if row else "" for row in self.rows)


@functools.cache
def _table_parser() -> markdown_it.MarkdownIt:
    """GFM表を解釈するMarkdownパーサーを返す。

    パーサーは解析ごとの状態を持たないため、生成コストを避けて使い回す。
    """
    return markdown_it.MarkdownIt("commonmark").enable("table")


def _table_row_cells(line: str) -> tuple[str, ...]:
    """表の1行を、その行が実際に持つ列数のままセルへ分割する。

    GFMのbody行はheaderの列数へ切り詰められて解釈されるため、
    行単体をheader行として解析し直し、列数不一致を後段で検出できるようにする。
    区切り行の列数はheader行の列数と一致した場合だけ表として解釈される性質を使い、
    パイプの数から上限を定めて一致する列数を探す。
    """
    parser = _table_parser()
    for count in range(1, line.count("|") + 2):
        delimiter = "|" + "|".join(["---"] * count) + "|"
        tokens = parser.parse(f"{line}\n{delimiter}\n")
        if not any(token.type == "table_open" for token in tokens):
            continue
        return tuple(token.content.strip() for token in tokens if token.type == "inline")
    return (line.strip(),)


def extract_tables(lines: list[tuple[int, str]]) -> list[MarkdownTable]:
    """行番号付き本文行からGFMの表を出現順に抽出する。

    表の境界判定とセル分割はmarkdown-it-pyのtable拡張へ委ね、
    区切り行のダッシュ数、整列コロン、行頭パイプの省略といった記法差を吸収する。
    行番号は入力行の並びから復元し、ファイル先頭基準1始まりで返す。
    """
    source_lines = [line for _lineno, line in lines]
    tables: list[MarkdownTable] = []
    lineno = 0
    header: tuple[str, ...] = ()
    rows: list[tuple[str, ...]] = []
    in_body = False
    for token in _table_parser().parse("\n".join(source_lines)):
        if token.type == "table_open":
            assert token.map is not None
            lineno = lines[token.map[0]][0]
            header, rows, in_body = (), [], False
        elif token.type == "thead_open":
            assert token.map is not None
            header = _table_row_cells(source_lines[token.map[0]])
        elif token.type == "tbody_open":
            in_body = True
        elif token.type == "tr_open" and in_body:
            assert token.map is not None
            rows.append(_table_row_cells(source_lines[token.map[0]]))
        elif token.type == "table_close":
            tables.append(MarkdownTable(lineno, header, tuple(rows)))
    return tables


@dataclass(frozen=True)
class PlanMetadata:
    """計画メタ情報の解析結果を表す。"""

    parent: str
    """メタ情報を収めていた親H2の見出し文。正規形では`目的`となる。"""

    entries: tuple[tuple[str, str], ...]
    """記載順の(項目名, 生の値)。順序と記法の検査に使う。"""

    values: dict[str, str]
    """正規4項目の値。バッククォートは除去済みで、欠落項目は含めない。"""

    base_commit_candidates: tuple[str, ...]
    """`ベースコミット`と旧別名`基準コミット`から抽出した16進値の全候補。"""

    @property
    def is_canonical(self) -> bool:
        """正規配置（`## 目的`直下）から読み取ったかを返す。"""
        return self.parent == PLAN_H2_PURPOSE


_METADATA_ENTRY_PATTERN = re.compile(r"^- (?P<field>[^:]+): (?P<value>.*?)\s*$")
_METADATA_BASE_COMMIT_LINE = re.compile(r"^\s*-\s*(?:ベースコミット|基準コミット):\s*`(?P<oid>[0-9a-fA-F]+)`.*$")
"""ベースコミットを記載した箇条書きからOIDを読み取る互換パターン。

正規形の記法検査は`entries`側で行うため、値抽出は既存計画の記法差を受け入れる。
行頭の字下げ、コロン前後の空白、閉じバッククォート以降の注記を許容し、
旧別名`基準コミット`も対象とする。
"""


def _strip_backticks(value: str) -> str:
    """前後のバッククォートを1組だけ取り除く。"""
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        return value[1:-1]
    return value


def parse_plan_metadata(content: str) -> tuple[PlanMetadata | None, list[str]]:
    """計画メタ情報を正規配置優先で解析し、(解析結果, 曖昧性エラー)を返す。

    `## 目的`直下の`### 計画メタ情報`を正規配置とする。
    正規配置が無い既存計画に限り`PLAN_METADATA_FALLBACK_H2`の旧配置を読み取り互換として使う。
    同一親に複数の`### 計画メタ情報`がある場合と、旧配置の候補が複数の親に散在する場合は
    曖昧として解析結果を返さない。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    sections: dict[str, list[int]] = {}
    for index, heading in enumerate(headings):
        if heading.level != 3 or heading.text != PLAN_METADATA_H3:
            continue
        parent = next(
            (candidate.text for candidate in reversed(headings[:index]) if candidate.level == 2),
            "",
        )
        sections.setdefault(parent, []).append(index)

    if PLAN_H2_PURPOSE in sections:
        parent = PLAN_H2_PURPOSE
    else:
        fallback_parents = [name for name in PLAN_METADATA_FALLBACK_H2 if name in sections]
        if not fallback_parents:
            return None, []
        if len(fallback_parents) > 1:
            return None, [f"計画メタ情報の配置が複数のH2に分かれています: {fallback_parents}"]
        parent = fallback_parents[0]

    indices = sections[parent]
    if len(indices) > 1:
        return None, [f"`## {parent}`直下の`### {PLAN_METADATA_H3}`が1件ではありません: 実際={len(indices)}件"]

    start, end = heading_subtree_range(headings, indices[0])
    section = lines_within(body, start, end)
    entries: list[tuple[str, str]] = []
    for _lineno, line in section:
        match = _METADATA_ENTRY_PATTERN.fullmatch(line)
        if match is not None:
            entries.append((match.group("field").strip(), match.group("value")))

    values: dict[str, str] = {}
    conflicts: list[str] = []
    for field, raw_value in entries:
        if field not in PLAN_METADATA_FIELDS:
            continue
        normalized = _strip_backticks(raw_value)
        if field in values and values[field] != normalized:
            conflicts.append(f"計画メタ情報の`{field}`に競合する値があります")
        values.setdefault(field, normalized)
    base_candidates = [
        match.group("oid") for _lineno, line in section if (match := _METADATA_BASE_COMMIT_LINE.fullmatch(line)) is not None
    ]
    if conflicts:
        return None, conflicts
    return PlanMetadata(parent, tuple(entries), values, tuple(base_candidates)), []


def extract_implementer_region(content: str) -> list[tuple[int, str]]:
    """実装者向け可変領域の本文行を行番号付きで返す。

    正規形では`## 対応方針`の直後のH2から`## 進捗ログ`の直前までを領域とし、
    領域内のH2・H3見出し行も含める。
    `## 対応方針`を持たない既存計画では`## 実装契約`配下を読み取り互換の領域とする。
    実装者向け領域が存在しない場合は空リストを返す。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    policy_index = find_heading_index(headings, 2, PLAN_H2_POLICY)
    if policy_index is None:
        return extract_h2_section_body(content, "実装契約")
    _policy_start, policy_end = heading_subtree_range(headings, policy_index)
    if policy_end is None:
        return []
    progress_index = find_heading_index(headings, 2, PLAN_H2_PROGRESS)
    region_end = headings[progress_index].lineno if progress_index is not None else None
    return [(lineno, line) for lineno, line in body if lineno >= policy_end and (region_end is None or lineno < region_end)]


def find_target_list_sections(content: str) -> list[list[tuple[int, str]]]:
    """実装者向け領域にある`### 対象ファイル一覧`の本文行をセクションごとに返す。"""
    sections: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] | None = None
    for lineno, line in extract_implementer_region(content):
        if line.startswith("## "):
            current = None
            continue
        if line.startswith("### "):
            current = [] if line[4:].strip() == PLAN_TARGET_LIST_H3 else None
            if current is not None:
                sections.append(current)
            continue
        if current is not None:
            current.append((lineno, line))
    return sections


def _target_list_body(content: str) -> list[tuple[int, str]]:
    """`### 対象ファイル一覧`の本文行を全セクション分連結して返す。"""
    return [entry for section in find_target_list_sections(content) for entry in section]


def extract_target_files_from_changes(content: str) -> list[str]:
    """実装者向け領域の対象一覧からパスを宣言順に抽出する。"""
    return [target.path for target in extract_plan_targets(content)]


def extract_plan_targets(content: str) -> list[PlanTarget]:
    """実装者向け領域の`### 対象ファイル一覧`の通常箇条書きを抽出する。"""
    targets: list[PlanTarget] = []
    for _lineno, line in _target_list_body(content):
        if (match := _TARGET_PATTERN.fullmatch(line)) is not None:
            state = {"新設": "new", "削除": "deleted"}.get(match.group("state"), "existing")
            targets.append(PlanTarget(match.group("path"), state))
    return targets


def find_invalid_target_entries(content: str) -> list[tuple[int, str]]:
    """対象ファイル一覧の箇条書き候補から契約形式に一致しない項目を返す。"""
    return [
        (lineno, line.strip())
        for lineno, line in _target_list_body(content)
        if _is_target_entry_candidate(line) and _TARGET_PATTERN.fullmatch(line) is None
    ]


def _is_target_entry_candidate(line: str) -> bool:
    """対象パスらしい箇条書きかを返す。"""
    match = _TARGET_BULLET_PATTERN.fullmatch(line)
    if match is None:
        return False
    body = match.group("body")
    return (
        body.startswith("`")
        or _TARGET_CHECKBOX_PATTERN.match(body) is not None
        or _TARGET_STATE_PATTERN.search(body) is not None
        or not any(character.isspace() for character in body)
    )


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
    `posttooluse.py`の条件付き禁止形の警告通知が対象種別の判定に使う。
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


# `(^|/)`接頭辞で先頭一致・任意の親ディレクトリ配下一致の両方を許容する
# （`pretooluse.py`側が絶対パス・tmp_path配下等の任意接頭辞パスを渡す既存挙動を保つ）。
AGENT_DOC_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)agent-toolkit/rules/.+\.md$"),
    re.compile(r"(^|/)agent-toolkit/skills/[^/]+/SKILL\.md$"),
    re.compile(r"(^|/)agent-toolkit/skills/[^/]+/references/.+\.md$"),
    re.compile(r"(^|/)agent-toolkit/agents/.+\.md$"),
    # chezmoi配布元のテンプレート（`<name>.md.tmpl`）も配布先ではエージェント向け文書として読み込まれるため、
    # `.tmpl`終端を受理する。原本側だけが対象から外れると、テンプレート経由の規範改訂を検査が素通りさせる。
    re.compile(r"(^|/)\.chezmoi-source/dot_claude/rules/.+\.md(\.tmpl)?$"),
    re.compile(r"(^|/)\.chezmoi-source/dot_claude/skills/.+\.md(\.tmpl)?$"),
    # 利用者プロジェクトが直接持つ規範文書。配布元固有パスだけを対象にすると、
    # プラグインとして配布された先のプロジェクトで検査が素通りする。
    # `skills`配下の粒度は`agent-toolkit/skills/`側と揃え、`SKILL.md`と`references/`配下に限定する。
    re.compile(r"(^|/)\.claude/rules/.+\.md$"),
    re.compile(r"(^|/)\.claude/skills/[^/]+/SKILL\.md$"),
    re.compile(r"(^|/)\.claude/skills/[^/]+/references/.+\.md$"),
)
# basenameで照合するコーディングエージェント向け文書判定対象ファイル名。
# ディレクトリ位置を問わず一致させる（ルート直下限定ではない）。
AGENT_DOC_TARGET_BASENAMES: frozenset[str] = frozenset({"AGENTS.md", "CLAUDE.md"})


def is_agent_doc_target_file(file_path: str | pathlib.Path) -> bool:
    """パス文字列がコーディングエージェント向け文書判定対象かを判定する。

    `agent-toolkit/scripts/pretooluse.py`と`agent-toolkit/skills/plan-mode/scripts/check_plan_file.py`が
    参照する対象パス判定のSSOTとする。
    `AGENT_DOC_TARGET_PATTERNS`のいずれかへ一致するか、
    basenameが`AGENT_DOC_TARGET_BASENAMES`に含まれる場合に真を返す。
    `is_agent_facing_md`とは判定対象範囲が異なる。
    利用箇所ごとに対象範囲を調整し、連続直接編集の抑止検査はプロジェクト固有文書を独自に除外する。
    """
    normalized = str(file_path).replace("\\", "/")
    if not normalized:
        return False
    if any(pat.search(normalized) for pat in AGENT_DOC_TARGET_PATTERNS):
        return True
    return pathlib.Path(normalized).name in AGENT_DOC_TARGET_BASENAMES


def has_bump_step_when_required(content: str) -> bool:
    """配布物の変更計画に版更新宣言があるかを判定する。"""
    paths = extract_target_files_from_changes(content)
    if not paths:
        return True
    agent_toolkit_paths = [p for p in paths if p.startswith("agent-toolkit/")]
    if not agent_toolkit_paths:
        return True
    if all(p.endswith("_test.py") for p in agent_toolkit_paths):
        return True
    contract_text = "\n".join(line for _lineno, line in extract_implementer_region(content))
    return "agent_toolkit_bump.py" in contract_text or "bump不要" in contract_text


def has_manifest_files_when_bump_step_present(content: str) -> bool:
    """版更新宣言がある計画に正本manifest 2件が含まれるかを判定する。"""
    contract_text = "\n".join(line for _lineno, line in extract_implementer_region(content))
    if "agent_toolkit_bump.py" not in contract_text:
        return True
    paths = extract_target_files_from_changes(content)
    return PLUGIN_MANIFEST_PATH in paths and MARKETPLACE_MANIFEST_PATH in paths


def find_invalid_target_file_paths(content: str) -> list[str]:
    """実装者向け領域の`### 対象ファイル一覧`配下の相対パス表記違反を検出する。

    絶対パス（`/`始まり）または親ディレクトリ参照（パス部品に`..`を含む）を
    プロジェクトルート相対の完全パス規範への違反として返す。
    `agent-toolkit/skills/plan-mode/SKILL.md`の計画契約が定めるパス記述形式を機械検査する。
    """
    invalid: list[str] = []
    for path in extract_target_files_from_changes(content):
        windows_path = pathlib.PureWindowsPath(path)
        if pathlib.PurePosixPath(path).is_absolute() or windows_path.drive or windows_path.root:
            invalid.append(path)
            continue
        parts = pathlib.PurePosixPath(path.replace("\\", "/")).parts
        if ".." in parts:
            invalid.append(path)
    return invalid


# --- 人間向け固定領域の構造検査 ---

_MATERIAL_ID_PATTERN = re.compile(r"^(?P<id>[A-Za-z0-9][0-9A-Za-z_-]*):$")
_MATERIAL_FENCE_PATTERN = re.compile(r"^\s*(?:`{3,}|~{3,})text\s*$")
_REFERENCE_SEPARATOR_PATTERN = re.compile(r"[、,・/\s]+")


def _is_placeholder_only(lines: list[tuple[int, str]]) -> bool:
    """本文が結論語だけで構成されているかを返す。"""
    contents = [line.strip().lstrip("-*+ ").strip("。 ") for _lineno, line in lines if line.strip()]
    contents = [content for content in contents if content and not content.startswith("#")]
    if not contents:
        return True
    return all(content in PLAN_PLACEHOLDER_WORDS for content in contents)


def _find_table_with_rows(tables: list[MarkdownTable], rows: tuple[str, ...]) -> MarkdownTable | None:
    """行名と順序が一致する表を返す。見つからない場合は`None`を返す。"""
    return next((table for table in tables if table.row_labels() == rows), None)


def _check_fixed_h2_layout(headings: list[PlanHeading], work_type: str | None) -> list[str]:
    """人間向け固定領域のH2の有無、順序、追加、`進捗ログ`の位置を検査する。"""
    errors: list[str] = []
    h2_texts = [heading.text for heading in headings if heading.level == 2]
    expected = [PLAN_H2_HISTORY, PLAN_H2_PURPOSE]
    if work_type == "バグ対応":
        expected.append(PLAN_H2_BUG)
    expected.append(PLAN_H2_POLICY)
    if h2_texts[: len(expected)] != expected:
        errors.append(f"人間向け固定領域のH2は{expected}をこの順序で置く: 実際={h2_texts[: len(expected)]}")
    for name in (*expected, PLAN_H2_PROGRESS):
        count = h2_texts.count(name)
        if count != 1:
            errors.append(f"固定H2`## {name}`は1件必要: 実際={count}件")
    if work_type == "通常変更" and PLAN_H2_BUG in h2_texts:
        errors.append(f"作業種別が`通常変更`の計画に`## {PLAN_H2_BUG}`は置かない")
    if h2_texts and h2_texts[-1] != PLAN_H2_PROGRESS:
        errors.append(f"`## {PLAN_H2_PROGRESS}`は最後のH2にする: 実際の末尾={h2_texts[-1]}")
    return errors


def _check_child_heading_sequence(
    headings: list[PlanHeading],
    index: int,
    level: int,
    expected: tuple[str, ...],
    parent_label: str,
) -> list[str]:
    """指定見出しの直下にある固定見出しの有無、一意性、順序を検査する。"""
    errors: list[str] = []
    children = [heading.text for _position, heading in child_headings(headings, index, level)]
    positions: list[int] = []
    for name in expected:
        count = children.count(name)
        if count != 1:
            errors.append(f"{parent_label}直下の`{'#' * level} {name}`は1件必要: 実際={count}件")
        elif count == 1:
            positions.append(children.index(name))
    if len(positions) == len(expected) and positions != sorted(positions):
        errors.append(f"{parent_label}直下の固定見出しは{list(expected)}の順序で置く: 実際={children}")
    return errors


def _check_metadata_block(content: str) -> tuple[str | None, list[str]]:
    """計画メタ情報の配置、項目、順序、記法、値を検査して(作業種別, エラー)を返す。"""
    metadata, errors = parse_plan_metadata(content)
    if metadata is None:
        return None, errors or [f"`## {PLAN_H2_PURPOSE}`直下の`### {PLAN_METADATA_H3}`を検査できない"]
    if not metadata.is_canonical:
        errors.append(f"計画メタ情報は`## {PLAN_H2_PURPOSE}`直下へ置く: 実際=`## {metadata.parent}`直下")
    fields = [field for field, _value in metadata.entries]
    if fields != list(PLAN_METADATA_FIELDS):
        errors.append(f"計画メタ情報は{list(PLAN_METADATA_FIELDS)}をこの順序で1行ずつ置く: 実際={fields}")
    for field, raw_value in metadata.entries:
        if field not in PLAN_METADATA_FIELDS:
            continue
        quoted = raw_value.startswith("`") and raw_value.endswith("`") and len(raw_value) >= 2
        if field in PLAN_METADATA_QUOTED_FIELDS and not quoted:
            errors.append(f"計画メタ情報の`{field}`はバッククォートで囲む")
        if field not in PLAN_METADATA_QUOTED_FIELDS and quoted:
            errors.append(f"計画メタ情報の`{field}`はバッククォートで囲まない")
        if not _strip_backticks(raw_value):
            errors.append(f"計画メタ情報の`{field}`が空である")
    work_type = metadata.values.get("作業種別")
    if work_type is not None and work_type not in PLAN_WORK_TYPES:
        errors.append(f"計画メタ情報の`作業種別`は{list(PLAN_WORK_TYPES)}のいずれかで記載する")
        work_type = None
    base_commit = metadata.values.get("ベースコミット")
    if base_commit is not None and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", base_commit) is None:
        errors.append("計画メタ情報の`ベースコミット`は完全長SHAで記載する")
    return work_type, errors


def _check_materials(
    content: str,
    body: list[tuple[int, str]],
    headings: list[PlanHeading],
    index: int,
) -> tuple[set[str], list[str]]:
    """`### 提示素材`の素材IDと逐語fenceを検査して(素材ID集合, エラー)を返す。"""
    raw_lines = content.splitlines()
    start, end = heading_subtree_range(headings, index)
    upper = len(raw_lines) if end is None else end - 1
    section = [(lineno, raw_lines[lineno - 1]) for lineno in range(start + 1, min(upper, len(raw_lines)) + 1)]
    structural = {lineno for lineno, _line in lines_within(body, start, end)}
    identifiers: set[str] = set()
    errors: list[str] = []
    position = 0
    while position < len(section):
        lineno, line = section[position]
        match = _MATERIAL_ID_PATTERN.fullmatch(line.strip())
        if match is None or lineno not in structural:
            position += 1
            continue
        identifiers.add(match.group("id"))
        follower = next(
            ((next_lineno, next_line) for next_lineno, next_line in section[position + 1 :] if next_line.strip()),
            None,
        )
        if follower is None or _MATERIAL_FENCE_PATTERN.fullmatch(follower[1]) is None:
            errors.append(f"提示素材`{match.group('id')}`の直後に`text`フェンスの逐語転記が無い")
        position += 1
    if not identifiers:
        errors.append("提示素材に素材IDと`text`フェンスの逐語転記が1件以上必要")
    return identifiers, errors


def _check_fixed_table(
    lines: list[tuple[int, str]],
    header: tuple[str, ...],
    label: str,
) -> tuple[MarkdownTable | None, list[str]]:
    """列名が一致する表の実在、行数、空cellを検査して(表, エラー)を返す。"""
    tables = extract_tables(lines)
    table = next((candidate for candidate in tables if candidate.header == header), None)
    if table is None:
        return None, [f"{label}は{list(header)}の列を持つ表にする"]
    errors: list[str] = []
    if not table.rows:
        errors.append(f"{label}の表に1行以上の内容が必要")
    for row in table.rows:
        if len(row) != len(header) or any(not cell for cell in row):
            errors.append(f"{label}の表に空cellまたは列数不一致の行がある: {list(row)}")
    return table, errors


def _check_bug_sections(body: list[tuple[int, str]], headings: list[PlanHeading], index: int) -> list[str]:
    """`## バグ調査結果`配下の各バグ単位が固定14行の2列表を持つかを検査する。"""
    errors: list[str] = []
    children = child_headings(headings, index, 3)
    if not children:
        errors.append(f"`## {PLAN_H2_BUG}`直下にバグ単位のH3が1件以上必要")
    for position, heading in children:
        start, end = heading_subtree_range(headings, position)
        table = _find_table_with_rows(extract_tables(lines_within(body, start, end)), PLAN_BUG_TABLE_ROWS)
        if table is None or table.header != PLAN_BUG_TABLE_HEADER:
            errors.append(f"`### {heading.text}`は{list(PLAN_BUG_TABLE_HEADER)}の2列と固定14行の調査表にする")
            continue
        for row in table.rows:
            if len(row) != 2 or not row[1]:
                errors.append(f"`### {heading.text}`の調査表に空の`内容`がある: {row[0] if row else ''}")
    return errors


def _check_permanence_sections(
    body: list[tuple[int, str]],
    headings: list[PlanHeading],
    index: int,
    work_type: str | None,
) -> list[str]:
    """恒久化、リファクタリング、類似見直しの検討実体を検査する。"""
    errors = _check_child_heading_sequence(headings, index, 4, PLAN_PERMANENCE_H4, "`### 恒久化・リファクタリング内容`")
    for position, heading in child_headings(headings, index, 4):
        if heading.text not in PLAN_PERMANENCE_H4:
            continue
        start, end = heading_subtree_range(headings, position)
        section = lines_within(body, start, end)
        if _is_placeholder_only(section):
            errors.append(f"`#### {heading.text}`は対象、比較、確認結果、理由を記載する（結論語だけの記載は成立しない）")
            continue
        tables = extract_tables(section)
        if heading.text == "恒久化" and work_type == "通常変更":
            if _find_table_with_rows(tables, PLAN_PERMANENCE_TABLE_ROWS) is None:
                errors.append(f"通常変更の`#### 恒久化`は対象ごとに{list(PLAN_PERMANENCE_TABLE_ROWS)}の5行表を置く")
        elif heading.text == "リファクタリング":
            if _find_table_with_rows(tables, PLAN_REFACTORING_TABLE_ROWS) is None:
                errors.append(f"`#### リファクタリング`は対象ごとに{list(PLAN_REFACTORING_TABLE_ROWS)}の4行表を置く")
        elif (
            heading.text == "類似見直し"
            and work_type == "通常変更"
            and _find_table_with_rows(tables, PLAN_SIMILAR_REVIEW_TABLE_ROWS) is None
        ):
            errors.append(f"通常変更の`#### 類似見直し`は{list(PLAN_SIMILAR_REVIEW_TABLE_ROWS)}の3行表を置く")
    return errors


def check_plan_structure(content: str) -> list[str]:
    """計画の人間向け固定領域と実装者向け領域の境界を検査して違反一覧を返す。

    検査対象は見出しの欠落、重複、順序違反、固定領域への追加H2、固定表の列と行、
    空cell、原文参照先の欠落、恒久化等の空欄または結論語だけの記載とする。
    原文と要約の意味照合、根拠の妥当性、検討の実質はreviewerが判定する。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    errors: list[str] = []

    h1_headings = [heading for heading in headings if heading.level == 1]
    if len(h1_headings) != 1:
        errors.append(f"先頭にATX H1が1件必要: 実際={len(h1_headings)}件")
    elif not h1_headings[0].text or headings[0] is not h1_headings[0]:
        errors.append("H1は本文の先頭見出しとし、主題を空にしない")

    work_type, metadata_errors = _check_metadata_block(content)
    errors.extend(metadata_errors)

    if not [heading for heading in headings if heading.level == 2]:
        errors.append("固定H2が1件も無い")
        return errors
    errors.extend(_check_fixed_h2_layout(headings, work_type))

    history_index = find_heading_index(headings, 2, PLAN_H2_HISTORY)
    if history_index is not None:
        start, end = heading_subtree_range(headings, history_index)
        _table, table_errors = _check_fixed_table(
            lines_within(body, start, end), PLAN_HISTORY_TABLE_HEADER, f"`## {PLAN_H2_HISTORY}`"
        )
        errors.extend(table_errors)

    purpose_index = find_heading_index(headings, 2, PLAN_H2_PURPOSE)
    identifiers: set[str] = set()
    if purpose_index is not None:
        errors.extend(_check_child_heading_sequence(headings, purpose_index, 3, PLAN_PURPOSE_H3, f"`## {PLAN_H2_PURPOSE}`"))
        for position, heading in child_headings(headings, purpose_index, 3):
            start, end = heading_subtree_range(headings, position)
            section = lines_within(body, start, end)
            if heading.text == "概要" and not [line for _lineno, line in section if line.strip()]:
                errors.append("`### 概要`に成果と解消する問題を記載する")
            elif heading.text == "提示素材":
                identifiers, material_errors = _check_materials(content, body, headings, position)
                errors.extend(material_errors)
            elif heading.text == "ユーザー合意済み事項":
                table, table_errors = _check_fixed_table(section, PLAN_AGREEMENT_TABLE_HEADER, "`### ユーザー合意済み事項`")
                errors.extend(table_errors)
                if table is not None:
                    errors.extend(_check_reference_ids(table, identifiers))

    bug_index = find_heading_index(headings, 2, PLAN_H2_BUG)
    if bug_index is not None:
        errors.extend(_check_bug_sections(body, headings, bug_index))

    policy_index = find_heading_index(headings, 2, PLAN_H2_POLICY)
    if policy_index is not None:
        errors.extend(_check_child_heading_sequence(headings, policy_index, 3, PLAN_POLICY_H3, f"`## {PLAN_H2_POLICY}`"))
        for position, heading in child_headings(headings, policy_index, 3):
            start, end = heading_subtree_range(headings, position)
            section = lines_within(body, start, end)
            if heading.text == "実施内容":
                table, table_errors = _check_fixed_table(section, PLAN_ACTION_TABLE_HEADER, "`### 実施内容`")
                errors.extend(table_errors)
                if table is not None:
                    errors.extend(_check_action_relations(table))
            elif heading.text == "恒久化・リファクタリング内容":
                errors.extend(_check_permanence_sections(body, headings, position, work_type))

    sections = find_target_list_sections(content)
    if len(sections) != 1:
        errors.append(f"実装者向け領域の`### {PLAN_TARGET_LIST_H3}`は1件必要: 実際={len(sections)}件")
    return errors


def _check_reference_ids(table: MarkdownTable, identifiers: set[str]) -> list[str]:
    """合意表の`原文参照`が提示素材の素材IDを指すかを検査する。"""
    column = table.header.index("原文参照")
    errors: list[str] = []
    for row in table.rows:
        if len(row) <= column or not row[column]:
            continue
        for token in _REFERENCE_SEPARATOR_PATTERN.split(row[column]):
            if token and token not in identifiers:
                errors.append(f"`### ユーザー合意済み事項`の原文参照が提示素材に無い: {token}")
    return errors


def _check_action_relations(table: MarkdownTable) -> list[str]:
    """実施内容表の`ユーザー指示との関係`が3値だけを取るかを検査する。"""
    column = table.header.index("ユーザー指示との関係")
    return [
        f"`### 実施内容`の`ユーザー指示との関係`は{list(PLAN_ACTION_RELATIONS)}のいずれかにする: {row[column]}"
        for row in table.rows
        if len(row) > column and row[column] and row[column] not in PLAN_ACTION_RELATIONS
    ]
