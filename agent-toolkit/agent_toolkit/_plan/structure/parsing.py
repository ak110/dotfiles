"""計画メタ情報、`### 実装単位`の固定表、実装担当が読む範囲とエージェント向け文書の判定など、計画ファイルの値の解析。"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

from agent_toolkit._plan.structure.constants import (
    _PLAN_METADATA_CANONICAL_BY_FIELD_ALIAS,
    _PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS,
    PLAN_DETAIL_H2_ORDER,
    PLAN_H2_ACTION,
    PLAN_H2_BUG,
    PLAN_H2_COMPLETION,
    PLAN_H2_HISTORY,
    PLAN_H2_IMPLEMENTATION,
    PLAN_H2_LEGACY_HISTORY,
    PLAN_H2_LEGACY_PROGRESS,
    PLAN_H2_MATERIALS,
    PLAN_H2_OVERVIEW,
    PLAN_H2_PERMANENCE,
    PLAN_H2_PROGRESS,
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
    """計画メタ情報の項目名の旧名称を正規名へ写す。未知の項目名はそのまま返す。"""
    return _PLAN_METADATA_CANONICAL_BY_FIELD_ALIAS.get(field, field)


def canonical_wi_origin(origin: str) -> str:
    """`由来`欄のWI区分の旧名称を正規名へ写す。未知の区分はそのまま返す。"""
    return _PLAN_WI_ORIGIN_CANONICAL_BY_ALIAS.get(origin, origin)


@dataclass(frozen=True)
class PlanImplementationUnit:
    """計画ファイル（詳細）の実装単位表1行を表す。"""

    unit_id: str
    purpose: str
    dependencies: tuple[str, ...]
    integration_order: int
    verification: str


@dataclass(frozen=True)
class PlanMetadata:
    """計画メタ情報の解析結果を表す。"""

    parent: str
    """メタ情報を収めていた親H2の見出し文。正規形では`概要`となる。"""

    entries: tuple[tuple[str, str], ...]
    """記載順の(項目名, 生の値)。項目の順序と記法が基準を満たすかの判定に使う。"""

    values: dict[str, str]
    """認識した項目の値。バッククォートは除去済みで、欠落項目は含めない。"""

    related_wi: tuple[tuple[str, str], ...]
    """`関連WI`の(WIファイル名, 1行要約)。記載順を保持する。"""

    base_commit_candidates: tuple[str, ...]
    """`ベースコミット`と旧別名`基準コミット`から抽出した16進値の全候補。"""

    @property
    def is_canonical(self) -> bool:
        """正規配置（`## 概要`直下）から読み取ったかを返す。"""
        return self.parent == PLAN_H2_OVERVIEW


_METADATA_ENTRY_PATTERN = re.compile(r"^- (?P<field>[^:]+):(?: (?P<value>.*?))?\s*$")
_METADATA_RELATED_WI_PATTERN = re.compile(r"^  - (?P<filename>[^:]+):(?: (?P<summary>.*?))?\s*$")
_METADATA_BASE_COMMIT_LINE = re.compile(r"^\s*-\s*(?:ベースコミット|基準コミット):\s*`(?P<oid>[0-9a-fA-F]+)`.*$")
"""ベースコミットを記載した箇条書きからOIDを読み取る互換パターン。

正規形の記法に従うかは`entries`側で判定するため、値抽出は既存計画の記法差を受け入れる。
行頭の字下げ、コロン前後の空白、閉じバッククォート以降の注記を許容し、
旧別名`基準コミット`も対象とする。
"""


def _strip_backticks(value: str) -> str:
    """前後のバッククォートを1組だけ取り除く。"""
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        return value[1:-1]
    return value


def parse_plan_metadata(content: str) -> tuple[PlanMetadata | None, list[str]]:
    """計画メタ情報を正規配置優先で解析し、(解析結果, 曖昧性エラー)を返す。

    `## 概要`直下の`### 計画メタ情報`を正規配置とする。
    正規配置が無い既存計画に限り`PLAN_METADATA_FALLBACK_H2`の旧配置を読み取り互換として使う。
    同一親に複数の`### 計画メタ情報`がある場合と、旧配置の候補が複数の親に散在する場合は
    曖昧として解析結果を返さない。
    """
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
    else:
        fallback_parents = [name for name in PLAN_METADATA_FALLBACK_H2 if name in sections]
        if not fallback_parents:
            return None, []
        if len(fallback_parents) > 1:
            return None, [f"計画メタ情報の配置が複数のH2に分かれています: {fallback_parents}"]
        parent = fallback_parents[0]

    indices = sections[parent]
    if len(indices) > 1:
        return None, [f"`## {parent}`直下の`### {PLAN_METADATA_H3}`が1件ではありません: 実際={len(indices)}件"]

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


def extract_implementer_region(content: str) -> list[tuple[int, str]]:
    """`## 変更履歴`直後から`## 進捗ログ`直前までの本文行を返す。"""
    body = list(iter_markdown_body_lines(content))
    headings = extract_headings(content)
    history_index = find_heading_index(headings, 2, PLAN_H2_HISTORY)
    if history_index is None:
        return []
    _history_start, history_end = heading_subtree_range(headings, history_index)
    if history_end is None:
        return []
    progress_index = find_heading_index(headings, 2, PLAN_H2_PROGRESS)
    region_end = headings[progress_index].lineno if progress_index is not None else None
    return [(lineno, line) for lineno, line in body if lineno >= history_end and (region_end is None or lineno < region_end)]


def is_agent_facing_md(rel_path: str) -> bool:
    """パス文字列がコーディングエージェント向けMarkdownの対象種別かを判定する。

    対象は拡張子`.md`のファイルのうち、次のいずれかに該当するもの。
    ルートの`AGENTS.md`・`CLAUDE.md`。パスを構成する要素に`rules`を含むもの
    （`agent-toolkit/rules/`・`.claude/rules/`・`.chezmoi-source/dot_claude/rules/`等）。
    末尾から3番目のパスを構成する要素が`skills`かつファイル名が`SKILL.md`のもの
    （`agent-toolkit/skills/<name>/SKILL.md`・`.claude/skills/<name>/SKILL.md`・
    `.chezmoi-source/dot_claude/skills/<name>/SKILL.md`等）。
    パスを構成する要素に`references`と`skills`の両方を含むもの。パスを構成する要素に`agents`を含むもの。
    パスを構成する要素の完全一致で判定し、部分文字列一致は行わない。
    `posttooluse.py`の条件付き禁止形の警告通知が対象種別の判定に使う。
    """
    p = pathlib.PurePosixPath(rel_path.replace("\\", "/"))
    parts = p.parts
    name = p.name
    if not name.endswith(".md"):
        return False
    if len(parts) == 1 and name in ("AGENTS.md", "CLAUDE.md"):
        return True
    if "rules" in parts[:-1]:
        return True
    if len(parts) >= 3 and parts[-3] == "skills" and name == "SKILL.md":
        return True
    if "references" in parts[:-1] and "skills" in parts[:-1]:
        return True
    return "agents" in parts[:-1]


# `(^|/)`接頭辞で先頭一致・任意の親ディレクトリ配下一致の両方を許容する
# （`pretooluse.py`側が絶対パス・tmp_path配下等の任意接頭辞パスを渡す既存挙動を保つ）。
AGENT_DOC_TARGET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(^|/)agent-toolkit/share/.+\.md$"),
    re.compile(r"(^|/)agent-toolkit/rules/.+\.md$"),
    re.compile(r"(^|/)agent-toolkit/skills/[^/]+/SKILL\.md$"),
    re.compile(r"(^|/)agent-toolkit/skills/[^/]+/references/.+\.md$"),
    re.compile(r"(^|/)agent-toolkit/agents/.+\.md$"),
    # chezmoi配布元のテンプレート（`<name>.md.tmpl`）も配布先ではエージェント向け文書として読み込まれるため、
    # `.tmpl`終端を受理する。原本側だけが対象から外れると、テンプレートによる規範の改訂を判定できなくなる。
    re.compile(r"(^|/)\.chezmoi-source/dot_claude/rules/.+\.md(\.tmpl)?$"),
    re.compile(r"(^|/)\.chezmoi-source/dot_claude/skills/.+\.md(\.tmpl)?$"),
    # ユーザーのプロジェクトが直接持つエージェント向け文書。配布元固有パスだけを対象にすると、
    # プラグインとして配布された先のプロジェクトで改訂を判定できなくなる。
    # `skills`配下の粒度は`agent-toolkit/skills/`側と揃え、`SKILL.md`と`references/`配下に限定する。
    re.compile(r"(^|/)\.claude/rules/.+\.md$"),
    re.compile(r"(^|/)\.claude/skills/[^/]+/SKILL\.md$"),
    re.compile(r"(^|/)\.claude/skills/[^/]+/references/.+\.md$"),
    # サブエージェント定義も`agent-toolkit/agents/`と同じ種類としてプロジェクト側の層で判定する。
    re.compile(r"(^|/)\.claude/agents/.+\.md$"),
)
# basenameとの一致で判定するエージェント向け文書の判定対象ファイル名。
# ディレクトリ位置を問わず一致させる（ルート直下限定ではない）。
AGENT_DOC_TARGET_BASENAMES: frozenset[str] = frozenset({"AGENTS.md", "CLAUDE.md"})


def is_agent_doc_target_file(file_path: str | pathlib.Path) -> bool:
    """パス文字列がエージェント向け文書の判定対象かを判定する。

    実行時の呼び出し元は`agent-toolkit/skills/plan-mode/scripts/list_agent_doc_changes.py`
    （`atk run-script agent-doc-changes`）であり、
    レーン統合の`変更したエージェント向け文書`の対象集合を定める。
    対象集合は`agent-toolkit:writing-standards`の成果物種別表が定めるエージェント向け文書
    （`AGENTS.md`・`CLAUDE.md`・ルール・`SKILL.md`・サブエージェント定義・`references/`）と
    `agent-toolkit/share/`の`<役割名>.subagent.md`とし、chezmoiの配布元にあるルールとスキルも含む。
    種類ごとに`agent-toolkit/`直下とプロジェクトの`.claude/`直下の両方の層を判定する。
    `AGENT_DOC_TARGET_PATTERNS`のいずれかへ一致するか、
    basenameが`AGENT_DOC_TARGET_BASENAMES`に含まれる場合に真を返す。
    `is_agent_facing_md`とは判定対象範囲が異なる。
    """
    normalized = str(file_path).replace("\\", "/")
    if not normalized:
        return False
    if any(pat.search(normalized) for pat in AGENT_DOC_TARGET_PATTERNS):
        return True
    return pathlib.Path(normalized).name in AGENT_DOC_TARGET_BASENAMES


# --- 人間向け固定領域の構造を判定する ---

# `P-256`は外部仕様でも使われるため、自由文では旧素材IDであるとは判定しない。
_INTERNAL_PLAN_ID_PATTERN = re.compile(
    r"(?<![0-9A-Za-z_-])(?:R-P-[0-9A-Za-z][0-9A-Za-z_-]*-[0-9]{3}|P-(?!256(?![0-9A-Za-z_-]))[0-9A-Za-z][0-9A-Za-z_-]*|U-[0-9]{3}|H-[0-9]{3}|C-[0-9]{3}|R[0-9]+-[a-z][a-z0-9]*(?:-[a-z0-9]+)*)(?![0-9A-Za-z_-])"
)


def _is_placeholder_only(lines: list[tuple[int, str]]) -> bool:
    """本文が結論語だけで構成されているかを返す。"""
    contents = [line.strip().lstrip("-*+ ").strip("。 ") for _lineno, line in lines if line.strip()]
    contents = [content for content in contents if content and not content.startswith("#")]
    if not contents:
        return True
    return all(content in PLAN_PLACEHOLDER_WORDS for content in contents)


def _find_table_with_rows(tables: list[MarkdownTable], rows: tuple[str, ...]) -> MarkdownTable | None:
    """行名と順序が一致する表を返す。見つからない場合は`None`を返す。"""
    return next((table for table in tables if table.row_labels() == rows), None)


def _legacy_expected_h2(work_type: str | None) -> list[str]:
    """旧形式（単一ファイル9節）の固定H2順序を返す。"""
    expected = [PLAN_H2_OVERVIEW, PLAN_H2_ACTION, PLAN_H2_MATERIALS, PLAN_H2_LEGACY_HISTORY]
    if work_type == "バグ対応":
        expected.append(PLAN_H2_BUG)
    expected.extend((PLAN_H2_PERMANENCE, PLAN_H2_IMPLEMENTATION, PLAN_H2_COMPLETION, PLAN_H2_LEGACY_PROGRESS))
    return expected


def _detail_expected_h2(work_type: str | None) -> list[str]:
    """新書式の計画ファイル（詳細）の固定H2順序を返す。"""
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
