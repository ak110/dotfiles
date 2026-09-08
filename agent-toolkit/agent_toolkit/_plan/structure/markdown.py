# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F821,I001
# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,unused-import,used-before-assignment,wrong-import-order
"""計画ファイルの構造検査の共通モジュール。

構造検査（`check_plan_file.py`）、AWI登録（`_atk_wi_add.py`）、
2系統のPreToolUse（`pretooluse.py`・`scripts/claude_hook_pretooluse.py`）、
PostToolUse（`posttooluse.py`）が本モジュールから同じ判定結果を得る。
成果物契約は`agent-toolkit/skills/plan-mode/references/plan-file-standards.md`が定める。
本モジュールの構造定数は計画ファイルの見出し、固定H3及び表の行名の正本であり、同書は当該定数から導いた受理形式を記述する。

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

from __future__ import annotations

import functools
import pathlib
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

import markdown_it
import markdown_it.common.html_re
import markdown_it.rules_inline
import markdown_it.token

from agent_toolkit._plan import locations as _plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error

if TYPE_CHECKING:
    from agent_toolkit._plan.structure.constants import (
        _FRONTMATTER_DELIMITER,
        _FRONTMATTER_SOURCE_PATTERN,
        _PLAN_H2_CANONICAL_BY_ALIAS,
        _PLAN_METADATA_CANONICAL_BY_FIELD_ALIAS,
        _PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS,
        BUMP_MANIFEST_PATHS,
        MARKETPLACE_MANIFEST_PATH,
        PLAN_ACTION_DECISIONS,
        PLAN_ACTION_NON_ADOPTED_DECISIONS,
        PLAN_ACTION_RELATIONS,
        PLAN_ACTION_TABLE_HEADER,
        PLAN_AGENT_WI_ORIGIN,
        PLAN_BUG_CAUSE_TABLE_HEADER,
        PLAN_BUG_CAUSE_TABLE_ROWS,
        PLAN_BUG_FILE_REFERENCE_LEGACY_PREFIX,
        PLAN_BUG_FILE_REFERENCE_PREFIX,
        PLAN_BUG_TABLE_HEADER,
        PLAN_BUG_TABLE_ROWS,
        PLAN_DETAIL_H2_ORDER,
        PLAN_DETAIL_SUFFIX,
        PLAN_EXCLUSION_H3,
        PLAN_EXCLUSION_TABLE_HEADER,
        PLAN_H2_ACTION,
        PLAN_H2_AGENT_JUDGMENT,
        PLAN_H2_ALIASES,
        PLAN_H2_BUG,
        PLAN_H2_COMPLETION,
        PLAN_H2_HISTORY,
        PLAN_H2_IMPLEMENTATION,
        PLAN_H2_LEGACY_AGENT_JUDGMENT,
        PLAN_H2_LEGACY_HISTORY,
        PLAN_H2_LEGACY_PROGRESS,
        PLAN_H2_MATERIALS,
        PLAN_H2_OVERVIEW,
        PLAN_H2_PERMANENCE,
        PLAN_H2_PROGRESS,
        PLAN_H2_TERMINATION,
        PLAN_H2_VERIFICATION,
        PLAN_HISTORY_ORIGINS,
        PLAN_HISTORY_REVIEW_ID_PATTERN,
        PLAN_HISTORY_TABLE_HEADER,
        PLAN_HISTORY_TRACK_VALUES,
        PLAN_HISTORY_USER_EVENT_PATTERN,
        PLAN_HISTORY_USER_EVENT_PREFIX,
        PLAN_HUMAN_ACTION_TABLE_HEADER,
        PLAN_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
        PLAN_HUMAN_JUDGMENT_TABLE_HEADER,
        PLAN_HUMAN_ORIGINS,
        PLAN_HUMAN_REVIEW_ORIGIN_PATTERN,
        PLAN_HUMAN_REVIEW_ROOT_PATTERN,
        PLAN_HUMAN_WI_ORIGIN,
        PLAN_IMPLEMENTATION_UNIT_ID_PATTERN,
        PLAN_IMPLEMENTATION_UNITS_H3,
        PLAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
        PLAN_LEGACY_ACTION_TABLE_HEADER,
        PLAN_LEGACY_AGENT_FEEDBACK_ORIGIN,
        PLAN_LEGACY_BUG_TABLE_ROWS,
        PLAN_LEGACY_EXCLUSION_TABLE_HEADER,
        PLAN_LEGACY_HISTORY_REVIEW_ID_PATTERN,
        PLAN_LEGACY_HISTORY_TRACK_VALUES,
        PLAN_LEGACY_HISTORY_USER_EVENT_PATTERN,
        PLAN_LEGACY_HUMAN_FEEDBACK_ORIGIN,
        PLAN_LEGACY_IMPLEMENTATION_UNITS_TABLE_HEADER,
        PLAN_LEGACY_MAIN_H2_ORDER,
        PLAN_LEGACY_PERMANENCE_H3,
        PLAN_LEGACY_STANDALONE_BUG_TABLE_ROWS,
        PLAN_MAIN_H2_ORDER,
        PLAN_MATERIAL_ID_PATTERN,
        PLAN_MATERIAL_TABLE_HEADER,
        PLAN_MATERIAL_TYPES,
        PLAN_METADATA_DETAIL_FIELD,
        PLAN_METADATA_FALLBACK_H2,
        PLAN_METADATA_FIELD_ALIASES,
        PLAN_METADATA_FIELDS,
        PLAN_METADATA_H3,
        PLAN_METADATA_LEGACY_DETAIL_FIELD,
        PLAN_METADATA_LEGACY_RELATED_FEEDBACK_FIELD,
        PLAN_METADATA_MAIN_FIELDS,
        PLAN_METADATA_QUOTED_FIELDS,
        PLAN_METADATA_RELATED_WI_FIELD,
        PLAN_METADATA_TWO_FILE_FIELDS,
        PLAN_NON_QUEUE_VALUE,
        PLAN_PERMANENCE_H3,
        PLAN_PERMANENCE_TABLE_HEADER,
        PLAN_PLACEHOLDER_WORDS,
        PLAN_PROGRESS_TABLE_HEADER,
        PLAN_QUEUE_ID_PATTERN,
        PLAN_REFACTORING_TABLE_ROWS,
        PLAN_REQUIREMENT_ID_PATTERN,
        PLAN_REQUIREMENT_TABLE_HEADER,
        PLAN_TWO_FILE_MAIN_H2_ORDER,
        PLAN_VERIFICATION_TABLE_HEADER,
        PLAN_VERIFICATION_TABLE_ROWS,
        PLAN_WI_ANSWER_HEADING,
        PLAN_WI_ORIGIN_ALIASES,
        PLAN_WI_ORIGIN_PATTERN,
        PLAN_WI_SOURCE_KEY,
        PLAN_WI_USER_COMMENT_HEADING,
        PLAN_WORK_TYPES,
        PLUGIN_MANIFEST_PATH,
    )
    from agent_toolkit._plan.structure.parsing import (
        _HEADING_PATTERN,
        _HUMAN_MATERIAL_LINE_PATTERN,
        _INTERNAL_PLAN_ID_PATTERN,
        _LEGACY_MATERIAL_ID_PATTERN,
        _MATERIAL_FENCE_PATTERN,
        _MATERIAL_ID_CANDIDATE_PATTERN,
        _METADATA_BASE_COMMIT_LINE,
        _METADATA_ENTRY_PATTERN,
        _METADATA_RELATED_WI_PATTERN,
        _REFERENCE_SEPARATOR_PATTERN,
        _STRICT_INTERNAL_PLAN_ID_PATTERN,
        AGENT_DOC_TARGET_BASENAMES,
        AGENT_DOC_TARGET_PATTERNS,
        PlanImplementationUnit,
        PlanMaterials,
        PlanMetadata,
        _detail_expected_h2,
        _find_table_with_rows,
        _is_placeholder_only,
        _legacy_expected_h2,
        _strip_backticks,
        canonical_h2_name,
        canonical_metadata_field,
        canonical_wi_origin,
        extract_implementer_region,
        h2_aliases,
        is_agent_doc_target_file,
        is_agent_facing_md,
        parse_plan_implementation_units,
        parse_plan_materials,
        parse_plan_metadata,
    )
    from agent_toolkit._plan.structure.references import (
        _check_action_decisions,
        _check_action_references,
        _check_action_relations,
        _check_exclusion_table,
        _check_history_rows,
        _check_reference_ids,
        _check_requirement_coverage,
    )
    from agent_toolkit._plan.structure.sections import (
        _bug_file_reference_values,
        _check_action_section,
        _check_action_table,
        _check_agent_judgment_section,
        _check_bug_and_permanence,
        _check_bug_sections,
        _check_bug_unit_sections,
        _check_child_heading_sequence,
        _check_fixed_h2_layout,
        _check_fixed_table,
        _check_h1,
        _check_h3_and_deeper,
        _check_history_section,
        _check_human_action_table,
        _check_human_history_section,
        _check_human_materials,
        _check_human_review_root,
        _check_materials,
        _check_materials_and_identifiers,
        _check_metadata_block,
        _check_new_materials,
        _check_overview_section,
        _check_permanence_sections,
        _check_progress_section,
        _check_termination_section,
        _check_verification_section,
        _collect_origin_notices,
        _comma_separated_values,
        _find_wi_source,
        _has_frontmatter_source,
        _has_machine_detectable_human_origin,
        _materials_section,
        _requirement_references,
        _split_material_references,
        _validate_material_row,
        check_bug_file_structure,
        check_plan_detail_structure,
        check_plan_main_structure,
        check_plan_related_wi,
        check_plan_structure,
        extract_bug_file_reference,
        has_adopted_human_user_instruction,
        has_human_action_table,
        has_legacy_action_table,
        has_legacy_bug_investigation_table,
        has_legacy_bug_table,
        has_legacy_history_user_event,
        has_progress_log_rows,
        is_canonical_main_format,
        legacy_wi_origins,
    )

"""`scripts/agent_toolkit_bump.py`が更新するmanifestファイルの相対パス集合。

`agent_toolkit_bump.py`側のリテラルとの一致は`scripts/agent_toolkit_bump_test.py`が検証する。
"""


from agent_toolkit._plan.structure.constants import *  # noqa: F403


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


def markdown_body_text(content: str) -> str:
    """Markdown本文の有効行だけを連結したテキストを返す。

    除外領域の定義は`iter_markdown_body_lines`に従う。
    本文を文字列パターンで走査する処理は、除外領域内の記述を対象にしないため、
    本関数または`iter_markdown_body_lines`を経由して入力を得る。
    """
    return "\n".join(line for _lineno, line in iter_markdown_body_lines(content))


@dataclass(frozen=True)
class PlanHeading:
    """Markdown本文で有効な見出し1件を表す。"""

    lineno: int
    level: int
    text: str


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


def check_duplicate_headings(content: str) -> list[str]:
    """同じ祖先見出しの下に同じ文言の見出しが複数現れる状態を検出する。

    節の置換で撤回済みの記述が重複ブロックとして残ると、実装担当が旧案を確定文面として読む。
    判定キーへ祖先見出しの文言列を含めるのは、同じ文言の見出しが別の親の下に現れる構成が
    計画書式では正当なためである（既存計画531件の実測で、経路による判定の一致は0件、
    文言だけの判定の一致は2ファイル）。
    """
    seen: dict[tuple[str, ...], int] = {}
    stack: list[PlanHeading] = []
    errors: list[str] = []
    for heading in extract_headings(content):
        while stack and stack[-1].level >= heading.level:
            stack.pop()
        key = tuple(item.text for item in stack) + (heading.text,)
        first = seen.get(key)
        if first is None:
            seen[key] = heading.lineno
        else:
            parent = "/".join(key[:-1]) or "文書直下"
            errors.append(
                f"同じ見出しが重複している: `{'#' * heading.level} {heading.text}`"
                f"（{parent}配下、{first}行目と{heading.lineno}行目）"
            )
        stack.append(heading)
    return errors


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
    """指定階層・指定見出し文（旧別名を含む）の最初の索引を返す。"""
    accepted = h2_aliases(text) if level == 2 else (text,)
    return next(
        (index for index, heading in enumerate(headings) if heading.level == level and heading.text in accepted),
        None,
    )


@dataclass(frozen=True)
class MarkdownTable:
    """パイプ表1件の見出し行と本文行を表す。"""

    lineno: int
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    row_linenos: tuple[int, ...]
    row_sources: tuple[str, ...]

    def row_labels(self) -> tuple[str, ...]:
        """各行の第1列を返す。"""
        return tuple(row[0] if row else "" for row in self.rows)

    def row_location(self, index: int) -> str:
        """指定した本文行の行番号と原文を返す。"""
        if index < 0 or index >= len(self.rows):
            return ""
        return f"{self.row_linenos[index]}行目: {self.row_sources[index]}"


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
    row_linenos: list[int] = []
    row_sources: list[str] = []
    in_body = False
    for token in _table_parser().parse("\n".join(source_lines)):
        if token.type == "table_open":
            assert token.map is not None
            lineno = lines[token.map[0]][0]
            header, rows, row_linenos, row_sources, in_body = (), [], [], [], False
        elif token.type == "thead_open":
            assert token.map is not None
            header = _table_row_cells(source_lines[token.map[0]])
        elif token.type == "tbody_open":
            in_body = True
        elif token.type == "tr_open" and in_body:
            assert token.map is not None
            source_index = token.map[0]
            rows.append(_table_row_cells(source_lines[source_index]))
            row_linenos.append(lines[source_index][0])
            row_sources.append(source_lines[source_index])
        elif token.type == "table_close":
            tables.append(MarkdownTable(lineno, header, tuple(rows), tuple(row_linenos), tuple(row_sources)))
    return tables
