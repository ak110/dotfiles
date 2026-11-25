from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

from agent_toolkit._common.frontmatter import normalize_newlines, parse_frontmatter
from agent_toolkit._plan.structure.constants import (
    _PLAN_METADATA_CANONICAL_BY_FIELD_ALIAS,
    _PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS,
    PLAN_DETAIL_H2_ORDER,
    PLAN_H2_ACTION,
    PLAN_H2_BUG,
    PLAN_H2_COMPLETION,
    PLAN_H2_IMPLEMENTATION,
    PLAN_H2_LEGACY_HISTORY,
    PLAN_H2_LEGACY_PROGRESS,
    PLAN_H2_MATERIALS,
    PLAN_H2_OVERVIEW,
    PLAN_H2_PERMANENCE,
    PLAN_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
    PLAN_IMPLEMENTATION_UNIT_ID_PATTERN,
    PLAN_IMPLEMENTATION_UNITS_H3,
    PLAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
    PLAN_LEGACY_CURRENT_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
    PLAN_LEGACY_CURRENT_IMPLEMENTATION_UNITS_TABLE_HEADER,
    PLAN_LEGACY_IMPLEMENTATION_UNITS_TABLE_HEADER,
    PLAN_METADATA_BUG_FIELD,
    PLAN_METADATA_CURRENT_FIELDS,
    PLAN_METADATA_DETAIL_FIELD,
    PLAN_METADATA_FALLBACK_H2,
    PLAN_METADATA_H3,
    PLAN_METADATA_MAIN_FIELDS,
    PLAN_METADATA_RELATED_WI_FIELD,
    PLAN_PLACEHOLDER_WORDS,
)
from agent_toolkit._plan.structure.markdown import (
    MarkdownTable,
    _comma_separated_values,
    child_headings,
    extract_headings,
    extract_tables,
    find_heading_index,
    heading_subtree_range,
    iter_markdown_body_lines,
    lines_within,
)
from agent_toolkit._plan.structure.materials import _STRICT_INTERNAL_PLAN_ID_PATTERN


def canonical_metadata_field(field: str) -> str:
    return _PLAN_METADATA_CANONICAL_BY_FIELD_ALIAS.get(field, field)


def canonical_wi_origin(origin: str) -> str:
    return _PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS.get(origin, origin)


@dataclass(frozen=True)
class PlanImplementationUnit:
    unit_id: str
    purpose: str
    dependencies: tuple[str, ...]
    integration_order: int
    verification: str


@dataclass(frozen=True)
class PlanMetadata:
    parent: str
    """親H2の見出し。frontmatterとH1直下では空文字列、8節形式では`概要`。 pyfltr: allow-comment"""

    entries: tuple[tuple[str, str], ...]
    """記載順の(項目名, 生の値)。項目の順序と記法が基準を満たすかの判定に使う。"""

    values: dict[str, str]
    """認識した項目の値。バッククォートは除去済みで、欠落項目は含めない。"""

    related_wi: tuple[tuple[str, str], ...]
    """`関連WI`の(WIファイル名, 1行要約)。記載順を保持する。"""

    base_commit_candidates: tuple[str, ...]
    """`ベースコミット`と旧別名から抽出した16進値の全候補。"""


_METADATA_ENTRY_PATTERN = re.compile(r"^- (?P<field>[^:]+):(?: (?P<value>.*?))?\s*$")
_METADATA_RELATED_WI_PATTERN = re.compile(r"^  - (?P<filename>[^:]+):(?: (?P<summary>.*?))?\s*$")
_METADATA_BASE_COMMIT_LINE = re.compile(r"^\s*-\s*(?:ベースコミット|基準コミット):\s*`(?P<oid>[0-9a-fA-F]+)`.*$")
"""ベースコミットを記載した箇条書きからOIDを読み取る互換パターン。

正規形の記法に従うかは`entries`側で判定するため、値抽出は既存計画の記法差を受け入れる。
行頭の字下げ、コロン前後の空白、閉じバッククォート以降の注記を許容し、
旧別名も対象とする。
"""


def _strip_backticks(value: str) -> str:
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        return value[1:-1]
    return value


PLAN_METADATA_TOP_PARENT: str = ""
"""計画メタ情報をH1直下（最初のH2より前）に置く現行の配置を表す、親H2の値。"""


def metadata_parent_label(parent: str) -> str:
    return "H1直下" if parent == PLAN_METADATA_TOP_PARENT else f"`## {parent}`直下"


def is_awi_plan(content: str) -> bool:
    parsed = parse_frontmatter(normalize_newlines(content.lstrip("\ufeff")))
    return parsed is not None and any(key in parsed[0] for key in ("launch_route", "related_wis", "work_kind"))


def _awi_plan_metadata(content: str) -> tuple[PlanMetadata | None, list[str]]:
    parsed = parse_frontmatter(normalize_newlines(content.lstrip("\ufeff")))
    assert parsed is not None
    header = parsed[0]
    route, repo, wis, kind = (header.get(key) for key in ("launch_route", "target_repo", "related_wis", "work_kind"))
    errors = []
    if not isinstance(route, str) or not route.strip():
        errors.append("launch_routeは空でない文字列にする")
    if not isinstance(repo, str) or not pathlib.Path(repo).is_absolute():
        errors.append("target_repoは対象リポジトリの絶対パスにする")
    if kind not in ("通常変更", "バグ対応"):
        errors.append("work_kindは通常変更またはバグ対応にする")
    if (
        not isinstance(wis, list)
        or not all(isinstance(name, str) and re.fullmatch(r"[0-9]{8}-[0-9]{6}-[0-9]{3}\.md", name) for name in wis)
        or len(set(wis)) != len(wis)
    ):
        errors.append("related_wisは重複のないWIファイル名のリストにする")
    if errors:
        return None, errors
    assert isinstance(route, str) and isinstance(repo, str) and isinstance(kind, str) and isinstance(wis, list)
    values = {"起動経路": route, "対象リポジトリ": repo, "作業種別": kind}
    return PlanMetadata("", tuple(values.items()), values, tuple((name, "") for name in wis), ()), []


def parse_plan_metadata(content: str) -> tuple[PlanMetadata | None, list[str]]:
    """計画メタ情報を正規配置優先で解析し、(解析結果, 曖昧性エラー)を返す。 pyfltr: allow-comment

    AWI書式の計画はfrontmatterを読む。旧形式の計画は`## 概要`直下かH1直下の`### 計画メタ情報`を読む。
    どちらも無い既存計画に限り`PLAN_METADATA_FALLBACK_H2`の旧配置を読み取り互換として使う。
    同一親に複数の`### 計画メタ情報`がある場合と、旧配置の候補が複数の親に散在する場合は
    曖昧として解析結果を返さない。
    """
    if is_awi_plan(content):
        return _awi_plan_metadata(content)
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    sections: dict[str, list[int]] = {}
    for index, heading in enumerate(headings):
        if heading.level != 3 or heading.text != PLAN_METADATA_H3:
            continue
        parent = next(
            (candidate.text for candidate in reversed(headings[:index]) if candidate.level == 2),
            "",
        )
        sections.setdefault(parent, []).append(index)

    if PLAN_H2_OVERVIEW in sections:
        parent = PLAN_H2_OVERVIEW
    elif PLAN_METADATA_TOP_PARENT in sections:
        parent = PLAN_METADATA_TOP_PARENT
    else:
        fallback_parents = [name for name in PLAN_METADATA_FALLBACK_H2 if name in sections]
        if not fallback_parents:
            return None, []
        if len(fallback_parents) > 1:
            return None, [f"計画メタ情報の配置が複数のH2に分かれています: {fallback_parents}"]
        parent = fallback_parents[0]

    indices = sections[parent]
    if len(indices) > 1:
        return None, [f"{metadata_parent_label(parent)}の`### {PLAN_METADATA_H3}`が1件ではありません: 実際={len(indices)}件"]

    start, end = heading_subtree_range(headings, indices[0])
    section = lines_within(body, start, end)
    entries: list[tuple[str, str]] = []
    for _lineno, line in section:
        match = _METADATA_ENTRY_PATTERN.fullmatch(line)
        if match is not None:
            entries.append((match.group("field").strip(), match.group("value") or ""))

    related_wi: list[tuple[str, str]] = []
    in_related_wi = False
    for _lineno, line in section:
        entry_match = _METADATA_ENTRY_PATTERN.fullmatch(line)
        if entry_match is not None:
            in_related_wi = canonical_metadata_field(entry_match.group("field").strip()) == PLAN_METADATA_RELATED_WI_FIELD
            continue
        if not in_related_wi:
            continue
        child_match = _METADATA_RELATED_WI_PATTERN.fullmatch(line)
        if child_match is not None:
            related_wi.append((child_match.group("filename").strip(), (child_match.group("summary") or "").strip()))
        elif line.strip():
            in_related_wi = False

    values: dict[str, str] = {}
    conflicts: list[str] = []
    for field, raw_value in entries:
        canonical_field = canonical_metadata_field(field)
        if canonical_field not in (
            *PLAN_METADATA_MAIN_FIELDS,
            *PLAN_METADATA_CURRENT_FIELDS,
            PLAN_METADATA_DETAIL_FIELD,
            PLAN_METADATA_BUG_FIELD,
        ):
            continue
        normalized = _strip_backticks(raw_value)
        if canonical_field in values and values[canonical_field] != normalized:
            conflicts.append(f"計画メタ情報の`{canonical_field}`に競合する値があります")
        values.setdefault(canonical_field, normalized)
    base_candidates = [
        match.group("oid") for _lineno, line in section if (match := _METADATA_BASE_COMMIT_LINE.fullmatch(line)) is not None
    ]
    if conflicts:
        return None, conflicts
    return PlanMetadata(parent, tuple(entries), values, tuple(related_wi), tuple(base_candidates)), []


# `(^|/)`接頭辞で先頭一致・任意の親ディレクトリ配下一致の両方を許容する
# （`pretooluse.py`側が絶対パス・tmp_path配下等の任意接頭辞パスを渡す既存挙動を保つ）。
AGENT_DOC_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)agent-toolkit/skills/[^/]+/SKILL\.md$"),
    re.compile(r"(^|/)agent-toolkit/skills/[^/]+/references/.+\.md$"),
    re.compile(r"(^|/)agent-toolkit/skills/[^/]+/(roles|playbooks)/.+\.md$"),
    re.compile(r"(^|/)agent-basics/skills/[^/]+/SKILL\.md$"),
    re.compile(r"(^|/)agent-basics/skills/[^/]+/references/.+\.md$"),
    re.compile(r"(^|/)agent-toolkit/agents/.+\.md$"),
    # chezmoi配布元のテンプレート（`<name>.md.tmpl`）も配布先ではエージェント向け文書として読み込まれるため、
    # `.tmpl`終了を受理する。原本側だけが対象から外れると、テンプレートによる規範の改訂を判定できなくなる。
    re.compile(r"(^|/)\.chezmoi-source/dot_claude/rules/.+\.md(\.tmpl)?$"),
    re.compile(r"(^|/)\.chezmoi-source/dot_claude/skills/.+\.md(\.tmpl)?$"),
    # ユーザーのプロジェクトが直接持つエージェント向け文書。配布元固有パスだけを対象にすると、
    # プラグインとして配布された先のプロジェクトで改訂を判定できなくなる。
    # `skills`配下の粒度は`agent-toolkit/skills/`側と揃え、`SKILL.md`と`references/`配下に限定する。
    re.compile(r"(^|/)\.claude/rules/.+\.md$"),
    re.compile(r"(^|/)\.claude/skills/[^/]+/SKILL\.md$"),
    re.compile(r"(^|/)\.claude/skills/[^/]+/references/.+\.md$"),
    re.compile(r"(^|/)\.claude/agents/.+\.md$"),
)


AGENT_DOC_TARGET_BASENAMES: frozenset[str] = frozenset({"AGENTS.md", "CLAUDE.md"})


def is_agent_doc_target_file(file_path: str | pathlib.Path) -> bool:
    """パス文字列がエージェント向け文書の判定対象かを判定する。

    実行時の呼び出し元は`agent-toolkit/skills/atk-mode/scripts/list_agent_doc_changes.py`
    （`atk run-script agent-doc-changes`）であり、
    レーン統合の`変更したエージェント向け文書`の対象集合を定める。
    対象集合は`agent-basics:writing-standards`の成果物種別表が定めるエージェント向け文書
    （`AGENTS.md`・`CLAUDE.md`・ルール・`SKILL.md`・サブエージェント定義・`references/`）と
    スキルの`roles/`・`playbooks/`にある役割文書・手順書とし、
    chezmoiの配布元にあるルールとスキルも含む。
    種類ごとに`agent-toolkit/`直下とプロジェクトの`.claude/`直下の両方の層を判定する。
    `AGENT_DOC_TARGET_PATTERNS`のいずれかへ一致するか、
    basenameが`AGENT_DOC_TARGET_BASENAMES`に含まれる場合に真を返す。
    """
    normalized = str(file_path).replace("\\", "/")
    if not normalized:
        return False
    if any(pat.search(normalized) for pat in AGENT_DOC_TARGET_PATTERNS):
        return True
    return pathlib.Path(normalized).name in AGENT_DOC_TARGET_BASENAMES


# `P-256`は外部仕様でも使われるため、自由文では旧素材IDであるとは判定しない。
_INTERNAL_PLAN_ID_PATTERN = re.compile(
    r"(?<![0-9A-Za-z_-])(?:R-P-[0-9A-Za-z][0-9A-Za-z_-]*-[0-9]{3}|P-(?!256(?![0-9A-Za-z_-]))[0-9A-Za-z][0-9A-Za-z_-]*|U-[0-9]{3}|H-[0-9]{3}|C-[0-9]{3}|R[0-9]+-[a-z][a-z0-9]*(?:-[a-z0-9]+)*)(?![0-9A-Za-z_-])"
)


def _is_placeholder_only(lines: list[tuple[int, str]]) -> bool:
    contents = [line.strip().lstrip("-*+ ").strip("。 ") for _lineno, line in lines if line.strip()]
    contents = [content for content in contents if content and not content.startswith("#")]
    if not contents:
        return True
    return all(content in PLAN_PLACEHOLDER_WORDS for content in contents)


def _find_table_with_rows(tables: list[MarkdownTable], rows: tuple[str, ...]) -> MarkdownTable | None:
    return next((table for table in tables if table.row_labels() == rows), None)


def _legacy_expected_h2(work_type: str | None) -> list[str]:
    expected = [PLAN_H2_OVERVIEW, PLAN_H2_ACTION, PLAN_H2_MATERIALS, PLAN_H2_LEGACY_HISTORY]
    if work_type == "バグ対応":
        expected.append(PLAN_H2_BUG)
    expected.extend((PLAN_H2_PERMANENCE, PLAN_H2_IMPLEMENTATION, PLAN_H2_COMPLETION, PLAN_H2_LEGACY_PROGRESS))
    return expected


def _detail_expected_h2(work_type: str | None) -> list[str]:
    expected = [PLAN_H2_BUG] if work_type == "バグ対応" else []
    expected.extend(PLAN_DETAIL_H2_ORDER)
    return expected


def parse_plan_implementation_units(
    content: str,
) -> tuple[tuple[PlanImplementationUnit, ...] | None, list[str]]:
    """計画ファイル（詳細）の`### 実装単位`の固定表を解析して構造違反を返す。

    単位表は新書式の計画ファイル（詳細）だけの必須契約であり、違反は計画を実装順へ分解できないためerrorとする。
    旧6列表は読み取り互換として受理し、`対象の実施内容`列の値が基準を満たすかは判定しない。
    """
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    implementation_index = find_heading_index(headings, 2, PLAN_H2_IMPLEMENTATION)
    if implementation_index is None:
        return None, []
    matching_headings = [
        (position, heading)
        for position, heading in child_headings(headings, implementation_index, 3)
        if heading.text == PLAN_IMPLEMENTATION_UNITS_H3
    ]
    if len(matching_headings) != 1:
        return None, [
            f"`## {PLAN_H2_IMPLEMENTATION}`直下に`### {PLAN_IMPLEMENTATION_UNITS_H3}`を1件置く: 実際={len(matching_headings)}件"
        ]

    position, _heading = matching_headings[0]
    start, end = heading_subtree_range(headings, position)
    tables = extract_tables(lines_within(body, start, end))
    matching = [
        candidate
        for candidate in tables
        if candidate.header
        in (
            PLAN_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
            PLAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
            PLAN_LEGACY_CURRENT_IMPLEMENTATION_UNITS_TABLE_HEADER,
            PLAN_LEGACY_CURRENT_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
            PLAN_LEGACY_IMPLEMENTATION_UNITS_TABLE_HEADER,
        )
    ]
    if not matching:
        return None, [f"`### {PLAN_IMPLEMENTATION_UNITS_H3}`は{list(PLAN_IMPLEMENTATION_UNITS_TABLE_HEADER)}の列を持つ表にする"]
    table = matching[0]
    errors = [f"`### {PLAN_IMPLEMENTATION_UNITS_H3}`の固定表は1件必要: 実際={len(matching)}件"] if len(matching) != 1 else []
    if not table.rows:
        errors.append(f"`### {PLAN_IMPLEMENTATION_UNITS_H3}`の表に1行以上の内容が必要")
    for index, row in enumerate(table.rows):
        if len(row) != len(table.header) or any(not cell for cell in row):
            errors.append(
                f"`### {PLAN_IMPLEMENTATION_UNITS_H3}`の表に空cellまたは列数不一致の行がある: {table.row_location(index)}。"
                "空cellを埋め、列数を表頭へそろえる"
            )

    is_human = table.header in (
        PLAN_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
        PLAN_LEGACY_CURRENT_HUMAN_IMPLEMENTATION_UNITS_TABLE_HEADER,
    )
    units: list[PlanImplementationUnit] = []
    for row in table.rows:
        if len(row) != len(table.header) or any(not cell for cell in row):
            continue
        if table.header == PLAN_LEGACY_IMPLEMENTATION_UNITS_TABLE_HEADER:
            unit_id, purpose, _action_value, dependency_value, order_value, verification = row
        else:
            unit_id, purpose, dependency_value, order_value, verification = row
        if not is_human and PLAN_IMPLEMENTATION_UNIT_ID_PATTERN.fullmatch(unit_id) is None:
            errors.append(f"実装単位IDは`U-[0-9]{{3}}`形式にする: {unit_id}")
        if is_human and (
            _STRICT_INTERNAL_PLAN_ID_PATTERN.fullmatch(unit_id)
            or _INTERNAL_PLAN_ID_PATTERN.search(unit_id)
            or unit_id in {"なし", "-"}
        ):
            errors.append(f"実装単位は合成IDではない説明的な名前にする: {unit_id}")
        if is_human and "," in unit_id:
            errors.append(f"実装単位名へASCIIカンマを含めない: {unit_id}")

        if dependency_value == "なし":
            dependencies: tuple[str, ...] = ()
        else:
            dependencies = _comma_separated_values(dependency_value)
            if any(not value for value in dependencies):
                errors.append(f"実装単位`{unit_id}`の`先行依存`に空の値を置かない")
            if not is_human and any(PLAN_IMPLEMENTATION_UNIT_ID_PATTERN.fullmatch(value) is None for value in dependencies):
                errors.append(f"実装単位`{unit_id}`の`先行依存`は実装単位IDまたは`なし`にする")
            if tuple(dict.fromkeys(dependencies)) != dependencies:
                errors.append(f"実装単位`{unit_id}`の`先行依存`は重複なしで列挙する")

        if not order_value.isdecimal() or int(order_value) < 1:
            errors.append(f"実装単位`{unit_id}`の`統合順`は1以上の整数にする")
            integration_order = 0
        else:
            integration_order = int(order_value)
        units.append(
            PlanImplementationUnit(
                unit_id,
                purpose,
                dependencies,
                integration_order,
                verification,
            )
        )

    unit_ids = tuple(unit.unit_id for unit in units)
    if is_human:
        if len(set(unit_ids)) != len(unit_ids):
            errors.append("実装単位は表内で一意の説明的な名前にする")
    else:
        expected_ids = tuple(f"U-{index:03d}" for index in range(1, len(units) + 1))
        if unit_ids != expected_ids:
            errors.append(f"実装単位IDは`U-001`から欠番なく昇順に置く: 実際={list(unit_ids)}")

    integration_orders = tuple(unit.integration_order for unit in units)
    if integration_orders != tuple(range(1, len(units) + 1)):
        errors.append(f"実装単位の`統合順`は1から欠番なく昇順に置く: 実際={list(integration_orders)}")

    units_by_id = {unit.unit_id: unit for unit in units}
    for unit in units:
        for dependency in unit.dependencies:
            dependency_unit = units_by_id.get(dependency)
            if dependency_unit is None:
                errors.append(
                    f"実装単位`{unit.unit_id}`の`先行依存`が実装単位表に無い: {dependency}。"
                    "実装単位表に在るIDへ直すか、`先行依存`から外す"
                )
            elif dependency_unit.integration_order >= unit.integration_order:
                errors.append(
                    f"実装単位`{unit.unit_id}`の`先行依存`が`統合順`より前にない: {dependency}。"
                    "先行する単位の`統合順`を小さくする"
                )
    return tuple(units), errors
