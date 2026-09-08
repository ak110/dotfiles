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

import markdown_it
import markdown_it.common.html_re
import markdown_it.rules_inline
import markdown_it.token

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _plan import locations as _plan_file  # noqa: E402  # pylint: disable=wrong-import-position,import-error


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _plan.structure.constants import (
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
        PLAN_IMPLEMENTATION_UNITS_H3,
        PLAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
        PLAN_IMPLEMENTATION_UNIT_ID_PATTERN,
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
        PLAN_METADATA_FIELDS,
        PLAN_METADATA_FIELD_ALIASES,
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
        _FRONTMATTER_DELIMITER,
        _FRONTMATTER_SOURCE_PATTERN,
        _PLAN_H2_CANONICAL_BY_ALIAS,
        _PLAN_METADATA_CANONICAL_BY_FIELD_ALIAS,
        _PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS,
        plan_human_review_path_is_absolute,
    )
    from _plan.structure.markdown import (
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
    from _plan.structure.parsing import (
        AGENT_DOC_TARGET_BASENAMES,
        AGENT_DOC_TARGET_PATTERNS,
        PlanImplementationUnit,
        PlanMaterials,
        PlanMetadata,
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
    from _plan.structure.references import (
        _check_action_decisions,
        _check_action_references,
        _check_action_relations,
        _check_exclusion_table,
        _check_history_rows,
        _check_reference_ids,
        _check_requirement_coverage,
    )


from _plan.structure.constants import *  # noqa: F403
from _plan.structure.markdown import *  # noqa: F403
from _plan.structure.parsing import *  # noqa: F403


def _check_fixed_h2_layout(
    headings: list[PlanHeading],
    expected: list[str],
    *,
    disallow_bug_for_normal: bool = False,
    work_type: str | None = None,
) -> list[str]:
    """全H2の有無、一意性、固定順序を検査する。"""
    errors: list[str] = []
    actual_h2_texts = [heading.text for heading in headings if heading.level == 2]
    expected_canonical = [canonical_h2_name(name) for name in expected]
    h2_texts = [canonical_h2_name(name) for name in actual_h2_texts]
    cardinality_error = False
    for name in expected_canonical:
        count = h2_texts.count(name)
        if count != 1:
            cardinality_error = True
            errors.append(f"固定H2`## {name}`は1件必要: 実際={count}件")

    unexpected = [name for name in h2_texts if name not in expected_canonical]
    has_unexpected = bool(unexpected)
    if disallow_bug_for_normal and work_type == "通常変更" and PLAN_H2_BUG in unexpected:
        errors.append(f"作業種別が`通常変更`の計画に`## {PLAN_H2_BUG}`は置かない")
        unexpected = [name for name in unexpected if name != PLAN_H2_BUG]
    if unexpected:
        errors.append(f"固定H2は{expected_canonical}だけをこの順序で置く: 実際={actual_h2_texts}")
    if not cardinality_error and not has_unexpected and h2_texts != expected_canonical:
        errors.append(f"固定H2は{expected_canonical}をこの順序で置く: 実際={actual_h2_texts}")
    return errors


def _check_child_heading_sequence(
    headings: list[PlanHeading],
    index: int,
    level: int,
    expected: tuple[str, ...],
    parent_label: str,
    optional: tuple[str, ...] = (),
) -> list[str]:
    """指定見出しの直下にある固定見出しの有無、一意性、順序を検査する。

    ``optional``は廃止済みの見出しなど、置かなくてよく、置いても違反としない見出しを指す。
    """
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
    unexpected = [name for name in children if name not in expected and name not in optional]
    if unexpected:
        errors.append(f"{parent_label}直下に固定見出し以外のH{level}は置かない: 実際={unexpected}")
    return errors


def _check_metadata_block(
    content: str, *, expected_fields: tuple[str, ...] = PLAN_METADATA_FIELDS
) -> tuple[str | None, list[str]]:
    """計画メタ情報の配置、項目、順序、記法、値を検査して(作業種別, エラー)を返す。

    `expected_fields`は対象書式が定める計画メタ情報の項目順を渡す。
    """
    metadata, errors = parse_plan_metadata(content)
    if metadata is None:
        return None, errors or [f"`## {PLAN_H2_OVERVIEW}`直下の`### {PLAN_METADATA_H3}`を検査できない"]
    if not metadata.is_canonical:
        errors.append(f"計画メタ情報は`## {PLAN_H2_OVERVIEW}`直下へ置く: 実際=`## {metadata.parent}`直下")
    fields = [canonical_metadata_field(field) for field, _value in metadata.entries]
    if fields != list(expected_fields):
        errors.append(f"計画メタ情報は{list(expected_fields)}をこの順序で1行ずつ置く: 実際={fields}")
    for field, raw_value in metadata.entries:
        canonical_field = canonical_metadata_field(field)
        if canonical_field not in expected_fields:
            continue
        quoted = raw_value.startswith("`") and raw_value.endswith("`") and len(raw_value) >= 2
        if canonical_field in PLAN_METADATA_QUOTED_FIELDS and not quoted:
            errors.append(f"計画メタ情報の`{field}`はバッククォートで囲む")
        if canonical_field not in PLAN_METADATA_QUOTED_FIELDS and quoted:
            errors.append(f"計画メタ情報の`{field}`はバッククォートで囲まない")
        if not _strip_backticks(raw_value) and canonical_field != PLAN_METADATA_RELATED_WI_FIELD:
            errors.append(f"計画メタ情報の`{field}`が空である")
    if PLAN_METADATA_RELATED_WI_FIELD in expected_fields:
        errors.extend(check_plan_related_wi(metadata))
    work_type = metadata.values.get("作業種別")
    if work_type is not None and work_type not in PLAN_WORK_TYPES:
        errors.append(f"計画メタ情報の`作業種別`は{list(PLAN_WORK_TYPES)}のいずれかで記載する")
        work_type = None
    # `ベースコミット`は計画作成時点の参照値であり、存在・完全長・HEADとの一致は
    # 計画構造の成立条件ではない。実際のGit操作へ渡す値だけを各操作側で検証する。
    return work_type, errors


def check_plan_related_wi(metadata: PlanMetadata) -> list[str]:
    """計画メタ情報の`関連WI`の値と子項目を検査する。"""
    errors: list[str] = []
    related_value = metadata.values.get(PLAN_METADATA_RELATED_WI_FIELD, "")
    if related_value == "なし":
        if metadata.related_wi:
            errors.append("計画メタ情報の`関連WI: なし`と子項目を併記しない")
    elif related_value:
        errors.append("計画メタ情報の`関連WI`は子項目又は`なし`で記載する")
    elif not metadata.related_wi:
        errors.append("計画メタ情報の`関連WI`には正本ファイル名と1行要約を1件以上記載する")
    seen_wi: set[str] = set()
    for filename, summary in metadata.related_wi:
        if PLAN_QUEUE_ID_PATTERN.fullmatch(filename) is None:
            errors.append(f"計画メタ情報の`関連WI`のファイル名が不正である: {filename}")
        if not summary:
            errors.append(f"計画メタ情報の`関連WI`の1行要約が空である: {filename}")
        if filename in seen_wi:
            errors.append(f"計画メタ情報の`関連WI`のファイル名が重複している: {filename}")
        seen_wi.add(filename)
    return errors


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

    if material_type in {"ユーザー指示", "利用者指示"}:
        if source == "本セッション" and citation != "全文":
            errors.append("ユーザー指示素材は本セッションの引用範囲を全文にする")
    elif material_type in {"ユーザー合意", "利用者合意"}:
        if source == "本セッション" and citation != "全文":
            errors.append("本セッションのユーザー合意素材の引用範囲を全文にする")
        elif (source == "AskUserQuestion" or source.startswith("TBD:")) and citation != "回答全文":
            errors.append("AskUserQuestion又はTBDのユーザー合意素材の引用範囲を回答全文にする")
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
    feedback_queue_ids: set[str] = set()
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
        if row[1] == "フィードバック" and PLAN_QUEUE_ID_PATTERN.fullmatch(row[2]):
            if row[2] in feedback_queue_ids:
                errors.append(f"フィードバック素材のキューIDが重複している: {row[2]}")
            feedback_queue_ids.add(row[2])
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
        feedback_queue_ids=frozenset(feedback_queue_ids),
    ), errors


def _check_human_materials(section: list[tuple[int, str]]) -> tuple[PlanMaterials | None, list[str]]:
    """新規書式の`## 提示素材`にファイル名だけが列挙されていることを検査する。"""
    nonempty = [(lineno, line.strip()) for lineno, line in section if line.strip()]
    if not nonempty:
        return PlanMaterials(frozenset(), frozenset(), False, is_human_readable=True), [
            "新規書式の`## 提示素材`はAWI又はUWIのファイル名を1件以上、または`なし`と記載する"
        ]

    if len(nonempty) == 1 and nonempty[0][1] == "なし":
        return PlanMaterials(frozenset(), frozenset(), False, is_human_readable=True), []

    errors: list[str] = []
    paths: list[str] = []
    for _lineno, line in nonempty:
        match = _HUMAN_MATERIAL_LINE_PATTERN.fullmatch(line)
        if match is None:
            errors.append(f"提示素材は正本ファイル名の箇条書き又は`なし`だけにする: {line}")
            continue
        path = match.group("path").strip()
        if _STRICT_INTERNAL_PLAN_ID_PATTERN.search(path):
            errors.append(f"提示素材へ合成IDを記載しない: {path}")
        if path in paths:
            errors.append(f"提示素材のファイル名を重複させない: {path}")
        paths.append(path)
    if not paths:
        errors.append("提示素材にAWI又はUWIのファイル名が1件以上必要")
    return PlanMaterials(
        frozenset(),
        frozenset(),
        False,
        is_human_readable=True,
        material_paths=frozenset(paths),
    ), errors


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


def _comma_separated_values(value: str) -> tuple[str, ...]:
    """ASCIIカンマ区切りの値を前後の空白を除いて返す。"""
    return tuple(part.strip() for part in value.split(","))


def _bug_file_reference_values(section: list[tuple[int, str]]) -> list[str]:
    """バグ調査節直下の計画ファイル（バグ）参照行からパス文字列を抽出する。"""
    prefixes = (PLAN_BUG_FILE_REFERENCE_PREFIX, PLAN_BUG_FILE_REFERENCE_LEGACY_PREFIX)
    return [
        stripped[len(prefix) :].strip()
        for _lineno, line in section
        if (stripped := line.strip())
        for prefix in prefixes
        if stripped.startswith(prefix)
    ]


def _check_bug_unit_sections(
    body: list[tuple[int, str]], headings: list[PlanHeading], children: list[tuple[int, PlanHeading]]
) -> list[str]:
    """バグ単位H3ごとに原因分析表と固定行の2列表を検査する。

    統廃合前の調査表は原因分析表を伴う形と伴わない形の2種があり、前者は現行と同じ2表構成で受理する。
    行数はいずれも構造定数から導出し、メッセージへリテラルで持たない。
    """
    errors: list[str] = []
    for position, heading in children:
        start, end = heading_subtree_range(headings, position)
        tables = extract_tables(lines_within(body, start, end))
        standalone_tables = [
            table
            for table in tables
            if table.row_labels() == PLAN_LEGACY_STANDALONE_BUG_TABLE_ROWS and table.header == PLAN_BUG_TABLE_HEADER
        ]
        if standalone_tables:
            if len(standalone_tables) != 1 or len(tables) != 1:
                errors.append(
                    f"`### {heading.text}`の旧形式は固定{len(PLAN_LEGACY_STANDALONE_BUG_TABLE_ROWS)}行の調査表1件だけにする"
                )
                continue
            standalone_table = standalone_tables[0]
            for row in standalone_table.rows:
                if len(row) != len(PLAN_BUG_TABLE_HEADER) or not row[1]:
                    errors.append(f"`### {heading.text}`の調査表に空の`内容`がある: {row[0] if row else ''}")
            continue

        investigation_tables = [
            table for table in tables if table.row_labels() in (PLAN_BUG_TABLE_ROWS, PLAN_LEGACY_BUG_TABLE_ROWS)
        ]
        if len(investigation_tables) != 1 or investigation_tables[0].header != PLAN_BUG_TABLE_HEADER:
            errors.append(
                f"`### {heading.text}`は{list(PLAN_BUG_TABLE_HEADER)}の2列と固定{len(PLAN_BUG_TABLE_ROWS)}行の調査表にする"
            )
            continue
        table = investigation_tables[0]
        cause_tables = [table for table in tables if table.row_labels() == PLAN_BUG_CAUSE_TABLE_ROWS]
        if (
            len(cause_tables) != 1
            or cause_tables[0].header != PLAN_BUG_CAUSE_TABLE_HEADER
            or len(tables) != 2
            or cause_tables[0].lineno > table.lineno
        ):
            errors.append(
                f"`### {heading.text}`は{list(PLAN_BUG_CAUSE_TABLE_HEADER)}の5列2行の原因分析表1件と"
                f"固定{len(PLAN_BUG_TABLE_ROWS)}行の調査表1件だけを置き、原因分析表を調査表より前に置く"
            )
            continue
        cause_table = cause_tables[0]
        for row in cause_table.rows:
            if len(row) != len(PLAN_BUG_CAUSE_TABLE_HEADER) or any(not cell for cell in row[1:]):
                errors.append(f"`### {heading.text}`の原因分析表に空のセルがある: {row[0] if row else ''}")
        for row in table.rows:
            if len(row) != len(PLAN_BUG_TABLE_HEADER) or not row[1]:
                errors.append(f"`### {heading.text}`の調査表に空の`内容`がある: {row[0] if row else ''}")
    return errors


def _check_bug_sections(body: list[tuple[int, str]], headings: list[PlanHeading], index: int) -> list[str]:
    """`## バグ調査結果`の分離先参照または旧形式の本文内調査表を検査する。"""
    start, end = heading_subtree_range(headings, index)
    section = lines_within(body, start, end)
    children = child_headings(headings, index, 3)
    references = _bug_file_reference_values(section)
    nonempty_lines = [line.strip() for _lineno, line in section if line.strip()]

    if children:
        if references:
            return [f"`## {PLAN_H2_BUG}`は分離先参照または本文内調査表のどちらか一方にする"] + _check_bug_unit_sections(
                body, headings, children
            )
        return _check_bug_unit_sections(body, headings, children)

    if len(references) == 1 and references[0] and len(nonempty_lines) == 1:
        return []
    return [f"`## {PLAN_H2_BUG}`直下に`{PLAN_BUG_FILE_REFERENCE_PREFIX}`で始まる分離先参照1行、またはバグ単位のH3が1件以上必要"]


def extract_bug_file_reference(content: str) -> str | None:
    """本文のバグ調査節が単独で参照する分離先パスを返す。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    bug_index = find_heading_index(headings, 2, PLAN_H2_BUG)
    if bug_index is None:
        return None
    start, end = heading_subtree_range(headings, bug_index)
    section = lines_within(body, start, end)
    if child_headings(headings, bug_index, 3):
        return None
    references = _bug_file_reference_values(section)
    nonempty_lines = [line.strip() for _lineno, line in section if line.strip()]
    if len(references) != 1 or not references[0] or len(nonempty_lines) != 1:
        return None
    return references[0]


def has_legacy_bug_table(content: str) -> bool:
    """本文の`## バグ調査結果`が旧形式のバグ単位調査表である場合に真を返す。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    bug_index = find_heading_index(headings, 2, PLAN_H2_BUG)
    if bug_index is None:
        return False
    children = child_headings(headings, bug_index, 3)
    return bool(children) and not _check_bug_unit_sections(body, headings, children)


def has_legacy_bug_investigation_table(content: str) -> bool:
    """統廃合前の行構成を持つ調査表を含む場合に真を返す。既存計画の移行案内にだけ用いる。"""
    tables = extract_tables(list(iter_markdown_body_lines(content)))
    return any(table.header == PLAN_BUG_TABLE_HEADER and table.row_labels() == PLAN_LEGACY_BUG_TABLE_ROWS for table in tables)


def check_bug_file_structure(content: str) -> list[str]:
    """計画ファイル（バグ）のH1、バグ単位H3、原因分析表と固定行の調査表を検査する。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    errors = _check_h1(headings)
    h1_index = next((index for index, heading in enumerate(headings) if heading.level == 1), None)
    if h1_index is None:
        return errors + ["バグ調査ファイルにバグ単位のH3が1件以上必要"]

    if any(heading.level == 2 for heading in headings):
        errors.append("バグ調査ファイルにH2は置かない")
    if any(heading.level >= 4 for heading in headings):
        errors.append("バグ調査ファイルにH4以深の見出しは置かない")

    children = child_headings(headings, h1_index, 3)
    if not children:
        errors.append("バグ調査ファイルにバグ単位のH3が1件以上必要")
    errors.extend(_check_bug_unit_sections(body, headings, children))
    return errors


def _check_permanence_sections(
    body: list[tuple[int, str]],
    headings: list[PlanHeading],
    index: int,
    work_type: str | None,
) -> list[str]:
    """恒久化とリファクタリングの検討実体を検査する。

    該当が無い場合に固定表の代わりへ置く地の文を受理し、表を置いた場合だけ固定の列名と行名を検査する。
    廃止済みの`### 類似見直し`は、既存計画の読み取りのため見出しの存在だけを受理する。
    """
    errors = _check_child_heading_sequence(
        headings,
        index,
        3,
        PLAN_PERMANENCE_H3,
        "`## 恒久化・リファクタリング内容`",
        optional=PLAN_LEGACY_PERMANENCE_H3,
    )
    for position, heading in child_headings(headings, index, 3):
        if heading.text not in (*PLAN_PERMANENCE_H3, *PLAN_LEGACY_PERMANENCE_H3):
            continue
        start, end = heading_subtree_range(headings, position)
        section = lines_within(body, start, end)
        if _is_placeholder_only(section):
            errors.append(f"`### {heading.text}`は対象、比較、確認結果、理由を記載する（結論語だけの記載は成立しない）")
            continue
        # 表記法の有無で地の文と表を分ける。抽出できない崩れた表を地の文として通さない。
        if not any(line.strip().startswith("|") for _lineno, line in section):
            continue
        tables = extract_tables(section)
        if heading.text == "恒久化" and work_type == "通常変更":
            table, table_errors = _check_fixed_table(section, PLAN_PERMANENCE_TABLE_HEADER, "通常変更の`### 恒久化`")
            if table is None:
                errors.append(f"通常変更の`### 恒久化`は{list(PLAN_PERMANENCE_TABLE_HEADER)}の4列表を置く")
            else:
                errors.extend(table_errors)
        elif heading.text == "リファクタリング" and _find_table_with_rows(tables, PLAN_REFACTORING_TABLE_ROWS) is None:
            errors.append(f"`### リファクタリング`は対象ごとに{list(PLAN_REFACTORING_TABLE_ROWS)}の4行表を置く")
    return errors


def _check_h1(headings: list[PlanHeading]) -> list[str]:
    """先頭ATX H1の有無と非空を検査する。"""
    h1_headings = [heading for heading in headings if heading.level == 1]
    if len(h1_headings) != 1:
        return [f"先頭にATX H1が1件必要: 実際={len(h1_headings)}件"]
    if not h1_headings[0].text or headings[0] is not h1_headings[0]:
        return ["H1は本文の先頭見出しとし、主題を空にしない"]
    return []


def _check_overview_section(body: list[tuple[int, str]], headings: list[PlanHeading], overview_index: int) -> list[str]:
    """`## 概要`直下のH3構成と地の文の記載を検査する。"""
    errors: list[str] = []
    children = child_headings(headings, overview_index, 3)
    if [heading.text for _position, heading in children] != [PLAN_METADATA_H3]:
        errors.append(f"`## {PLAN_H2_OVERVIEW}`直下のH3は`### {PLAN_METADATA_H3}`1件だけにする")
    overview_start, overview_end = heading_subtree_range(headings, overview_index)
    prose_end = children[0][1].lineno if children else overview_end
    if not [line for _lineno, line in lines_within(body, overview_start, prose_end) if line.strip()]:
        errors.append(f"`## {PLAN_H2_OVERVIEW}`直下の地の文に全体像、目的、対象範囲を記載する")
    return errors


def _check_materials_and_identifiers(
    content: str, headings: list[PlanHeading]
) -> tuple[set[str], set[str], set[str], PlanMaterials | None, list[str]]:
    """`## 提示素材`を解析し、(素材ID集合, 要求ID集合, 採用要求ID集合, 解析結果, エラー)を返す。"""
    materials_index = find_heading_index(headings, 2, PLAN_H2_MATERIALS)
    identifiers: set[str] = set()
    requirement_ids: set[str] = set()
    adopted_requirement_ids: set[str] = set()
    materials: PlanMaterials | None = None
    errors: list[str] = []
    if materials_index is not None:
        materials, material_errors = parse_plan_materials(content)
        errors.extend(material_errors)
        if materials is not None:
            identifiers = set(materials.material_ids)
            requirement_ids = set(materials.requirement_ids)
            adopted_requirement_ids = set(materials.adopted_requirement_ids)
    return identifiers, requirement_ids, adopted_requirement_ids, materials, errors


def _check_action_table(tables: list[MarkdownTable]) -> tuple[MarkdownTable | None, list[str]]:
    """新旧の実施内容表を新形式優先で検査し、互換形式を含む表を返す。"""
    candidates = [
        table
        for table in tables
        if table.header in (PLAN_HUMAN_ACTION_TABLE_HEADER, PLAN_ACTION_TABLE_HEADER, PLAN_LEGACY_ACTION_TABLE_HEADER)
    ]
    if not candidates:
        return None, [f"`## {PLAN_H2_ACTION}`は{list(PLAN_ACTION_TABLE_HEADER)}の列を持つ表にする"]

    table = candidates[0]
    errors = [f"`## {PLAN_H2_ACTION}`の固定表は1件必要: 実際={len(candidates)}件"] if len(candidates) != 1 else []
    if len(table.rows) < 1:
        errors.append(f"`## {PLAN_H2_ACTION}`の表に1行以上の内容が必要")
    for row in table.rows:
        if len(row) != len(table.header) or any(not cell for cell in row):
            errors.append(f"`## {PLAN_H2_ACTION}`の表に空cellまたは列数不一致の行がある: {list(row)}")
    return table, errors


def has_legacy_action_table(content: str) -> bool:
    """本文の`## 実施内容`が旧3列表である場合に真を返す。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    action_index = find_heading_index(headings, 2, PLAN_H2_ACTION)
    if action_index is None:
        return False
    start, end = heading_subtree_range(headings, action_index)
    return any(table.header == PLAN_LEGACY_ACTION_TABLE_HEADER for table in extract_tables(lines_within(body, start, end)))


def legacy_wi_origins(content: str) -> tuple[str, ...]:
    """`## 実施内容`の`由来`欄が使っている改名前のWI区分を、正規名の並び順で返す。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    action_index = find_heading_index(headings, 2, PLAN_H2_ACTION)
    if action_index is None:
        return ()
    start, end = heading_subtree_range(headings, action_index)
    origins: set[str] = set()
    for table in extract_tables(lines_within(body, start, end)):
        if table.header != PLAN_HUMAN_ACTION_TABLE_HEADER:
            continue
        origin_index = table.header.index("由来")
        for row in table.rows:
            if len(row) != len(PLAN_HUMAN_ACTION_TABLE_HEADER):
                continue
            match = PLAN_WI_ORIGIN_PATTERN.fullmatch(row[origin_index])
            origins.add(match.group("kind") if match is not None else row[origin_index])
    return tuple(canonical for canonical, aliases in PLAN_WI_ORIGIN_ALIASES.items() if origins & (set(aliases) - {canonical}))


def _check_action_section(
    body: list[tuple[int, str]],
    headings: list[PlanHeading],
    action_index: int | None,
    materials: PlanMaterials | None,
    requirement_ids: set[str],
    adopted_requirement_ids: set[str],
    identifiers: set[str],
    related_wi: frozenset[str] = frozenset(),
    *,
    origin_notices: list[str] | None = None,
    origin_skips: list[str] | None = None,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> list[str]:
    """`## 実施内容`の固定表と新旧の除外・保持表を検査する。

    `origin_notices`と`origin_skips`を渡した場合だけ、WI由来行を正本へ照合する。
    照合の結果はエラーではなく呼び出し元の警告として扱うため、戻り値の違反一覧へ混ぜない。
    """
    if action_index is None:
        return []
    errors: list[str] = []
    start, end = heading_subtree_range(headings, action_index)
    section = lines_within(body, start, end)
    table, table_errors = _check_action_table(extract_tables(section))
    errors.extend(table_errors)
    if table is not None and table.header == PLAN_HUMAN_ACTION_TABLE_HEADER:
        errors.extend(
            _check_human_action_table(
                table,
                materials,
                related_wi,
                origin_notices=origin_notices,
                origin_skips=origin_skips,
                private_notes=private_notes,
                home=home,
            )
        )
        if any(heading.level == 3 for _position, heading in child_headings(headings, action_index, 3)):
            errors.append(f"`## {PLAN_H2_ACTION}`直下にH3を置かない")
        return errors
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
        errors.extend(_check_action_decisions(table))
        errors.extend(_check_action_relations(table))
        if materials is not None and not materials.is_legacy:
            errors.extend(_check_action_references(table, requirement_ids, adopted_requirement_ids))
            errors.extend(_check_requirement_coverage(table, exclusion, materials))
    return errors


def _has_frontmatter_source(content: str) -> bool:
    """WIの正本のfrontmatterが値を伴う第1階層の`source`を持つかを返す。

    キーの有無だけを判定するため、YAMLパーサーへ依存せず行頭一致で確定する。
    本モジュールはhookとPEP 723スクリプトから読み込まれるため、依存を広げない選択とする。
    """
    lines = content.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        return False
    for line in lines[1:]:
        if line.strip() == _FRONTMATTER_DELIMITER:
            return False
        if _FRONTMATTER_SOURCE_PATTERN.match(line):
            return True
    return False


def _has_machine_detectable_human_origin(content: str) -> bool:
    """機械判定できる明示由来を持つかを返す。

    末尾の厳密なH2`## ユーザーコメント`と、UWIの`## 回答`を対象とする。
    """
    h2_headings = [heading for heading in extract_headings(content) if heading.level == 2]
    if any(heading.text == PLAN_WI_ANSWER_HEADING for heading in h2_headings):
        return True
    return bool(h2_headings) and h2_headings[-1].text == PLAN_WI_USER_COMMENT_HEADING


def _find_wi_source(name: str, root: pathlib.Path) -> pathlib.Path | None:
    """キュー管理リポジトリのルート配下から正本ファイルを探す。

    状態ディレクトリ名を固定せず1階層下だけを走査するため、キューの状態が増減しても追随する。
    """
    for candidate in sorted(root.iterdir()):
        source = candidate / name
        if candidate.is_dir() and source.is_file():
            return source
    return None


def _collect_origin_notices(
    name: str,
    origin_notices: list[str],
    origin_skips: list[str],
    private_notes: pathlib.Path | str | None,
    home: pathlib.Path | str | None,
) -> None:
    """`人間由来のWI`行を正本へ照合し、移行の指摘と省略の事実を積む。

    正本を解決できない場合とキュー管理リポジトリのルートが実在しない場合は当該行の照合だけを省略し、
    他の検査の結果を変えない。
    """
    root = _plan_file.private_notes_root(private_notes, home=home)
    try:
        if not root.is_dir():
            origin_skips.append(f"`## {PLAN_H2_ACTION}`の由来照合を省略した。キュー管理リポジトリが実在しない: {root}")
            return
        source = _find_wi_source(name, root)
        if source is None:
            origin_skips.append(f"`## {PLAN_H2_ACTION}`の由来照合を省略した。正本を解決できない: {name}")
            return
        content = source.read_text(encoding="utf-8")
    except OSError as error:
        origin_skips.append(f"`## {PLAN_H2_ACTION}`の由来照合を省略した。正本を取得できない: {name}: {error}")
        return
    if _has_frontmatter_source(content) and not _has_machine_detectable_human_origin(content):
        origin_notices.append(
            f"`## {PLAN_H2_ACTION}`の`{PLAN_HUMAN_WI_ORIGIN}`が正本の由来と一致しない: {name}。"
            f"正本は`{PLAN_WI_SOURCE_KEY}`を持ち機械判定できる明示由来が無いため、"
            f"`{PLAN_AGENT_WI_ORIGIN}`とするか、機械判定できない明示由来を根拠とする場合は`[対話由来]`注記を付ける"
        )


def _check_human_action_table(  # pylint: disable=too-many-arguments
    table: MarkdownTable,
    materials: PlanMaterials | None,
    related_wi: frozenset[str],
    *,
    origin_notices: list[str] | None = None,
    origin_skips: list[str] | None = None,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> list[str]:
    """新規書式の実施内容4列表を検査する。

    `人間由来のWI`と記載した行は、`origin_notices`と`origin_skips`を渡した場合だけ
    正本のfrontmatterと本文へ照合する。`[対話由来]`注記のある行は機械判定できない明示由来を
    根拠とするため照合の対象から除く。`エージェント由来のWI`は採否にかかわらず根拠を必要とし、
    採用行の根拠が`-`の場合は`origin_notices`を渡した場合だけ移行の指摘を積む。
    改名前の由来は読み取り互換で受理する。
    """
    errors: list[str] = []
    if not table.rows:
        return [f"`## {PLAN_H2_ACTION}`の表に1行以上の内容が必要"]
    origin_index = table.header.index("由来")
    decision_index = table.header.index("採否")
    root_index = table.header.index("根拠")
    for row in table.rows:
        if len(row) != len(PLAN_HUMAN_ACTION_TABLE_HEADER) or any(not cell for cell in row):
            errors.append(f"`## {PLAN_H2_ACTION}`の表に空cellまたは列数不一致の行がある: {list(row)}")
            continue
        origin = row[origin_index]
        canonical_origin = canonical_wi_origin(origin)
        review_origin = PLAN_HUMAN_REVIEW_ORIGIN_PATTERN.fullmatch(origin)
        wi_origin_match = PLAN_WI_ORIGIN_PATTERN.fullmatch(origin)
        wi_origin_kind = canonical_wi_origin(wi_origin_match.group("kind")) if wi_origin_match is not None else None
        if canonical_origin in PLAN_HUMAN_ORIGINS:
            if canonical_origin in PLAN_WI_ORIGIN_ALIASES:
                errors.append(
                    f"`## {PLAN_H2_ACTION}`のWI由来には"
                    "半角空白1字に続けて半角丸括弧で囲んだ正本ファイル名を記載する"
                    f"（例: `{PLAN_AGENT_WI_ORIGIN} (20260831-000000-001.md)`）: {origin}"
                )
        elif any(origin.startswith(f"{alias} (") for alias in _PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS):
            if wi_origin_match is None:
                errors.append(f"`## {PLAN_H2_ACTION}`の`由来`は正本ファイル名付きの4値にする: {origin}")
            elif wi_origin_match.group("name") not in related_wi and (
                materials is None or wi_origin_match.group("name") not in materials.material_paths
            ):
                errors.append(f"`## {PLAN_H2_ACTION}`のWI由来が`関連WI`に無い: {wi_origin_match.group('name')}")
            elif (
                origin_notices is not None
                and origin_skips is not None
                and wi_origin_kind == PLAN_HUMAN_WI_ORIGIN
                and wi_origin_match.group("note") is None
            ):
                _collect_origin_notices(wi_origin_match.group("name"), origin_notices, origin_skips, private_notes, home)
        elif review_origin is None:
            errors.append(
                f"`## {PLAN_H2_ACTION}`の`由来`は{list(PLAN_HUMAN_ORIGINS)}、計画レビュー第nラウンド、又は"
                "区分と半角空白1字と半角丸括弧で囲んだ正本ファイル名"
                f"（例: `{PLAN_AGENT_WI_ORIGIN} (20260831-000000-001.md)`）にする: "
                f"{origin}"
            )
        decision = row[decision_index]
        if decision not in PLAN_ACTION_DECISIONS:
            errors.append(f"`## {PLAN_H2_ACTION}`の`採否`は{list(PLAN_ACTION_DECISIONS)}のいずれかにする: {decision}")
        root = row[root_index]
        if review_origin is not None:
            errors.extend(_check_human_review_root(root, review_origin.group("round"), decision))
        elif origin == "エージェント提案":
            if not root or root == "-":
                errors.append(f"`## {PLAN_H2_ACTION}`のエージェント提案行には観測可能な根拠を記載する: {root}")
        elif wi_origin_kind == PLAN_AGENT_WI_ORIGIN:
            if not root or root == "-":
                if decision == "採用":
                    if origin_notices is not None:
                        origin_notices.append(
                            f"`## {PLAN_H2_ACTION}`の`{PLAN_AGENT_WI_ORIGIN}`の採用行の`根拠`へ、"
                            "適用範囲を再導出した結果と根拠を記載する"
                        )
                else:
                    errors.append(f"`## {PLAN_H2_ACTION}`の採用以外の`根拠`は理由を自足して記載する: {root}")
        elif decision == "採用":
            if root != "-":
                errors.append(f"`## {PLAN_H2_ACTION}`の採用行の`根拠`は`-`にする: {root}")
        elif not root or root == "-":
            errors.append(f"`## {PLAN_H2_ACTION}`の採用以外の`根拠`は理由を自足して記載する: {root}")
        for value in row:
            if _INTERNAL_PLAN_ID_PATTERN.search(value):
                errors.append(f"`## {PLAN_H2_ACTION}`へ素材・要求・履歴・実装単位の合成IDを記載しない: {value}")
    return errors


def is_canonical_main_format(content_or_headings: str | list[PlanHeading]) -> bool:
    """新しいメイン計画書式（`エージェント提案詳細`を含む）かを返す。"""
    headings = extract_headings(content_or_headings) if isinstance(content_or_headings, str) else content_or_headings
    h2_names = {heading.text for heading in headings if heading.level == 2}
    return bool(h2_names & {PLAN_H2_AGENT_JUDGMENT, PLAN_H2_LEGACY_AGENT_JUDGMENT, PLAN_H2_HISTORY, PLAN_H2_PROGRESS})


def _check_agent_judgment_section(
    body: list[tuple[int, str]],
    headings: list[PlanHeading],
    action_index: int | None,
    judgment_index: int | None,
) -> list[str]:
    """新書式の`## エージェント提案詳細`を実施内容の提案行と照合する。"""
    if judgment_index is None:
        return [f"`## {PLAN_H2_AGENT_JUDGMENT}`が無いためエージェント提案の詳細を検査できない"]

    proposal_names: list[str] = []
    if action_index is not None:
        action_start, action_end = heading_subtree_range(headings, action_index)
        action_tables = extract_tables(lines_within(body, action_start, action_end))
        action_table = next((table for table in action_tables if table.header == PLAN_HUMAN_ACTION_TABLE_HEADER), None)
        if action_table is not None:
            origin_index = action_table.header.index("由来")
            proposal_names = [
                row[0]
                for row in action_table.rows
                if len(row) == len(action_table.header) and row[origin_index] == "エージェント提案"
            ]

    start, end = heading_subtree_range(headings, judgment_index)
    section = lines_within(body, start, end)
    if not proposal_names:
        nonempty = [line.strip() for _lineno, line in section if line.strip() and not line.strip().startswith("<!--")]
        if nonempty != ["なし"]:
            return [f"`## {PLAN_H2_AGENT_JUDGMENT}`にエージェント提案が無い場合は`なし`と記載する: 実際={nonempty}"]
        return []

    table, errors = _check_fixed_table(
        section,
        PLAN_HUMAN_JUDGMENT_TABLE_HEADER,
        f"`## {PLAN_H2_AGENT_JUDGMENT}`",
        minimum_rows=len(proposal_names),
    )
    if table is None:
        return errors
    actual_names = [row[0] for row in table.rows if row]
    if actual_names != proposal_names:
        errors.append(
            f"`## {PLAN_H2_AGENT_JUDGMENT}`の`実施内容`はエージェント提案行と同じ順序で1件ずつ置く: "
            f"期待={proposal_names}, 実際={actual_names}"
        )
    return errors


def _check_human_review_root(root: str, expected_round: str, decision: str) -> list[str]:
    """計画レビュー由来の行が絶対パスと同じラウンドを指すか検査する。"""
    match = PLAN_HUMAN_REVIEW_ROOT_PATTERN.fullmatch(root)
    if match is None or not plan_human_review_path_is_absolute(match.group("path")):
        return [f"`## {PLAN_H2_ACTION}`の計画レビュー由来の`根拠`は絶対パスのTSVと同じroundを指定する: {root}"]
    if match.group("round") != expected_round:
        return [
            f"`## {PLAN_H2_ACTION}`の計画レビュー由来の`根拠`は由来と同じroundを指定する: "
            f"由来={expected_round}, 根拠={match.group('round')}"
        ]
    if decision != "採用" and not match.group("reason"):
        return [f"`## {PLAN_H2_ACTION}`の計画レビュー由来の採用以外の`根拠`はTSV参照に続けて理由を記載する: {root}"]
    review_path = pathlib.Path(match.group("path"))
    if not review_path.is_file():
        return [f"`## {PLAN_H2_ACTION}`の計画レビュー表が実在しない: {review_path}"]
    try:
        has_round = any(
            line.split("\t", 1)[0].strip('"') == expected_round
            for line in review_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except (OSError, UnicodeDecodeError):
        has_round = False
    if not has_round:
        return [f"`## {PLAN_H2_ACTION}`の計画レビュー表に同じroundが無い: {review_path} round {expected_round}"]
    return []


def _check_history_section(
    body: list[tuple[int, str]],
    headings: list[PlanHeading],
    history_index: int | None,
    identifiers: set[str],
    *,
    allow_legacy_review_ids: bool = False,
    allow_legacy_review_tracks: bool = False,
) -> list[str]:
    """`## 変更履歴`の固定表を検査する。

    ``allow_legacy_review_ids`` は旧単一ファイルの ``C-`` IDを許可し、
    ``allow_legacy_review_tracks`` は旧二ファイル計画に残るIDと系統名を読み取り専用で受理する。
    """
    if history_index is None:
        return []
    start, end = heading_subtree_range(headings, history_index)
    history, errors = _check_fixed_table(
        lines_within(body, start, end), PLAN_HISTORY_TABLE_HEADER, f"`## {PLAN_H2_LEGACY_HISTORY}`"
    )
    if history is not None:
        errors.extend(
            _check_history_rows(
                history,
                identifiers,
                allow_legacy_review_ids=allow_legacy_review_ids,
                allow_legacy_review_tracks=allow_legacy_review_tracks,
            )
        )
    return errors


def _check_human_history_section(
    headings: list[PlanHeading], history_index: int | None, content: str, *, require_user_event: bool = False
) -> list[str]:
    """新規書式の変更履歴（自然な見出しと逐語入力）を検査する。"""
    if history_index is None:
        return []
    start, end = heading_subtree_range(headings, history_index)
    raw_lines = content.splitlines()
    upper = len(raw_lines) + 1 if end is None else end
    section = [(lineno, raw_lines[lineno - 1]) for lineno in range(start + 1, upper) if lineno <= len(raw_lines)]
    body_section = [(lineno, line) for lineno, line in iter_markdown_body_lines(content) if start < lineno < upper]
    errors: list[str] = []
    if extract_tables(section):
        errors.append(f"`## {PLAN_H2_LEGACY_HISTORY}`へ表を置かない")
    if any(_INTERNAL_PLAN_ID_PATTERN.search(line) for _lineno, line in body_section):
        errors.append(f"`## {PLAN_H2_LEGACY_HISTORY}`へ履歴・要求・実装単位の合成IDを記載しない")
    children = child_headings(headings, history_index, 3)
    if not children and not [line for _lineno, line in section if line.strip()]:
        errors.append(f"`## {PLAN_H2_LEGACY_HISTORY}`に変更内容を判別できる記録が必要")
    user_event_count = 0
    user_event_sequences: list[int] = []
    for position, heading in children:
        child_start, child_end = heading_subtree_range(headings, position)
        child_upper = len(raw_lines) + 1 if child_end is None else child_end
        child_lines = [
            (lineno, raw_lines[lineno - 1]) for lineno in range(child_start + 1, child_upper) if lineno <= len(raw_lines)
        ]
        if _INTERNAL_PLAN_ID_PATTERN.search(heading.text):
            errors.append(f"`## {PLAN_H2_LEGACY_HISTORY}`の見出しへ履歴・要求・実装単位の合成IDを記載しない: {heading.text}")
        if not heading.text.startswith(PLAN_HISTORY_USER_EVENT_PREFIX):
            continue
        numbered = PLAN_HISTORY_USER_EVENT_PATTERN.fullmatch(heading.text)
        if numbered is not None:
            user_event_sequences.append(int(numbered.group("sequence")))
        elif PLAN_LEGACY_HISTORY_USER_EVENT_PATTERN.fullmatch(heading.text) is None:
            errors.append(
                f"`## {PLAN_H2_LEGACY_HISTORY}`のユーザー発言の見出しは"
                f"`### {PLAN_HISTORY_USER_EVENT_PREFIX}<1から始まる連番>`にする: {heading.text}"
            )
            continue
        user_event_count += 1
        fence_positions = [
            index for index, (_lineno, line) in enumerate(child_lines) if _MATERIAL_FENCE_PATTERN.fullmatch(line)
        ]
        if not fence_positions:
            errors.append(f"`## {PLAN_H2_LEGACY_HISTORY}`のユーザー発言には`text`コードブロックを置く: {heading.text}")
            continue
        closing_index = next(
            (
                index
                for index, (_lineno, line) in enumerate(child_lines[fence_positions[0] + 1 :], fence_positions[0] + 1)
                if line.strip() == "```"
            ),
            None,
        )
        if closing_index is None:
            errors.append(f"`## {PLAN_H2_LEGACY_HISTORY}`のユーザー発言の`text`コードブロックを閉じる: {heading.text}")
        elif not any(line.strip() for _lineno, line in child_lines[fence_positions[0] + 1 : closing_index]):
            errors.append(f"`## {PLAN_H2_LEGACY_HISTORY}`のユーザー発言の`text`コードブロックを空にしない: {heading.text}")
    if user_event_sequences != list(range(1, len(user_event_sequences) + 1)):
        errors.append(
            f"`## {PLAN_H2_LEGACY_HISTORY}`のユーザー発言の連番は1から欠番なく昇順に置く: 実際={user_event_sequences}"
        )
    if require_user_event and user_event_count == 0:
        errors.append(
            f"`## {PLAN_H2_LEGACY_HISTORY}`に`### {PLAN_HISTORY_USER_EVENT_PREFIX}1`見出しと空でない`text`コードブロックが必要"
        )
    return errors


def has_human_action_table(content: str) -> bool:
    """本文の`## 実施内容`が新規人間向け4列表である場合に真を返す。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    action_index = find_heading_index(headings, 2, PLAN_H2_ACTION)
    if action_index is None:
        return False
    start, end = heading_subtree_range(headings, action_index)
    return any(table.header == PLAN_HUMAN_ACTION_TABLE_HEADER for table in extract_tables(lines_within(body, start, end)))


def has_adopted_human_user_instruction(content: str) -> bool:
    """新規書式の採用済み`ユーザー指示`行がある場合に真を返す。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    action_index = find_heading_index(headings, 2, PLAN_H2_ACTION)
    if action_index is None:
        return False
    start, end = heading_subtree_range(headings, action_index)
    table = next(
        (
            candidate
            for candidate in extract_tables(lines_within(body, start, end))
            if candidate.header == PLAN_HUMAN_ACTION_TABLE_HEADER
        ),
        None,
    )
    if table is None:
        return False
    origin_index = table.header.index("由来")
    decision_index = table.header.index("採否")
    return any(
        row[origin_index] == "ユーザー指示" and row[decision_index] in ("採用", "部分採用")
        for row in table.rows
        if len(row) == len(table.header)
    )


def has_legacy_history_user_event(content: str) -> bool:
    """`## 変更履歴`に要旨を書く旧書式のユーザー発言見出しがある場合に真を返す。"""
    headings = extract_headings(content)
    history_index = find_heading_index(headings, 2, PLAN_H2_HISTORY)
    if history_index is None:
        return False
    return any(
        PLAN_LEGACY_HISTORY_USER_EVENT_PATTERN.fullmatch(heading.text) is not None
        for _position, heading in child_headings(headings, history_index, 3)
    )


def has_progress_log_rows(content: str) -> bool:
    """`## 進捗ログ`の固定表に内容行がある場合に真を返す。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    progress_index = find_heading_index(headings, 2, PLAN_H2_PROGRESS)
    if progress_index is None:
        return False
    start, end = heading_subtree_range(headings, progress_index)
    return any(
        bool(table.rows)
        for table in extract_tables(lines_within(body, start, end))
        if table.header == PLAN_PROGRESS_TABLE_HEADER
    )


def _check_progress_section(body: list[tuple[int, str]], headings: list[PlanHeading], progress_index: int | None) -> list[str]:
    """`## 進捗ログ`の固定表を検査する。"""
    if progress_index is None:
        return []
    start, end = heading_subtree_range(headings, progress_index)
    _table, errors = _check_fixed_table(
        lines_within(body, start, end),
        PLAN_PROGRESS_TABLE_HEADER,
        f"`## {PLAN_H2_LEGACY_PROGRESS}`",
        minimum_rows=0,
    )
    return errors


def _check_verification_section(
    body: list[tuple[int, str]], headings: list[PlanHeading], verification_index: int | None
) -> list[str]:
    """`## 検証区分`の固定2行2列表を検査する。"""
    if verification_index is None:
        return []
    start, end = heading_subtree_range(headings, verification_index)
    section = lines_within(body, start, end)
    table = _find_table_with_rows(extract_tables(section), PLAN_VERIFICATION_TABLE_ROWS)
    if table is None or table.header != PLAN_VERIFICATION_TABLE_HEADER:
        return [
            f"`## {PLAN_H2_VERIFICATION}`は{list(PLAN_VERIFICATION_TABLE_HEADER)}の2列と"
            f"固定2行（{list(PLAN_VERIFICATION_TABLE_ROWS)}）の表にする"
        ]
    return [
        f"`## {PLAN_H2_VERIFICATION}`の表に空の検証コマンドがある: {row[0] if row else ''}"
        for row in table.rows
        if len(row) != 2 or not row[1]
    ]


def _check_termination_section(
    body: list[tuple[int, str]], headings: list[PlanHeading], termination_index: int | None
) -> list[str]:
    """`## 終端工程`に記載があるかを検査する（終端工程が無い場合は`なし`と書く運用を許容する）。"""
    if termination_index is None:
        return []
    start, end = heading_subtree_range(headings, termination_index)
    section = lines_within(body, start, end)
    if not [line for _lineno, line in section if line.strip()]:
        return [f"`## {PLAN_H2_TERMINATION}`は終端工程を記載する（無い場合は`なし`と書く）"]
    return []


def _check_bug_and_permanence(body: list[tuple[int, str]], headings: list[PlanHeading], work_type: str | None) -> list[str]:
    """`## バグ調査結果`と`## 恒久化・リファクタリング内容`の実体を検査する。"""
    errors: list[str] = []
    bug_index = find_heading_index(headings, 2, PLAN_H2_BUG)
    if bug_index is not None:
        errors.extend(_check_bug_sections(body, headings, bug_index))
    permanence_index = find_heading_index(headings, 2, PLAN_H2_PERMANENCE)
    if permanence_index is not None:
        errors.extend(_check_permanence_sections(body, headings, permanence_index, work_type))
    return errors


def _check_h3_and_deeper(
    headings: list[PlanHeading], allowed_h3_parents: set[str], freeform_parents: frozenset[str]
) -> list[str]:
    """固定H2直下に自由なH3を置かないこと、H4以深を置かないことを検査する。"""
    errors: list[str] = []
    for index, heading in enumerate(headings):
        if heading.level < 3:
            continue
        parent = canonical_h2_name(
            next((candidate.text for candidate in reversed(headings[:index]) if candidate.level == 2), "")
        )
        if parent in freeform_parents:
            continue
        if heading.level == 3 and parent not in allowed_h3_parents:
            errors.append(f"`## {parent}`直下に自由なH3は置かない: `### {heading.text}`")
        elif heading.level > 3:
            errors.append(f"`## {parent}`配下にH4以深の見出しは置かない: `{'#' * heading.level} {heading.text}`")
    return errors


def check_plan_structure(content: str) -> list[str]:
    """旧形式（単一ファイル9節）の計画を検査して違反一覧を返す。

    検査対象は見出しの欠落、重複、順序違反、固定領域への追加H2、固定表の列と行、
    空cell、素材・要求参照先の欠落、恒久化等の空欄または結論語だけの記載とする。
    素材と要求の意味照合、根拠の妥当性、検討の実質はレビュー担当が判定する。
    新規作成では生成しない読み取り互換の形式であり、新書式の2ファイルは
    `check_plan_main_structure`・`check_plan_detail_structure`で検査する。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    errors = _check_h1(headings)
    errors.extend(check_duplicate_headings(content))

    work_type, metadata_errors = _check_metadata_block(content)
    errors.extend(metadata_errors)

    if not [heading for heading in headings if heading.level == 2]:
        errors.append("固定H2が1件も無い")
        return errors
    errors.extend(
        _check_fixed_h2_layout(headings, _legacy_expected_h2(work_type), disallow_bug_for_normal=True, work_type=work_type)
    )

    overview_index = find_heading_index(headings, 2, PLAN_H2_OVERVIEW)
    if overview_index is not None:
        errors.extend(_check_overview_section(body, headings, overview_index))

    identifiers, requirement_ids, adopted_requirement_ids, materials, material_errors = _check_materials_and_identifiers(
        content, headings
    )
    errors.extend(material_errors)

    action_index = find_heading_index(headings, 2, PLAN_H2_ACTION)
    errors.extend(
        _check_action_section(body, headings, action_index, materials, requirement_ids, adopted_requirement_ids, identifiers)
    )

    history_index = find_heading_index(headings, 2, PLAN_H2_HISTORY)
    errors.extend(_check_history_section(body, headings, history_index, identifiers, allow_legacy_review_ids=True))

    progress_index = find_heading_index(headings, 2, PLAN_H2_PROGRESS)
    errors.extend(_check_progress_section(body, headings, progress_index))

    errors.extend(_check_bug_and_permanence(body, headings, work_type))

    allowed_h3_parents = {PLAN_H2_OVERVIEW, PLAN_H2_ACTION, PLAN_H2_BUG, PLAN_H2_PERMANENCE}
    errors.extend(_check_h3_and_deeper(headings, allowed_h3_parents, frozenset({PLAN_H2_IMPLEMENTATION})))
    return errors


def check_plan_main_structure(
    content: str,
    *,
    origin_notices: list[str] | None = None,
    origin_skips: list[str] | None = None,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> tuple[str | None, list[str]]:
    """新書式の計画ファイル（メイン）`<計画名>.md`を検査して(作業種別, 違反一覧)を返す。

    固定H2順は`PLAN_MAIN_H2_ORDER`とし、計画メタ情報は`関連WI`を含む5項目とする。
    改訂前の二ファイル形式は提示素材と計画ファイル（詳細）参照を読み取り互換で受理する。
    `origin_notices`と`origin_skips`を渡した場合だけ、実施内容表のWI由来行を正本へ照合し、
    移行を促す指摘と照合を省略した事実をそれぞれへ積む。照合の結果は違反一覧へ含めない。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    errors = _check_h1(headings)
    errors.extend(check_duplicate_headings(content))

    metadata, _parse_errors = parse_plan_metadata(content)
    old_two_file_format = bool(
        metadata is not None
        and PLAN_METADATA_DETAIL_FIELD in metadata.values
        and PLAN_METADATA_RELATED_WI_FIELD not in metadata.values
    )
    expected_metadata = PLAN_METADATA_TWO_FILE_FIELDS if old_two_file_format else PLAN_METADATA_MAIN_FIELDS
    work_type, metadata_errors = _check_metadata_block(content, expected_fields=expected_metadata)
    errors.extend(metadata_errors)

    if not [heading for heading in headings if heading.level == 2]:
        errors.append("固定H2が1件も無い")
        return work_type, errors
    canonical_format = is_canonical_main_format(headings)
    if old_two_file_format:
        expected_h2 = list(PLAN_TWO_FILE_MAIN_H2_ORDER if canonical_format else PLAN_LEGACY_MAIN_H2_ORDER)
    else:
        expected_h2 = list(PLAN_MAIN_H2_ORDER if canonical_format else PLAN_LEGACY_MAIN_H2_ORDER)
    errors.extend(_check_fixed_h2_layout(headings, expected_h2))

    overview_index = find_heading_index(headings, 2, PLAN_H2_OVERVIEW)
    if overview_index is not None:
        errors.extend(_check_overview_section(body, headings, overview_index))

    if old_two_file_format or not canonical_format:
        identifiers, requirement_ids, adopted_requirement_ids, materials, material_errors = _check_materials_and_identifiers(
            content, headings
        )
    else:
        identifiers, requirement_ids, adopted_requirement_ids, materials, material_errors = set(), set(), set(), None, []
    errors.extend(material_errors)

    action_index = find_heading_index(headings, 2, PLAN_H2_ACTION)
    errors.extend(
        _check_action_section(
            body,
            headings,
            action_index,
            materials,
            requirement_ids,
            adopted_requirement_ids,
            identifiers,
            frozenset(filename for filename, _summary in metadata.related_wi) if metadata is not None else frozenset(),
            origin_notices=origin_notices,
            origin_skips=origin_skips,
            private_notes=private_notes,
            home=home,
        )
    )

    judgment_index = find_heading_index(headings, 2, PLAN_H2_AGENT_JUDGMENT)
    if canonical_format:
        errors.extend(_check_agent_judgment_section(body, headings, action_index, judgment_index))

    history_index = find_heading_index(headings, 2, PLAN_H2_HISTORY)
    human_format = has_human_action_table(content)
    if human_format:
        errors.extend(
            _check_human_history_section(
                headings,
                history_index,
                content,
                require_user_event=has_adopted_human_user_instruction(content),
            )
        )
    else:
        errors.extend(
            _check_history_section(
                body,
                headings,
                history_index,
                identifiers,
                allow_legacy_review_ids=not canonical_format,
                allow_legacy_review_tracks=not canonical_format,
            )
        )

    verification_index = find_heading_index(headings, 2, PLAN_H2_VERIFICATION)
    errors.extend(_check_verification_section(body, headings, verification_index))

    termination_index = find_heading_index(headings, 2, PLAN_H2_TERMINATION)
    errors.extend(_check_termination_section(body, headings, termination_index))

    progress_index = find_heading_index(headings, 2, PLAN_H2_PROGRESS)
    errors.extend(_check_progress_section(body, headings, progress_index))

    allowed_h3_parents = {PLAN_H2_OVERVIEW, PLAN_H2_ACTION}
    if human_format:
        allowed_h3_parents.add(PLAN_H2_HISTORY)
    errors.extend(_check_h3_and_deeper(headings, allowed_h3_parents, frozenset()))
    return work_type, errors


def check_plan_detail_structure(content: str, work_type: str | None) -> list[str]:
    """新書式の計画ファイル（詳細）`<計画名>.detail.md`を検査して違反一覧を返す。

    計画ファイル（詳細）は計画メタ情報を持たないため、作業種別は計画ファイル（メイン）の検査結果から受け取る。
    固定H2順は`PLAN_DETAIL_H2_ORDER`（恒久化・リファクタリング内容・実装資料・完了条件）とし、
    作業種別が`バグ対応`の場合だけ先頭へ`バグ調査結果`を加える。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    errors = check_duplicate_headings(content)

    if not [heading for heading in headings if heading.level == 2]:
        errors.append("固定H2が1件も無い")
        return errors
    errors.extend(
        _check_fixed_h2_layout(headings, _detail_expected_h2(work_type), disallow_bug_for_normal=True, work_type=work_type)
    )

    errors.extend(_check_bug_and_permanence(body, headings, work_type))

    _units, unit_errors = parse_plan_implementation_units(content)
    errors.extend(unit_errors)
    allowed_h3_parents = {PLAN_H2_BUG, PLAN_H2_PERMANENCE}
    errors.extend(_check_h3_and_deeper(headings, allowed_h3_parents, frozenset({PLAN_H2_IMPLEMENTATION})))
    return errors
