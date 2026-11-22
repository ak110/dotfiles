import pathlib

import pytest

from agent_toolkit._plan import structure as _plan_format
from agent_toolkit._plan.structure import constants as plan_constants
from agent_toolkit._plan.structure import markdown as plan_markdown
from agent_toolkit._plan.structure import materials as plan_materials
from agent_toolkit._plan.structure import parsing as plan_parsing
from agent_toolkit._plan.structure import sections as plan_sections
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.plan_structure_support import (
    _BUG_FILE_CONTENT,
    _HUMAN_DETAIL_CONTENT,
    _HUMAN_MAIN_CONTENT,
    _HUMAN_PARTIAL_ROW,
    _LEGACY_CONTENT,
    _LEGACY_TABLES_BUG_FILE_CONTENT,
    _VALID_CONTENT,
    _VALID_DETAIL_CONTENT,
    _VALID_MAIN_CONTENT,
    _canonical_human_main_content,
    _plan_with_uncovered_requirement,
    _wi_source,
    _write_wi,
)

pytestmark = pytest.mark.windows


@pytest.mark.parametrize("heading", ["### ユーザー発言0", "### ユーザー発言2"])
def test_human_readable_history_rejects_invalid_user_event_sequence(heading: str) -> None:
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_EVENT_HEADING, heading, 1)
    errors = plan_sections.check_plan_main_structure(content)[1]
    assert errors, errors


@pytest.mark.parametrize("decision", plan_constants.PLAN_ACTION_DECISIONS)
def test_human_readable_review_origin_applies_root_rule_before_decision(tmp_path: pathlib.Path, decision: str) -> None:
    review_path = tmp_path / "review.tsv"
    review_path.write_text("2\tplan-review\t指摘\n", encoding="utf-8")
    root = f"{review_path.as_posix()}のround 2"
    if decision != "採用":
        root += "。実施しない範囲と理由を記録する。"
    if decision == "移管":
        root += "20261027-162634-998.mdへ移管する。"
    content = _HUMAN_MAIN_CONTENT.replace(
        _HUMAN_PARTIAL_ROW,
        f"| 入力の境界を追加確認する | 計画レビュー第2ラウンド | {decision} | {root} |",
        1,
    )
    assert not plan_sections.check_plan_main_structure(content)[1]


def test_agent_wi_adopted_action_without_reason_yields_migration_notice() -> None:
    row = f"| 入力の境界を追加確認する | エージェント由来のWI ({_plan_fixture.WI_FILES[0][0]}) | 採用 | - |"
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.WI_ACTION_ROW, row, 1)
    notices: list[str] = []
    _work_type, errors = plan_sections.check_plan_main_structure(content, origin_notices=notices)
    assert not errors, errors
    assert any("適用範囲を再導出した結果と根拠" in notice for notice in notices), notices


def test_wi_origin_diagnostic_shows_expected_format() -> None:
    example = "`エージェント由来のWI (20260831-000000-001.md)`"
    origin = f"人間由来のWI ({_plan_fixture.WI_FILES[0][0]})"
    full_width = _HUMAN_MAIN_CONTENT.replace(origin, f"人間由来のWI（{_plan_fixture.WI_FILES[0][0]}）", 1)
    errors = plan_sections.check_plan_main_structure(full_width)[1]
    assert any(example in error for error in errors), errors
    without_name = _HUMAN_MAIN_CONTENT.replace(origin, "人間由来のWI", 1)
    errors = plan_sections.check_plan_main_structure(without_name)[1]
    assert any(example in error for error in errors), errors


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ("- 関連WI:\n  - 20260817-223603-001.md:", "1行要約が空"),
        (
            "- 関連WI:\n  - 20260817-223603-001.md: 入力の境界を追加確認する\n  - 20260817-223603-001.md: 重複した要求",
            "ファイル名が重複",
        ),
        (
            "- 関連WI: なし\n  - 20260817-223603-001.md: 入力の境界を追加確認する",
            "なし`と子項目",
        ),
    ],
)
def test_related_wi_rejects_invalid_children(replacement: str, expected: str) -> None:
    content = _HUMAN_MAIN_CONTENT.replace(
        "- 関連WI:\n  - 20260817-223603-001.md: 入力の境界を追加確認する",
        replacement,
        1,
    )
    errors = plan_sections.check_plan_main_structure(content)[1]
    assert any(expected in error for error in errors), errors


def test_bug_file_structure_rejects_missing_record_line() -> None:
    content = _BUG_FILE_CONTENT.replace(f"{_plan_fixture.cause_record_line('確認')}\n", "")
    errors = plan_sections.check_bug_file_structure(content)
    content_of_check = _plan_format.CAUSE_RECORD_REQUIRED_LABELS["確認"]
    assert errors == [f"`### {_plan_fixture.BUG_UNIT_H3}`: `確認:`の行が無い（書く内容: {content_of_check}）"]


def test_bug_file_structure_accepts_saved_legacy_tables() -> None:
    assert not plan_sections.check_bug_file_structure(_LEGACY_TABLES_BUG_FILE_CONTENT)
    assert plan_sections.has_legacy_cause_record(_LEGACY_TABLES_BUG_FILE_CONTENT)
    assert not plan_sections.has_legacy_cause_record(_BUG_FILE_CONTENT)


def test_history_review_rows_reject_duplicate_track_and_round() -> None:
    review_rows = (
        "| R1-conformance | レビュー指摘 | 主要な指摘。 | 1件を採用した。 | `## 実施内容` |\n"
        "| R01-conformance | レビュー指摘 | 追加の指摘。 | 1件を採用した。 | `## 変更履歴` |\n"
    )
    content = _VALID_CONTENT.replace(
        f"{_plan_fixture.HISTORY_USER_ROW}\n",
        review_rows,
        1,
    )
    errors = plan_sections.check_plan_structure(content)
    assert any("レビュー指摘行は系統・ラウンドを重複させない: conformance, 1" in error for error in errors), errors


def test_duplicate_fixed_table_is_rejected() -> None:
    duplicate = """| 実施内容 | 採否 | ユーザー指示との関係 | 根拠 |
| --- | --- | --- | --- |
| 追加の変更 | 採用 | 具体化 | R-P-001-001 |

"""
    content = _VALID_CONTENT.replace("### 合意済みの除外・保持", duplicate + "### 合意済みの除外・保持", 1)
    errors = plan_sections.check_plan_structure(content)
    assert any("固定表は1件必要" in error for error in errors), errors


def test_permanence_table_accepts_multiple_findings() -> None:
    row = _plan_fixture.PERMANENCE_ROW
    additional = "| 公開契約を維持する | P-001 | 計画限り | 既存文書へ記載済みのため。 |"
    assert not plan_sections.check_plan_structure(_VALID_CONTENT.replace(row, f"{row}\n{additional}"))


def test_permanence_sections_reject_conclusion_words_only() -> None:
    content = _VALID_CONTENT.replace(_plan_fixture.REFACTORING_TABLE, "該当なし", 1)
    errors = plan_sections.check_plan_structure(content)
    assert any("結論語だけの記載は成立しない" in error for error in errors), errors


def test_extract_tables_keeps_row_column_count() -> None:
    table = "| A | B |\n| --- | --- |\n| 1 | 2 | 3 |\n| 4 |"
    lines: list[tuple[int, str]] = list(enumerate(table.splitlines(), start=1))
    assert plan_markdown.extract_tables(lines)[0].rows == (("1", "2", "3"), ("4",))


def test_parse_plan_materials_returns_structured_ids_and_legacy_flag() -> None:
    materials, errors = plan_materials.parse_plan_materials(_VALID_CONTENT)
    assert not errors
    assert materials == plan_materials.PlanMaterials(
        frozenset({"P-001", "P-002"}),
        frozenset({"R-P-001-001", "R-P-001-002", "R-P-002-001"}),
        False,
        frozenset({"R-P-001-001", "R-P-002-001"}),
        feedback_queue_ids=frozenset({"20260817-223603-001.md"}),
    )

    legacy_materials, legacy_errors = plan_materials.parse_plan_materials(_LEGACY_CONTENT)
    assert not legacy_errors
    assert legacy_materials == plan_materials.PlanMaterials(frozenset({"P-001"}), frozenset(), True)


@pytest.mark.parametrize("decision", plan_constants.PLAN_ACTION_DECISIONS)
def test_action_decisions_accept_all_declared_values(decision: str) -> None:
    relation = "指示どおり" if decision in {"採用", "部分採用"} else "非該当"
    root = "R-P-001-001" if decision in {"採用", "部分採用"} else "R-P-001-002を採用しない理由を記載する。"
    if decision == "移管":
        root += "20261027-162634-999.mdへ移管する。"
    row = f"| 診断件数を2件から1件へ減らす | {decision} | {relation} | {root} |"
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        row,
        1,
    )
    if decision not in {"採用", "部分採用"}:
        content = content.replace(
            "| 公開契約を維持する | 対象の公開API | P-002, R-P-002-001 |",
            "| 公開契約を維持する | 対象の公開API | P-002, R-P-002-001, R-P-001-001 |",
            1,
        )
    errors = plan_sections.check_plan_structure(content)
    assert not errors, errors


def test_requirement_coverage_excludes_terminal_only_adopted_requirement() -> None:
    assert not plan_sections.check_plan_structure(_plan_with_uncovered_requirement("公開工程のみ適用する"))


def test_structured_material_contract_rejects_duplicate_material_id() -> None:
    content = _VALID_CONTENT.replace(
        "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        "| P-001 | 利用者合意 | 非該当 | 本セッション | 全文 |\n| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        1,
    )
    _materials, errors = plan_materials.parse_plan_materials(content)
    assert any("素材IDが重複している" in error for error in errors), errors


@pytest.mark.parametrize(
    "row",
    [
        "| P-002 | 利用者指示 | 非該当 | 本セッション | 全文 |",
        "| P-002 | 利用者指示 | 非該当 | 委譲元:user message | 第1段落 |",
        "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        "| P-002 | 利用者合意 | 非該当 | AskUserQuestion | 回答全文 |",
        "| P-002 | 利用者合意 | 非該当 | TBD:decision.md#回答 | 回答全文 |",
        "| P-002 | 参考素材 | 非該当 | docs/reference.md | 節1 |",
        "| P-002 | 処理対象資料 | 非該当 | input.json | $.items |",
        "| P-002 | 起動事実 | 非該当 | 常駐自動起動 | 非該当 |",
    ],
)
def test_structured_material_types_preserve_source_and_citation(row: str) -> None:
    content = _VALID_CONTENT.replace(
        "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        row,
        1,
    )
    materials, errors = plan_materials.parse_plan_materials(content)
    assert not errors
    assert materials is not None
    assert not materials.is_legacy


def test_metadata_falls_back_to_legacy_placement() -> None:
    """正規配置が無い既存計画は旧配置を読み取り互換で解析する。"""
    content = f"## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`\n"
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert not errors
    assert metadata is not None
    assert metadata.parent == "背景"
    assert metadata.base_commit_candidates == ("a" * 40,)


def test_metadata_rejects_conflicting_values() -> None:
    """同じ項目に異なる値を持つ計画は競合として拒否する。"""
    content = _VALID_CONTENT.replace(
        "- 作業種別: 通常変更\n",
        "- 作業種別: 通常変更\n- 作業種別: バグ対応\n",
    )
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert metadata is None
    assert any("競合する値" in error for error in errors)


def test_duplicate_headings_rejects_same_text_under_same_parent() -> None:
    content = "# 計画\n\n## 親\n\n### 子\n\n### 子\n"

    errors = plan_markdown.check_duplicate_headings(content)

    assert len(errors) == 1
    assert errors[0].startswith("同じ見出しが重複している: `### 子`（計画/親配下、5行目と7行目）。")

    assert "一方を削除するか、見出しの文言を区別する" in errors[0]


def test_duplicate_headings_checks_deep_headings_and_ignores_fences() -> None:
    content = "# 計画\n\n## 親\n\n### 子\n\n#### 深部\n\n```md\n#### 深部\n```\n\n#### 深部\n"

    errors = plan_markdown.check_duplicate_headings(content)

    assert len(errors) == 1
    assert "`#### 深部`" in errors[0]
    assert "7行目と13行目" in errors[0]


def test_human_main_structure_requires_one_judgment_row_per_agent_proposal() -> None:
    _work_type, errors = plan_sections.check_plan_main_structure(_canonical_human_main_content())
    assert not errors, errors

    missing = _canonical_human_main_content().replace(
        f"{_plan_fixture.JUDGMENT_ROW}\n",
        "",
        1,
    )
    errors = plan_sections.check_plan_main_structure(missing)[1]
    assert any("実施内容`はエージェント提案行と同じ順序" in error for error in errors), errors


def test_detail_structure_requires_implementation_units() -> None:
    start = _VALID_DETAIL_CONTENT.index("### 実装単位")
    end = _VALID_DETAIL_CONTENT.index(f"### {_plan_fixture.IMPLEMENTATION_H3}")
    content = _VALID_DETAIL_CONTENT[:start] + _VALID_DETAIL_CONTENT[end:]
    errors = plan_sections.check_plan_detail_structure(content, "通常変更")
    assert any("`### 実装単位`を1件置く" in error for error in errors), errors


def test_human_detail_structure_accepts_multiple_ascii_comma_dependencies() -> None:
    second = "| 調査結果の整理 | 既存の判断材料を整理する | なし | 2 | `pytest` |\n"
    content = _HUMAN_DETAIL_CONTENT.replace(
        "| 回帰検証の追加 | 更新後の挙動を検証する | 契約境界の更新 | 2 | `pytest` |",
        second + "| 回帰検証の追加 | 更新後の挙動を検証する | 契約境界の更新, 調査結果の整理 | 3 | `pytest` |",
        1,
    )
    units, errors = plan_parsing.parse_plan_implementation_units(content)
    assert not errors, errors
    assert units is not None
    assert units[-1].dependencies == ("契約境界の更新", "調査結果の整理")


def test_main_structure_rejects_empty_verification_command() -> None:
    content = _VALID_MAIN_CONTENT.replace(
        f"| {_plan_format.PLAN_VERIFICATION_TABLE_ROWS[1]} | {_plan_fixture.INTEGRATION_COMMAND} |", "| 統合後検証 |  |"
    )
    _work_type, errors = plan_sections.check_plan_main_structure(content)
    matched = [error for error in errors if "空の検証コマンドがある" in error]
    assert matched, errors

    assert all("検証コマンドの欄には実行するコマンドを書く" in error for error in matched)


def test_origin_check_is_inactive_without_collectors(tmp_path: pathlib.Path) -> None:
    _write_wi(tmp_path, _wi_source(source=True))
    _work_type, errors = plan_sections.check_plan_main_structure(_HUMAN_MAIN_CONTENT)
    assert not errors, errors
