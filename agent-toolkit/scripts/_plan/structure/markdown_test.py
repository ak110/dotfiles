# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=duplicate-code,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order
"""計画形式の共通解析を検証する。"""

import pathlib
import sys

import pytest
from pyfltr.colloquial import check as _colloquial_check

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from _plan import fixture as _plan_fixture  # noqa: E402  # pylint: disable=wrong-import-position
from _plan import structure as _plan_format  # noqa: E402  # pylint: disable=wrong-import-position

_BASE = _plan_fixture.BASE_COMMIT

_VALID_CONTENT = _plan_fixture.single_file_plan()
_BUG_CONTENT = _plan_fixture.single_file_plan(bug=True)
_LEGACY_CONTENT = _plan_fixture.legacy_materials_single_file_plan()

_BUG_SECTION = _plan_fixture.inline_bug_section()
_BUG_CAUSE_TABLE = _plan_fixture.bug_cause_table()
_BUG_INVESTIGATION_TABLE = _plan_fixture.bug_investigation_table()
_BUG_FILE_CONTENT = _plan_fixture.bug_file()
_LEGACY_ROWS_BUG_FILE_CONTENT = _plan_fixture.bug_file(variant=_plan_fixture.BUG_VARIANT_LEGACY_ROWS)
_LEGACY_BUG_FILE_CONTENT = _plan_fixture.bug_file(variant=_plan_fixture.BUG_VARIANT_LEGACY_STANDALONE)

_HUMAN_MAIN_CONTENT = _plan_fixture.human_main(related_wi=_plan_fixture.WI_FILES)
_HUMAN_DETAIL_CONTENT = _plan_fixture.human_detail()
_HUMAN_PARTIAL_ROW = _plan_fixture.WI_ACTION_ROW
_HUMAN_PARTIAL_REASON = _plan_fixture.WI_ACTION_REASON

_VALID_MAIN_CONTENT = _plan_fixture.two_file_main()
_VALID_DETAIL_CONTENT = _plan_fixture.two_file_detail()


from _plan.structure.test_support_test import *  # noqa: F403


def test_human_readable_main_and_detail_pass_structure_check() -> None:
    """新規の人間向け計画ファイル（メイン）と計画ファイル（詳細）がIDなしの判断・実装契約を満たす。"""
    work_type, main_errors = _plan_format.check_plan_main_structure(_HUMAN_MAIN_CONTENT)
    assert work_type == "通常変更"
    assert not main_errors, main_errors
    assert not _plan_format.check_plan_detail_structure(_HUMAN_DETAIL_CONTENT, work_type)


def test_human_readable_history_accepts_legacy_user_heading() -> None:
    """要旨を見出しへ書く旧書式のユーザー発言を読み取り互換として受理する。"""
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_EVENT_HEADING, _plan_fixture.LEGACY_USER_EVENT_HEADING, 1)
    assert not _plan_format.check_plan_main_structure(content)[1]
    assert _plan_format.has_legacy_history_user_event(content)
    assert not _plan_format.has_legacy_history_user_event(_HUMAN_MAIN_CONTENT)


def test_human_readable_partial_user_instruction_counts_as_adopted() -> None:
    """ユーザー指示の部分採用も採用済みとして扱う。"""
    content = _HUMAN_MAIN_CONTENT.replace(
        _plan_fixture.USER_ACTION_ROW,
        "| 公開契約に必要な変更を実装する | ユーザー指示 | 部分採用 | - |",
        1,
    )
    assert _plan_format.has_adopted_human_user_instruction(content)


@pytest.mark.parametrize("decision", _plan_format.PLAN_ACTION_DECISIONS)
def test_human_readable_agent_proposal_requires_reason_for_every_decision(decision: str) -> None:
    """エージェント提案は全採否で空でもハイフンでもない根拠を持つ。"""
    original = _plan_fixture.PROPOSAL_ACTION_ROW
    accepted = _HUMAN_MAIN_CONTENT.replace(
        original,
        f"| 類似するが対象外の記述は変更しない | エージェント提案 | {decision} | 観測可能な根拠。 |",
        1,
    )
    assert not _plan_format.check_plan_main_structure(accepted)[1]

    rejected = accepted.replace("| 観測可能な根拠。 |", "| - |", 1)
    errors = _plan_format.check_plan_main_structure(rejected)[1]
    assert any("エージェント提案行" in error for error in errors), errors


def test_human_readable_action_rejects_arbitrary_h3() -> None:
    """実施内容直下へ置いた任意のH3を、拒否する対象をH3と述べるメッセージで拒否する。"""
    content = _HUMAN_MAIN_CONTENT.replace(
        f"\n## {_plan_format.PLAN_H2_AGENT_JUDGMENT}\n",
        f"\n### 補足の観点\n\n対象範囲の補足を述べる。\n\n## {_plan_format.PLAN_H2_AGENT_JUDGMENT}\n",
        1,
    )
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("実施内容" in error and "H3を置かない" in error for error in errors), errors


def test_related_wi_rejects_invalid_filename() -> None:
    """関連WIは正本ファイル名だけを受理する。"""
    content = _HUMAN_MAIN_CONTENT.replace("20260817-223603-001.md", "docs/notes.md", 1)
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("ファイル名が不正" in error for error in errors), errors


def test_human_readable_history_rejects_table() -> None:
    """変更履歴配下へ置いた表を、拒否する対象を表と述べるメッセージで拒否する。"""
    table = "\n\n| 対象 | 内容 |\n| --- | --- |\n| 反映先 | 反映した。 |"
    content = _HUMAN_MAIN_CONTENT.replace(
        _plan_fixture.HISTORY_REVIEW_BODY,
        f"{_plan_fixture.HISTORY_REVIEW_BODY}{table}",
        1,
    )
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert f"`## {_plan_format.PLAN_H2_LEGACY_HISTORY}`へ表を置かない" in errors, errors


def test_human_readable_units_reject_duplicate_descriptive_name() -> None:
    """人間向けdetail側の実装単位名は重複させない。"""
    content = _HUMAN_DETAIL_CONTENT.replace(
        "| 回帰検証の追加 | 更新後の挙動を検証する | 契約境界の更新 | 2 | `pytest` |",
        "| 契約境界の更新 | 更新後の挙動を検証する | 契約境界の更新 | 2 | `pytest` |",
        1,
    )
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any("表内で一意の説明的な名前" in error for error in errors), errors


def test_bug_cause_table_rows_pass_colloquial_check() -> None:
    """許容語彙表から対象項目が失われた場合に失敗し、固定行名の改称を判断する契機とする。"""
    deny_patterns = _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)
    allow_patterns = _colloquial_check.load_patterns(_colloquial_check.ALLOW_PATH)

    for row_name in _plan_format.PLAN_BUG_CAUSE_TABLE_ROWS:
        diagnostics = _colloquial_check.scan_text(row_name, deny_patterns, allow_patterns)
        assert not diagnostics, (row_name, diagnostics)


def test_bug_file_structure_rejects_duplicate_investigation_table() -> None:
    """同じバグ単位の調査表が重複した場合を拒否する。"""
    content = _BUG_FILE_CONTENT.replace(
        _BUG_INVESTIGATION_TABLE,
        f"{_BUG_INVESTIGATION_TABLE}\n\n{_BUG_INVESTIGATION_TABLE}",
        1,
    )
    assert _plan_format.check_bug_file_structure(content)


@pytest.mark.parametrize("review_id", ["arbitrary", "R0-conformance", "R1-conformance-a"])
def test_history_review_rows_reject_invalid_identifier(review_id: str) -> None:
    """系統と正のラウンド番号を分離できないレビュー指摘IDを拒否する。"""
    review_row = f"| {review_id} | レビュー指摘 | 主要な指摘。 | 1件を採用した。 | `## 実施内容` |\n"
    content = _VALID_CONTENT.replace(
        f"{_plan_fixture.HISTORY_USER_ROW}\n",
        review_row,
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("レビュー指摘行の`ID`は`R<正の整数>-<系統名>`形式にする" in error for error in errors), errors


def test_implementation_materials_allows_free_h3_composition() -> None:
    """実装資料配下では自由なH3構成を受理する。"""
    content = _VALID_CONTENT.replace(f"### {_plan_fixture.IMPLEMENTATION_H3}", "### 実行方法\n\n手順。\n\n### 変更説明")
    assert not _plan_format.check_plan_structure(content)


@pytest.mark.parametrize(
    "rows",
    [
        "",
        "| 2026-08-09 12:00 | 実装 | 成功。 |\n",
        "| 2026-08-09 12:00 | 実装 | 成功。 |\n| 2026-08-09 13:00 | 検証 | 成功。 |\n",
    ],
)
def test_progress_table_accepts_zero_one_or_multiple_rows(rows: str) -> None:
    """進捗表だけは0件を許容し、1件以上の既存形式も受理する。"""
    marker = "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n"
    content = _VALID_CONTENT.replace(marker, marker + rows, 1)
    assert not _plan_format.check_plan_structure(content)


def test_permanence_sections_accept_and_omit_retired_similar_review_heading() -> None:
    """廃止した`### 類似見直し`を持つ既存計画と、置かない計画のいずれも受理する。"""
    assert _plan_fixture.LEGACY_SIMILAR_REVIEW_SECTION in _VALID_CONTENT
    assert not _plan_format.check_plan_structure(_VALID_CONTENT)
    without = _VALID_CONTENT.replace(_plan_fixture.LEGACY_SIMILAR_REVIEW_SECTION, "", 1)
    assert not _plan_format.check_plan_structure(without), _plan_format.check_plan_structure(without)


def test_structure_check_accepts_short_delimiter_tables() -> None:
    """区切り行が1ダッシュの固定表を受理する。"""
    assert not _plan_format.check_plan_structure(_VALID_CONTENT.replace(" --- ", " - "))


def test_legacy_materials_require_verbatim_fence() -> None:
    """旧形式では素材IDの直後に逐語fenceが無い提示素材を拒否する。"""
    content = _LEGACY_CONTENT.replace(
        "```text\n診断件数を2件から1件へ減らし、公開APIと対象外の挙動を変更しないでほしい。\n```",
        "診断件数を2件から1件へ減らし、公開APIと対象外の挙動を変更しないでほしい。",
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("逐語転記が無い" in error for error in errors)


def test_action_references_rejected_requirement_are_rejected() -> None:
    """採用系の実施内容の根拠に不採用要求を指定できない。"""
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 採用 | 指示どおり | R-P-001-002 |",
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("不採用要求を参照できない: R-P-001-002" in error for error in errors), errors


def test_non_adopted_action_requires_free_text_reason() -> None:
    """非採用系の実施内容は要求IDでなく理由を根拠へ記載する。"""
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 不採用 | 非該当 |  |",
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("非採用系の`根拠`は理由を記載する" in error for error in errors), errors


def test_adopted_action_rejects_non_queue_relation() -> None:
    """採用系の実施内容に非該当の関係を指定できない。"""
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 採用 | 非該当 | R-P-001-001 |",
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("ユーザー指示との関係" in error and "非該当" in error for error in errors), errors


def test_requirement_coverage_keeps_checking_adopted_requirement_outside_terminal_only() -> None:
    """`終端工程のみ`で始まらない採用要求は除外の影響を受けず被覆検査の対象に残る。"""
    errors = _plan_format.check_plan_structure(_plan_with_uncovered_requirement("公開APIの維持"))
    assert any("採用要求を被覆しない: R-P-002-001" in error for error in errors), errors


def test_structured_material_contract_rejects_requirement_table_order() -> None:
    """要求表を要求IDの昇順以外で並べた場合に拒否する。"""
    first = (
        "| R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | "
        "診断件数の更新 | 非該当 | 指示と合意を反映するため。 |"
    )
    rejected = "| R-P-001-002 | P-001 | 対象外の検査を追加しない。 | 不採用 | 非該当 | 対象外の検査 | 実装上不要であるため。 |"
    second = "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |"
    content = _VALID_CONTENT.replace(f"{first}\n{rejected}\n{second}", f"{second}\n{rejected}\n{first}", 1)
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("要求表は要求ID昇順" in error for error in errors), errors


def test_structured_material_types_reject_unreferenced_user_agreement() -> None:
    """利用者指示又は利用者合意を要求表から未参照にしない。"""
    content = _VALID_CONTENT.replace("P-001, P-002", "P-001", 1).replace(
        "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |\n",
        "",
        1,
    )
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("要求表から参照されていない" in error for error in errors), errors


def test_material_id_candidate_check_ignores_normal_notes_and_fenced_text() -> None:
    content = _LEGACY_CONTENT.replace(
        "P-001:\n\n```text\n対象を更新してほしい。",
        "注記: 提示素材の説明\nSource: user transcript\n\nP-001:\n\n```text\nP-999: fence内の文字列\n対象を更新してほしい。",
    )

    errors = _plan_format.check_plan_structure(content)

    assert not any("素材ID行に注記" in error for error in errors)


@pytest.mark.parametrize(
    "line",
    [
        f"- ベースコミット: `{'a' * 40}`（`git rev-parse HEAD`で実測）",
        f"- ベースコミット: `{'a' * 40}`（実測値）。",
        f"  - ベースコミット: `{'a' * 40}`",
        f"- 基準コミット:  `{'a' * 40}`",
    ],
)
def test_metadata_reads_base_commit_with_legacy_notations(line: str) -> None:
    """注記付き、字下げ、旧別名のベースコミット記法からもOIDを読み取る。"""
    content = f"## 背景\n\n### 計画メタ情報\n\n{line}\n"
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert not errors
    assert metadata is not None
    assert metadata.base_commit_candidates == ("a" * 40,)


def test_markdown_body_text_excludes_code_fence() -> None:
    """本文テキストにはコードフェンス内の行を含めない。"""
    content = "# 計画\n\n参照は`~/.claude/plans/01-計画-1a2b.bugs.md`。\n\n```text\n~/.claude/plans/ のパスで書く\n```\n"

    body = _plan_format.markdown_body_text(content)

    assert "`~/.claude/plans/01-計画-1a2b.bugs.md`" in body
    assert "のパスで書く" not in body


@pytest.mark.parametrize("row_name", _plan_format.PLAN_BUG_TABLE_ROWS)
def test_root_cause_analysis_states_every_bug_table_row(row_name: str) -> None:
    """調査表の固定行名を原因分析契約の集約表が明記する。

    行名の正本を構造定数に置くため、集約表の追随漏れをここで検出する。
    """
    assert f"| {row_name} | " in _ROOT_CAUSE_ANALYSIS.read_text(encoding="utf-8")


def test_main_and_detail_canonical_pass_structure_check() -> None:
    """新書式のメイン側・detail側の正規形はいずれも構造検査を通過する。"""
    work_type, main_errors = _plan_format.check_plan_main_structure(_canonical_main_content())
    assert work_type == "通常変更"
    assert not main_errors
    assert not _plan_format.check_plan_detail_structure(_VALID_DETAIL_CONTENT, work_type)


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
    """判断表の列不足と空cellを拒否する。"""
    content = _canonical_human_main_content().replace(*mutation, 1)
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any(f"`## {_plan_format.PLAN_H2_AGENT_JUDGMENT}`" in error for error in errors), errors


def test_human_detail_structure_rejects_non_ascii_dependency_separator() -> None:
    """先行依存の全角読点を区切りとして扱わず、未解決の依存として拒否する。"""
    content = _HUMAN_DETAIL_CONTENT.replace("契約境界の更新 | 2 |", "契約境界の更新、回帰検証の追加 | 2 |", 1)
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any("先行依存`が実装単位表に無い" in error for error in errors), errors


def test_detail_structure_accepts_legacy_implementation_unit_column() -> None:
    """既存計画の6列表を読み取り互換として受理する。"""
    legacy = _VALID_DETAIL_CONTENT.replace(
        _plan_fixture.TWO_FILE_UNITS_TABLE,
        "| 単位ID | 目的 | 対象の実施内容 | 先行依存 | 統合順 | 近接検証 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| U-001 | 診断件数を更新する | 任意の旧値 | なし | 1 | {_plan_fixture.VERIFICATION_COMMAND} |",
    )
    assert not _plan_format.check_plan_detail_structure(legacy, "通常変更")


def test_main_structure_rejects_bug_section() -> None:
    """メイン側に`## バグ調査結果`は置かない（detail側専用）。"""
    content = _VALID_MAIN_CONTENT.replace(
        "## 検証区分",
        "## バグ調査結果\n\n未使用。\n\n## 検証区分",
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any("固定H2は" in error for error in errors), errors


def test_origin_check_skips_conversational_note(tmp_path: pathlib.Path) -> None:
    """`[対話由来]`注記のある行は書式として受理し、照合の対象から除く。"""
    _write_wi(tmp_path, _wi_source(source=True))
    content = _HUMAN_MAIN_CONTENT.replace(
        f"({_plan_fixture.WI_FILES[0][0]})",
        f"({_plan_fixture.WI_FILES[0][0]}) [対話由来]",
    )
    errors, notices, skips = _origin_check(tmp_path, content)
    assert not errors, errors
    assert not notices, notices
    assert not skips, skips
