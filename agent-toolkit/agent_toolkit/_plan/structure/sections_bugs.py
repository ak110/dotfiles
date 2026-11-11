"""計画のバグ調査と原因分析表の検査。"""

from __future__ import annotations

import re

from agent_toolkit._plan.structure.constants import (
    PLAN_BUG_CAUSE_TABLE_HEADER,
    PLAN_BUG_CAUSE_TABLE_ROWS,
    PLAN_BUG_FILE_REFERENCE_LEGACY_PREFIX,
    PLAN_BUG_FILE_REFERENCE_PREFIX,
    PLAN_BUG_TABLE_HEADER,
    PLAN_BUG_TABLE_ROWS,
    PLAN_H2_BUG,
    PLAN_LEGACY_BUG_TABLE_ROWS,
    PLAN_LEGACY_STANDALONE_BUG_TABLE_ROWS,
    PLAN_METADATA_BUG_FIELD,
    PLAN_PREVIOUS_BUG_TABLE_ROWS,
)
from agent_toolkit._plan.structure.markdown import (
    _EMPTY_CELL_FIX,
    PlanHeading,
    _column_count_error,
    child_headings,
    extract_headings,
    extract_tables,
    find_heading_index,
    heading_subtree_range,
    iter_markdown_body_lines,
    lines_within,
)
from agent_toolkit._plan.structure.parsing import parse_plan_metadata
from agent_toolkit._plan.structure.sections_layout import _check_h1


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


def check_cause_analysis_format(content: str) -> list[str]:
    """原因分析表のL4と系統別候補の記入を検査する。内容の妥当性は検収者が判断する。"""
    errors: list[str] = []
    tables = extract_tables(list(iter_markdown_body_lines(content)))
    for table in tables:
        if table.header == PLAN_BUG_CAUSE_TABLE_HEADER:
            for index, row in enumerate(table.rows):
                if len(row) != len(PLAN_BUG_CAUSE_TABLE_HEADER):
                    continue
                value = row[-1].strip().strip("*_`~").strip()
                if not value or (value.startswith("該当なし") and not value[4:].strip(" 。、.：:（）()")):
                    errors.append(f"L4は空欄にせず、該当なしには理由を書く: {table.row_location(index)}")
        if table.header == PLAN_BUG_TABLE_HEADER:
            for index, row in enumerate(table.rows):
                if len(row) != 2 or row[0] not in ("類似見直し観点", "再発防止策"):
                    continue
                decided = _cause_candidate_decisions(row[1])
                for factor in PLAN_BUG_CAUSE_TABLE_ROWS:
                    if factor not in decided:
                        errors.append(
                            f"{row[0]}には{factor}の候補と採否（不採用は理由付き）を書く: {table.row_location(index)}"
                        )
    return errors


_CAUSE_FACTOR_PATTERN = r"(作り込み|見逃し)(?:要因|側|候補)?"


def _cause_candidate_decisions(value: str) -> set[str]:
    """文内の列挙と、両候補を明示する後続文の採否を対応付ける。

    候補名の再出現は補足でもあり得るため、確定済みの採否を上書きしない。
    内容の妥当性は意味検収へ委ね、ここでは決定と不採用理由の記載を確かめる。
    """
    decided: set[str] = set()
    mentioned: set[str] = set()
    previous: set[str] = set()
    for sentence in re.finditer(r"[^。;；\n]+[。;；\n]?", value):
        pending: set[str] = set()
        text = sentence.group()
        for token in re.finditer(_CAUSE_FACTOR_PATTERN + r"|不採用|採用", text):
            name = token.group()
            if token[1] is not None:
                name = token[1] + "要因"
                pending.add(name)
                mentioned.add(name)
                continue
            tail = text[token.end() :]
            if re.match(r"(?:する|とする|した)?(?:予定|方針|か|を|候補)", tail):
                continue
            if re.search(r"保留|未決定|後に.*決める|今後.*決める", text):
                continue
            targets = pending
            if not targets and re.search(r"両(?:候補|系統|方)|双方|ともに|いずれも", text):
                targets = mentioned
            elif not targets and text.lstrip().startswith("を"):
                targets = previous
            if name == "不採用":
                # 理由は同文の前後にも、採否の直後の文にも記載できる。
                reason_tail = value[sentence.start() + token.end() :]
                reason_tail = re.split(_CAUSE_FACTOR_PATTERN, reason_tail, maxsplit=1)[0]
                reason_tail = re.sub(r"^(?:とする|する|した)?[ 。、.：:（）()]*", "", reason_tail)
                if not reason_tail and not re.search(r"ため|ので|理由|見合わない|上回る", text[: token.start()]):
                    continue
            decided.update(targets)
            pending = set()
        previous = pending
    return decided


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
        errors.extend(check_cause_analysis_format("\n".join(line for _lineno, line in lines_within(body, start, end))))
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
            table
            for table in tables
            if table.row_labels() in (PLAN_BUG_TABLE_ROWS, PLAN_PREVIOUS_BUG_TABLE_ROWS, PLAN_LEGACY_BUG_TABLE_ROWS)
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
    """現行より前の行構成を持つ調査表を含む場合に真を返す。既存計画の移行案内にだけ用いる。"""
    tables = extract_tables(list(iter_markdown_body_lines(content)))
    return any(
        table.header == PLAN_BUG_TABLE_HEADER
        and table.row_labels() in (PLAN_PREVIOUS_BUG_TABLE_ROWS, PLAN_LEGACY_BUG_TABLE_ROWS)
        for table in tables
    )


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
