"""計画の見出し配置とメタ情報の検査。"""

from __future__ import annotations

import re

from agent_toolkit._plan.structure.constants import (
    PLAN_H2_BUG,
    PLAN_H2_OVERVIEW,
    PLAN_METADATA_FIELDS,
    PLAN_METADATA_H3,
    PLAN_METADATA_QUOTED_FIELDS,
    PLAN_METADATA_RELATED_WI_FIELD,
    PLAN_QUEUE_ID_PATTERN,
    PLAN_WORK_TYPES,
    canonical_h2_name,
)
from agent_toolkit._plan.structure.markdown import (
    PlanHeading,
    child_headings,
    find_heading_index,
    heading_subtree_range,
    lines_within,
)
from agent_toolkit._plan.structure.parsing import PlanMetadata, _strip_backticks, canonical_metadata_field, parse_plan_metadata


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


def _check_nonempty_section(body: list[tuple[int, str]], headings: list[PlanHeading], heading_name: str) -> list[str]:
    """指定したH2の本文が空でないか確かめる。"""
    index = find_heading_index(headings, 2, heading_name)
    if index is None:
        return []
    start, end = heading_subtree_range(headings, index)
    if any(line.strip() and re.fullmatch(r"\s*<!--.*-->\s*", line) is None for _lineno, line in lines_within(body, start, end)):
        return []
    return [f"`## {heading_name}`を空にしない"]
