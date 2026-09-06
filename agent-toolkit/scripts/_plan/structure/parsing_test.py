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


def test_human_readable_main_accepts_satisfied_action() -> None:
    """新書式の実施内容表は裏付けを持つ`充足済み`を受理する。"""
    source = _HUMAN_PARTIAL_ROW
    replacement = (
        "| 入力の境界を追加確認する | 人間由来のWI (20260817-223603-001.md) | 充足済み | "
        "対象実装と要求を突合して充足を確認した。 |"
    )
    content = _HUMAN_MAIN_CONTENT.replace(
        source,
        replacement,
        1,
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert not errors, errors


@pytest.mark.parametrize(
    ("origin", "root", "expected_error"),
    [
        ("計画レビュー第0ラウンド", "-", "`由来`は"),
        ("計画レビュー第1ラウンド", "{path}のround 2", "計画レビュー由来"),
    ],
)
def test_human_readable_action_rejects_invalid_review_origin_or_root(
    tmp_path: pathlib.Path, origin: str, root: str, expected_error: str
) -> None:
    """計画レビュー由来の採用行は正のラウンドと対応する根拠を必要とする。"""
    review_path = tmp_path / "review.tsv"
    review_path.write_text("1\tplan-review\t指摘\n", encoding="utf-8")
    content = _HUMAN_MAIN_CONTENT.replace(
        _HUMAN_PARTIAL_ROW,
        f"| 入力の境界を追加確認する | {origin} | 採用 | {root.format(path=review_path.as_posix())} |",
        1,
    )
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any(expected_error in error for error in errors), errors


def test_human_readable_action_rejects_independent_exclusion_table() -> None:
    """人間向けメイン側は実施内容と別の除外・保持表を持たない。"""
    content = _HUMAN_MAIN_CONTENT.replace(
        f"\n## {_plan_format.PLAN_H2_AGENT_JUDGMENT}\n",
        f"\n### 合意済みの除外・保持\n\n対象外の類似箇所は維持する。\n\n## {_plan_format.PLAN_H2_AGENT_JUDGMENT}\n",
        1,
    )
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("直下にH3を置かない" in error for error in errors), errors


def test_legacy_wi_origins_reports_only_legacy_names() -> None:
    """改名前の`由来`欄だけを移行対象として報告する。"""
    assert not _plan_format.legacy_wi_origins(_HUMAN_MAIN_CONTENT)
    assert _plan_format.legacy_wi_origins(_plan_fixture.legacy_wi_names(_HUMAN_MAIN_CONTENT)) == (
        _plan_format.PLAN_HUMAN_WI_ORIGIN,
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("関連WI", "ファイル名が不正"),
        (
            "| 対象外の類似するが対象外の記述は変更しない |",
            "`## 実施内容`へ素材・要求・履歴・実装単位の合成IDを記載しない",
        ),
    ],
)
def test_human_readable_main_rejects_internal_identifiers(mutation: str, expected: str) -> None:
    """人間向けメイン側に内部管理IDを持ち込まない。"""
    if mutation == "関連WI":
        content = _HUMAN_MAIN_CONTENT.replace("20260817-223603-001.md", "P-001.md", 1)
    else:
        content = _HUMAN_MAIN_CONTENT.replace(
            _plan_fixture.PROPOSAL_ACTION_ROW,
            "| P-001 | エージェント提案 | 対象外 | 当初目的と公開契約への影響が無いため。 |",
            1,
        )
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any(expected in error for error in errors), errors


def test_bug_file_structure_rejects_empty_content_cell() -> None:
    """付属ファイルの固定表で`内容`が空欄の場合を拒否する。"""
    content = _BUG_FILE_CONTENT.replace(
        _plan_fixture.bug_row("直接的原因"),
        "| 直接的原因 |  |",
    )
    errors = _plan_format.check_bug_file_structure(content)
    assert any("空の`内容`" in error for error in errors), errors


def test_bug_file_structure_accepts_legacy_standalone_table() -> None:
    """原因分析表を持たない旧調査表を読み取り互換で受理する。"""
    assert not _plan_format.check_bug_file_structure(_LEGACY_BUG_FILE_CONTENT)


def test_optional_exclusion_section_may_be_absent() -> None:
    """除外・保持の合意が無い計画は任意H3を省略できる。"""
    start = _VALID_CONTENT.index("### 合意済みの除外・保持")
    end = _VALID_CONTENT.index("## 提示素材")
    content = _VALID_CONTENT[:start] + _VALID_CONTENT[end:]
    # 除外表を欠くため、当該表でだけ被覆されていた採用要求の参照を`根拠`列へ追加して被覆を維持する。
    content = content.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 採用 | 指示どおり | R-P-001-001, R-P-002-001 |",
    )
    assert not _plan_format.check_plan_structure(content)


@pytest.mark.parametrize("empty_column", range(len(_plan_format.PLAN_HISTORY_TABLE_HEADER)))
def test_history_review_rows_reject_empty_columns(empty_column: int) -> None:
    """レビュー指摘行の全列を必須とする。"""
    cells = ["R1-conformance", "レビュー指摘", "主要な指摘。", "1件を採用した。", "`## 実施内容`"]
    cells[empty_column] = ""
    review_row = f"| {' | '.join(cells)} |\n"
    content = _VALID_CONTENT.replace(
        f"{_plan_fixture.HISTORY_USER_ROW}\n",
        review_row,
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("空cellまたは列数不一致" in error for error in errors), errors


def test_permanence_section_accepts_prose_instead_of_table() -> None:
    """候補0件の恒久化とリファクタリングは、固定表の代わりに理由を書いた地の文を受理する。"""
    content = _VALID_CONTENT.replace(
        _plan_fixture.PERMANENCE_TABLE,
        "提示素材と調査結果を確認し、当該計画固有でない知見は無かった。",
        1,
    )
    content = content.replace(
        _plan_fixture.REFACTORING_TABLE,
        "変更対象とその参照元を確認し、本計画の変更が是正を必要にする箇所は無かった。",
        1,
    )
    assert not _plan_format.check_plan_structure(content), _plan_format.check_plan_structure(content)


@pytest.mark.parametrize(
    "table",
    [
        "| 項目 | 内容 |\n| --- | --- |\n| 母集団 | 全体。 |",
        "| 項目 | 内容 |\n| - | - |\n| 母集団 | 全体。 |",
        "| 項目 | 内容 |\n|:-:|:-:|\n| 母集団 | 全体。 |",
        "項目 | 内容\n--- | ---\n母集団 | 全体。",
    ],
)
def test_extract_tables_accepts_gfm_notations(table: str) -> None:
    """区切り行のダッシュ数、整列コロン、行頭パイプ省略の各記法を表として抽出する。"""
    lines: list[tuple[int, str]] = list(enumerate(table.splitlines(), start=1))
    assert _plan_format.extract_tables(lines) == [_plan_format.MarkdownTable(1, ("項目", "内容"), (("母集団", "全体。"),))]


def test_structured_material_ids_preserve_full_namespace() -> None:
    """英字、ハイフン及びアンダースコアを含む素材IDを要求IDの名前空間へ保持する。"""
    content = _VALID_CONTENT.replace("P-001", "P-alpha_1-x")
    content = content.replace("P-alpha_1-x, P-002", "P-002, P-alpha_1-x")
    first = (
        "| R-P-alpha_1-x-001 | P-002, P-alpha_1-x | 診断件数を2件から1件へ減らす。 | "
        "採用 | 診断件数の更新 | 非該当 | 指示と合意を反映するため。 |"
    )
    rejected = (
        "| R-P-alpha_1-x-002 | P-alpha_1-x | 対象外の検査を追加しない。 | 不採用 | 非該当 | "
        "対象外の検査 | 実装上不要であるため。 |"
    )
    second = "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |"
    content = content.replace(f"{first}\n{rejected}\n{second}", f"{second}\n{first}\n{rejected}", 1)
    materials, errors = _plan_format.parse_plan_materials(content)
    assert not errors
    assert materials is not None
    assert materials.material_ids == frozenset({"P-alpha_1-x", "P-002"})
    assert materials.requirement_ids == frozenset({"R-P-alpha_1-x-001", "R-P-alpha_1-x-002", "R-P-002-001"})
    assert materials.adopted_requirement_ids == frozenset({"R-P-alpha_1-x-001", "R-P-002-001"})


def test_requirement_coverage_accepts_content_where_every_adopted_requirement_is_referenced() -> None:
    """採用要求が`根拠`又は合意表の`素材・要求参照`のいずれかで被覆されていれば検出しない。"""
    errors = _plan_format.check_plan_structure(_VALID_CONTENT)
    assert not any("採用要求を被覆しない" in error for error in errors), errors


def test_requirement_coverage_rejects_adopted_requirement_referenced_by_neither_action_nor_exclusion() -> None:
    """採用要求が`根拠`にも合意表の`素材・要求参照`にも現れない場合を検出する。"""
    content = _VALID_CONTENT.replace("P-002, R-P-002-001", "P-001, R-P-001-002")
    errors = _plan_format.check_plan_structure(content)
    assert any("採用要求を被覆しない: R-P-002-001" in error for error in errors), errors


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
            "| P-002 | 利用者合意 | queue.md | 本セッション | 全文 |",
            "キューIDは非該当にする",
        ),
        (
            "| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |",
            "| P-001 | フィードバック | 非該当 | 値なし | 本文全文 |",
            "フィードバック素材のキューIDが不正である",
        ),
        (
            "| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |",
            "| P-001 | フィードバック | feedback.md | 値なし | 本文全文 |",
            "フィードバック素材のキューIDが不正である",
        ),
        (
            "| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |",
            "| P-001 | フィードバック | 20260817-223603-001.md | 値なし | 要約 |",
            "引用範囲は本文全文にする",
        ),
        (
            "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
            "| P-002 | 利用者合意 | 非該当 | AskUserQuestion | 全文 |",
            "回答全文にする",
        ),
        (
            "R-P-002-001 | P-002 |",
            "R-P-002-000 | P-002 |",
            "末尾連番が001から欠番なく続かない",
        ),
        (
            "R-P-002-001 | P-002 |",
            "R-P-999-001 | P-002 |",
            "素材表に無い",
        ),
        (
            "R-P-001-001 | P-001, P-002 |",
            "R-P-001-001 | P-002, P-001 |",
            "素材参照はID昇順で並べる",
        ),
        (
            "R-P-001-001 | P-001, P-002 |",
            "R-P-001-001 | P-001, P-001 |",
            "素材参照が重複している",
        ),
        (
            "R-P-001-001 | P-001, P-002 |",
            "R-P-001-001 | P-001/P-002 |",
            "素材参照は`P-001, P-002`形式で記載する",
        ),
        (
            "R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | 診断件数の更新 | 非該当 |",
            "R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 保留 | 診断件数の更新 | 非該当 |",
            "採否は採用又は不採用にする",
        ),
        (
            "R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | 診断件数の更新 | 非該当 |",
            "R-P-001-001 | P-001, P-002 | 診断件数を2件から1件へ減らす。 | 採用 | 非該当 | 非該当 |",
            "採用範囲又は除外範囲が不正である",
        ),
    ],
)
def test_structured_material_contract_rejects_invalid_combinations(old: str, new: str, message: str) -> None:
    """素材種別、参照、要求ID及び採否の不整合を拒否する。"""
    materials, errors = _plan_format.parse_plan_materials(_VALID_CONTENT.replace(old, new, 1))
    assert materials is not None
    assert any(message in error for error in errors), errors


def test_main_structure_requires_judgment_for_canonical_headings() -> None:
    """新しい固定H2を使う計画では判断節を省略できない。"""
    content = _VALID_MAIN_CONTENT.replace("## 変更履歴", "## 変更履歴（計画時）", 1).replace(
        "## 進捗ログ", "## 進捗ログ（実行時）", 1
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any(f"`## {_plan_format.PLAN_H2_AGENT_JUDGMENT}`が無い" in error for error in errors), errors


@pytest.mark.parametrize("review_id", ["C-002", "H-005", "R1-planreview", "R2-planconformance", "R1-plan", "R5-review"])
def test_legacy_two_file_main_accepts_legacy_review_ids_and_tracks(review_id: str) -> None:
    """旧二ファイル計画に残るレビューIDと系統名を読み取り互換で受理する。"""
    content = _VALID_MAIN_CONTENT.replace(
        _plan_fixture.HISTORY_USER_ROW,
        f"| {review_id} | レビュー指摘 | 主要な指摘。 | 1件を採用した。 | `## 実施内容` |",
        1,
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert not any("レビュー指摘行の`ID`" in error for error in errors), errors


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("| U-001 | 診断件数を更新する | なし | 1 |", "| unit-1 | 診断件数を更新する | なし | 1 |", "U-[0-9]{3}"),
        (
            "| U-001 | 診断件数を更新する | なし | 1 |",
            "| U-002 | 診断件数を更新する | なし | 1 |",
            "U-001`から欠番なく",
        ),
        ("| U-001 | 診断件数を更新する | なし | 1 |", "| U-001 | 診断件数を更新する | U-999 | 1 |", "実装単位表に無い"),
        ("| U-001 | 診断件数を更新する | なし | 1 |", "| U-001 | 診断件数を更新する | なし | 2 |", "1から欠番なく"),
    ],
)
def test_detail_structure_rejects_invalid_implementation_unit_contract(old: str, new: str, message: str) -> None:
    """実装単位ID、依存及び統合順の構造違反を拒否する。"""
    errors = _plan_format.check_plan_detail_structure(_VALID_DETAIL_CONTENT.replace(old, new), "通常変更")
    assert any(message in error for error in errors), errors


def test_main_structure_rejects_missing_verification_table() -> None:
    """メイン側の`## 検証区分`は固定2行2列表にする。"""
    content = _VALID_MAIN_CONTENT.replace(
        _plan_fixture.VERIFICATION_TABLE,
        "",
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any(f"`## {_plan_format.PLAN_H2_VERIFICATION}`は" in error for error in errors), errors


def test_detail_structure_requires_bug_section_for_bug_work_type() -> None:
    """detail側は作業種別が`バグ対応`の場合だけ`## バグ調査結果`を先頭へ要求する。"""
    errors = _plan_format.check_plan_detail_structure(_VALID_DETAIL_CONTENT, "バグ対応")
    assert any(f"固定H2`## {_plan_format.PLAN_H2_BUG}`は1件必要" in error for error in errors), errors


@pytest.mark.parametrize(
    "body",
    [
        _wi_source(source=False),
        _wi_source(source=True, trailing_user_comment=True),
        _wi_source(source=True, answer=True),
    ],
)
def test_origin_check_accepts_human_origin(tmp_path: pathlib.Path, body: str) -> None:
    """`source`の欠落と機械判定できる明示由来を持つ正本は指摘の対象にしない。"""
    _write_wi(tmp_path, body)
    errors, notices, skips = _origin_check(tmp_path)
    assert not errors, errors
    assert not notices, notices
    assert not skips, skips
