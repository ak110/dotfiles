"""計画の実施内容表と由来・根拠の検査。"""

from __future__ import annotations

import pathlib

from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan.structure.constants import (
    _PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS,
    PLAN_ACTION_DECISIONS,
    PLAN_ACTION_TABLE_HEADER,
    PLAN_AGENT_WI_ORIGIN,
    PLAN_EXCLUSION_H3,
    PLAN_H2_ACTION,
    PLAN_H2_AGENT_JUDGMENT,
    PLAN_HUMAN_ACTION_TABLE_HEADER,
    PLAN_HUMAN_JUDGMENT_TABLE_HEADER,
    PLAN_HUMAN_ORIGINS,
    PLAN_HUMAN_REVIEW_ORIGIN_PATTERN,
    PLAN_HUMAN_REVIEW_ROOT_PATTERN,
    PLAN_HUMAN_WI_ORIGIN,
    PLAN_LEGACY_ACTION_TABLE_HEADER,
    PLAN_USER_INSTRUCTION_ORIGIN,
    PLAN_WI_ORIGIN_ALIASES,
    PLAN_WI_ORIGIN_PATTERN,
    PLAN_WI_SCOPE_HEADING,
    plan_human_review_path_is_absolute,
)
from agent_toolkit._plan.structure.markdown import (
    _EMPTY_CELL_FIX,
    MarkdownTable,
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
from agent_toolkit._plan.structure.materials import PlanMaterials
from agent_toolkit._plan.structure.parsing import _INTERNAL_PLAN_ID_PATTERN, canonical_wi_origin
from agent_toolkit._plan.structure.references import (
    _check_action_decisions,
    _check_action_references,
    _check_action_relations,
    _check_exclusion_table,
    _check_reference_ids,
    _check_requirement_coverage,
    _check_transfer_root,
)


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

    根拠の記入に関する移行案内とWI参照の取得省略は、呼び出し元へ別々に渡す。
    """
    if action_index is None:
        return []
    errors: list[str] = []
    start, end = heading_subtree_range(headings, action_index)
    section = lines_within(body, start, end)
    table, table_errors = _check_action_table(extract_tables(section))
    errors.extend(table_errors)
    if table is not None and table.header in (PLAN_ACTION_TABLE_HEADER, PLAN_HUMAN_ACTION_TABLE_HEADER):
        decision_index = table.header.index("採否")
        root_index = table.header.index("根拠")
        for row in table.rows:
            if len(row) == len(table.header) and row[decision_index] == "移管":
                errors.extend(
                    _check_transfer_root(row[root_index], origin_skips=origin_skips, private_notes=private_notes, home=home)
                )
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


def _wi_has_scope(
    name: str,
    origin_skips: list[str],
    private_notes: pathlib.Path | str | None,
    home: pathlib.Path | str | None,
) -> bool:
    """登録時に対象を確定したWI本文を持つかを返す。

    登録済みの反映内容または旧形式の適用範囲を参照し、計画で再導出しない。
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
    for heading in ("反映内容と反映先", PLAN_WI_SCOPE_HEADING):
        index = find_heading_index(headings, 2, heading)
        if index is not None:
            start, end = heading_subtree_range(headings, index)
            if any(line.strip() for _lineno, line in lines_within(list(enumerate(content.splitlines(), 1)), start, end)):
                return True
    return False


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

    由来の区分とWI参照の構文を確かめる。内容の由来と承認範囲はレビューで検収する。
    `[対話由来]`注記は既存書式として受理する。`エージェント由来のWI`は採否にかかわらず根拠を必要とし、
    採用行の根拠が`-`の場合は`origin_notices`を渡した場合だけ移行の指摘を積む。
    `origin_skips`も渡した場合はWI本文を読み、非空の`## 反映内容と反映先`または`## 適用範囲`を持てば指摘を積まない。
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
                            "関連WIの本文が非空の`## 反映内容と反映先`または"
                            f"`## {PLAN_WI_SCOPE_HEADING}`を持つ場合は`-`のままWIを参照する"
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
    except OSError, UnicodeDecodeError:
        has_round = False
    if not has_round:
        return [f"`## {PLAN_H2_ACTION}`の計画レビュー表に同じroundが無い: {review_path} round {expected_round}"]
    return []


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
