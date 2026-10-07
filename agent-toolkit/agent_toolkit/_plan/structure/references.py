"""計画ファイルの要求・素材・実施内容・変更履歴の間の参照の整合（要求の網羅、除外表、参照ID、採否と関係）の判定。"""

from __future__ import annotations

import re

from agent_toolkit._plan.structure.constants import (
    PLAN_ACTION_DECISIONS,
    PLAN_ACTION_NON_ADOPTED_DECISIONS,
    PLAN_ACTION_RELATIONS,
    PLAN_ACTION_TABLE_HEADER,
    PLAN_EXCLUSION_TABLE_HEADER,
    PLAN_HISTORY_ORIGINS,
    PLAN_HISTORY_REVIEW_ID_PATTERN,
    PLAN_HISTORY_TABLE_HEADER,
    PLAN_HISTORY_TRACK_VALUES,
    PLAN_LEGACY_ACTION_TABLE_HEADER,
    PLAN_LEGACY_EXCLUSION_TABLE_HEADER,
    PLAN_LEGACY_HISTORY_REVIEW_ID_PATTERN,
    PLAN_LEGACY_HISTORY_TRACK_VALUES,
    PLAN_MATERIAL_ID_PATTERN,
    PLAN_NON_QUEUE_VALUE,
)
from agent_toolkit._plan.structure.markdown import MarkdownTable, _check_fixed_table, extract_tables
from agent_toolkit._plan.structure.materials import PlanMaterials, _requirement_references, _split_material_references

_MISSING_MATERIAL_FIX = "提示素材の素材表・要求表に在るIDへ直すか、素材を追加する"


def _missing_requirement_reference_error(reference: str) -> str:
    """要求表に無い要求IDを`根拠`が参照する違反文を返す。"""
    return (
        f"`## 実施内容`の`根拠`が提示素材の要求表に無い: {reference}。要求表に在る採用要求のIDへ直すか、要求表へ要求を追加する"
    )


def _non_adopted_requirement_reference_error(reference: str) -> str:
    """不採用要求を`根拠`が参照する違反文を返す。"""
    return (
        f"`## 実施内容`の`根拠`へ不採用要求を参照できない: {reference}。"
        "採用要求のIDへ直すか、不採用要求を根拠にする場合は要求表の採否を見直す"
    )


def _check_action_references(
    table: MarkdownTable,
    requirement_ids: set[str],
    adopted_requirement_ids: set[str],
) -> list[str]:
    """実施内容表の採否に応じて、根拠と要求参照が記載されているか確かめる。"""
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
                    errors.append(_missing_requirement_reference_error(reference))
                elif reference not in adopted_requirement_ids:
                    errors.append(_non_adopted_requirement_reference_error(reference))
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
                    errors.append(_missing_requirement_reference_error(reference))
                elif reference not in adopted_requirement_ids:
                    errors.append(_non_adopted_requirement_reference_error(reference))
            continue
        if decision in PLAN_ACTION_NON_ADOPTED_DECISIONS:
            if not root:
                errors.append("`## 実施内容`の非採用系の`根拠`は理由を記載する")
                continue
            for reference in _requirement_references(root):
                if reference not in requirement_ids:
                    errors.append(_missing_requirement_reference_error(reference))
    return errors


def _check_requirement_coverage(
    action_table: MarkdownTable,
    exclusion_table: MarkdownTable | None,
    materials: PlanMaterials,
) -> list[str]:
    """採用要求IDが`## 実施内容`の`根拠`または`### 合意済みの除外・保持`の`素材・要求参照`で被覆されるかを確かめる。

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
        f"`## 実施内容`の`根拠`または`### 合意済みの除外・保持`の`素材・要求参照`が採用要求を被覆しない: {requirement_id}"
        for requirement_id in sorted(uncovered)
    ]


def _check_exclusion_table(
    lines: list[tuple[int, str]],
    label: str,
) -> tuple[MarkdownTable | None, list[str], bool]:
    """新形式を優先して新旧の合意表が基準を満たすか判定し、形式を返す。"""
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
    """新旧合意表の素材・要求参照が基準を満たすか確かめる。"""
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
                errors.append(f"{label}の{column_name}が提示素材に無い: {token}。{_MISSING_MATERIAL_FIX}")
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
    """変更履歴の起点、レビューIDおよびユーザー発言行の素材IDが所定の記法に従っているか確かめる。"""
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
                errors.append(
                    f"`## 変更履歴`のユーザー発言行が参照する素材IDが提示素材に無い: {reference}。{_MISSING_MATERIAL_FIX}"
                )
    return errors


def _check_action_decisions(table: MarkdownTable) -> list[str]:
    """新形式の実施内容表の`採否`が宣言済みの値であるか確かめる。"""
    if table.header != PLAN_ACTION_TABLE_HEADER:
        return []
    column = table.header.index("採否")
    return [
        f"`## 実施内容`の`採否`は{list(PLAN_ACTION_DECISIONS)}のいずれかにする: {row[column]}"
        for row in table.rows
        if len(row) > column and row[column] and row[column] not in PLAN_ACTION_DECISIONS
    ]


def _check_action_relations(table: MarkdownTable) -> list[str]:
    """実施内容表の`ユーザー指示との関係`の値が採否に応じた許容値であるか確かめる。"""
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
