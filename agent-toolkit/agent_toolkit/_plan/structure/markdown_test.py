import pathlib

import pytest
from pyfltr.colloquial import check as _colloquial_check

from agent_toolkit._common import markdown_headings
from agent_toolkit._plan import structure as _plan_format
from agent_toolkit._plan.structure import constants as plan_constants
from agent_toolkit._plan.structure import markdown as plan_markdown
from agent_toolkit._plan.structure import materials as plan_materials
from agent_toolkit._plan.structure import sections as plan_sections
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.plan_structure_support import (
    _HUMAN_DETAIL_CONTENT,
    _HUMAN_MAIN_CONTENT,
    _LEGACY_CONTENT,
    _VALID_CONTENT,
    _VALID_DETAIL_CONTENT,
    _VALID_MAIN_CONTENT,
    _canonical_human_main_content,
    _canonical_main_content,
    _origin_check,
    _plan_with_uncovered_requirement,
    _wi_source,
    _write_wi,
)

pytestmark = pytest.mark.windows


def test_markdown_table_keeps_first_and_last_row_locations() -> None:
    source = "| 項目 | 内容 |\n| --- | --- |\n| 先頭 | 一。 |\n| 末尾 | 二。 |"
    lines: list[tuple[int, str]] = list(enumerate(source.splitlines(), start=7))

    table = plan_markdown.extract_tables(lines)[0]

    assert table.row_location(0) == "9行目: | 先頭 | 一。 |"
    assert table.row_location(1) == "10行目: | 末尾 | 二。 |"


@pytest.mark.parametrize(
    "content",
    [
        "# 主題\n本文中\ufeffの文字\n",
        "## 節\n本文\n",
        "---\nkey: value\n---\n# 主題\n",
        "```text\n# 偽見出し\n```\n# 主題\n",
    ],
)
def test_bom_structure_matches_plain_input(content: str) -> None:
    """見出し・管理情報・先頭フェンスで同じ行位置を認識し、文中のBOMは保持する。"""
    marked = "\ufeff" + content
    assert plan_markdown.extract_headings(marked) == plan_markdown.extract_headings(content)
    assert plan_markdown.markdown_body_start_index(marked) == plan_markdown.markdown_body_start_index(content)
    assert plan_markdown.markdown_body_text(marked) == plan_markdown.markdown_body_text(content)
    for level in (1, 2):
        before = [(token.map, title) for token, title in markdown_headings.top_level_atx_headings(content, level)]
        after = [(token.map, title) for token, title in markdown_headings.top_level_atx_headings(marked, level)]
        assert after == before


@pytest.mark.parametrize("index", [-1, 2])
def test_markdown_table_returns_empty_location_for_out_of_range_index(index: int) -> None:
    table = plan_markdown.MarkdownTable(1, ("項目",), (("値",),), (3,), ("| 値 |",))

    assert table.row_location(index) == ""


def test_human_readable_main_and_detail_pass_structure_check() -> None:
    """新規の人間向け計画ファイル（メイン）と計画ファイル（詳細）がIDなしの判断・実装契約を満たす。"""
    work_type, main_errors = plan_sections.check_plan_main_structure(_HUMAN_MAIN_CONTENT)
    assert work_type == "通常変更"
    assert not main_errors, main_errors
    assert not plan_sections.check_plan_detail_structure(_HUMAN_DETAIL_CONTENT, work_type)


def test_human_readable_history_accepts_legacy_user_heading() -> None:
    """要旨を見出しへ書く旧書式のユーザー発言を読み取り互換として受理する。"""
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_EVENT_HEADING, _plan_fixture.LEGACY_USER_EVENT_HEADING, 1)
    assert not plan_sections.check_plan_main_structure(content)[1]
    assert plan_sections.has_legacy_history_user_event(content)
    assert not plan_sections.has_legacy_history_user_event(_HUMAN_MAIN_CONTENT)


def test_human_readable_partial_user_instruction_counts_as_adopted() -> None:
    content = _HUMAN_MAIN_CONTENT.replace(
        _plan_fixture.USER_ACTION_ROW,
        "| 公開契約に必要な変更を実装する | ユーザー指示 | 部分採用 | - |",
        1,
    )
    assert plan_sections.has_adopted_human_user_instruction(content)


@pytest.mark.parametrize("decision", plan_constants.PLAN_ACTION_DECISIONS)
def test_human_readable_agent_proposal_requires_reason_for_every_decision(decision: str) -> None:
    original = _plan_fixture.PROPOSAL_ACTION_ROW
    reason = "観測可能な根拠。"
    if decision == "移管":
        reason += "20261027-162634-998.mdへ移管する。"
    accepted = _HUMAN_MAIN_CONTENT.replace(
        original,
        f"| 類似するが対象外の記述は変更しない | エージェント提案 | {decision} | {reason} |",
        1,
    )
    assert not plan_sections.check_plan_main_structure(accepted)[1]

    rejected = accepted.replace(f"| {reason} |", "| - |", 1)
    errors = plan_sections.check_plan_main_structure(rejected)[1]
    assert any("エージェント提案行" in error for error in errors), errors


def test_human_readable_action_rejects_arbitrary_h3() -> None:
    content = _HUMAN_MAIN_CONTENT.replace(
        f"\n## {_plan_format.PLAN_H2_AGENT_JUDGMENT}\n",
        f"\n### 補足の観点\n\n対象範囲の補足を述べる。\n\n## {_plan_format.PLAN_H2_AGENT_JUDGMENT}\n",
        1,
    )
    errors = plan_sections.check_plan_main_structure(content)[1]
    assert any("実施内容" in error and "H3を置かない" in error for error in errors), errors


def test_related_wi_rejects_invalid_filename() -> None:
    content = _HUMAN_MAIN_CONTENT.replace("20260817-223603-001.md", "docs/notes.md", 1)
    errors = plan_sections.check_plan_main_structure(content)[1]
    assert any("ファイル名が不正" in error for error in errors), errors


def test_human_readable_history_rejects_table() -> None:
    table = "\n\n| 対象 | 内容 |\n| --- | --- |\n| 反映先 | 反映した。 |"
    content = _HUMAN_MAIN_CONTENT.replace(
        _plan_fixture.HISTORY_REVIEW_BODY,
        f"{_plan_fixture.HISTORY_REVIEW_BODY}{table}",
        1,
    )
    errors = plan_sections.check_plan_main_structure(content)[1]
    assert f"`## {_plan_format.PLAN_H2_LEGACY_HISTORY}`へ表を置かない" in errors, errors


def test_human_readable_units_reject_duplicate_descriptive_name() -> None:
    content = _HUMAN_DETAIL_CONTENT.replace(
        "| 回帰検証の追加 | 更新後の挙動を検証する | 契約境界の更新 | 2 | `pytest` |",
        "| 契約境界の更新 | 更新後の挙動を検証する | 契約境界の更新 | 2 | `pytest` |",
        1,
    )
    errors = plan_sections.check_plan_detail_structure(content, "通常変更")
    assert any("表内で一意の説明的な名前" in error for error in errors), errors


def test_cause_record_labels_pass_colloquial_check() -> None:
    """許容語彙表から対象項目が失われた場合に失敗し、行頭語の改称を判断する契機とする。"""
    deny_patterns = _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)
    allow_patterns = _colloquial_check.load_patterns(_colloquial_check.ALLOW_PATH)

    for label in (*_plan_format.CAUSE_RECORD_REQUIRED_LABELS, *_plan_format.CAUSE_RECORD_OPTIONAL_LABELS):
        diagnostics = _colloquial_check.scan_text(label, deny_patterns, allow_patterns)
        assert not diagnostics, (label, diagnostics)


@pytest.mark.parametrize("review_id", ["arbitrary", "R0-conformance", "R1-conformance-a"])
def test_history_review_rows_reject_invalid_identifier(review_id: str) -> None:
    review_row = f"| {review_id} | レビュー指摘 | 主要な指摘。 | 1件を採用した。 | `## 実施内容` |\n"
    content = _VALID_CONTENT.replace(
        f"{_plan_fixture.HISTORY_USER_ROW}\n",
        review_row,
        1,
    )
    errors = plan_sections.check_plan_structure(content)
    assert any("レビュー指摘行の`ID`は`R<正の整数>-<系統名>`形式にする" in error for error in errors), errors


def test_implementation_materials_allows_free_h3_composition() -> None:
    content = _VALID_CONTENT.replace(f"### {_plan_fixture.IMPLEMENTATION_H3}", "### 実行方法\n\n手順。\n\n### 変更説明")
    assert not plan_sections.check_plan_structure(content)


@pytest.mark.parametrize(
    "rows",
    [
        "",
        "| 2026-08-09 12:00 | 実装 | 成功。 |\n",
        "| 2026-08-09 12:00 | 実装 | 成功。 |\n| 2026-08-09 13:00 | 検証 | 成功。 |\n",
    ],
)
def test_progress_table_accepts_zero_one_or_multiple_rows(rows: str) -> None:
    marker = "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n"
    content = _VALID_CONTENT.replace(marker, marker + rows, 1)
    assert not plan_sections.check_plan_structure(content)


def test_permanence_sections_accept_and_omit_retired_similar_review_heading() -> None:
    assert _plan_fixture.LEGACY_SIMILAR_REVIEW_SECTION in _VALID_CONTENT
    assert not plan_sections.check_plan_structure(_VALID_CONTENT)
    without = _VALID_CONTENT.replace(_plan_fixture.LEGACY_SIMILAR_REVIEW_SECTION, "", 1)
    assert not plan_sections.check_plan_structure(without), plan_sections.check_plan_structure(without)


def test_structure_check_accepts_short_delimiter_tables() -> None:
    assert not plan_sections.check_plan_structure(_VALID_CONTENT.replace(" --- ", " - "))


def test_legacy_materials_require_verbatim_fence() -> None:
    content = _LEGACY_CONTENT.replace(
        "```text\n診断件数を2件から1件へ減らし、公開APIと対象外の挙動を変更しないでほしい。\n```",
        "診断件数を2件から1件へ減らし、公開APIと対象外の挙動を変更しないでほしい。",
    )
    errors = plan_sections.check_plan_structure(content)
    assert any("逐語転記が無い" in error for error in errors)


def test_action_references_rejected_requirement_are_rejected() -> None:
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 採用 | 指示どおり | R-P-001-002 |",
        1,
    )
    errors = plan_sections.check_plan_structure(content)
    assert any("不採用要求を参照できない: R-P-001-002" in error for error in errors), errors


def test_non_adopted_action_requires_free_text_reason() -> None:
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 不採用 | 非該当 |  |",
        1,
    )
    errors = plan_sections.check_plan_structure(content)
    assert any("非採用系の`根拠`は理由を記載する" in error for error in errors), errors


def test_adopted_action_rejects_non_queue_relation() -> None:
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 採用 | 非該当 | R-P-001-001 |",
        1,
    )
    errors = plan_sections.check_plan_structure(content)
    assert any("ユーザー指示との関係" in error and "非該当" in error for error in errors), errors


def test_requirement_coverage_keeps_checking_adopted_requirement_outside_terminal_only() -> None:
    errors = plan_sections.check_plan_structure(_plan_with_uncovered_requirement("公開APIの維持"))
    assert any("採用要求を被覆しない: R-P-002-001" in error for error in errors), errors


def test_structured_material_contract_rejects_requirement_table_order() -> None:
    first = (
        "| R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | "
        "診断件数の更新 | 非該当 | 指示と合意を反映するため。 |"
    )
    rejected = "| R-P-001-002 | P-001 | 対象外の検査を追加しない。 | 不採用 | 非該当 | 対象外の検査 | 実装上不要であるため。 |"
    second = "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |"
    content = _VALID_CONTENT.replace(f"{first}\n{rejected}\n{second}", f"{second}\n{rejected}\n{first}", 1)
    _materials, errors = plan_materials.parse_plan_materials(content)
    assert any("要求表は要求ID昇順" in error for error in errors), errors


def test_structured_material_types_reject_unreferenced_user_agreement() -> None:
    content = _VALID_CONTENT.replace("P-001, P-002", "P-001", 1).replace(
        "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |\n",
        "",
        1,
    )
    _materials, errors = plan_materials.parse_plan_materials(content)
    assert any("要求表から参照されていない" in error for error in errors), errors


def test_material_id_candidate_check_ignores_normal_notes_and_fenced_text() -> None:
    """素材ID行の前の注記・`Source:`行と、コードブロック内の素材ID形式の行を素材IDへ誤認しない。"""
    material_start = "P-001:\n\n```text\n"

    assert _LEGACY_CONTENT.count(material_start) == 1
    content = _LEGACY_CONTENT.replace(
        material_start,
        f"注記: 提示素材の説明\nSource: user transcript\n\n{material_start}P-999: fence内の文字列\n",
        1,
    )
    assert "注記: 提示素材の説明\nSource: user transcript\n\nP-001:\n\n```text\nP-999: fence内の文字列\n" in content

    errors = plan_sections.check_plan_structure(content)

    assert not any("素材ID行に注記" in error for error in errors)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (f"- ベースコミット: `{'a' * 7}`（`git rev-parse --short=7 HEAD`で実測）", "a" * 7),
        (f"- ベースコミット: `{'a' * 40}`（実測値）。", "a" * 40),
        (f"  - ベースコミット: `{'a' * 40}`", "a" * 40),
        (f"- 基準コミット:  `{'a' * 40}`", "a" * 40),
    ],
)
def test_metadata_reads_base_commit_with_legacy_notations(line: str, expected: str) -> None:
    content = f"## 背景\n\n### 計画メタ情報\n\n{line}\n"
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert not errors
    assert metadata is not None
    assert metadata.base_commit_candidates == (expected,)


def test_markdown_body_text_excludes_code_fence() -> None:
    content = "# 計画\n\n参照は`~/.claude/plans/01-計画-1a2b.bugs.md`。\n\n```text\n~/.claude/plans/ のパスで書く\n```\n"

    body = plan_markdown.markdown_body_text(content)

    assert "`~/.claude/plans/01-計画-1a2b.bugs.md`" in body
    assert "のパスで書く" not in body


def test_main_and_detail_canonical_pass_structure_check() -> None:
    work_type, main_errors = plan_sections.check_plan_main_structure(_canonical_main_content())
    assert work_type == "通常変更"
    assert not main_errors
    assert not plan_sections.check_plan_detail_structure(_VALID_DETAIL_CONTENT, work_type)


@pytest.mark.parametrize(
    "mutation",
    [
        (
            "| 実施内容 | 観測事象 | ユーザー要求との関係 | 具体化した内容 | 根拠 |",
            "| 実施内容 | 観測事象 | ユーザー要求との関係 | 根拠 |",
        ),
        (
            _plan_fixture.JUDGMENT_ROW,
            "| 類似するが対象外の記述は変更しない |  | 対象外。 | 維持する。 | 実測。 |",
        ),
    ],
)
def test_human_main_structure_rejects_judgment_table_shape_or_empty_cells(
    mutation: tuple[str, str],
) -> None:
    content = _canonical_human_main_content().replace(*mutation, 1)
    errors = plan_sections.check_plan_main_structure(content)[1]
    assert any(f"`## {_plan_format.PLAN_H2_AGENT_JUDGMENT}`" in error for error in errors), errors


def test_human_detail_structure_rejects_non_ascii_dependency_separator() -> None:
    content = _HUMAN_DETAIL_CONTENT.replace("契約境界の更新 | 2 |", "契約境界の更新、回帰検証の追加 | 2 |", 1)
    errors = plan_sections.check_plan_detail_structure(content, "通常変更")
    assert any("先行依存`が実装単位表に無い" in error for error in errors), errors


def test_detail_structure_accepts_legacy_implementation_unit_column() -> None:
    """既存計画の6列表を読み取り互換として受理する。"""
    legacy = _VALID_DETAIL_CONTENT.replace(
        _plan_fixture.TWO_FILE_UNITS_TABLE,
        "| 単位ID | 目的 | 対象の実施内容 | 先行依存 | 統合順 | 近接検証 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| U-001 | 診断件数を更新する | 任意の旧値 | なし | 1 | {_plan_fixture.VERIFICATION_COMMAND} |",
    )
    assert not plan_sections.check_plan_detail_structure(legacy, "通常変更")


def test_main_structure_rejects_bug_section() -> None:
    content = _VALID_MAIN_CONTENT.replace(
        "## 検証区分",
        "## バグ調査結果\n\n未使用。\n\n## 検証区分",
    )
    _work_type, errors = plan_sections.check_plan_main_structure(content)
    assert any("固定H2は" in error for error in errors), errors


def test_origin_check_skips_conversational_note(tmp_path: pathlib.Path) -> None:
    _write_wi(tmp_path, _wi_source(source=True))
    content = _HUMAN_MAIN_CONTENT.replace(
        f"({_plan_fixture.WI_FILES[0][0]})",
        f"({_plan_fixture.WI_FILES[0][0]}) [対話由来]",
    )
    errors, notices, skips = _origin_check(tmp_path, content)
    assert not errors, errors
    assert not notices, notices
    assert not skips, skips
