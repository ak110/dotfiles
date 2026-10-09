"""計画ファイル（メイン・詳細・バグ）の固定H2と各節の構造の判定、旧形式の判別と`由来`の判定。"""

from __future__ import annotations

import pathlib

from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan.structure.constants import (
    _FRONTMATTER_DELIMITER,
    _FRONTMATTER_SOURCE_PATTERN,
    _PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS,
    PLAN_ACCEPTANCE_H3,
    PLAN_ACCEPTANCE_TABLE_HEADER,
    PLAN_ACTION_DECISIONS,
    PLAN_ACTION_TABLE_HEADER,
    PLAN_AGENT_WI_ORIGIN,
    PLAN_BUG_CAUSE_TABLE_HEADER,
    PLAN_BUG_CAUSE_TABLE_ROWS,
    PLAN_BUG_FILE_REFERENCE_LEGACY_PREFIX,
    PLAN_BUG_FILE_REFERENCE_PREFIX,
    PLAN_BUG_TABLE_HEADER,
    PLAN_BUG_TABLE_ROWS,
    PLAN_CURRENT_VERIFICATION_TABLE_ROWS,
    PLAN_EXCLUSION_H3,
    PLAN_H2_ACTION,
    PLAN_H2_AGENT_JUDGMENT,
    PLAN_H2_BUG,
    PLAN_H2_CURRENT_HISTORY,
    PLAN_H2_CURRENT_PERMANENCE,
    PLAN_H2_CURRENT_PROGRESS,
    PLAN_H2_CURRENT_VERIFICATION,
    PLAN_H2_HISTORY,
    PLAN_H2_IMPLEMENTATION,
    PLAN_H2_LEGACY_AGENT_JUDGMENT,
    PLAN_H2_LEGACY_HISTORY,
    PLAN_H2_LEGACY_PROGRESS,
    PLAN_H2_MATERIALS,
    PLAN_H2_OVERVIEW,
    PLAN_H2_PERMANENCE,
    PLAN_H2_PROGRESS,
    PLAN_H2_REQUIREMENTS,
    PLAN_H2_TERMINATION,
    PLAN_H2_VERIFICATION,
    PLAN_HISTORY_TABLE_HEADER,
    PLAN_HISTORY_USER_EVENT_PATTERN,
    PLAN_HISTORY_USER_EVENT_PREFIX,
    PLAN_HUMAN_ACTION_TABLE_HEADER,
    PLAN_HUMAN_JUDGMENT_TABLE_HEADER,
    PLAN_HUMAN_ORIGINS,
    PLAN_HUMAN_REVIEW_ORIGIN_PATTERN,
    PLAN_HUMAN_REVIEW_ROOT_PATTERN,
    PLAN_HUMAN_WI_ORIGIN,
    PLAN_LEGACY_ACCEPTANCE_TABLE_HEADERS,
    PLAN_LEGACY_ACTION_TABLE_HEADER,
    PLAN_LEGACY_BUG_TABLE_ROWS,
    PLAN_LEGACY_CURRENT_SINGLE_VERIFICATION_TABLE_ROWS,
    PLAN_LEGACY_CURRENT_VERIFICATION_TABLE_ROWS,
    PLAN_LEGACY_HISTORY_USER_EVENT_PATTERN,
    PLAN_LEGACY_MAIN_H2_ORDER,
    PLAN_LEGACY_PERMANENCE_H3,
    PLAN_LEGACY_REFACTORING_TABLE_ROWS,
    PLAN_LEGACY_STANDALONE_BUG_TABLE_ROWS,
    PLAN_MAIN_H2_ORDER,
    PLAN_METADATA_BUG_FIELD,
    PLAN_METADATA_CURRENT_FIELDS,
    PLAN_METADATA_DETAIL_FIELD,
    PLAN_METADATA_FIELDS,
    PLAN_METADATA_H3,
    PLAN_METADATA_MAIN_FIELDS,
    PLAN_METADATA_QUOTED_FIELDS,
    PLAN_METADATA_RELATED_WI_FIELD,
    PLAN_METADATA_TWO_FILE_FIELDS,
    PLAN_PERMANENCE_H3,
    PLAN_PERMANENCE_TABLE_HEADER,
    PLAN_PROGRESS_TABLE_HEADER,
    PLAN_QUEUE_ID_PATTERN,
    PLAN_REFACTORING_TABLE_HEADER,
    PLAN_SINGLE_FILE_H2_ORDER,
    PLAN_TWO_FILE_MAIN_H2_ORDER,
    PLAN_USER_INSTRUCTION_ORIGIN,
    PLAN_VERIFICATION_TABLE_HEADER,
    PLAN_VERIFICATION_TABLE_ROWS,
    PLAN_WI_MACHINE_DETECTABLE_ORIGIN_HEADINGS,
    PLAN_WI_ORIGIN_ALIASES,
    PLAN_WI_ORIGIN_PATTERN,
    PLAN_WI_SCOPE_HEADING,
    PLAN_WI_SOURCE_KEY,
    PLAN_WORK_TYPES,
    canonical_h2_name,
    plan_human_review_path_is_absolute,
)
from agent_toolkit._plan.structure.markdown import (
    _EMPTY_CELL_FIX,
    MarkdownTable,
    PlanHeading,
    _check_fixed_table,
    _column_count_error,
    check_duplicate_headings,
    child_headings,
    extract_headings,
    extract_tables,
    find_heading_index,
    heading_subtree_range,
    iter_markdown_body_lines,
    lines_within,
)
from agent_toolkit._plan.structure.materials import _MATERIAL_FENCE_PATTERN, PlanMaterials, parse_plan_materials
from agent_toolkit._plan.structure.parsing import (
    _INTERNAL_PLAN_ID_PATTERN,
    PlanMetadata,
    _detail_expected_h2,
    _find_table_with_rows,
    _is_placeholder_only,
    _legacy_expected_h2,
    _strip_backticks,
    canonical_metadata_field,
    canonical_wi_origin,
    parse_plan_implementation_units,
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
from agent_toolkit._plan.structure.verification import commands_from_cell

_NO_FIXED_H2_ERROR = (
    "固定H2が1件も無い。対象が計画ファイルか確かめる。"
    "計画ファイルなら`agent-toolkit:plan-mode`の`references/plan-file-standards.md`「初回起草の雛形」の固定H2を置く"
)
_PROGRESS_TABLE_FIX = (
    f"`## {PLAN_H2_PROGRESS}`見出しと、表頭（{list(PLAN_PROGRESS_TABLE_HEADER)}の列）だけの固定表を置いてから再実行する"
)


def _check_fixed_h2_layout(
    headings: list[PlanHeading],
    expected: list[str],
    *,
    disallow_bug_for_normal: bool = False,
    work_type: str | None = None,
) -> list[str]:
    """全H2が一意に存在し、固定された順に並ぶか確かめる。"""
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
    """指定見出しの直下に固定見出しが一意に存在し、所定の順に並ぶか確かめる。

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
    """計画メタ情報の配置、項目、順序、記法、値が基準を満たすか判定し、(作業種別, エラー)を返す。

    `expected_fields`は対象書式が定める計画メタ情報の項目順を渡す。
    """
    metadata, errors = parse_plan_metadata(content)
    if metadata is None:
        return None, errors or [f"`## {PLAN_H2_OVERVIEW}`直下の`### {PLAN_METADATA_H3}`の構造を判定できない"]
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
            errors.append(f"計画メタ情報の`{field}`が空である。値を記載する")
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
    """計画メタ情報の`関連WI`の値と子項目が所定の形式であるか確かめる。"""
    errors: list[str] = []
    related_value = metadata.values.get(PLAN_METADATA_RELATED_WI_FIELD, "")
    if related_value == "なし":
        if metadata.related_wi:
            errors.append("計画メタ情報の`関連WI: なし`と子項目を併記しない")
    elif related_value:
        errors.append("計画メタ情報の`関連WI`は子項目または`なし`で記載する")
    elif not metadata.related_wi:
        errors.append("計画メタ情報の`関連WI`にはWIファイル名と1行要約を1件以上記載する")
    seen_wi: set[str] = set()
    for filename, summary in metadata.related_wi:
        if PLAN_QUEUE_ID_PATTERN.fullmatch(filename) is None:
            errors.append(
                f"計画メタ情報の`関連WI`のファイル名が不正である: {filename}。"
                "`YYYYMMDD-HHMMSS-NNN.md`形式のWIファイル名を`atk wi list`で確かめて直す"
            )
        if not summary:
            errors.append(f"計画メタ情報の`関連WI`の1行要約が空である: {filename}。1行要約を記載する")
        if filename in seen_wi:
            errors.append(f"計画メタ情報の`関連WI`のファイル名が重複している: {filename}。重複した行を削除する")
        seen_wi.add(filename)
    return errors


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
    """バグ単位H3ごとに原因分析表と固定行の2列表が基準を満たすか確かめる。

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
            for index, row in enumerate(standalone_table.rows):
                if len(row) != len(PLAN_BUG_TABLE_HEADER):
                    errors.append(
                        _column_count_error(
                            f"`### {heading.text}`の調査表",
                            len(PLAN_BUG_TABLE_HEADER),
                            row,
                            standalone_table.row_location(index),
                        )
                    )
                elif not row[1]:
                    errors.append(
                        f"`### {heading.text}`の調査表に空の`内容`がある: {standalone_table.row_location(index)}。"
                        f"{_EMPTY_CELL_FIX}"
                    )
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
        for index, row in enumerate(cause_table.rows):
            if len(row) != len(PLAN_BUG_CAUSE_TABLE_HEADER):
                errors.append(
                    _column_count_error(
                        f"`### {heading.text}`の原因分析表",
                        len(PLAN_BUG_CAUSE_TABLE_HEADER),
                        row,
                        cause_table.row_location(index),
                    )
                )
            elif any(not cell for cell in row[1:]):
                errors.append(
                    f"`### {heading.text}`の原因分析表に空のセルがある: {cause_table.row_location(index)}。{_EMPTY_CELL_FIX}"
                )
        for index, row in enumerate(table.rows):
            if len(row) != len(PLAN_BUG_TABLE_HEADER):
                errors.append(
                    _column_count_error(
                        f"`### {heading.text}`の調査表",
                        len(PLAN_BUG_TABLE_HEADER),
                        row,
                        table.row_location(index),
                    )
                )
            elif not row[1]:
                errors.append(f"`### {heading.text}`の調査表に空の`内容`がある: {table.row_location(index)}。{_EMPTY_CELL_FIX}")
    return errors


def _check_bug_sections(body: list[tuple[int, str]], headings: list[PlanHeading], index: int) -> list[str]:
    """`## バグ調査結果`の分離先参照または旧形式の本文内調査表が基準を満たすか確かめる。"""
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
    """計画メタ情報または旧バグ調査節が単独で参照する分離先パスを返す。"""
    metadata, _errors = parse_plan_metadata(content)
    if metadata is not None and PLAN_METADATA_BUG_FIELD in metadata.values:
        return metadata.values[PLAN_METADATA_BUG_FIELD]
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
    """計画ファイル（バグ）のH1、バグ単位H3、原因分析表と固定行の調査表が基準を満たすか確かめる。"""
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
    *,
    parent_label: str = "`## 恒久化・リファクタリング内容`",
    current_format: bool = False,
) -> list[str]:
    """恒久化とリファクタリングの検討内容が存在し、基準を満たすか確かめる。

    該当が無い場合に固定表の代わりへ置く地の文を受理し、表を置いた場合だけ列名と行名が固定された名称に一致するか確かめる。
    廃止済みの`### 類似見直し`は、既存計画の読み取りのため見出しの存在だけを受理する。
    """
    errors = _check_child_heading_sequence(
        headings,
        index,
        3,
        PLAN_PERMANENCE_H3,
        parent_label,
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
        # 表記法の有無で地の文と表を分ける。書式が不正で抽出できない表を地の文として通さない。
        if not any(line.strip().startswith("|") for _lineno, line in section):
            continue
        tables = extract_tables(section)
        if heading.text == "恒久化" and (work_type == "通常変更" or current_format):
            label = "`### 恒久化`" if current_format else "通常変更の`### 恒久化`"
            table, table_errors = _check_fixed_table(section, PLAN_PERMANENCE_TABLE_HEADER, label)
            if table is None:
                errors.append(f"{label}は{list(PLAN_PERMANENCE_TABLE_HEADER)}の4列表を置く")
            else:
                errors.extend(table_errors)
        elif heading.text == "リファクタリング":
            current, current_errors = _check_fixed_table(
                section,
                PLAN_REFACTORING_TABLE_HEADER,
                "`### リファクタリング`",
            )
            legacy = [
                table
                for table in tables
                if table.header == PLAN_BUG_TABLE_HEADER and table.row_labels() == PLAN_LEGACY_REFACTORING_TABLE_ROWS
            ]
            if current is not None:
                errors.extend(current_errors)
            elif not legacy:
                errors.append(f"`### リファクタリング`は{list(PLAN_REFACTORING_TABLE_HEADER)}の3列表を1件置く")
            elif current_format:
                for table in legacy:
                    for row_index, row in enumerate(table.rows):
                        if len(row) != len(PLAN_BUG_TABLE_HEADER):
                            errors.append(
                                _column_count_error(
                                    "`### リファクタリング`の表",
                                    len(PLAN_BUG_TABLE_HEADER),
                                    row,
                                    table.row_location(row_index),
                                )
                            )
                        elif any(not cell for cell in row):
                            errors.append(
                                f"`### リファクタリング`の表に空cellがある: {table.row_location(row_index)}。{_EMPTY_CELL_FIX}"
                            )
    return errors


def has_legacy_refactoring_table(content: str) -> bool:
    """旧2列4行のリファクタリング表が本文にある場合に真を返す。"""
    tables = extract_tables(list(iter_markdown_body_lines(content)))
    return any(
        table.header == PLAN_BUG_TABLE_HEADER and table.row_labels() == PLAN_LEGACY_REFACTORING_TABLE_ROWS for table in tables
    )


def has_legacy_acceptance_table(content: str) -> bool:
    """改名前の列名を持つ受入シナリオ表が本文にある場合に真を返す。"""
    tables = extract_tables(list(iter_markdown_body_lines(content)))
    return any(table.header in PLAN_LEGACY_ACCEPTANCE_TABLE_HEADERS for table in tables)


def _check_h1(headings: list[PlanHeading]) -> list[str]:
    """先頭に空でないATX H1があるか確かめる。"""
    h1_headings = [heading for heading in headings if heading.level == 1]
    if len(h1_headings) != 1:
        return [f"先頭にATX H1が1件必要: 実際={len(h1_headings)}件"]
    if not h1_headings[0].text or headings[0] is not h1_headings[0]:
        return ["H1は本文の先頭見出しとし、主題を空にしない"]
    return []


def _check_overview_section(body: list[tuple[int, str]], headings: list[PlanHeading], overview_index: int) -> list[str]:
    """`## 概要`直下のH3構成と地の文の記載が基準を満たすか確かめる。"""
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
    """新形式を優先して新旧の実施内容表が基準を満たすか判定し、互換形式を含む表を返す。"""
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
    for index, row in enumerate(table.rows):
        if len(row) != len(table.header):
            errors.append(_column_count_error(f"`## {PLAN_H2_ACTION}`の表", len(table.header), row, table.row_location(index)))
        elif any(not cell for cell in row):
            errors.append(f"`## {PLAN_H2_ACTION}`の表に空cellがある: {table.row_location(index)}。{_EMPTY_CELL_FIX}")
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
    """`## 実施内容`の固定表と新旧の除外・保持表が基準を満たすか確かめる。

    `origin_notices`と`origin_skips`を渡した場合だけ、WI由来行をWI本文と比べて確かめる。
    WI本文との比較結果はエラーではなく呼び出し元の警告として扱うため、戻り値の違反一覧へ混ぜない。
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
    """WIファイルのfrontmatterが値を伴う第1階層の`source`を持つかを返す。

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

    `PLAN_WI_MACHINE_DETECTABLE_ORIGIN_HEADINGS`の各見出しを、末尾のH2に限るものは末尾の厳密なH2として、
    それ以外は本文のいずれかのH2として探す。
    """
    h2_texts = [heading.text for heading in extract_headings(content) if heading.level == 2]
    for text, trailing_only in PLAN_WI_MACHINE_DETECTABLE_ORIGIN_HEADINGS.items():
        if (h2_texts[-1:] == [text]) if trailing_only else (text in h2_texts):
            return True
    return False


def _collect_origin_notices(
    name: str,
    origin_notices: list[str],
    origin_skips: list[str],
    private_notes: pathlib.Path | str | None,
    home: pathlib.Path | str | None,
) -> None:
    """`人間由来のWI`行をWI本文と比べて確かめ、移行の指摘と省略の事実を積む。

    WIファイルを特定できない場合とprivate-notesのルートが実在しない場合はその行とWI本文との比較だけを省略し、
    他の判定結果を変えない。
    """
    root = _plan_file.private_notes_root(private_notes, home=home)
    try:
        if not root.is_dir():
            origin_skips.append(f"`## {PLAN_H2_ACTION}`の由来をWI本文と比べられなかった。private-notesが実在しない: {root}")
            return
        source = _plan_file.find_wi_source(name, root)
        if source is None:
            origin_skips.append(f"`## {PLAN_H2_ACTION}`の由来をWI本文と比べられなかった。WIファイルを特定できない: {name}")
            return
        content = source.read_text(encoding="utf-8")
    except OSError as error:
        origin_skips.append(f"`## {PLAN_H2_ACTION}`の由来をWI本文と比べられなかった。WI本文を取得できない: {name}: {error}")
        return
    if _has_frontmatter_source(content) and not _has_machine_detectable_human_origin(content):
        origin_notices.append(
            f"`## {PLAN_H2_ACTION}`の`{PLAN_HUMAN_WI_ORIGIN}`がWI本文の由来と一致しない: {name}。"
            f"WI本文は`{PLAN_WI_SOURCE_KEY}`を持ち機械判定できる明示由来が無いため、"
            f"`{PLAN_AGENT_WI_ORIGIN}`とするか、機械判定できない明示由来を根拠とする場合は`[対話由来]`注記を付ける"
        )


def _wi_has_scope(
    name: str,
    origin_skips: list[str],
    private_notes: pathlib.Path | str | None,
    home: pathlib.Path | str | None,
) -> bool:
    """`エージェント由来のWI`の本文が非空の`## 適用範囲`を持つかを返す。

    WIが確定した適用範囲は計画で再導出せず参照するため、同節を持つWIの採用行は根拠を省略できる。
    WIファイルを特定できない場合はWI本文との比較を省略した事実を`origin_skips`へ積んで真を返し、作成を遮断しない。
    """
    root = _plan_file.private_notes_root(private_notes, home=home)
    try:
        if not root.is_dir():
            origin_skips.append(f"`## {PLAN_H2_ACTION}`の由来をWI本文と比べられなかった。private-notesが実在しない: {root}")
            return True
        source = _plan_file.find_wi_source(name, root)
        if source is None:
            origin_skips.append(f"`## {PLAN_H2_ACTION}`の由来をWI本文と比べられなかった。WIファイルを特定できない: {name}")
            return True
        content = source.read_text(encoding="utf-8")
    except OSError as error:
        origin_skips.append(f"`## {PLAN_H2_ACTION}`の由来をWI本文と比べられなかった。WI本文を取得できない: {name}: {error}")
        return True
    headings = extract_headings(content)
    index = find_heading_index(headings, 2, PLAN_WI_SCOPE_HEADING)
    if index is None:
        return False
    start, end = heading_subtree_range(headings, index)
    return any(line.strip() for _lineno, line in lines_within(list(enumerate(content.splitlines(), 1)), start, end))


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
    """新規書式の実施内容4列表が基準を満たすか確かめる。

    `人間由来のWI`と記載した行は、`origin_notices`と`origin_skips`を渡した場合だけ
    WIファイルのfrontmatterと本文に一致するか確かめる。`[対話由来]`注記のある行は機械判定できない明示由来を
    根拠とするためWI本文とは比較しない。`エージェント由来のWI`は採否にかかわらず根拠を必要とし、
    採用行の根拠が`-`の場合は`origin_notices`を渡した場合だけ移行の指摘を積む。
    `origin_skips`も渡した場合はWI本文を読み、非空の`## 適用範囲`を持てば指摘を積まない。
    改名前の由来は読み取り互換で受理する。

    人間由来の2区分（`人間由来のWI`と`ユーザー指示`）は、採否にかかわらず原文の要求単位ごとの
    分解結果を`根拠`へ必要とする。`根拠`が`-`である行は`origin_notices`へ積み、
    新規作成と改訂を確かめるときだけエラーとする。分解結果を持たない計画は、原文が示した集合と
    成果物が扱う集合との差を誰も観測できないためである。
    既に保存した計画を読み取りだけで確かめる処理は、同じ指摘を警告として扱う。
    """
    errors: list[str] = []
    if not table.rows:
        return [f"`## {PLAN_H2_ACTION}`の表に1行以上の内容が必要"]
    origin_index = table.header.index("由来")
    decision_index = table.header.index("採否")
    root_index = table.header.index("根拠")
    for index, row in enumerate(table.rows):
        if len(row) != len(PLAN_HUMAN_ACTION_TABLE_HEADER):
            errors.append(
                _column_count_error(
                    f"`## {PLAN_H2_ACTION}`の表",
                    len(PLAN_HUMAN_ACTION_TABLE_HEADER),
                    row,
                    table.row_location(index),
                )
            )
            continue
        if any(not cell for cell in row):
            errors.append(f"`## {PLAN_H2_ACTION}`の表に空cellがある: {table.row_location(index)}。{_EMPTY_CELL_FIX}")
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
                    "半角空白1字に続けて半角丸括弧で囲んだWIファイル名を記載する"
                    f"（例: `{PLAN_AGENT_WI_ORIGIN} (20260831-000000-001.md)`）: {origin}"
                )
        elif any(origin.startswith(f"{alias} (") for alias in _PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS):
            if wi_origin_match is None:
                errors.append(f"`## {PLAN_H2_ACTION}`の`由来`はWIファイル名付きの4値にする: {origin}")
            elif wi_origin_match.group("name") not in related_wi and (
                materials is None or wi_origin_match.group("name") not in materials.material_paths
            ):
                errors.append(
                    f"`## {PLAN_H2_ACTION}`のWI由来が`関連WI`に無い: {wi_origin_match.group('name')}。"
                    "計画メタ情報の`関連WI`へ加えるか、由来のファイル名を直す"
                )
            elif (
                origin_notices is not None
                and origin_skips is not None
                and wi_origin_kind == PLAN_HUMAN_WI_ORIGIN
                and wi_origin_match.group("note") is None
            ):
                _collect_origin_notices(wi_origin_match.group("name"), origin_notices, origin_skips, private_notes, home)
        elif review_origin is None:
            errors.append(
                f"`## {PLAN_H2_ACTION}`の`由来`は{list(PLAN_HUMAN_ORIGINS)}、計画レビュー第nラウンド、または"
                "区分と半角空白1字と半角丸括弧で囲んだWIファイル名"
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
        elif wi_origin_kind == PLAN_AGENT_WI_ORIGIN and wi_origin_match is not None:
            if not root or root == "-":
                if decision == "採用":
                    if origin_notices is not None and (
                        origin_skips is None
                        or not _wi_has_scope(wi_origin_match.group("name"), origin_skips, private_notes, home)
                    ):
                        origin_notices.append(
                            f"`## {PLAN_H2_ACTION}`の`{PLAN_AGENT_WI_ORIGIN}`の採用行の`根拠`へ、"
                            "適用範囲を再導出した結果と根拠を記載する。"
                            f"関連WIの本文が非空の`## {PLAN_WI_SCOPE_HEADING}`を持つ場合は`-`のままWIを参照する"
                        )
                else:
                    errors.append(f"`## {PLAN_H2_ACTION}`の採用以外の`根拠`は理由を自足して記載する: {root}")
        elif wi_origin_kind == PLAN_HUMAN_WI_ORIGIN or canonical_origin == PLAN_USER_INSTRUCTION_ORIGIN:
            if (not root or root == "-") and origin_notices is not None:
                origin_notices.append(
                    f"`## {PLAN_H2_ACTION}`の人間由来行の`根拠`へ、原文の要求単位ごとの分解結果と採否を記載する:"
                    f" {table.row_location(index)}"
                )
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
    """新書式の`## エージェント提案詳細`が実施内容の提案行に対応するか確かめる。"""
    if judgment_index is None:
        return [f"`## {PLAN_H2_AGENT_JUDGMENT}`が無いためエージェント提案の詳細が基準を満たすか判定できない"]

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
    """計画レビュー由来の行が絶対パスと同じラウンドを指すか確かめる。"""
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
    """`## 変更履歴`の表が固定された書式を満たすか確かめる。

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
    """新規書式の変更履歴（自然な見出しと逐語入力）が基準を満たすか確かめる。"""
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


def progress_log_rows(content: str) -> list[tuple[str, str, str]]:
    """`## 進捗ログ`の固定表の内容行を出現順に返す。

    各行の3つの値は`PLAN_PROGRESS_TABLE_HEADER`の並びに対応する。
    節が無い場合、固定表が無い場合および列数の異なる行がある場合は`ValueError`を送出する。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    progress_index = find_heading_index(headings, 2, PLAN_H2_PROGRESS)
    if progress_index is None:
        raise ActionableError(f"`## {PLAN_H2_PROGRESS}`が無い", next_action=_PROGRESS_TABLE_FIX)
    start, end = heading_subtree_range(headings, progress_index)
    tables = [table for table in extract_tables(lines_within(body, start, end)) if table.header == PLAN_PROGRESS_TABLE_HEADER]
    if not tables:
        raise ActionableError(
            f"`## {PLAN_H2_PROGRESS}`に{list(PLAN_PROGRESS_TABLE_HEADER)}の固定表が無い", next_action=_PROGRESS_TABLE_FIX
        )
    rows: list[tuple[str, str, str]] = []
    for table in tables:
        for index, row in enumerate(table.rows):
            if len(row) != len(PLAN_PROGRESS_TABLE_HEADER):
                raise ValueError(
                    _column_count_error(
                        f"`## {PLAN_H2_PROGRESS}`の表",
                        len(PLAN_PROGRESS_TABLE_HEADER),
                        row,
                        table.row_location(index),
                    )
                )
            rows.append((row[0], row[1], row[2]))
    return rows


def _check_progress_section(body: list[tuple[int, str]], headings: list[PlanHeading], progress_index: int | None) -> list[str]:
    """`## 進捗ログ`の表が固定された書式を満たすか確かめる。"""
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
    """`## 検証区分`の表が固定された2行2列の書式を満たすか確かめる。"""
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
    errors: list[str] = []
    for index, row in enumerate(table.rows):
        if len(row) != len(PLAN_VERIFICATION_TABLE_HEADER):
            errors.append(
                _column_count_error(
                    f"`## {PLAN_H2_VERIFICATION}`の表",
                    len(PLAN_VERIFICATION_TABLE_HEADER),
                    row,
                    table.row_location(index),
                )
            )
        elif not row[1]:
            errors.append(
                f"`## {PLAN_H2_VERIFICATION}`の表に空の検証コマンドがある: {table.row_location(index)}。{_EMPTY_CELL_FIX}"
            )
    return errors


def _check_termination_section(
    body: list[tuple[int, str]], headings: list[PlanHeading], termination_index: int | None
) -> list[str]:
    """`## 終端工程`に記載があるかを確かめる（終端工程が無い場合は`なし`と書く運用を許容する）。"""
    if termination_index is None:
        return []
    start, end = heading_subtree_range(headings, termination_index)
    section = lines_within(body, start, end)
    if not [line for _lineno, line in section if line.strip()]:
        return [f"`## {PLAN_H2_TERMINATION}`は終端工程を記載する（無い場合は`なし`と書く）"]
    return []


def _check_bug_and_permanence(body: list[tuple[int, str]], headings: list[PlanHeading], work_type: str | None) -> list[str]:
    """`## バグ調査結果`と`## 恒久化・リファクタリング内容`が存在し、基準を満たすか確かめる。"""
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
    """固定H2直下に自由なH3がなく、H4以深の見出しもないことを確かめる。"""
    errors: list[str] = []
    for index, heading in enumerate(headings):
        if heading.level < 3:
            continue
        parent = canonical_h2_name(
            next((candidate.text for candidate in reversed(headings[:index]) if candidate.level == 2), "")
        )
        if heading.level > 3:
            errors.append(f"`## {parent}`配下にH4以深の見出しは置かない: `{'#' * heading.level} {heading.text}`")
        elif parent in freeform_parents:
            continue
        elif heading.level == 3 and parent not in allowed_h3_parents:
            errors.append(f"`## {parent}`直下に自由なH3は置かない: `### {heading.text}`")
    return errors


def check_plan_structure(content: str) -> list[str]:
    """旧形式（単一ファイル9節）の計画が基準を満たすか判定し、違反一覧を返す。

    判定する対象は見出しの欠落、重複、順序違反、固定領域への追加H2、固定表の列と行、
    空cell、素材・要求参照先の欠落、恒久化等の空欄または結論語だけの記載とする。
    素材と要求の意味が一致するかの確認、根拠の妥当性、検討の実質はレビュー担当が判定する。
    新規作成では生成しない読み取り互換の形式であり、新書式の2ファイルは
    `check_plan_main_structure`・`check_plan_detail_structure`で基準を満たすか判定する。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    errors = _check_h1(headings)
    errors.extend(check_duplicate_headings(content))

    work_type, metadata_errors = _check_metadata_block(content)
    errors.extend(metadata_errors)

    if not [heading for heading in headings if heading.level == 2]:
        errors.append(_NO_FIXED_H2_ERROR)
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
    """新書式の計画ファイル（メイン）`<計画名>.md`が基準を満たすか判定し、(作業種別, 違反一覧)を返す。

    固定H2順は`PLAN_MAIN_H2_ORDER`とし、計画メタ情報は`関連WI`を含む5項目とする。
    改訂前の二ファイル形式は提示素材と計画ファイル（詳細）参照を読み取り互換で受理する。
    `origin_notices`と`origin_skips`を渡した場合だけ、実施内容表のWI由来行をWI本文と比べて確かめ、
    移行を促す指摘とWI本文との比較を省略した事実をそれぞれへ積む。WI本文との比較結果は違反一覧へ含めない。
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
        errors.append(_NO_FIXED_H2_ERROR)
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


def _check_nonempty_section(body: list[tuple[int, str]], headings: list[PlanHeading], heading_name: str) -> list[str]:
    """指定したH2の本文が空でないか確かめる。"""
    index = find_heading_index(headings, 2, heading_name)
    if index is None:
        return []
    start, end = heading_subtree_range(headings, index)
    if any(line.strip() for _lineno, line in lines_within(body, start, end)):
        return []
    return [f"`## {heading_name}`を空にしない"]


def check_plan_single_file_structure(
    content: str,
    *,
    origin_notices: list[str] | None = None,
    origin_skips: list[str] | None = None,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> tuple[str | None, list[str]]:
    """現行の1ファイル計画が基準を満たすか判定し、(作業種別, 違反一覧)を返す。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    errors = _check_h1(headings)
    errors.extend(check_duplicate_headings(content))

    parsed, _parse_errors = parse_plan_metadata(content)
    parsed_work_type = parsed.values.get("作業種別") if parsed is not None else None
    expected_metadata = PLAN_METADATA_CURRENT_FIELDS
    # 関連WIの`## 原因分析`が原因分析の記録先となるバグ対応計画は計画ファイル（バグ）を持たない。
    # 入力WIが無い計画は原因分析の記録先が他に無いため、計画内の同行を必須とする。
    if (
        parsed is not None
        and parsed_work_type == "バグ対応"
        and (PLAN_METADATA_BUG_FIELD in parsed.values or not parsed.related_wi)
    ):
        expected_metadata = (*expected_metadata, PLAN_METADATA_BUG_FIELD)
    work_type, metadata_errors = _check_metadata_block(content, expected_fields=expected_metadata)
    errors.extend(metadata_errors)
    errors.extend(_check_fixed_h2_layout(headings, list(PLAN_SINGLE_FILE_H2_ORDER)))

    overview_index = find_heading_index(headings, 2, PLAN_H2_OVERVIEW)
    if overview_index is not None:
        errors.extend(_check_overview_section(body, headings, overview_index))

    action_index = find_heading_index(headings, 2, PLAN_H2_ACTION)
    errors.extend(
        _check_action_section(
            body,
            headings,
            action_index,
            None,
            set(),
            set(),
            set(),
            frozenset(filename for filename, _summary in parsed.related_wi) if parsed is not None else frozenset(),
            origin_notices=origin_notices,
            origin_skips=origin_skips,
            private_notes=private_notes,
            home=home,
        )
    )
    if not has_human_action_table(content):
        errors.append(f"`## {PLAN_H2_ACTION}`には{list(PLAN_HUMAN_ACTION_TABLE_HEADER)}の4列表が必要")

    errors.extend(_check_nonempty_section(body, headings, PLAN_H2_REQUIREMENTS))

    verification_index = find_heading_index(headings, 2, PLAN_H2_CURRENT_VERIFICATION)
    verification_tables = []
    if verification_index is not None:
        verification_start, verification_end = heading_subtree_range(headings, verification_index)
        verification_tables = extract_tables(lines_within(body, verification_start, verification_end))
    legacy_verification = any(
        table.header == PLAN_VERIFICATION_TABLE_HEADER and table.row_labels() == PLAN_LEGACY_CURRENT_VERIFICATION_TABLE_ROWS
        for table in verification_tables
    )

    requirements_index = find_heading_index(headings, 2, PLAN_H2_REQUIREMENTS)
    acceptance_index = (
        next(
            (index for index, heading in child_headings(headings, requirements_index, 3) if heading.text == PLAN_ACCEPTANCE_H3),
            None,
        )
        if requirements_index is not None
        else None
    )
    if acceptance_index is None:
        if not legacy_verification:
            errors.append(f"`## {PLAN_H2_REQUIREMENTS}`に`### {PLAN_ACCEPTANCE_H3}`が必要")
    else:
        start, end = heading_subtree_range(headings, acceptance_index)
        acceptance_lines = lines_within(body, start, end)
        acceptance_tables = extract_tables(acceptance_lines)
        has_adopted_action = False
        if action_index is not None:
            action_start, action_end = heading_subtree_range(headings, action_index)
            for action_table in extract_tables(lines_within(body, action_start, action_end)):
                if action_table.header == PLAN_HUMAN_ACTION_TABLE_HEADER:
                    has_adopted_action = any(
                        len(row) == len(action_table.header) and row[2] in ("採用", "部分採用") for row in action_table.rows
                    )
                    break
        if has_adopted_action:
            table = next(
                (
                    item
                    for item in acceptance_tables
                    if item.header in (PLAN_ACCEPTANCE_TABLE_HEADER, *PLAN_LEGACY_ACCEPTANCE_TABLE_HEADERS)
                ),
                None,
            )
            if table is None or not table.rows:
                errors.append(f"`### {PLAN_ACCEPTANCE_H3}`には{list(PLAN_ACCEPTANCE_TABLE_HEADER)}の表が必要")
            else:
                for row_index, row in enumerate(table.rows):
                    if len(row) != len(PLAN_ACCEPTANCE_TABLE_HEADER) or any(not cell for cell in row):
                        errors.append(
                            f"`### {PLAN_ACCEPTANCE_H3}`に空セルまたは列数不一致がある: {table.row_location(row_index)}。"
                            f"{_EMPTY_CELL_FIX}"
                        )
        elif [line.strip() for _lineno, line in acceptance_lines if line.strip()] != ["なし"]:
            errors.append(f"採用行が無い場合は`### {PLAN_ACCEPTANCE_H3}`の本文を`なし`にする")

    permanence_index = find_heading_index(headings, 2, PLAN_H2_CURRENT_PERMANENCE)
    if permanence_index is not None:
        errors.extend(
            _check_permanence_sections(
                body,
                headings,
                permanence_index,
                work_type,
                parent_label=f"`## {PLAN_H2_CURRENT_PERMANENCE}`",
                current_format=True,
            )
        )

    history_index = find_heading_index(headings, 2, PLAN_H2_CURRENT_HISTORY)
    errors.extend(
        _check_human_history_section(
            headings,
            history_index,
            content,
            require_user_event=has_adopted_human_user_instruction(content),
        )
    )

    if verification_index is not None:
        table = _find_table_with_rows(verification_tables, PLAN_CURRENT_VERIFICATION_TABLE_ROWS)
        if table is None:
            table = _find_table_with_rows(verification_tables, PLAN_LEGACY_CURRENT_SINGLE_VERIFICATION_TABLE_ROWS)
        if table is None:
            table = _find_table_with_rows(verification_tables, PLAN_LEGACY_CURRENT_VERIFICATION_TABLE_ROWS)
        if table is None or table.header != PLAN_VERIFICATION_TABLE_HEADER:
            errors.append(
                f"`## {PLAN_H2_CURRENT_VERIFICATION}`は{list(PLAN_VERIFICATION_TABLE_HEADER)}の2列と"
                f"固定行（{list(PLAN_CURRENT_VERIFICATION_TABLE_ROWS)}）の表にする"
            )
        else:
            for index, row in enumerate(table.rows):
                if len(row) != len(PLAN_VERIFICATION_TABLE_HEADER):
                    errors.append(
                        _column_count_error(
                            f"`## {PLAN_H2_CURRENT_VERIFICATION}`の表",
                            len(PLAN_VERIFICATION_TABLE_HEADER),
                            row,
                            table.row_location(index),
                        )
                    )
                elif not row[1]:
                    errors.append(
                        f"`## {PLAN_H2_CURRENT_VERIFICATION}`の表に空の検証コマンドがある: {table.row_location(index)}。"
                        f"{_EMPTY_CELL_FIX}"
                    )
                elif table.row_labels() == PLAN_CURRENT_VERIFICATION_TABLE_ROWS:
                    try:
                        commands_from_cell(row[1])
                    except ActionableError as error:
                        errors.append(f"{table.row_location(index)}: {error.message}")

    termination_index = find_heading_index(headings, 2, PLAN_H2_TERMINATION)
    errors.extend(_check_termination_section(body, headings, termination_index))
    progress_index = find_heading_index(headings, 2, PLAN_H2_CURRENT_PROGRESS)
    errors.extend(_check_progress_section(body, headings, progress_index))

    allowed_h3_parents = {
        PLAN_H2_OVERVIEW,
        PLAN_H2_REQUIREMENTS,
        PLAN_H2_CURRENT_PERMANENCE,
        PLAN_H2_CURRENT_HISTORY,
        PLAN_H2_HISTORY,
    }
    errors.extend(
        _check_h3_and_deeper(
            headings,
            allowed_h3_parents,
            frozenset({PLAN_H2_REQUIREMENTS, PLAN_H2_CURRENT_HISTORY, PLAN_H2_HISTORY}),
        )
    )
    return work_type, errors


def check_plan_detail_structure(content: str, work_type: str | None) -> list[str]:
    """新書式の計画ファイル（詳細）`<計画名>.detail.md`が基準を満たすか判定し、違反一覧を返す。

    計画ファイル（詳細）は計画メタ情報を持たないため、作業種別は計画ファイル（メイン）の判定結果から受け取る。
    固定H2順は`PLAN_DETAIL_H2_ORDER`（恒久化・リファクタリング内容・実装資料・完了条件）とし、
    作業種別が`バグ対応`の場合だけ先頭へ`バグ調査結果`を加える。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    errors = check_duplicate_headings(content)

    if not [heading for heading in headings if heading.level == 2]:
        errors.append(_NO_FIXED_H2_ERROR)
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
