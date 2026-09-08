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


from agent_toolkit._plan.structure.constants import *  # noqa: F403
from agent_toolkit._plan.structure.markdown import *  # noqa: F403
from agent_toolkit._plan.structure.parsing import *  # noqa: F403
from agent_toolkit._plan.structure.sections import *  # noqa: F403


def _check_action_references(
    table: MarkdownTable,
    requirement_ids: set[str],
    adopted_requirement_ids: set[str],
) -> list[str]:
    """実施内容表の採否に応じた根拠の記載と要求参照を検査する。"""
    column = table.header.index("根拠")
    errors: list[str] = []

    if table.header == PLAN_LEGACY_ACTION_TABLE_HEADER:
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

    decision_column = table.header.index("採否")
    for row in table.rows:
        if len(row) <= decision_column:
            continue
        decision = row[decision_column]
        root = row[column] if len(row) > column else ""
        if decision in ("採用", "部分採用"):
            if not root:
                errors.append("`## 実施内容`の採用系の`根拠`は採用要求IDを1件以上記載する")
                continue
            references = _requirement_references(root)
            if not references:
                errors.append(f"`## 実施内容`の採用系の`根拠`は採用要求IDを参照する: {root}")
                continue
            for reference in references:
                if reference not in requirement_ids:
                    errors.append(f"`## 実施内容`の`根拠`が提示素材の要求表に無い: {reference}")
                elif reference not in adopted_requirement_ids:
                    errors.append(f"`## 実施内容`の`根拠`へ不採用要求を参照できない: {reference}")
            continue
        if decision in PLAN_ACTION_NON_ADOPTED_DECISIONS:
            if not root:
                errors.append("`## 実施内容`の非採用系の`根拠`は理由を記載する")
                continue
            for reference in _requirement_references(root):
                if reference not in requirement_ids:
                    errors.append(f"`## 実施内容`の`根拠`が提示素材の要求表に無い: {reference}")
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
    decision_column = action_table.header.index("採否") if action_table.header == PLAN_ACTION_TABLE_HEADER else None
    for row in action_table.rows:
        if len(row) > action_column and (
            decision_column is None or (len(row) > decision_column and row[decision_column] in ("採用", "部分採用"))
        ):
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


def _check_history_rows(
    table: MarkdownTable,
    identifiers: set[str],
    *,
    allow_legacy_review_ids: bool = False,
    allow_legacy_review_tracks: bool = False,
) -> list[str]:
    """変更履歴の起点、レビューID及びユーザー発言行の素材ID記法を検査する。"""
    errors: list[str] = []
    review_ids: set[str] = set()
    review_keys: set[tuple[str, int]] = set()
    for row in table.rows:
        if len(row) < len(PLAN_HISTORY_TABLE_HEADER):
            continue
        origin, detail = row[1], row[2]
        if origin not in PLAN_HISTORY_ORIGINS:
            errors.append(f"`## 変更履歴`の`起点`は{list(PLAN_HISTORY_ORIGINS)}のいずれかにする: {origin}")
        if origin == "レビュー指摘":
            review_id = row[0]
            if review_id in review_ids:
                errors.append(f"`## 変更履歴`のレビュー指摘行は`ID`を重複させない: {review_id}")
            review_ids.add(review_id)
            if allow_legacy_review_tracks:
                continue
            if allow_legacy_review_ids and PLAN_LEGACY_HISTORY_REVIEW_ID_PATTERN.fullmatch(review_id):
                continue
            match = PLAN_HISTORY_REVIEW_ID_PATTERN.fullmatch(review_id)
            if match is None or int(match["round"]) == 0:
                errors.append(f"`## 変更履歴`のレビュー指摘行の`ID`は`R<正の整数>-<系統名>`形式にする: {review_id}")
            elif match["track"] not in (
                *PLAN_HISTORY_TRACK_VALUES,
                *(PLAN_LEGACY_HISTORY_TRACK_VALUES if allow_legacy_review_ids else ()),
            ):
                errors.append(
                    f"`## 変更履歴`のレビュー指摘行の`ID`は`R<正の整数>-<系統名>`形式にする。系統名は"
                    f"{list(PLAN_HISTORY_TRACK_VALUES)}のいずれかにする: {review_id}"
                )
            else:
                review_key = (match["track"], int(match["round"]))
                if review_key in review_keys:
                    errors.append(
                        f"`## 変更履歴`のレビュー指摘行は系統・ラウンドを重複させない: {review_key[0]}, {review_key[1]}"
                    )
                review_keys.add(review_key)
        if origin != "ユーザー発言":
            continue
        references = [token for token in re.split(r",\s*", detail) if token]
        if not references or any(PLAN_MATERIAL_ID_PATTERN.fullmatch(token) is None for token in references):
            errors.append(f"`## 変更履歴`のユーザー発言行は`指摘内容`へ素材IDだけを書く: {detail}")
        for reference in references:
            if reference not in identifiers:
                errors.append(f"`## 変更履歴`のユーザー発言行が参照する素材IDが提示素材に無い: {reference}")
    return errors


def _check_action_decisions(table: MarkdownTable) -> list[str]:
    """新形式の実施内容表の`採否`が宣言済みの値であることを検査する。"""
    if table.header != PLAN_ACTION_TABLE_HEADER:
        return []
    column = table.header.index("採否")
    return [
        f"`## 実施内容`の`採否`は{list(PLAN_ACTION_DECISIONS)}のいずれかにする: {row[column]}"
        for row in table.rows
        if len(row) > column and row[column] and row[column] not in PLAN_ACTION_DECISIONS
    ]


def _check_action_relations(table: MarkdownTable) -> list[str]:
    """実施内容表の`ユーザー指示との関係`の許容値を採否別に検査する。"""
    column = table.header.index("ユーザー指示との関係")
    decision_column = table.header.index("採否") if table.header == PLAN_ACTION_TABLE_HEADER else None
    errors: list[str] = []
    for row in table.rows:
        if len(row) <= column or not row[column]:
            continue
        allowed = PLAN_ACTION_RELATIONS
        if (
            decision_column is not None
            and len(row) > decision_column
            and row[decision_column] in PLAN_ACTION_NON_ADOPTED_DECISIONS
        ):
            allowed = (*PLAN_ACTION_RELATIONS, PLAN_NON_QUEUE_VALUE)
        if row[column] not in allowed:
            errors.append(f"`## 実施内容`の`ユーザー指示との関係`は{list(allowed)}のいずれかにする: {row[column]}")
    return errors
