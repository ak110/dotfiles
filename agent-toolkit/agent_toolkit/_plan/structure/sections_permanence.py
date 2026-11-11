"""計画の恒久化・リファクタリングと公開・検証節の検査。"""

from __future__ import annotations

from agent_toolkit._plan.structure.constants import (
    PLAN_BUG_TABLE_HEADER,
    PLAN_H2_BUG,
    PLAN_H2_PERMANENCE,
    PLAN_H2_TERMINATION,
    PLAN_H2_VERIFICATION,
    PLAN_LEGACY_ACCEPTANCE_TABLE_HEADERS,
    PLAN_LEGACY_PERMANENCE_H3,
    PLAN_LEGACY_REFACTORING_TABLE_ROWS,
    PLAN_PERMANENCE_H3,
    PLAN_PERMANENCE_TABLE_HEADER,
    PLAN_REFACTORING_TABLE_HEADER,
    PLAN_VERIFICATION_TABLE_HEADER,
    PLAN_VERIFICATION_TABLE_ROWS,
)
from agent_toolkit._plan.structure.markdown import (
    _EMPTY_CELL_FIX,
    PlanHeading,
    _check_fixed_table,
    _column_count_error,
    child_headings,
    extract_tables,
    find_heading_index,
    heading_subtree_range,
    iter_markdown_body_lines,
    lines_within,
)
from agent_toolkit._plan.structure.parsing import _find_table_with_rows, _is_placeholder_only
from agent_toolkit._plan.structure.sections_bugs import _check_bug_sections
from agent_toolkit._plan.structure.sections_layout import _check_child_heading_sequence


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
    """`## 公開工程`に記載があるかを確かめる（公開工程が無い場合は`なし`と書く運用を許容する）。"""
    if termination_index is None:
        return []
    start, end = heading_subtree_range(headings, termination_index)
    section = lines_within(body, start, end)
    if not [line for _lineno, line in section if line.strip()]:
        return [f"`## {PLAN_H2_TERMINATION}`は公開工程を記載する（無い場合は`なし`と書く）"]
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
