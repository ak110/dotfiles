# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F403,F405,I001
# pylint: disable=function-redefined,pointless-string-statement,undefined-variable,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order
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


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/home/a/x.tsv", True),
        (r"C:\dir\x.tsv", True),
        ("C:/dir/x.tsv", True),
        ("//?/C:/dir/x.tsv", True),
        ("C:x.tsv", False),
        ("rel/x.tsv", False),
    ],
)
def test_plan_human_review_path_is_absolute(path: str, expected: bool) -> None:
    """POSIXとWindowsの絶対パスをOSによらず受理する。"""
    assert _plan_format.plan_human_review_path_is_absolute(path) is expected


def test_canonical_plan_passes_structure_check() -> None:
    """通常変更とバグ対応の正規形はいずれも構造検査を通過する。"""
    assert not _plan_format.check_plan_structure(_VALID_CONTENT)
    assert not _plan_format.check_plan_structure(_BUG_CONTENT)


def test_human_readable_history_requires_canonical_user_heading() -> None:
    """新規書式の直接入力は連番のユーザー発言見出しと空でない逐語本文を持つ。"""
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_EVENT_HEADING, "### 利用者からの確認", 1)
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any(f"`### {_plan_format.PLAN_HISTORY_USER_EVENT_PREFIX}1`見出し" in error for error in errors), errors


def test_human_readable_history_accepts_sequential_user_events() -> None:
    """連番のユーザー発言見出しを複数置いた変更履歴を受理する。"""
    second = f"### {_plan_format.PLAN_HISTORY_USER_EVENT_PREFIX}2\n\n```text\n追加の指示。\n```\n\n"
    content = _HUMAN_MAIN_CONTENT.replace(
        f"{_plan_fixture.HISTORY_REVIEW_HEADING}\n", f"{second}{_plan_fixture.HISTORY_REVIEW_HEADING}\n", 1
    )
    assert not _plan_format.check_plan_main_structure(content)[1]


@pytest.mark.parametrize("decision", [value for value in _plan_format.PLAN_ACTION_DECISIONS if value != "採用"])
def test_human_readable_review_origin_rejects_missing_non_adopted_reason(tmp_path: pathlib.Path, decision: str) -> None:
    """計画レビュー由来の採用以外はTSV参照だけの根拠を拒否する。"""
    review_path = tmp_path / "review.tsv"
    review_path.write_text("2\tplan-review\t指摘\n", encoding="utf-8")
    content = _HUMAN_MAIN_CONTENT.replace(
        _HUMAN_PARTIAL_ROW,
        f"| 入力の境界を追加確認する | 計画レビュー第2ラウンド | {decision} | {review_path.as_posix()}のround 2 |",
        1,
    )
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("TSV参照に続けて理由" in error for error in errors), errors


def test_agent_wi_non_adopted_action_requires_reason() -> None:
    """エージェント由来のWIの採用以外は自足した根拠を必要とする。"""
    row = f"| 入力の境界を追加確認する | エージェント由来のWI ({_plan_fixture.WI_FILES[0][0]}) | 部分採用 | - |"
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.WI_ACTION_ROW, row, 1)
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("採用以外の`根拠`" in error for error in errors), errors


def test_human_readable_action_rejects_wi_missing_from_metadata() -> None:
    """WI由来の正本は`関連WI`から逆照合できる。"""
    content = _HUMAN_MAIN_CONTENT.replace("(20260817-223603-001.md)", "(20260817-223603-999.md)", 1)
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("WI由来が`関連WI`に無い" in error for error in errors), errors


def test_human_readable_main_accepts_legacy_wi_names() -> None:
    """改名前の項目名と`由来`欄を持つ計画を読み取り経路で受理する。"""
    content = _plan_fixture.legacy_wi_names(_HUMAN_MAIN_CONTENT)
    work_type, errors = _plan_format.check_plan_main_structure(content)
    assert work_type == "通常変更"
    assert not errors, errors
    metadata, metadata_errors = _plan_format.parse_plan_metadata(content)
    assert not metadata_errors, metadata_errors
    assert metadata is not None
    assert _plan_format.PLAN_METADATA_RELATED_WI_FIELD in metadata.values
    assert tuple(filename for filename, _summary in metadata.related_wi) == tuple(
        filename for filename, _summary in _plan_fixture.WI_FILES
    )


def test_human_readable_main_accepts_external_identifiers_and_verbatim_ids() -> None:
    """通常の外部識別子と利用者発言の逐語文は合成IDとして誤拒否しない。"""
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_ACTION_SUBJECT, "MCP-toolとTLSのP-256を維持する", 1)
    content = content.replace(_plan_fixture.USER_EVENT_TEXT, "P-001という入力を変更しない。", 1)
    assert not _plan_format.check_plan_main_structure(content)[1]


@pytest.mark.parametrize(
    "internal_id",
    ["P-001", "P-100", "P-999", "P-1000", "U-001", "R-P-001-001", "H-001", "C-001", "R1-plan"],
)
def test_human_readable_main_rejects_internal_identifiers_before_japanese(internal_id: str) -> None:
    """日本語の助詞が続く場合も既存の合成IDを検出する。"""
    content = _HUMAN_MAIN_CONTENT.replace(
        _plan_fixture.USER_ACTION_SUBJECT,
        f"{internal_id}を実装する",
        1,
    )
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("`## 実施内容`へ素材・要求・履歴・実装単位の合成ID" in error for error in errors), errors


def test_bug_file_structure_rejects_cause_table_after_investigation_table() -> None:
    """原因分析表が調査表の後にある新形式のバグ単位を拒否する。"""
    content = _BUG_FILE_CONTENT.replace(f"{_BUG_CAUSE_TABLE}\n\n", "") + f"\n{_BUG_CAUSE_TABLE}\n"
    errors = _plan_format.check_bug_file_structure(content)
    assert any("原因分析表を調査表より前に置く" in error for error in errors), errors


def test_bug_file_structure_rejects_additional_table() -> None:
    """正規の2表に追加表が置かれた場合を拒否する。"""
    extra_table = "| 補足 | 内容 |\n| --- | --- |\n| 任意 | 値 |"
    assert _plan_format.check_bug_file_structure(f"{_BUG_FILE_CONTENT}\n\n{extra_table}")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("| H-001 | ユーザー発言 | P-001 |", "| H-001 | 実装経過 | P-001 |"), "`起点`は"),
        (("| H-001 | ユーザー発言 | P-001 |", "| H-001 | ユーザー発言 | 要約 |"), "素材IDだけを書く"),
        (("| H-001 | ユーザー発言 | P-001 |", "| H-001 | ユーザー発言 | P-999 |"), "素材IDが提示素材に無い"),
    ],
)
def test_history_origin_and_user_material_reference_are_checked(mutation: tuple[str, str], message: str) -> None:
    """変更履歴の起点固定値とユーザー発言の素材ID参照を検査する。"""
    errors = _plan_format.check_plan_structure(_VALID_CONTENT.replace(*mutation, 1))
    assert any(message in error for error in errors), errors


def test_legacy_bug_table_predicate_requires_valid_fixed_table() -> None:
    """バグ調査表は固定行の調査表を満たす場合だけ検出する。"""
    content = _BUG_CONTENT
    assert _plan_format.has_legacy_bug_table(content)
    invalid = content.replace(f"{_plan_fixture.bug_row(_plan_format.PLAN_BUG_TABLE_ROWS[0])}\n", "")
    assert not _plan_format.has_legacy_bug_table(invalid)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("# 計画の主題\n\n", ""), "ATX H1が1件必要"),
        (("成果を得る。\n\n### 計画メタ情報", "### 計画メタ情報"), "直下の地の文"),
        (("## 変更履歴", "## 追加のH2\n\n補足。\n\n## 変更履歴"), "固定H2は"),
        (
            (
                "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n",
                "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n\n## 後書き\n\n補足。\n",
            ),
            "固定H2は",
        ),
        (("### 計画メタ情報", "### 総論"), "`### 計画メタ情報`を検査できない"),
        (("## 提示素材", "## 素材"), "固定H2は"),
        (("- 起動経路: `agent-toolkit:plan-mode`\n", ""), "この順序で1行ずつ置く"),
        (("- 作業種別: 通常変更", "- 作業種別: `通常変更`"), "バッククォートで囲まない"),
        (("- 対象リポジトリ: `/repo`", "- 対象リポジトリ: /repo"), "バッククォートで囲む"),
        (("- 作業種別: 通常変更", "- 作業種別: 改善"), "`作業種別`は"),
        ((f"{_plan_fixture.HISTORY_USER_ROW}\n", ""), "1行以上の内容が必要"),
        (
            ("| ID | 起点 | 指摘内容 | 採否・現在の結論 | 同期先 |", "| ID | 起点 | 指摘内容 | 結論 | 同期先 |"),
            "`## 変更履歴`は",
        ),
        (("| 日時 | 完了した工程 | 結果・特記事項 |", "| 日時 | 工程 | 結果 |"), "`## 進捗ログ`は"),
        (
            (
                _plan_fixture.EXCLUSION_ROWS[0],
                "| 公開契約を維持する | 対象の公開API | P-999 | 差分を確認する |",
            ),
            "素材・要求参照が提示素材に無い",
        ),
        (
            (
                _plan_fixture.TWO_FILE_ACTION_ROW,
                "| 診断件数を2件から1件へ減らす | 採用 | 追加対応 | R-P-001-001 |",
            ),
            "`ユーザー指示との関係`は",
        ),
        (
            (
                _plan_fixture.EXCLUSION_ROWS[0],
                "| 公開契約を維持する |  | P-002, R-P-002-001 | 差分を確認する |",
            ),
            "空cell",
        ),
        (("### 類似見直し\n", "### 追加見直し\n"), "固定見出し以外のH3は置かない"),
        ((f"| {' | '.join(_plan_format.PLAN_PERMANENCE_TABLE_HEADER)} |", "| 項目 | 内容 |"), "4列表を置く"),
        (
            (f"{_plan_fixture.PERMANENCE_ROW}\n", ""),
            "表に1行以上の内容が必要",
        ),
        ((f"{_plan_fixture.item_row('現状の問題')}\n", ""), "4行表を置く"),
        (("## 実装資料", "## 変更対象"), "固定H2は"),
        (
            (
                _plan_fixture.COMPLETION_BODY,
                "基準値は診断2件、目標は1件とし、CLIを再実行して標準エラーの行数を測定する。\n\n#### 自由なH4",
            ),
            "H4以深の見出しは置かない",
        ),
    ],
)
def test_structure_violations_are_rejected(mutation: tuple[str, str], message: str) -> None:
    """固定領域の欠落、順序違反、追加H2、表違反を個別に拒否する。"""
    content = _VALID_CONTENT.replace(*mutation, 1)
    errors = _plan_format.check_plan_structure(content)
    assert any(message in error for error in errors), errors


def test_structured_material_contract_rejects_requirement_order_and_gap() -> None:
    """要求表のID順序と素材内連番の欠落を拒否する。"""
    content = _VALID_CONTENT.replace("R-P-002-001 | P-002 |", "R-P-002-003 | P-002 |", 1)
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("末尾連番が001から欠番なく続かない" in error for error in errors), errors


def test_structured_material_contract_requires_adjacent_tables() -> None:
    """素材表と要求表の間に説明文又は別表を置かない。"""
    content = _VALID_CONTENT.replace(
        "\n| 要求ID | 素材参照 |",
        "\n説明文を配置する。\n\n| 要求ID | 素材参照 |",
        1,
    )
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("素材表の直後に要求表" in error for error in errors), errors


@pytest.mark.parametrize("material_id", ["P-001（利用者発言）:", "P-001: 利用者発言"])
def test_material_id_annotation_is_rejected_near_the_invalid_line(material_id: str) -> None:
    content = _LEGACY_CONTENT.replace("P-001:", material_id, 1)

    errors = _plan_format.check_plan_structure(content)

    assert f"提示素材の素材ID行に注記を含めない: {material_id}" in errors


def test_bug_section_requires_fixed_twelve_rows() -> None:
    """バグ調査表の12行から1行を削除した計画を拒否する。"""
    content = _BUG_CONTENT.replace(f"{_plan_fixture.bug_row(_plan_format.PLAN_BUG_TABLE_ROWS[0])}\n", "")
    errors = _plan_format.check_plan_structure(content)
    assert any(f"固定{len(_plan_format.PLAN_BUG_TABLE_ROWS)}行の調査表" in error for error in errors)


def test_bug_section_is_required_only_for_bug_work_type() -> None:
    """バグ対応でのみバグ調査結果を要求し、通常変更では置かせない。"""
    missing = _BUG_CONTENT.replace(_BUG_SECTION, "\n")
    assert any("固定H2" in error for error in _plan_format.check_plan_structure(missing))
    extra = _VALID_CONTENT.replace("\n## 恒久化・リファクタリング内容", f"{_BUG_SECTION}\n## 恒久化・リファクタリング内容")
    assert any("作業種別が`通常変更`" in error for error in _plan_format.check_plan_structure(extra))


@pytest.mark.parametrize(
    "content",
    [
        f"## 実装契約\n\n### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`\n\n"
        f"### 計画メタ情報\n\n- ベースコミット: `{'b' * 40}`\n",
        f"## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`\n\n"
        f"## 実装契約\n\n### 計画メタ情報\n\n- ベースコミット: `{'b' * 40}`\n",
    ],
)
def test_metadata_rejects_ambiguous_placement(content: str) -> None:
    """旧配置の候補が複数ある計画は曖昧として解析結果を返さない。"""
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert metadata is None
    assert errors


def test_duplicate_headings_accepts_same_text_under_different_parents() -> None:
    """異なる親の下にある同じ文言の見出しは受理する。"""
    content = "# 計画\n\n## 親A\n\n### 子\n\n## 親B\n\n### 子\n"

    assert not _plan_format.check_duplicate_headings(content)


@pytest.mark.parametrize(
    "expected",
    [
        *(f"`## {name}`" for name in _plan_format.PLAN_MAIN_H2_ORDER),
        *(f"`## {name}`" for name in _plan_format.PLAN_DETAIL_H2_ORDER),
        *(f"`### {name}`" for name in _plan_format.PLAN_PERMANENCE_H3),
        f"`### {_plan_format.PLAN_HISTORY_USER_EVENT_PREFIX}<1から始まる連番>`",
    ],
)
def test_plan_file_standards_states_every_structure_constant(expected: str) -> None:
    """構造検査が用いる見出し名を計画ファイル基準の本文が明記する。

    実装だけが要件を持つ状態を避け、構造定数を改訂した場合に正本の追随漏れを検出する。
    """
    assert expected in _PLAN_FILE_STANDARDS.read_text(encoding="utf-8")


@pytest.mark.parametrize("track", _plan_format.PLAN_HISTORY_TRACK_VALUES)
def test_main_history_accepts_review_table_track_values(track: str) -> None:
    """新形式の変更履歴はレビュー表の正規trackを受理する。"""
    content = _canonical_main_content().replace(
        _plan_fixture.HISTORY_USER_ROW,
        f"| R1-{track} | レビュー指摘 | 主要な指摘。 | 1件を採用した。 | `## 実施内容` |",
        1,
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert not errors, errors


@pytest.mark.parametrize("review_id", ["C-002", "R1-planreview", "R2-planconformance"])
def test_new_main_rejects_legacy_history_review_identifier(review_id: str) -> None:
    """新書式のメイン側では旧形式のレビュー指摘IDを拒否する。"""
    content = _canonical_main_content().replace(
        _plan_fixture.HISTORY_USER_ROW,
        f"| {review_id} | レビュー指摘 | 主要な指摘。 | 1件を採用した。 | `## 実施内容` |",
        1,
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any("レビュー指摘行の`ID`は`R<正の整数>-<系統名>`形式にする" in error for error in errors), errors


def test_human_detail_structure_rejects_ascii_comma_in_unit_name() -> None:
    """説明的な実装単位名へASCIIカンマを含めない。"""
    content = _HUMAN_DETAIL_CONTENT.replace("| 契約境界の更新 |", "| 契約境界,更新 |", 1)
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any("実装単位名へASCIIカンマを含めない" in error for error in errors), errors


def test_main_structure_requires_detail_metadata_field() -> None:
    """計画ファイル（メイン）の計画メタ情報は詳細参照を末尾へ含む5項目にする。"""
    content = _VALID_MAIN_CONTENT.replace(f"- {_plan_format.PLAN_METADATA_DETAIL_FIELD}: `sample.detail.md`\n", "")
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any("この順序で1行ずつ置く" in error for error in errors), errors


def test_main_structure_rejects_empty_termination_section() -> None:
    """`## 終端工程`は空欄を拒否する（無い場合は`なし`と書く）。"""
    content = _VALID_MAIN_CONTENT.replace("## 終端工程\n\nなし\n", "## 終端工程\n\n")
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any(f"`## {_plan_format.PLAN_H2_TERMINATION}`は" in error for error in errors), errors


def test_detail_structure_permanence_rejects_free_h3() -> None:
    """detail側の恒久化領域でも固定3見出し以外のH3を拒否する。"""
    content = _VALID_DETAIL_CONTENT.replace("\n## 実装資料", "\n### 任意の補足\n\n補足する。\n\n## 実装資料")
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any("固定見出し以外のH3" in error for error in errors), errors


def test_origin_check_skips_when_queue_repository_is_absent(tmp_path: pathlib.Path) -> None:
    """キュー管理リポジトリのルートが実在しない環境では照合だけを省略する。"""
    absent = tmp_path / "absent"
    errors, notices, skips = _origin_check(absent)
    assert not errors, errors
    assert not notices, notices
    assert any(str(absent) in skip for skip in skips), skips
    assert errors == _plan_format.check_plan_main_structure(_HUMAN_MAIN_CONTENT)[1]
