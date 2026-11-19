"""計画ファイル（メイン・詳細・バグ）の固定H2と各節の構造の判定、旧形式の判別と`由来`の判定。"""

from __future__ import annotations

import pathlib

from agent_toolkit._common import markdown_headings
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._plan.structure.constants import (
    PLAN_ACCEPTANCE_H3,
    PLAN_ACCEPTANCE_TABLE_HEADER,
    PLAN_CURRENT_VERIFICATION_TABLE_ROWS,
    PLAN_EIGHT_SECTION_H2_ORDER,
    PLAN_H2_ACTION,
    PLAN_H2_AGENT_JUDGMENT,
    PLAN_H2_BUG,
    PLAN_H2_CURRENT_PROGRESS,
    PLAN_H2_CURRENT_VERIFICATION,
    PLAN_H2_DESIGN,
    PLAN_H2_EIGHT_SECTION_HISTORY,
    PLAN_H2_EIGHT_SECTION_PERMANENCE,
    PLAN_H2_HISTORY,
    PLAN_H2_IMPLEMENTATION,
    PLAN_H2_LEGACY_AGENT_JUDGMENT,
    PLAN_H2_MATERIALS,
    PLAN_H2_ORDER_HEAD,
    PLAN_H2_ORDER_TAIL,
    PLAN_H2_OVERVIEW,
    PLAN_H2_PERMANENCE,
    PLAN_H2_PROGRESS,
    PLAN_H2_REQUIREMENTS,
    PLAN_H2_TERMINATION,
    PLAN_H2_VERIFICATION,
    PLAN_HUMAN_ACTION_TABLE_HEADER,
    PLAN_LEGACY_ACCEPTANCE_TABLE_HEADERS,
    PLAN_LEGACY_CURRENT_SINGLE_VERIFICATION_TABLE_ROWS,
    PLAN_LEGACY_CURRENT_VERIFICATION_TABLE_ROWS,
    PLAN_LEGACY_MAIN_H2_ORDER,
    PLAN_MAIN_H2_ORDER,
    PLAN_METADATA_BUG_FIELD,
    PLAN_METADATA_CURRENT_FIELDS,
    PLAN_METADATA_DETAIL_FIELD,
    PLAN_METADATA_H3,
    PLAN_METADATA_MAIN_FIELDS,
    PLAN_METADATA_RELATED_WI_FIELD,
    PLAN_METADATA_TWO_FILE_FIELDS,
    PLAN_TWO_FILE_MAIN_H2_ORDER,
    PLAN_VERIFICATION_TABLE_HEADER,
    REQUEST_H2_CAUSE,
    REQUEST_H2_ORDER,
    REQUEST_H2_QUOTATION,
)
from agent_toolkit._plan.structure.markdown import (
    _EMPTY_CELL_FIX,
    MarkdownTable,
    PlanHeading,
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
from agent_toolkit._plan.structure.materials import PlanMaterials, parse_plan_materials
from agent_toolkit._plan.structure.parsing import (
    PLAN_METADATA_TOP_PARENT,
    _detail_expected_h2,
    _find_table_with_rows,
    _legacy_expected_h2,
    parse_plan_implementation_units,
    parse_plan_metadata,
)
from agent_toolkit._plan.structure.request_sections import request_section_problems, request_sections
from agent_toolkit._plan.structure.sections_actions import (
    _check_action_section,
    _check_agent_judgment_section,
    has_adopted_human_user_instruction,
    has_human_action_table,
    has_legacy_action_table,
    legacy_wi_origins,
)
from agent_toolkit._plan.structure.sections_bugs import (
    cause_record_errors,
    cause_record_lines,
    check_bug_file_structure,
    extract_bug_file_reference,
    has_legacy_bug_table,
    has_legacy_cause_record,
)
from agent_toolkit._plan.structure.sections_history import (
    _check_history_section,
    _check_human_history_section,
    _check_progress_section,
    has_legacy_history_user_event,
    has_progress_log_rows,
    progress_log_rows,
)
from agent_toolkit._plan.structure.sections_layout import (
    _check_fixed_h2_layout,
    _check_h1,
    _check_h3_and_deeper,
    _check_metadata_block,
    _check_nonempty_section,
    _check_overview_section,
)
from agent_toolkit._plan.structure.sections_permanence import (
    _check_bug_and_permanence,
    _check_permanence_sections,
    _check_termination_section,
    _check_verification_section,
    has_legacy_acceptance_table,
    has_legacy_refactoring_table,
)
from agent_toolkit._plan.structure.verification import commands_from_cell

_NO_FIXED_H2_ERROR = (
    "固定H2が1件も無い。対象が計画ファイルか確かめる。"
    "計画ファイルなら`agent-toolkit:write-plan`の`references/plan-file-standards.md`「初回起草の雛形」の固定H2を置く"
)


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


def is_canonical_main_format(content_or_headings: str | list[PlanHeading]) -> bool:
    """新しいメイン計画書式（`エージェント提案詳細`を含む）かを返す。"""
    headings = extract_headings(content_or_headings) if isinstance(content_or_headings, str) else content_or_headings
    h2_names = {heading.text for heading in headings if heading.level == 2}
    return bool(h2_names & {PLAN_H2_AGENT_JUDGMENT, PLAN_H2_LEGACY_AGENT_JUDGMENT, PLAN_H2_HISTORY, PLAN_H2_PROGRESS})


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
    `origin_notices`は根拠の記入に関する移行案内を受け取り、`origin_skips`は参照取得の省略を受け取る。
    WI本文を参照するのは、採用行で根拠を省略できる適用範囲の記載を確かめる場合である。
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


def _check_human_action(
    content: str,
    body: list[tuple[int, str]],
    headings: list[PlanHeading],
    related_wi: frozenset[str],
    origin_notices: list[str] | None,
    origin_skips: list[str] | None,
    *,
    private_notes: pathlib.Path | str | None,
    home: pathlib.Path | str | None,
) -> list[str]:
    """1ファイルの計画の`## 実施内容`が、由来・採否・根拠を持つ4列表の基準を満たすか確かめる。"""
    errors = _check_action_section(
        body,
        headings,
        find_heading_index(headings, 2, PLAN_H2_ACTION),
        None,
        set(),
        set(),
        set(),
        related_wi,
        origin_notices=origin_notices,
        origin_skips=origin_skips,
        private_notes=private_notes,
        home=home,
    )
    if not has_human_action_table(content):
        errors.append(f"`## {PLAN_H2_ACTION}`には{list(PLAN_HUMAN_ACTION_TABLE_HEADER)}の4列表が必要")
    return errors


def check_plan_eight_section_structure(
    content: str,
    *,
    origin_notices: list[str] | None = None,
    origin_skips: list[str] | None = None,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> tuple[str | None, list[str]]:
    """8節形式の1ファイル計画が基準を満たすか判定し、(作業種別, 違反一覧)を返す。保存済みの計画の読み取り専用である。"""
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
    expected_h2 = list(PLAN_EIGHT_SECTION_H2_ORDER)
    if find_heading_index(headings, 2, PLAN_H2_DESIGN) is not None:
        expected_h2.insert(expected_h2.index(PLAN_H2_REQUIREMENTS) + 1, PLAN_H2_DESIGN)
    errors.extend(_check_fixed_h2_layout(headings, expected_h2))

    overview_index = find_heading_index(headings, 2, PLAN_H2_OVERVIEW)
    if overview_index is not None:
        errors.extend(_check_overview_section(body, headings, overview_index))

    related_wi = frozenset(filename for filename, _summary in parsed.related_wi) if parsed is not None else frozenset()
    action_index = find_heading_index(headings, 2, PLAN_H2_ACTION)
    errors.extend(
        _check_human_action(
            content, body, headings, related_wi, origin_notices, origin_skips, private_notes=private_notes, home=home
        )
    )

    errors.extend(_check_nonempty_section(body, headings, PLAN_H2_REQUIREMENTS))
    errors.extend(_check_nonempty_section(body, headings, PLAN_H2_DESIGN))

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
                    if len(row) != len(table.header) or any(not cell for cell in row):
                        errors.append(
                            f"`### {PLAN_ACCEPTANCE_H3}`に空セルまたは列数不一致がある: {table.row_location(row_index)}。"
                            f"{_EMPTY_CELL_FIX}"
                        )
        elif [line.strip() for _lineno, line in acceptance_lines if line.strip()] != ["なし"]:
            errors.append(f"採用行が無い場合は`### {PLAN_ACCEPTANCE_H3}`の本文を`なし`にする")

    permanence_index = find_heading_index(headings, 2, PLAN_H2_EIGHT_SECTION_PERMANENCE)
    if permanence_index is not None:
        errors.extend(
            _check_permanence_sections(
                body,
                headings,
                permanence_index,
                work_type,
                parent_label=f"`## {PLAN_H2_EIGHT_SECTION_PERMANENCE}`",
                current_format=True,
            )
        )

    history_index = find_heading_index(headings, 2, PLAN_H2_EIGHT_SECTION_HISTORY)
    errors.extend(
        _check_human_history_section(
            headings,
            history_index,
            content,
            require_user_event=has_adopted_human_user_instruction(content),
        )
    )

    if verification_index is not None:
        errors.extend(
            _check_changed_scope_verification(
                verification_tables,
                (
                    PLAN_CURRENT_VERIFICATION_TABLE_ROWS,
                    PLAN_LEGACY_CURRENT_SINGLE_VERIFICATION_TABLE_ROWS,
                    PLAN_LEGACY_CURRENT_VERIFICATION_TABLE_ROWS,
                ),
            )
        )

    termination_index = find_heading_index(headings, 2, PLAN_H2_TERMINATION)
    errors.extend(_check_termination_section(body, headings, termination_index))
    progress_index = find_heading_index(headings, 2, PLAN_H2_CURRENT_PROGRESS)
    errors.extend(_check_progress_section(body, headings, progress_index))

    allowed_h3_parents = {
        PLAN_H2_OVERVIEW,
        PLAN_H2_REQUIREMENTS,
        PLAN_H2_DESIGN,
        PLAN_H2_EIGHT_SECTION_PERMANENCE,
        PLAN_H2_EIGHT_SECTION_HISTORY,
        PLAN_H2_HISTORY,
    }
    errors.extend(
        _check_h3_and_deeper(
            headings,
            allowed_h3_parents,
            frozenset({PLAN_H2_REQUIREMENTS, PLAN_H2_EIGHT_SECTION_HISTORY, PLAN_H2_HISTORY}),
        )
    )
    return work_type, errors


def _check_changed_scope_verification(tables: list[MarkdownTable], accepted_rows: tuple[tuple[str, ...], ...]) -> list[str]:
    """`## 検証`の固定表の行名、列数、検証コマンドの形を確かめる。

    `accepted_rows`は受け付ける行名の組を優先順に渡す。先頭の組が現行の行名である。
    """
    errors: list[str] = []
    table = next(
        (found for rows in accepted_rows if (found := _find_table_with_rows(tables, rows)) is not None),
        None,
    )
    if table is None or table.header != PLAN_VERIFICATION_TABLE_HEADER:
        return [
            f"`## {PLAN_H2_CURRENT_VERIFICATION}`は{list(PLAN_VERIFICATION_TABLE_HEADER)}の2列と"
            f"固定行（{list(PLAN_CURRENT_VERIFICATION_TABLE_ROWS)}）の表にする"
        ]
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
    return errors


def _check_request_plan_layout(headings: list[PlanHeading]) -> list[str]:
    """現行の計画の見出しの配置（H1直下、固定H2と要求を書く節の並び、固定H2の配下）を確かめる。"""
    errors: list[str] = []
    first_h2 = next((index for index, heading in enumerate(headings) if heading.level == 2), len(headings))
    if [(heading.level, heading.text) for heading in headings[1:first_h2]] != [(3, PLAN_METADATA_H3)]:
        errors.append(f"H1直下の見出しは`### {PLAN_METADATA_H3}`1件だけにする")

    frame = (*PLAN_H2_ORDER_HEAD, *PLAN_H2_ORDER_TAIL)
    h2_names = [heading.text for heading in headings if heading.level == 2]
    for name in frame:
        if (count := h2_names.count(name)) != 1:
            errors.append(f"固定H2`## {name}`は1件必要: 実際={count}件")
    if unknown := [name for name in h2_names if name not in frame and name not in REQUEST_H2_ORDER]:
        errors.append(
            f"H2は{list(PLAN_H2_ORDER_HEAD)}、要求を書く節{list(REQUEST_H2_ORDER)}、{list(PLAN_H2_ORDER_TAIL)}だけを置く: "
            f"実際={unknown}"
        )
    known = [name for name in h2_names if name in frame or name in REQUEST_H2_ORDER]
    expected = [
        *PLAN_H2_ORDER_HEAD,
        *(name for name in known if name in REQUEST_H2_ORDER),
        *PLAN_H2_ORDER_TAIL,
    ]
    if all(h2_names.count(name) == 1 for name in frame) and known != expected:
        errors.append(f"H2は{list(PLAN_H2_ORDER_HEAD)}、要求を書く節、{list(PLAN_H2_ORDER_TAIL)}の順に置く: 実際={h2_names}")
    parent = ""
    for heading in headings:
        if heading.level == 2:
            parent = heading.text
        elif heading.level > 2 and parent in frame:
            errors.append(f"`## {parent}`の配下に見出しは置かない: `{'#' * heading.level} {heading.text}`")
    return errors


def check_plan_request_structure(
    content: str,
    *,
    origin_notices: list[str] | None = None,
    origin_skips: list[str] | None = None,
    private_notes: pathlib.Path | str | None = None,
    home: pathlib.Path | str | None = None,
) -> tuple[str | None, list[str]]:
    """現行の計画ファイルが基準を満たすか判定し、(作業種別, 違反一覧)を返す。

    H1と要約、計画メタ情報、`## 実施内容`、要求を書く節、`## 検証`、`## 進捗ログ`をこの順に持つ。
    要求を書く節の必須節、空の節、順序は、通常AWIの本文と同じ`request_section_problems`で判定する。
    `## 原因分析`は`cause_record_errors`で判定し、関連WIが無いバグ対応の計画では必須とする。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    errors = _check_h1(headings)
    errors.extend(check_duplicate_headings(content))

    parsed, _parse_errors = parse_plan_metadata(content)
    work_type, metadata_errors = _check_metadata_block(
        content, expected_fields=PLAN_METADATA_CURRENT_FIELDS, parent=PLAN_METADATA_TOP_PARENT
    )
    errors.extend(metadata_errors)
    related_wi = frozenset(filename for filename, _summary in parsed.related_wi) if parsed is not None else frozenset()

    if headings and headings[0].level == 1:
        summary_end = headings[1].lineno if len(headings) > 1 else None
        if not any(line.strip() for _lineno, line in lines_within(body, headings[0].lineno, summary_end)):
            errors.append("H1直下に要約（全体像、目的、対象範囲）を書く")
    errors.extend(_check_request_plan_layout(headings))
    # 要求を書く節はMarkdown構文の見出しで切り出すため、`extract_headings`が見ない字下げ付きのH2も節になる。
    # 見出しの配置の検査を抜けないよう、そのH2を拒否する。
    body_linenos = {lineno for lineno, _line in body}
    h2_linenos = {heading.lineno for heading in headings if heading.level == 2}
    errors.extend(
        f"H2の`##`は行頭に置く: {token.map[0] + 1}行目`## {name.strip()}`"
        for token, name in markdown_headings.top_level_atx_headings(content, 2)
        if token.map is not None and token.map[0] + 1 in body_linenos and token.map[0] + 1 not in h2_linenos
    )

    errors.extend(
        _check_human_action(
            content, body, headings, related_wi, origin_notices, origin_skips, private_notes=private_notes, home=home
        )
    )

    frame = (*PLAN_H2_ORDER_HEAD, *PLAN_H2_ORDER_TAIL)
    sections = [(name, has_body) for name, has_body in request_sections(content) if name not in frame]
    errors.extend(f"要求を書く節: {problem}" for problem in request_section_problems(sections))
    filled = {name for name, has_body in sections if has_body}
    cause = next(
        (lines for name, lines in markdown_headings.h2_section_bodies(content) if name == REQUEST_H2_CAUSE),
        None,
    )
    if cause is not None and work_type == "通常変更":
        errors.append(f"作業種別が`通常変更`の計画に`## {REQUEST_H2_CAUSE}`は置かない")
    elif cause is not None:
        errors.extend(f"`## {REQUEST_H2_CAUSE}`: {error}" for error in cause_record_errors("\n".join(cause)))
    elif work_type == "バグ対応" and not related_wi:
        errors.append(
            f"関連WIが無いバグ対応の計画には`## {REQUEST_H2_CAUSE}`が必要。"
            "`agent-basics:bugfix`の`SKILL.md`「記録」の行頭語で始まる行で書く"
        )
    if has_adopted_human_user_instruction(content) and REQUEST_H2_QUOTATION not in filled:
        errors.append(
            f"`ユーザー指示`の行を`採用`か`部分採用`とした計画には、ユーザーの原文を転記した`## {REQUEST_H2_QUOTATION}`が必要"
        )

    verification_index = find_heading_index(headings, 2, PLAN_H2_CURRENT_VERIFICATION)
    if verification_index is not None:
        start, end = heading_subtree_range(headings, verification_index)
        errors.extend(
            _check_changed_scope_verification(
                extract_tables(lines_within(body, start, end)), (PLAN_CURRENT_VERIFICATION_TABLE_ROWS,)
            )
        )
    progress_index = find_heading_index(headings, 2, PLAN_H2_CURRENT_PROGRESS)
    errors.extend(_check_progress_section(body, headings, progress_index))
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


__all__ = [
    "cause_record_errors",
    "cause_record_lines",
    "check_bug_file_structure",
    "extract_bug_file_reference",
    "has_adopted_human_user_instruction",
    "has_human_action_table",
    "has_legacy_acceptance_table",
    "has_legacy_action_table",
    "has_legacy_bug_table",
    "has_legacy_cause_record",
    "has_legacy_history_user_event",
    "has_legacy_refactoring_table",
    "has_progress_log_rows",
    "legacy_wi_origins",
    "progress_log_rows",
]
