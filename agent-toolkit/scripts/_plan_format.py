"""計画ファイルの構造検査の共通モジュール。

構造検査（`check_plan_file.py`）、フィードバック登録（`_atk_mq_add.py`）、
2系統のPreToolUse（`pretooluse.py`・`scripts/claude_hook_pretooluse.py`）、
PostToolUse（`posttooluse.py`）が本モジュールから同じ判定結果を得る。
SSOTは`agent-toolkit/skills/plan-mode/references/plan-file-standards.md`の「計画ファイルの完成条件」節。

構造認識と原記法の検査は分離する。
見出し、コードフェンス、表の範囲、節の親子関係は、標準準拠のパーサーが1回生成した
トークン列と位置情報から認識する。解析結果を各検査へ渡し、検査ごとの再解析を避ける。
パーサーが補完、除去、段落へ変換する可能性がある原記法は、表トークンと段落トークンの
位置情報から取得した原文を別関数で検査する。
- 表の列数、外側パイプ、セル数の一致を原文から判定する
- 表に変換されなかった段落では、パイプ区切りのヘッダー候補と直後の区切り行候補について
  セル数の不一致を検出する
- 原文検査の対象範囲は、パーサーが表または段落として分類した箇所に限定する
- 正規表現は原文の内容照合に限定し、列数は区切り記号で分割した要素数から計算する

各検査関数のdocstringへ担当する検査、受け取るトークン範囲、段落内の候補条件を記載する。
検査項目を追加する場合は、次の構文境界を含む共通コーパスへ検体を追加してから実装する。
- インデント、コードブロック内の記述、見出しの閉じ記号
- 表の外側パイプの省略、表トークンになる列数不一致、段落へ変換される列数不一致
- 同名節の重複、親節の違い
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

PLAN_H2_OVERVIEW: str = "概要"
PLAN_H2_ACTION: str = "実施内容"
PLAN_H2_MATERIALS: str = "提示素材"
PLAN_H2_HISTORY: str = "変更履歴"
PLAN_H2_BUG: str = "バグ調査結果"
PLAN_H2_PERMANENCE: str = "恒久化・リファクタリング内容"
PLAN_H2_IMPLEMENTATION: str = "実装資料"
PLAN_H2_COMPLETION: str = "完了条件"
PLAN_H2_PROGRESS: str = "進捗ログ"

PLAN_PERMANENCE_H3: tuple[str, ...] = ("恒久化", "リファクタリング", "類似見直し")
"""`## 恒久化・リファクタリング内容`直下に固定順で置くH3。"""

PLAN_METADATA_H3: str = "計画メタ情報"
PLAN_EXCLUSION_H3: str = "合意済みの除外・保持"

PLAN_METADATA_FIELDS: tuple[str, ...] = ("起動経路", "対象リポジトリ", "作業種別", "ベースコミット")
"""計画メタ情報の正規形が持つ項目と順序。"""

PLAN_METADATA_QUOTED_FIELDS: frozenset[str] = frozenset({"起動経路", "対象リポジトリ", "ベースコミット"})
"""値をバッククォートで囲む項目。`作業種別`だけは固定値を裸で書く。"""

PLAN_WORK_TYPES: tuple[str, ...] = ("バグ対応", "通常変更")

PLAN_METADATA_FALLBACK_H2: tuple[str, ...] = ("目的", "実装契約", "背景")
"""正規配置を持たない既存計画で計画メタ情報を読み取る旧配置。読み取り専用の互換経路とする。"""

PLAN_HISTORY_TABLE_HEADER: tuple[str, ...] = ("ID", "起点", "指摘内容", "採否・現在の結論", "同期先")
PLAN_HISTORY_ORIGINS: tuple[str, ...] = ("ユーザー発言", "レビュー指摘", "方針転換")
PLAN_PROGRESS_TABLE_HEADER: tuple[str, ...] = ("日時", "完了した工程", "結果・特記事項")
PLAN_EXCLUSION_TABLE_HEADER: tuple[str, ...] = ("合意内容", "対象と箇所", "素材・要求参照", "確認方法")
PLAN_LEGACY_EXCLUSION_TABLE_HEADER: tuple[str, ...] = ("合意内容", "対象と箇所", "原文参照", "確認方法")
PLAN_ACTION_TABLE_HEADER: tuple[str, ...] = ("実施内容", "ユーザー指示との関係", "根拠")
PLAN_ACTION_RELATIONS: tuple[str, ...] = ("指示どおり", "具体化", "エージェント追加")

PLAN_MATERIAL_TABLE_HEADER: tuple[str, ...] = ("素材ID", "種別", "キューID", "投入元", "引用範囲")
PLAN_REQUIREMENT_TABLE_HEADER: tuple[str, ...] = (
    "要求ID",
    "素材参照",
    "実装に必要な要件",
    "採否",
    "採用範囲",
    "除外範囲",
    "根拠",
)
PLAN_MATERIAL_TYPES: tuple[str, ...] = (
    "フィードバック",
    "利用者指示",
    "利用者合意",
    "参考素材",
    "処理対象資料",
    "起動事実",
)
PLAN_NON_QUEUE_VALUE: str = "非該当"
PLAN_MATERIAL_ID_PATTERN = re.compile(r"^P-[0-9A-Za-z][0-9A-Za-z_-]*$")
PLAN_REQUIREMENT_ID_PATTERN = re.compile(r"^R-(?P<material>P-[0-9A-Za-z][0-9A-Za-z_-]*)-(?P<sequence>[0-9]{3})$")
PLAN_QUEUE_ID_PATTERN = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9]{3,}\.md$")

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

PLAN_PERMANENCE_TABLE_HEADER: tuple[str, ...] = ("知見", "出所", "反映先", "根拠")
"""通常変更の恒久化表の固定4列。バグ対応はバグ調査表の14行を正本とする。"""

PLAN_REFACTORING_TABLE_ROWS: tuple[str, ...] = ("対象", "現状の問題", "対応", "本計画に含めるか")
PLAN_SIMILAR_REVIEW_TABLE_ROWS: tuple[str, ...] = ("母集団", "点検観点", "該当箇所")
"""構造検査が通常変更だけに要求する類似見直し表の固定3行。

バグ対応は起草担当の規定に基づき、バグ調査表との意味上の対応をレビュー担当が照合する。
"""

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


@dataclass(frozen=True)
class PlanMaterials:
    """計画の提示素材と要求の解析結果を表す。"""

    material_ids: frozenset[str]
    requirement_ids: frozenset[str]
    is_legacy: bool
    adopted_requirement_ids: frozenset[str] = frozenset()
    terminal_only_requirement_ids: frozenset[str] = frozenset()


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
    """メタ情報を収めていた親H2の見出し文。正規形では`概要`となる。"""

    entries: tuple[tuple[str, str], ...]
    """記載順の(項目名, 生の値)。順序と記法の検査に使う。"""

    values: dict[str, str]
    """正規4項目の値。バッククォートは除去済みで、欠落項目は含めない。"""

    base_commit_candidates: tuple[str, ...]
    """`ベースコミット`と旧別名`基準コミット`から抽出した16進値の全候補。"""

    @property
    def is_canonical(self) -> bool:
        """正規配置（`## 概要`直下）から読み取ったかを返す。"""
        return self.parent == PLAN_H2_OVERVIEW


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

    `## 概要`直下の`### 計画メタ情報`を正規配置とする。
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

    if PLAN_H2_OVERVIEW in sections:
        parent = PLAN_H2_OVERVIEW
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
    """`## 変更履歴`直後から`## 進捗ログ`直前までの本文行を返す。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    history_index = find_heading_index(headings, 2, PLAN_H2_HISTORY)
    if history_index is None:
        return []
    _history_start, history_end = heading_subtree_range(headings, history_index)
    if history_end is None:
        return []
    progress_index = find_heading_index(headings, 2, PLAN_H2_PROGRESS)
    region_end = headings[progress_index].lineno if progress_index is not None else None
    return [(lineno, line) for lineno, line in body if lineno >= history_end and (region_end is None or lineno < region_end)]


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


# --- 人間向け固定領域の構造検査 ---

_LEGACY_MATERIAL_ID_PATTERN = re.compile(r"^(?P<id>[A-Za-z0-9][0-9A-Za-z_-]*):$")
_MATERIAL_ID_CANDIDATE_PATTERN = re.compile(r"^P-[0-9A-Za-z][0-9A-Za-z_-]*(?:(?:（[^）\n]+）|\([^)\n]+\)):|:\s+\S.*)$")
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
    """全H2の有無、一意性、固定順序を検査する。"""
    errors: list[str] = []
    h2_texts = [heading.text for heading in headings if heading.level == 2]
    expected = [PLAN_H2_OVERVIEW, PLAN_H2_ACTION, PLAN_H2_MATERIALS, PLAN_H2_HISTORY]
    if work_type == "バグ対応":
        expected.append(PLAN_H2_BUG)
    expected.extend((PLAN_H2_PERMANENCE, PLAN_H2_IMPLEMENTATION, PLAN_H2_COMPLETION, PLAN_H2_PROGRESS))
    cardinality_error = False
    for name in expected:
        count = h2_texts.count(name)
        if count != 1:
            cardinality_error = True
            errors.append(f"固定H2`## {name}`は1件必要: 実際={count}件")

    unexpected = [name for name in h2_texts if name not in expected]
    has_unexpected = bool(unexpected)
    if work_type == "通常変更" and PLAN_H2_BUG in unexpected:
        errors.append(f"作業種別が`通常変更`の計画に`## {PLAN_H2_BUG}`は置かない")
        unexpected = [name for name in unexpected if name != PLAN_H2_BUG]
    if unexpected:
        errors.append(f"固定H2は{expected}だけをこの順序で置く: 実際={h2_texts}")
    if not cardinality_error and not has_unexpected and h2_texts != expected:
        errors.append(f"固定H2は{expected}をこの順序で置く: 実際={h2_texts}")
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
    unexpected = [name for name in children if name not in expected]
    if unexpected:
        errors.append(f"{parent_label}直下に固定見出し以外のH{level}は置かない: 実際={unexpected}")
    return errors


def _check_metadata_block(content: str) -> tuple[str | None, list[str]]:
    """計画メタ情報の配置、項目、順序、記法、値を検査して(作業種別, エラー)を返す。"""
    metadata, errors = parse_plan_metadata(content)
    if metadata is None:
        return None, errors or [f"`## {PLAN_H2_OVERVIEW}`直下の`### {PLAN_METADATA_H3}`を検査できない"]
    if not metadata.is_canonical:
        errors.append(f"計画メタ情報は`## {PLAN_H2_OVERVIEW}`直下へ置く: 実際=`## {metadata.parent}`直下")
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


def _materials_section(
    content: str,
    headings: list[PlanHeading],
    index: int,
) -> list[tuple[int, str]]:
    """提示素材H2の本文を、行番号付きで返す。"""
    raw_lines = content.splitlines()
    start, end = heading_subtree_range(headings, index)
    upper = len(raw_lines) if end is None else end - 1
    return [(lineno, raw_lines[lineno - 1]) for lineno in range(start + 1, min(upper, len(raw_lines)) + 1)]


def _split_material_references(value: str) -> list[str]:
    """素材参照cellを区切って返す。"""
    return [token for token in _REFERENCE_SEPARATOR_PATTERN.split(value.strip()) if token]


def _requirement_references(value: str) -> list[str]:
    """説明文から要求IDの参照を抽出する。"""
    return [match.group(0) for match in re.finditer(r"R-P-[0-9A-Za-z][0-9A-Za-z_-]*-[0-9]{3}", value)]


def _validate_material_row(row: tuple[str, ...], identifiers: set[str]) -> list[str]:
    """新形式の素材表1行を検査する。"""
    material_id, material_type, queue_id, source, citation = row
    errors: list[str] = []
    if not PLAN_MATERIAL_ID_PATTERN.fullmatch(material_id):
        errors.append(f"提示素材の素材IDが不正である: {material_id}")
    elif material_id in identifiers:
        errors.append(f"提示素材の素材IDが重複している: {material_id}")
    if material_type not in PLAN_MATERIAL_TYPES:
        errors.append(f"提示素材の種別が不正である: {material_type}")
        return errors

    if material_type == "フィードバック":
        if PLAN_QUEUE_ID_PATTERN.fullmatch(queue_id) is None:
            errors.append(f"フィードバック素材のキューIDが不正である: {queue_id}")
        if citation != "本文全文":
            errors.append("フィードバック素材の引用範囲は本文全文にする")
    elif queue_id != PLAN_NON_QUEUE_VALUE:
        errors.append(f"{material_type}素材のキューIDは非該当にする")

    if material_type == "利用者指示":
        if source == "本セッション" and citation != "全文":
            errors.append("利用者指示素材は本セッションの引用範囲を全文にする")
    elif material_type == "利用者合意":
        if source == "本セッション" and citation != "全文":
            errors.append("本セッションの利用者合意素材の引用範囲を全文にする")
        elif (source == "AskUserQuestion" or source.startswith("TBD:")) and citation != "回答全文":
            errors.append("AskUserQuestion又はTBDの利用者合意素材の引用範囲を回答全文にする")
    elif material_type in {"参考素材", "処理対象資料"} and citation == PLAN_NON_QUEUE_VALUE:
        errors.append(f"{material_type}素材の引用範囲は非該当にしない")
    elif material_type == "起動事実" and (source != "常駐自動起動" or citation != PLAN_NON_QUEUE_VALUE):
        errors.append("起動事実素材は投入元を常駐自動起動、引用範囲を非該当にする")

    if material_type != "起動事実" and not source:
        errors.append(f"{material_type}素材の投入元が空である")
    if not citation:
        errors.append(f"{material_type}素材の引用範囲が空である")
    return errors


def _check_new_materials(section: list[tuple[int, str]]) -> tuple[PlanMaterials, list[str]]:
    """新形式の素材表と要求表を検査する。"""
    tables = extract_tables(section)
    material_tables = [table for table in tables if table.header == PLAN_MATERIAL_TABLE_HEADER]
    requirement_tables = [table for table in tables if table.header == PLAN_REQUIREMENT_TABLE_HEADER]
    errors: list[str] = []
    if len(material_tables) != 1:
        errors.append(f"提示素材の素材表は{list(PLAN_MATERIAL_TABLE_HEADER)}の1件だけを置く: 実際={len(material_tables)}件")
    if len(requirement_tables) != 1:
        errors.append(
            f"提示素材の要求表は{list(PLAN_REQUIREMENT_TABLE_HEADER)}の1件だけを置く: 実際={len(requirement_tables)}件"
        )
    if len(tables) != 2:
        errors.append(f"提示素材には素材表と要求表だけを置く: 実際={len(tables)}件")
    identifiers: set[str] = set()
    if not material_tables:
        return PlanMaterials(frozenset(), frozenset(), False), errors

    material_table = material_tables[0]
    if not material_table.rows:
        errors.append("提示素材の素材表に1行以上の内容が必要")
    for row in material_table.rows:
        if len(row) != len(PLAN_MATERIAL_TABLE_HEADER) or any(not cell for cell in row):
            errors.append(f"提示素材の素材表に空cellまたは列数不一致の行がある: {list(row)}")
            continue
        errors.extend(_validate_material_row(row, identifiers))
        identifiers.add(row[0])

    requirement_ids: set[str] = set()
    adopted_requirement_ids: set[str] = set()
    terminal_only_requirement_ids: set[str] = set()
    requirements = requirement_tables[0] if requirement_tables else None
    if requirements is not None:
        if not requirements.rows:
            errors.append("提示素材の要求表に1行以上の内容が必要")
        requirement_id_values = [row[0] for row in requirements.rows if row]
        if requirement_id_values != sorted(requirement_id_values):
            errors.append("提示素材の要求表は要求ID昇順で並べる")
        for row in requirements.rows:
            if len(row) != len(PLAN_REQUIREMENT_TABLE_HEADER) or any(not cell for cell in row):
                errors.append(f"提示素材の要求表に空cellまたは列数不一致の行がある: {list(row)}")
                continue
            requirement_id, references, _description, decision, adopted, excluded, _reason = row
            match = PLAN_REQUIREMENT_ID_PATTERN.fullmatch(requirement_id)
            if match is None:
                errors.append(f"要求IDが不正である: {requirement_id}")
                continue
            if requirement_id in requirement_ids:
                errors.append(f"要求IDが重複している: {requirement_id}")
            requirement_ids.add(requirement_id)
            namespace = match.group("material")
            if namespace not in identifiers:
                errors.append(f"要求IDの素材名前空間が素材表に無い: {namespace}")
            refs = _split_material_references(references)
            if not refs:
                errors.append(f"要求{requirement_id}の素材参照が空である")
            elif references != ", ".join(refs):
                errors.append(f"要求{requirement_id}の素材参照は`P-001, P-002`形式で記載する: {references}")
            if refs != sorted(refs):
                errors.append(f"要求{requirement_id}の素材参照はID昇順で並べる: {references}")
            if len(refs) != len(set(refs)):
                errors.append(f"要求{requirement_id}の素材参照が重複している: {references}")
            for reference in refs:
                if reference not in identifiers:
                    errors.append(f"要求{requirement_id}の素材参照が素材表に無い: {reference}")
            if namespace not in refs:
                errors.append(f"要求{requirement_id}の素材名前空間を素材参照に含める: {namespace}")
            if decision not in {"採用", "不採用"}:
                errors.append(f"要求{requirement_id}の採否は採用又は不採用にする: {decision}")
            elif decision == "採用":
                adopted_requirement_ids.add(requirement_id)
                if adopted.startswith("終端工程のみ"):
                    terminal_only_requirement_ids.add(requirement_id)
            if decision == "採用" and (adopted == PLAN_NON_QUEUE_VALUE or excluded != PLAN_NON_QUEUE_VALUE):
                errors.append(f"要求{requirement_id}の採用範囲又は除外範囲が不正である")
            if decision == "不採用" and (adopted != PLAN_NON_QUEUE_VALUE or excluded == PLAN_NON_QUEUE_VALUE):
                errors.append(f"要求{requirement_id}の採用範囲又は除外範囲が不正である")

        sequences_by_namespace: dict[str, list[int]] = {}
        for requirement_id in requirement_ids:
            match = PLAN_REQUIREMENT_ID_PATTERN.fullmatch(requirement_id)
            assert match is not None
            sequences_by_namespace.setdefault(match.group("material"), []).append(int(match.group("sequence")))
        for namespace, sequences in sequences_by_namespace.items():
            if sorted(sequences) != list(range(1, len(sequences) + 1)):
                errors.append(f"素材{namespace}の要求ID末尾連番が001から欠番なく続かない")

    if material_tables and requirement_tables:
        material_end = material_tables[0].lineno + len(material_tables[0].rows) + 1
        requirement_lineno = requirement_tables[0].lineno
        section_lines = dict(section)
        intervening = [
            section_lines[lineno] for lineno in range(material_end + 1, requirement_lineno) if lineno in section_lines
        ]
        if material_tables[0].lineno > requirement_lineno or any(line.strip() for line in intervening):
            errors.append("提示素材の素材表の直後に要求表を置く")

    referenced = {
        reference
        for row in (requirements.rows if requirements is not None else ())
        if len(row) == len(PLAN_REQUIREMENT_TABLE_HEADER)
        for reference in _split_material_references(row[1])
    }
    for row in material_table.rows:
        if (
            len(row) == len(PLAN_MATERIAL_TABLE_HEADER)
            and row[0] not in referenced
            and row[1] not in {"参考素材", "処理対象資料", "起動事実"}
        ):
            errors.append(f"素材{row[0]}が要求表から参照されていない")
    return PlanMaterials(
        frozenset(identifiers),
        frozenset(requirement_ids),
        False,
        frozenset(adopted_requirement_ids),
        frozenset(terminal_only_requirement_ids),
    ), errors


def parse_plan_materials(content: str) -> tuple[PlanMaterials | None, list[str]]:
    """提示素材を新形式または旧形式として解析する。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    index = find_heading_index(headings, 2, PLAN_H2_MATERIALS)
    if index is None:
        return None, ["固定H2の提示素材を検査できない"]
    section = _materials_section(content, headings, index)
    tables = extract_tables(section)
    if any(table.header in {PLAN_MATERIAL_TABLE_HEADER, PLAN_REQUIREMENT_TABLE_HEADER} for table in tables):
        return _check_new_materials(section)
    identifiers, errors = _check_materials(content, body, headings, index)
    return PlanMaterials(frozenset(identifiers), frozenset(), True), errors


def _check_materials(
    content: str,
    body: list[tuple[int, str]],
    headings: list[PlanHeading],
    index: int,
) -> tuple[set[str], list[str]]:
    """`## 提示素材`の素材IDと逐語fenceを検査して(素材ID集合, エラー)を返す。"""
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
        stripped = line.strip()
        match = _LEGACY_MATERIAL_ID_PATTERN.fullmatch(stripped)
        if match is None and lineno in structural and _MATERIAL_ID_CANDIDATE_PATTERN.fullmatch(stripped) is not None:
            errors.append(f"提示素材の素材ID行に注記を含めない: {stripped}")
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
    minimum_rows: int = 1,
) -> tuple[MarkdownTable | None, list[str]]:
    """列名が一致する表の件数、最低行数、空cellを検査して(表, エラー)を返す。"""
    tables = extract_tables(lines)
    matching = [candidate for candidate in tables if candidate.header == header]
    if not matching:
        return None, [f"{label}は{list(header)}の列を持つ表にする"]
    table = matching[0]
    errors = [f"{label}の固定表は1件必要: 実際={len(matching)}件"] if len(matching) != 1 else []
    if len(table.rows) < minimum_rows:
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
    errors = _check_child_heading_sequence(headings, index, 3, PLAN_PERMANENCE_H3, "`## 恒久化・リファクタリング内容`")
    for position, heading in child_headings(headings, index, 3):
        if heading.text not in PLAN_PERMANENCE_H3:
            continue
        start, end = heading_subtree_range(headings, position)
        section = lines_within(body, start, end)
        if _is_placeholder_only(section):
            errors.append(f"`### {heading.text}`は対象、比較、確認結果、理由を記載する（結論語だけの記載は成立しない）")
            continue
        if heading.text == "恒久化" and work_type == "通常変更":
            table, table_errors = _check_fixed_table(
                section,
                PLAN_PERMANENCE_TABLE_HEADER,
                "通常変更の`### 恒久化`",
            )
            if table is None:
                errors.append(f"通常変更の`### 恒久化`は{list(PLAN_PERMANENCE_TABLE_HEADER)}の4列表を置く")
            else:
                errors.extend(table_errors)
                if len(table.rows) != 1 and any(row and row[0] == "候補なし" for row in table.rows):
                    errors.append("通常変更の`### 恒久化`で`候補なし`を記載する場合は、`候補なし`の行だけを置く")
        elif heading.text == "リファクタリング":
            tables = extract_tables(section)
            if _find_table_with_rows(tables, PLAN_REFACTORING_TABLE_ROWS) is None:
                errors.append(f"`### リファクタリング`は対象ごとに{list(PLAN_REFACTORING_TABLE_ROWS)}の4行表を置く")
        elif (
            heading.text == "類似見直し"
            and work_type == "通常変更"
            and _find_table_with_rows(extract_tables(section), PLAN_SIMILAR_REVIEW_TABLE_ROWS) is None
        ):
            errors.append(f"通常変更の`### 類似見直し`は{list(PLAN_SIMILAR_REVIEW_TABLE_ROWS)}の3行表を置く")
    return errors


def check_plan_structure(content: str) -> list[str]:
    """計画の人間向け固定領域と実装者向け領域の境界を検査して違反一覧を返す。

    検査対象は見出しの欠落、重複、順序違反、固定領域への追加H2、固定表の列と行、
    空cell、素材・要求参照先の欠落、恒久化等の空欄または結論語だけの記載とする。
    素材と要求の意味照合、根拠の妥当性、検討の実質はレビュー担当が判定する。
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

    overview_index = find_heading_index(headings, 2, PLAN_H2_OVERVIEW)
    if overview_index is not None:
        children = child_headings(headings, overview_index, 3)
        if [heading.text for _position, heading in children] != [PLAN_METADATA_H3]:
            errors.append(f"`## {PLAN_H2_OVERVIEW}`直下のH3は`### {PLAN_METADATA_H3}`1件だけにする")
        overview_start, overview_end = heading_subtree_range(headings, overview_index)
        prose_end = children[0][1].lineno if children else overview_end
        if not [line for _lineno, line in lines_within(body, overview_start, prose_end) if line.strip()]:
            errors.append(f"`## {PLAN_H2_OVERVIEW}`直下の地の文に全体像、目的、対象範囲を記載する")

    materials_index = find_heading_index(headings, 2, PLAN_H2_MATERIALS)
    identifiers: set[str] = set()
    requirement_ids: set[str] = set()
    adopted_requirement_ids: set[str] = set()
    materials: PlanMaterials | None = None
    if materials_index is not None:
        materials, material_errors = parse_plan_materials(content)
        errors.extend(material_errors)
        if materials is not None:
            identifiers = set(materials.material_ids)
            requirement_ids = set(materials.requirement_ids)
            adopted_requirement_ids = set(materials.adopted_requirement_ids)

    action_index = find_heading_index(headings, 2, PLAN_H2_ACTION)
    if action_index is not None:
        start, end = heading_subtree_range(headings, action_index)
        section = lines_within(body, start, end)
        table, table_errors = _check_fixed_table(section, PLAN_ACTION_TABLE_HEADER, "`## 実施内容`")
        errors.extend(table_errors)

        children = child_headings(headings, action_index, 3)
        if any(heading.text != PLAN_EXCLUSION_H3 for _position, heading in children) or len(children) > 1:
            errors.append(f"`## 実施内容`直下のH3は任意の`### {PLAN_EXCLUSION_H3}`だけにする")
        exclusion: MarkdownTable | None = None
        if children:
            position, _heading = children[0]
            child_start, child_end = heading_subtree_range(headings, position)
            exclusion, exclusion_errors, exclusion_is_new = _check_exclusion_table(
                lines_within(body, child_start, child_end),
                "合意済みの除外・保持",
            )
            errors.extend(exclusion_errors)
            if exclusion is not None:
                errors.extend(
                    _check_reference_ids(
                        exclusion,
                        identifiers,
                        requirement_ids,
                        "合意済みの除外・保持",
                        exclusion_is_new,
                    )
                )

        if table is not None:
            errors.extend(_check_action_relations(table))
            if materials is not None and not materials.is_legacy:
                errors.extend(_check_action_references(table, requirement_ids, adopted_requirement_ids))
                errors.extend(_check_requirement_coverage(table, exclusion, materials))

    history_index = find_heading_index(headings, 2, PLAN_H2_HISTORY)
    if history_index is not None:
        start, end = heading_subtree_range(headings, history_index)
        history, table_errors = _check_fixed_table(
            lines_within(body, start, end), PLAN_HISTORY_TABLE_HEADER, f"`## {PLAN_H2_HISTORY}`"
        )
        errors.extend(table_errors)
        if history is not None:
            errors.extend(_check_history_rows(history, identifiers))

    progress_index = find_heading_index(headings, 2, PLAN_H2_PROGRESS)
    if progress_index is not None:
        start, end = heading_subtree_range(headings, progress_index)
        _table, table_errors = _check_fixed_table(
            lines_within(body, start, end),
            PLAN_PROGRESS_TABLE_HEADER,
            f"`## {PLAN_H2_PROGRESS}`",
            minimum_rows=0,
        )
        errors.extend(table_errors)

    bug_index = find_heading_index(headings, 2, PLAN_H2_BUG)
    if bug_index is not None:
        errors.extend(_check_bug_sections(body, headings, bug_index))

    permanence_index = find_heading_index(headings, 2, PLAN_H2_PERMANENCE)
    if permanence_index is not None:
        errors.extend(_check_permanence_sections(body, headings, permanence_index, work_type))

    allowed_h3_parents = {PLAN_H2_OVERVIEW, PLAN_H2_ACTION, PLAN_H2_BUG, PLAN_H2_PERMANENCE}
    for index, heading in enumerate(headings):
        if heading.level < 3:
            continue
        parent = next((candidate.text for candidate in reversed(headings[:index]) if candidate.level == 2), "")
        if parent == PLAN_H2_IMPLEMENTATION:
            continue
        if heading.level == 3 and parent not in allowed_h3_parents:
            errors.append(f"`## {parent}`直下に自由なH3は置かない: `### {heading.text}`")
        elif heading.level > 3:
            errors.append(f"`## {parent}`配下にH4以深の見出しは置かない: `{'#' * heading.level} {heading.text}`")
    return errors


def _check_action_references(
    table: MarkdownTable,
    requirement_ids: set[str],
    adopted_requirement_ids: set[str],
) -> list[str]:
    """新形式の実施内容表が採用要求だけを参照するかを検査する。"""
    column = table.header.index("根拠")
    errors: list[str] = []
    for row in table.rows:
        if len(row) <= column or not row[column]:
            errors.append("`## 実施内容`の`根拠`へ要求IDを1件以上記載する")
            continue
        references = _requirement_references(row[column])
        if not references:
            errors.append(f"`## 実施内容`の`根拠`は要求IDを参照する: {row[column]}")
            continue
        for reference in references:
            if reference not in requirement_ids:
                errors.append(f"`## 実施内容`の`根拠`が提示素材の要求表に無い: {reference}")
            elif reference not in adopted_requirement_ids:
                errors.append(f"`## 実施内容`の`根拠`へ不採用要求を参照できない: {reference}")
    return errors


def _check_requirement_coverage(
    action_table: MarkdownTable,
    exclusion_table: MarkdownTable | None,
    materials: PlanMaterials,
) -> list[str]:
    """採用要求IDが`## 実施内容`の`根拠`又は`### 合意済みの除外・保持`の`素材・要求参照`で被覆されるかを検査する。

    `採用範囲`が`終端工程のみ`で始まる採用要求は被覆対象から除く。
    合意表が存在しない、または新形式でない場合は`根拠`列だけで被覆を判定する。
    """
    covered: set[str] = set()
    action_column = action_table.header.index("根拠")
    for row in action_table.rows:
        if len(row) > action_column:
            covered.update(_requirement_references(row[action_column]))
    if exclusion_table is not None and "素材・要求参照" in exclusion_table.header:
        exclusion_column = exclusion_table.header.index("素材・要求参照")
        for row in exclusion_table.rows:
            if len(row) > exclusion_column:
                covered.update(_requirement_references(row[exclusion_column]))
    target = set(materials.adopted_requirement_ids) - set(materials.terminal_only_requirement_ids)
    uncovered = target - covered
    return [
        f"`## 実施内容`の`根拠`又は`### 合意済みの除外・保持`の`素材・要求参照`が採用要求を被覆しない: {requirement_id}"
        for requirement_id in sorted(uncovered)
    ]


def _check_exclusion_table(
    lines: list[tuple[int, str]],
    label: str,
) -> tuple[MarkdownTable | None, list[str], bool]:
    """新旧の合意表を新形式優先で検査し、形式を返す。"""
    tables = extract_tables(lines)
    if any(table.header == PLAN_EXCLUSION_TABLE_HEADER for table in tables):
        table, errors = _check_fixed_table(lines, PLAN_EXCLUSION_TABLE_HEADER, label)
        return table, errors, True
    if any(table.header == PLAN_LEGACY_EXCLUSION_TABLE_HEADER for table in tables):
        table, errors = _check_fixed_table(lines, PLAN_LEGACY_EXCLUSION_TABLE_HEADER, label)
        return table, errors, False
    return None, [f"{label}は{list(PLAN_EXCLUSION_TABLE_HEADER)}の列を持つ表にする"], True


def _check_reference_ids(
    table: MarkdownTable,
    identifiers: set[str],
    requirement_ids: set[str],
    label: str,
    is_new: bool,
) -> list[str]:
    """新旧合意表の素材・要求参照を検査する。"""
    column_name = "素材・要求参照" if is_new else "原文参照"
    column = table.header.index(column_name)
    errors: list[str] = []
    valid_ids = identifiers | requirement_ids
    for row in table.rows:
        if len(row) <= column or not row[column]:
            continue
        references = _split_material_references(row[column])
        for token in references:
            if token not in valid_ids:
                errors.append(f"{label}の{column_name}が提示素材に無い: {token}")
        if is_new:
            if not any(token in identifiers for token in references):
                errors.append(f"{label}の{column_name}へ素材IDを1件以上記載する: {row[column]}")
            if not any(token in requirement_ids for token in references):
                errors.append(f"{label}の{column_name}へ要求IDを1件以上記載する: {row[column]}")
    return errors


def _check_history_rows(table: MarkdownTable, identifiers: set[str]) -> list[str]:
    """変更履歴の起点とユーザー発言行の素材ID記法を検査する。"""
    errors: list[str] = []
    for row in table.rows:
        if len(row) < len(PLAN_HISTORY_TABLE_HEADER):
            continue
        origin, detail = row[1], row[2]
        if origin not in PLAN_HISTORY_ORIGINS:
            errors.append(f"`## 変更履歴`の`起点`は{list(PLAN_HISTORY_ORIGINS)}のいずれかにする: {origin}")
        if origin != "ユーザー発言":
            continue
        references = [token for token in re.split(r",\s*", detail) if token]
        if not references or any(PLAN_MATERIAL_ID_PATTERN.fullmatch(token) is None for token in references):
            errors.append(f"`## 変更履歴`のユーザー発言行は`指摘内容`へ素材IDだけを書く: {detail}")
        for reference in references:
            if reference not in identifiers:
                errors.append(f"`## 変更履歴`のユーザー発言行が参照する素材IDが提示素材に無い: {reference}")
    return errors


def _check_action_relations(table: MarkdownTable) -> list[str]:
    """実施内容表の`ユーザー指示との関係`が3値だけを取るかを検査する。"""
    column = table.header.index("ユーザー指示との関係")
    return [
        f"`## 実施内容`の`ユーザー指示との関係`は{list(PLAN_ACTION_RELATIONS)}のいずれかにする: {row[column]}"
        for row in table.rows
        if len(row) > column and row[column] and row[column] not in PLAN_ACTION_RELATIONS
    ]
