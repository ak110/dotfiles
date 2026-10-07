"""計画の`## 提示素材`（素材表・要求表・旧形式の素材ID・人間向けのファイル名列挙）の解析と、形式の違反の判定。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent_toolkit._plan.structure.constants import (
    PLAN_H2_MATERIALS,
    PLAN_MATERIAL_ID_PATTERN,
    PLAN_MATERIAL_TABLE_HEADER,
    PLAN_MATERIAL_TYPES,
    PLAN_NON_QUEUE_VALUE,
    PLAN_QUEUE_ID_PATTERN,
    PLAN_REQUIREMENT_ID_PATTERN,
    PLAN_REQUIREMENT_TABLE_HEADER,
)
from agent_toolkit._plan.structure.markdown import (
    PlanHeading,
    _column_count_error,
    extract_headings,
    extract_tables,
    find_heading_index,
    heading_subtree_range,
    iter_markdown_body_lines,
    lines_within,
)


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
    """新形式の素材表1行が所定の形式であるか確かめる。"""
    material_id, material_type, queue_id, source, citation = row
    errors: list[str] = []
    if not PLAN_MATERIAL_ID_PATTERN.fullmatch(material_id):
        errors.append(f"提示素材の素材IDが不正である: {material_id}。`P-`で始まる英数字（例: `P-001`）にする")
    elif material_id in identifiers:
        errors.append(f"提示素材の素材IDが重複している: {material_id}。重複した素材IDを別のIDにする")
    if material_type not in PLAN_MATERIAL_TYPES:
        errors.append(f"提示素材の種別が不正である: {material_type}。{list(PLAN_MATERIAL_TYPES)}のいずれかにする")
        return errors

    if material_type == "フィードバック":
        if PLAN_QUEUE_ID_PATTERN.fullmatch(queue_id) is None:
            errors.append(f"フィードバック素材のキューIDが不正である: {queue_id}。`YYYYMMDD-HHMMSS-NNN.md`形式にする")
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
            errors.append("AskUserQuestionまたはTBDのユーザー合意素材の引用範囲を回答全文にする")
    elif material_type in {"参考素材", "処理対象資料"} and citation == PLAN_NON_QUEUE_VALUE:
        errors.append(f"{material_type}素材の引用範囲は非該当にしない")
    elif material_type == "起動事実" and (source != "常駐自動起動" or citation != PLAN_NON_QUEUE_VALUE):
        errors.append("起動事実素材は投入元を常駐自動起動、引用範囲を非該当にする")

    if material_type != "起動事実" and not source:
        errors.append(f"{material_type}素材の投入元が空である。値を記載する")
    if not citation:
        errors.append(f"{material_type}素材の引用範囲が空である。値を記載する")
    return errors


def _check_new_materials(section: list[tuple[int, str]]) -> tuple[PlanMaterials, list[str]]:
    """新形式の素材表と要求表が所定の形式であるか確かめる。"""
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
    for index, row in enumerate(material_table.rows):
        if len(row) != len(PLAN_MATERIAL_TABLE_HEADER):
            errors.append(
                _column_count_error(
                    "提示素材の素材表", len(PLAN_MATERIAL_TABLE_HEADER), row, material_table.row_location(index)
                )
            )
            continue
        if any(not cell for cell in row):
            errors.append(f"提示素材の素材表に空cellがある: {material_table.row_location(index)}。空cellを埋める")
            continue
        errors.extend(_validate_material_row(row, identifiers))
        if row[1] == "フィードバック" and PLAN_QUEUE_ID_PATTERN.fullmatch(row[2]):
            if row[2] in feedback_queue_ids:
                errors.append(f"フィードバック素材のキューIDが重複している: {row[2]}。重複を除く")
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
        for index, row in enumerate(requirements.rows):
            if len(row) != len(PLAN_REQUIREMENT_TABLE_HEADER):
                errors.append(
                    _column_count_error(
                        "提示素材の要求表", len(PLAN_REQUIREMENT_TABLE_HEADER), row, requirements.row_location(index)
                    )
                )
                continue
            if any(not cell for cell in row):
                errors.append(f"提示素材の要求表に空cellがある: {requirements.row_location(index)}。空cellを埋める")
                continue
            requirement_id, references, _description, decision, adopted, excluded, _reason = row
            match = PLAN_REQUIREMENT_ID_PATTERN.fullmatch(requirement_id)
            if match is None:
                errors.append(f"要求IDが不正である: {requirement_id}。`R-<素材ID>-<3桁の連番>`（例: `R-P-001-001`）にする")
                continue
            if requirement_id in requirement_ids:
                errors.append(f"要求IDが重複している: {requirement_id}。重複を除く")
            requirement_ids.add(requirement_id)
            namespace = match.group("material")
            if namespace not in identifiers:
                errors.append(f"要求IDの素材名前空間が素材表に無い: {namespace}。素材表に在る素材IDへ直すか、素材を追加する")
            refs = _split_material_references(references)
            if not refs:
                errors.append(f"要求{requirement_id}の素材参照が空である。素材参照を記載する")
            elif references != ", ".join(refs):
                errors.append(f"要求{requirement_id}の素材参照は`P-001, P-002`形式で記載する: {references}")
            if refs != sorted(refs):
                errors.append(f"要求{requirement_id}の素材参照はID昇順で並べる: {references}")
            if len(refs) != len(set(refs)):
                errors.append(f"要求{requirement_id}の素材参照が重複している: {references}。重複を除く")
            for reference in refs:
                if reference not in identifiers:
                    errors.append(
                        f"要求{requirement_id}の素材参照が素材表に無い: {reference}。素材表に在る素材IDへ直すか、素材を追加する"
                    )
            if namespace not in refs:
                errors.append(f"要求{requirement_id}の素材名前空間を素材参照に含める: {namespace}")
            if decision not in {"採用", "不採用"}:
                errors.append(f"要求{requirement_id}の採否は採用または不採用にする: {decision}")
            elif decision == "採用":
                adopted_requirement_ids.add(requirement_id)
                if adopted.startswith("終端工程のみ"):
                    terminal_only_requirement_ids.add(requirement_id)
            if decision == "採用" and (adopted == PLAN_NON_QUEUE_VALUE or excluded != PLAN_NON_QUEUE_VALUE):
                errors.append(
                    f"要求{requirement_id}の採用範囲または除外範囲が不正である（採用範囲={adopted}、除外範囲={excluded}）。"
                    f"採用行は`採用範囲`を記載し、`除外範囲`を`{PLAN_NON_QUEUE_VALUE}`にする"
                )
            if decision == "不採用" and (adopted != PLAN_NON_QUEUE_VALUE or excluded == PLAN_NON_QUEUE_VALUE):
                errors.append(
                    f"要求{requirement_id}の採用範囲または除外範囲が不正である（採用範囲={adopted}、除外範囲={excluded}）。"
                    f"不採用行は`採用範囲`を`{PLAN_NON_QUEUE_VALUE}`にし、`除外範囲`を記載する"
                )

        sequences_by_namespace: dict[str, list[int]] = {}
        for requirement_id in requirement_ids:
            match = PLAN_REQUIREMENT_ID_PATTERN.fullmatch(requirement_id)
            assert match is not None
            sequences_by_namespace.setdefault(match.group("material"), []).append(int(match.group("sequence")))
        for namespace, sequences in sequences_by_namespace.items():
            if sorted(sequences) != list(range(1, len(sequences) + 1)):
                errors.append(f"素材{namespace}の要求ID末尾連番が001から欠番なく続かない。連番を001から振り直す")

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
            errors.append(f"素材{row[0]}が要求表から参照されていない。要求表の素材参照へ加えるか、素材表から外す")
    return PlanMaterials(
        frozenset(identifiers),
        frozenset(requirement_ids),
        False,
        frozenset(adopted_requirement_ids),
        frozenset(terminal_only_requirement_ids),
        feedback_queue_ids=frozenset(feedback_queue_ids),
    ), errors


def _check_human_materials(section: list[tuple[int, str]]) -> tuple[PlanMaterials | None, list[str]]:
    """新規書式の`## 提示素材`にファイル名だけが列挙されているか確かめる。"""
    nonempty = [(lineno, line.strip()) for lineno, line in section if line.strip()]
    if not nonempty:
        return PlanMaterials(frozenset(), frozenset(), False, is_human_readable=True), [
            "新規書式の`## 提示素材`はAWIまたはUWIのファイル名を1件以上、または`なし`と記載する"
        ]

    if len(nonempty) == 1 and nonempty[0][1] == "なし":
        return PlanMaterials(frozenset(), frozenset(), False, is_human_readable=True), []

    errors: list[str] = []
    paths: list[str] = []
    for _lineno, line in nonempty:
        match = _HUMAN_MATERIAL_LINE_PATTERN.fullmatch(line)
        if match is None:
            errors.append(f"提示素材はファイル名の箇条書きまたは`なし`だけにする: {line}")
            continue
        path = match.group("path").strip()
        if _STRICT_INTERNAL_PLAN_ID_PATTERN.search(path):
            errors.append(f"提示素材へ合成IDを記載しない: {path}")
        if path in paths:
            errors.append(f"提示素材のファイル名を重複させない: {path}")
        paths.append(path)
    if not paths:
        errors.append("提示素材にAWIまたはUWIのファイル名が1件以上必要")
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
    """`## 提示素材`の素材IDと逐語fenceが所定の形式であるか判定し、(素材ID集合, エラー)を返す。"""
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
            errors.append(
                f"提示素材`{match.group('id')}`の直後に`text`フェンスの逐語転記が無い。素材ID行の直後へ`text`フェンスで原文を転記する"
            )
        position += 1
    if not identifiers:
        errors.append("提示素材に素材IDと`text`フェンスの逐語転記が1件以上必要")
    return identifiers, errors


@dataclass(frozen=True)
class PlanMaterials:
    """計画の提示素材と要求の解析結果を表す。"""

    material_ids: frozenset[str]
    requirement_ids: frozenset[str]
    is_legacy: bool
    adopted_requirement_ids: frozenset[str] = frozenset()
    terminal_only_requirement_ids: frozenset[str] = frozenset()
    is_human_readable: bool = False
    material_paths: frozenset[str] = frozenset()
    feedback_queue_ids: frozenset[str] = frozenset()


_LEGACY_MATERIAL_ID_PATTERN = re.compile(r"^(?P<id>[A-Za-z0-9][0-9A-Za-z_-]*):$")


_MATERIAL_ID_CANDIDATE_PATTERN = re.compile(r"^P-[0-9A-Za-z][0-9A-Za-z_-]*(?:(?:（[^）\n]+）|\([^)\n]+\)):|:\s+\S.*)$")


_MATERIAL_FENCE_PATTERN = re.compile(r"^\s*(?:`{3,}|~{3,})text\s*$")


_REFERENCE_SEPARATOR_PATTERN = re.compile(r"[、,・/\s]+")


_HUMAN_MATERIAL_LINE_PATTERN = re.compile(r"^\s*-\s+(?P<path>[^/\\\s]+\.md)\s*$")


_STRICT_INTERNAL_PLAN_ID_PATTERN = re.compile(
    r"(?<![0-9A-Za-z_-])(?:R-P-[0-9A-Za-z][0-9A-Za-z_-]*-[0-9]{3}|P-[0-9A-Za-z][0-9A-Za-z_-]*|U-[0-9]{3}|H-[0-9]{3}|C-[0-9]{3}|R[0-9]+-[a-z][a-z0-9]*(?:-[a-z0-9]+)*)(?![0-9A-Za-z_-])"
)


def parse_plan_materials(content: str) -> tuple[PlanMaterials | None, list[str]]:
    """提示素材を新形式または旧形式として解析する。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    index = find_heading_index(headings, 2, PLAN_H2_MATERIALS)
    if index is None:
        return None, ["固定H2の提示素材の構造を判定できない"]
    section = _materials_section(content, headings, index)
    tables = extract_tables(section)
    if (
        not any(table.header in {PLAN_MATERIAL_TABLE_HEADER, PLAN_REQUIREMENT_TABLE_HEADER} for table in tables)
        and not any(_LEGACY_MATERIAL_ID_PATTERN.fullmatch(line.strip()) for _lineno, line in section)
        and not any(_MATERIAL_ID_CANDIDATE_PATTERN.fullmatch(line.strip()) for _lineno, line in section)
    ):
        human_materials, human_errors = _check_human_materials(section)
        if human_materials is not None:
            return human_materials, human_errors
    if any(table.header in {PLAN_MATERIAL_TABLE_HEADER, PLAN_REQUIREMENT_TABLE_HEADER} for table in tables):
        return _check_new_materials(section)
    identifiers, errors = _check_materials(content, body, headings, index)
    return PlanMaterials(frozenset(identifiers), frozenset(), True), errors
