# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F821,I001
# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,unused-import,used-before-assignment,wrong-import-order
"""計画ファイルの構造検査の共通モジュール。

構造検査（`check_plan_file.py`）、AWI登録（`_atk_wi_add.py`）、
2系統のPreToolUse（`pretooluse.py`・`pytools/claude_hook/pretooluse.py`）、
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
    from agent_toolkit._plan.structure.markdown import (
        MarkdownTable,
        PlanHeading,
        _code_span_comment_starts,
        _is_escaped,
        _markdown_excluded_line_indices,
        _table_parser,
        _table_row_cells,
        check_duplicate_headings,
        child_headings,
        extract_headings,
        extract_tables,
        find_heading_index,
        heading_subtree_range,
        iter_markdown_body_lines,
        lines_within,
        markdown_body_start_index,
        markdown_body_text,
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


_HEADING_PATTERN = re.compile(r"^(#{1,6}) +(.*?)\s*$")


from agent_toolkit._plan.structure.constants import *  # noqa: F403
from agent_toolkit._plan.structure.markdown import *  # noqa: F403


def canonical_h2_name(text: str) -> str:
    """固定H2の旧別名を新書式の正規名へ写す。未知の見出しはそのまま返す。"""
    return _PLAN_H2_CANONICAL_BY_ALIAS.get(text, text)


def h2_aliases(text: str) -> tuple[str, ...]:
    """固定H2の正規名に対応する受理名を返す。"""
    canonical = canonical_h2_name(text)
    return PLAN_H2_ALIASES.get(canonical, (canonical,))


def canonical_metadata_field(field: str) -> str:
    """計画メタ情報の項目名の旧名称を正規名へ写す。未知の項目名はそのまま返す。"""
    return _PLAN_METADATA_CANONICAL_BY_FIELD_ALIAS.get(field, field)


def canonical_wi_origin(origin: str) -> str:
    """`由来`欄のWI区分の旧名称を正規名へ写す。未知の区分はそのまま返す。"""
    return _PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS.get(origin, origin)


@dataclass(frozen=True)
class PlanMaterials:
    """計画の提示素材と要求の解析結果を表す。"""

    material_ids: frozenset[str]
    requirement_ids: frozenset[str]
    is_legacy: bool
    adopted_requirement_ids: frozenset[str] = frozenset()
    terminal_only_requirement_ids: frozenset[str] = frozenset()
    is_human_readable: bool = False
    material_paths: frozenset[str] = frozenset()
    feedback_queue_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PlanImplementationUnit:
    """計画ファイル（詳細）の実装単位表1行を表す。"""

    unit_id: str
    purpose: str
    dependencies: tuple[str, ...]
    integration_order: int
    verification: str


@dataclass(frozen=True)
class PlanMetadata:
    """計画メタ情報の解析結果を表す。"""

    parent: str
    """メタ情報を収めていた親H2の見出し文。正規形では`概要`となる。"""

    entries: tuple[tuple[str, str], ...]
    """記載順の(項目名, 生の値)。順序と記法の検査に使う。"""

    values: dict[str, str]
    """認識した項目の値。バッククォートは除去済みで、欠落項目は含めない。"""

    related_wi: tuple[tuple[str, str], ...]
    """`関連WI`の(正本ファイル名, 1行要約)。記載順を保持する。"""

    base_commit_candidates: tuple[str, ...]
    """`ベースコミット`と旧別名`基準コミット`から抽出した16進値の全候補。"""

    @property
    def is_canonical(self) -> bool:
        """正規配置（`## 概要`直下）から読み取ったかを返す。"""
        return self.parent == PLAN_H2_OVERVIEW


_METADATA_ENTRY_PATTERN = re.compile(r"^- (?P<field>[^:]+):(?: (?P<value>.*?))?\s*$")
_METADATA_RELATED_WI_PATTERN = re.compile(r"^  - (?P<filename>[^:]+):(?: (?P<summary>.*?))?\s*$")
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
            entries.append((match.group("field").strip(), match.group("value") or ""))

    related_wi: list[tuple[str, str]] = []
    in_related_wi = False
    for _lineno, line in section:
        entry_match = _METADATA_ENTRY_PATTERN.fullmatch(line)
        if entry_match is not None:
            in_related_wi = canonical_metadata_field(entry_match.group("field").strip()) == PLAN_METADATA_RELATED_WI_FIELD
            continue
        if not in_related_wi:
            continue
        child_match = _METADATA_RELATED_WI_PATTERN.fullmatch(line)
        if child_match is not None:
            related_wi.append((child_match.group("filename").strip(), (child_match.group("summary") or "").strip()))
        elif line.strip():
            in_related_wi = False

    values: dict[str, str] = {}
    conflicts: list[str] = []
    for field, raw_value in entries:
        canonical_field = canonical_metadata_field(field)
        if canonical_field not in (*PLAN_METADATA_MAIN_FIELDS, PLAN_METADATA_DETAIL_FIELD):
            continue
        normalized = _strip_backticks(raw_value)
        if canonical_field in values and values[canonical_field] != normalized:
            conflicts.append(f"計画メタ情報の`{canonical_field}`に競合する値があります")
        values.setdefault(canonical_field, normalized)
    base_candidates = [
        match.group("oid") for _lineno, line in section if (match := _METADATA_BASE_COMMIT_LINE.fullmatch(line)) is not None
    ]
    if conflicts:
        return None, conflicts
    return PlanMetadata(parent, tuple(entries), values, tuple(related_wi), tuple(base_candidates)), []


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
    re.compile(r"(^|/)agent-toolkit/share/rules-[^/]+\.md$"),
    re.compile(r"(^|/)agent-toolkit/rules/.+\.md$"),
    re.compile(r"(^|/)agent-toolkit/skills/[^/]+/SKILL\.md$"),
    re.compile(r"(^|/)agent-toolkit/skills/[^/]+/references/.+\.md$"),
    re.compile(r"(^|/)agent-toolkit/agents/.+\.md$"),
    # chezmoi配布元のテンプレート（`<name>.md.tmpl`）も配布先ではエージェント向け文書として読み込まれるため、
    # `.tmpl`終端を受理する。原本側だけが対象から外れると、テンプレート経由の規範改訂を検査が素通りさせる。
    re.compile(r"(^|/)\.chezmoi-source/dot_claude/rules/.+\.md(\.tmpl)?$"),
    re.compile(r"(^|/)\.chezmoi-source/dot_claude/skills/.+\.md(\.tmpl)?$"),
    # ユーザーのプロジェクトが直接持つ規範文書。配布元固有パスだけを対象にすると、
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

    `agent-toolkit/scripts/_hooks/pretooluse.py`と`agent-toolkit/skills/plan-mode/scripts/check_plan_file.py`が
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
_HUMAN_MATERIAL_LINE_PATTERN = re.compile(r"^\s*-\s+(?P<path>[^/\\\s]+\.md)\s*$")
_STRICT_INTERNAL_PLAN_ID_PATTERN = re.compile(
    r"(?<![0-9A-Za-z_-])(?:R-P-[0-9A-Za-z][0-9A-Za-z_-]*-[0-9]{3}|P-[0-9A-Za-z][0-9A-Za-z_-]*|U-[0-9]{3}|H-[0-9]{3}|C-[0-9]{3}|R[0-9]+-[a-z][a-z0-9]*(?:-[a-z0-9]+)*)(?![0-9A-Za-z_-])"
)
# `P-256`は外部仕様でも使われるため、自由文では旧素材IDとの区別を断定しない。
_INTERNAL_PLAN_ID_PATTERN = re.compile(
    r"(?<![0-9A-Za-z_-])(?:R-P-[0-9A-Za-z][0-9A-Za-z_-]*-[0-9]{3}|P-(?!256(?![0-9A-Za-z_-]))[0-9A-Za-z][0-9A-Za-z_-]*|U-[0-9]{3}|H-[0-9]{3}|C-[0-9]{3}|R[0-9]+-[a-z][a-z0-9]*(?:-[a-z0-9]+)*)(?![0-9A-Za-z_-])"
)


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


def _legacy_expected_h2(work_type: str | None) -> list[str]:
    """旧形式（単一ファイル9節）の固定H2順序を返す。"""
    expected = [PLAN_H2_OVERVIEW, PLAN_H2_ACTION, PLAN_H2_MATERIALS, PLAN_H2_LEGACY_HISTORY]
    if work_type == "バグ対応":
        expected.append(PLAN_H2_BUG)
    expected.extend((PLAN_H2_PERMANENCE, PLAN_H2_IMPLEMENTATION, PLAN_H2_COMPLETION, PLAN_H2_LEGACY_PROGRESS))
    return expected


def _detail_expected_h2(work_type: str | None) -> list[str]:
    """新書式の計画ファイル（詳細）の固定H2順序を返す。"""
    expected = [PLAN_H2_BUG] if work_type == "バグ対応" else []
    expected.extend(PLAN_DETAIL_H2_ORDER)
    return expected


def parse_plan_materials(content: str) -> tuple[PlanMaterials | None, list[str]]:
    """提示素材を新形式または旧形式として解析する。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    index = find_heading_index(headings, 2, PLAN_H2_MATERIALS)
    if index is None:
        return None, ["固定H2の提示素材を検査できない"]
    section = _materials_section(content, headings, index)
    tables = extract_tables(section)
    if (
        not any(table.header in {PLAN_MATERIAL_TABLE_HEADER, PLAN_REQUIREMENT_TABLE_HEADER} for table in tables)
        and not any(_LEGACY_MATERIAL_ID_PATTERN.fullmatch(line.strip()) for _lineno, line in section)
        and not any(_MATERIAL_ID_CANDIDATE_PATTERN.fullmatch(line.strip()) for _lineno, line in section)
    ):
        human_materials, human_errors = _check_human_materials(section)
        if human_materials is not None:
            return human_materials, human_errors
    if any(table.header in {PLAN_MATERIAL_TABLE_HEADER, PLAN_REQUIREMENT_TABLE_HEADER} for table in tables):
        return _check_new_materials(section)
    identifiers, errors = _check_materials(content, body, headings, index)
    return PlanMaterials(frozenset(identifiers), frozenset(), True), errors


def parse_plan_implementation_units(
    content: str,
) -> tuple[tuple[PlanImplementationUnit, ...] | None, list[str]]:
    """計画ファイル（詳細）の`### 実装単位`の固定表を解析して構造違反を返す。

    単位表は新書式の計画ファイル（詳細）だけの必須契約であり、違反は計画を実装順へ分解できないためerrorとする。
    旧6列表は読み取り互換として受理し、`対象の実施内容`列の値を検査しない。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    implementation_index = find_heading_index(headings, 2, PLAN_H2_IMPLEMENTATION)
    if implementation_index is None:
        return None, []
    matching_headings = [
        (position, heading)
        for position, heading in child_headings(headings, implementation_index, 3)
        if heading.text == PLAN_IMPLEMENTATION_UNITS_H3
    ]
    if len(matching_headings) != 1:
        return None, [
            f"`## {PLAN_H2_IMPLEMENTATION}`直下に`### {PLAN_IMPLEMENTATION_UNITS_H3}`を1件置く: 実際={len(matching_headings)}件"
        ]

    position, _heading = matching_headings[0]
    start, end = heading_subtree_range(headings, position)
    tables = extract_tables(lines_within(body, start, end))
    matching = [
        candidate
        for candidate in tables
        if candidate.header
        in (
            PLAN_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
            PLAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
            PLAN_LEGACY_IMPLEMENTATION_UNITS_TABLE_HEADER,
        )
    ]
    if not matching:
        return None, [f"`### {PLAN_IMPLEMENTATION_UNITS_H3}`は{list(PLAN_IMPLEMENTATION_UNITS_TABLE_HEADER)}の列を持つ表にする"]
    table = matching[0]
    errors = [f"`### {PLAN_IMPLEMENTATION_UNITS_H3}`の固定表は1件必要: 実際={len(matching)}件"] if len(matching) != 1 else []
    if not table.rows:
        errors.append(f"`### {PLAN_IMPLEMENTATION_UNITS_H3}`の表に1行以上の内容が必要")
    for index, row in enumerate(table.rows):
        if len(row) != len(table.header) or any(not cell for cell in row):
            errors.append(
                f"`### {PLAN_IMPLEMENTATION_UNITS_H3}`の表に空cellまたは列数不一致の行がある: {table.row_location(index)}"
            )

    is_human = table.header == PLAN_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER
    units: list[PlanImplementationUnit] = []
    for row in table.rows:
        if len(row) != len(table.header) or any(not cell for cell in row):
            continue
        if table.header == PLAN_LEGACY_IMPLEMENTATION_UNITS_TABLE_HEADER:
            unit_id, purpose, _action_value, dependency_value, order_value, verification = row
        else:
            unit_id, purpose, dependency_value, order_value, verification = row
        if not is_human and PLAN_IMPLEMENTATION_UNIT_ID_PATTERN.fullmatch(unit_id) is None:
            errors.append(f"実装単位IDは`U-[0-9]{{3}}`形式にする: {unit_id}")
        if is_human and (
            _STRICT_INTERNAL_PLAN_ID_PATTERN.fullmatch(unit_id)
            or _INTERNAL_PLAN_ID_PATTERN.search(unit_id)
            or unit_id in {"なし", "-"}
        ):
            errors.append(f"実装単位は合成IDではない説明的な名前にする: {unit_id}")
        if is_human and "," in unit_id:
            errors.append(f"実装単位名へASCIIカンマを含めない: {unit_id}")

        if dependency_value == "なし":
            dependencies: tuple[str, ...] = ()
        else:
            dependencies = _comma_separated_values(dependency_value)
            if any(not value for value in dependencies):
                errors.append(f"実装単位`{unit_id}`の`先行依存`に空の値を置かない")
            if not is_human and any(PLAN_IMPLEMENTATION_UNIT_ID_PATTERN.fullmatch(value) is None for value in dependencies):
                errors.append(f"実装単位`{unit_id}`の`先行依存`は実装単位ID又は`なし`にする")
            if tuple(dict.fromkeys(dependencies)) != dependencies:
                errors.append(f"実装単位`{unit_id}`の`先行依存`は重複なしで列挙する")

        if not order_value.isdecimal() or int(order_value) < 1:
            errors.append(f"実装単位`{unit_id}`の`統合順`は1以上の整数にする")
            integration_order = 0
        else:
            integration_order = int(order_value)
        units.append(
            PlanImplementationUnit(
                unit_id,
                purpose,
                dependencies,
                integration_order,
                verification,
            )
        )

    unit_ids = tuple(unit.unit_id for unit in units)
    if is_human:
        if len(set(unit_ids)) != len(unit_ids):
            errors.append("実装単位は表内で一意の説明的な名前にする")
    else:
        expected_ids = tuple(f"U-{index:03d}" for index in range(1, len(units) + 1))
        if unit_ids != expected_ids:
            errors.append(f"実装単位IDは`U-001`から欠番なく昇順に置く: 実際={list(unit_ids)}")

    integration_orders = tuple(unit.integration_order for unit in units)
    if integration_orders != tuple(range(1, len(units) + 1)):
        errors.append(f"実装単位の`統合順`は1から欠番なく昇順に置く: 実際={list(integration_orders)}")

    units_by_id = {unit.unit_id: unit for unit in units}
    for unit in units:
        for dependency in unit.dependencies:
            dependency_unit = units_by_id.get(dependency)
            if dependency_unit is None:
                errors.append(f"実装単位`{unit.unit_id}`の`先行依存`が実装単位表に無い: {dependency}")
            elif dependency_unit.integration_order >= unit.integration_order:
                errors.append(f"実装単位`{unit.unit_id}`の`先行依存`が`統合順`より前にない: {dependency}")
    return tuple(units), errors
