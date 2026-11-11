"""計画の変更履歴と進捗ログの検査。"""

from __future__ import annotations

from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._plan.structure.constants import (
    PLAN_H2_HISTORY,
    PLAN_H2_LEGACY_HISTORY,
    PLAN_H2_LEGACY_PROGRESS,
    PLAN_H2_PROGRESS,
    PLAN_HISTORY_TABLE_HEADER,
    PLAN_HISTORY_USER_EVENT_PATTERN,
    PLAN_HISTORY_USER_EVENT_PREFIX,
    PLAN_LEGACY_HISTORY_USER_EVENT_PATTERN,
    PLAN_PROGRESS_TABLE_HEADER,
)
from agent_toolkit._plan.structure.markdown import (
    PlanHeading,
    _check_fixed_table,
    _column_count_error,
    child_headings,
    extract_headings,
    extract_tables,
    find_heading_index,
    heading_subtree_range,
    iter_markdown_body_lines,
    lines_within,
)
from agent_toolkit._plan.structure.materials import _MATERIAL_FENCE_PATTERN
from agent_toolkit._plan.structure.parsing import _INTERNAL_PLAN_ID_PATTERN
from agent_toolkit._plan.structure.references import _check_history_rows

_PROGRESS_TABLE_FIX = (
    f"`## {PLAN_H2_PROGRESS}`見出しと、表頭（{list(PLAN_PROGRESS_TABLE_HEADER)}の列）だけの固定表を置いてから再実行する"
)


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
