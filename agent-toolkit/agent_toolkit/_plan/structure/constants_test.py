import pathlib

import pytest

from agent_toolkit._plan import structure as _plan_format
from agent_toolkit._plan.structure import constants as plan_constants
from agent_toolkit._plan.structure import markdown as plan_markdown
from agent_toolkit._plan.structure import materials as plan_materials
from agent_toolkit._plan.structure import sections as plan_sections
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.plan_structure_support import (
    _BUG_CONTENT,
    _BUG_SECTION,
    _HUMAN_DETAIL_CONTENT,
    _HUMAN_MAIN_CONTENT,
    _HUMAN_PARTIAL_ROW,
    _LEGACY_CONTENT,
    _VALID_CONTENT,
    _VALID_DETAIL_CONTENT,
    _VALID_MAIN_CONTENT,
    _canonical_main_content,
    _origin_check,
)

pytestmark = pytest.mark.windows


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
    assert plan_constants.plan_human_review_path_is_absolute(path) is expected


def test_canonical_plan_passes_structure_check() -> None:
    assert not plan_sections.check_plan_structure(_VALID_CONTENT)
    assert not plan_sections.check_plan_structure(_BUG_CONTENT)


def test_human_readable_history_requires_canonical_user_heading() -> None:
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_EVENT_HEADING, "### ユーザーからの確認", 1)
    errors = plan_sections.check_plan_main_structure(content)[1]
    assert any(f"`### {_plan_format.PLAN_HISTORY_USER_EVENT_PREFIX}1`見出し" in error for error in errors), errors


def test_human_readable_history_accepts_sequential_user_events() -> None:
    second = f"### {_plan_format.PLAN_HISTORY_USER_EVENT_PREFIX}2\n\n```text\n追加の指示。\n```\n\n"
    content = _HUMAN_MAIN_CONTENT.replace(
        f"{_plan_fixture.HISTORY_REVIEW_HEADING}\n", f"{second}{_plan_fixture.HISTORY_REVIEW_HEADING}\n", 1
    )
    assert not plan_sections.check_plan_main_structure(content)[1]


@pytest.mark.parametrize("decision", [value for value in plan_constants.PLAN_ACTION_DECISIONS if value != "採用"])
def test_human_readable_review_origin_rejects_missing_non_adopted_reason(tmp_path: pathlib.Path, decision: str) -> None:
    review_path = tmp_path / "review.tsv"
    review_path.write_text("2\tplan-review\t指摘\n", encoding="utf-8")
    content = _HUMAN_MAIN_CONTENT.replace(
        _HUMAN_PARTIAL_ROW,
        f"| 入力の境界を追加確認する | 計画レビュー第2ラウンド | {decision} | {review_path.as_posix()}のround 2 |",
        1,
    )
    errors = plan_sections.check_plan_main_structure(content)[1]
    assert any("TSV参照に続けて理由" in error for error in errors), errors


def test_agent_wi_non_adopted_action_requires_reason() -> None:
    row = f"| 入力の境界を追加確認する | エージェント由来のWI ({_plan_fixture.WI_FILES[0][0]}) | 部分採用 | - |"
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.WI_ACTION_ROW, row, 1)
    errors = plan_sections.check_plan_main_structure(content)[1]
    assert any("採用以外の`根拠`" in error for error in errors), errors


def test_human_readable_action_rejects_wi_missing_from_metadata() -> None:
    content = _HUMAN_MAIN_CONTENT.replace("(20260817-223603-001.md)", "(20260817-223603-999.md)", 1)
    errors = plan_sections.check_plan_main_structure(content)[1]
    assert any("WI由来が`関連WI`に無い" in error for error in errors), errors


def test_human_readable_main_accepts_legacy_wi_names() -> None:
    content = _plan_fixture.legacy_wi_names(_HUMAN_MAIN_CONTENT)
    work_type, errors = plan_sections.check_plan_main_structure(content)
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
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_ACTION_SUBJECT, "MCP-toolとTLSのP-256を維持する", 1)
    content = content.replace(_plan_fixture.USER_EVENT_TEXT, "P-001という入力を変更しない。", 1)
    assert not plan_sections.check_plan_main_structure(content)[1]


@pytest.mark.parametrize(
    "internal_id",
    ["P-001", "P-100", "P-999", "P-1000", "U-001", "R-P-001-001", "H-001", "C-001", "R1-plan"],
)
def test_human_readable_main_rejects_internal_identifiers_before_japanese(internal_id: str) -> None:
    content = _HUMAN_MAIN_CONTENT.replace(
        _plan_fixture.USER_ACTION_SUBJECT,
        f"{internal_id}を実装する",
        1,
    )
    errors = plan_sections.check_plan_main_structure(content)[1]
    assert any("`## 実施内容`へ素材・要求・履歴・実装単位の合成ID" in error for error in errors), errors


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("| H-001 | ユーザー発言 | P-001 |", "| H-001 | 実装経過 | P-001 |"), "`起点`は"),
        (("| H-001 | ユーザー発言 | P-001 |", "| H-001 | ユーザー発言 | 要約 |"), "素材IDだけを書く"),
        (("| H-001 | ユーザー発言 | P-001 |", "| H-001 | ユーザー発言 | P-999 |"), "素材IDが提示素材に無い"),
    ],
)
def test_history_origin_and_user_material_reference_are_checked(mutation: tuple[str, str], message: str) -> None:
    errors = plan_sections.check_plan_structure(_VALID_CONTENT.replace(*mutation, 1))
    assert any(message in error for error in errors), errors


def test_legacy_bug_table_predicate_requires_valid_record() -> None:
    content = _BUG_CONTENT
    assert plan_sections.has_legacy_bug_table(content)
    invalid = content.replace(f"{_plan_fixture.cause_record_line('類似見直し')}\n", "")
    assert not plan_sections.has_legacy_bug_table(invalid)


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
        (("### 計画メタ情報", "### 総論"), "`### 計画メタ情報`の構造を判定できない"),
        (("## 提示素材", "## 素材"), "固定H2は"),
        (("- 起動経路: `agent-toolkit:write-plan`\n", ""), "この順序で1行ずつ置く"),
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
        (
            (f"| {' | '.join(_plan_format.PLAN_REFACTORING_TABLE_HEADER)} |", "| 項目 | 内容 |"),
            "3列表を1件置く",
        ),
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
    content = _VALID_CONTENT.replace(*mutation, 1)
    errors = plan_sections.check_plan_structure(content)
    assert any(message in error for error in errors), errors


def test_structured_material_contract_rejects_requirement_order_and_gap() -> None:
    content = _VALID_CONTENT.replace("R-P-002-001 | P-002 |", "R-P-002-003 | P-002 |", 1)
    _materials, errors = plan_materials.parse_plan_materials(content)
    assert any("末尾連番が001から欠番なく続かない" in error for error in errors), errors


def test_structured_material_contract_requires_adjacent_tables() -> None:
    content = _VALID_CONTENT.replace(
        "\n| 要求ID | 素材参照 |",
        "\n説明文を配置する。\n\n| 要求ID | 素材参照 |",
        1,
    )
    _materials, errors = plan_materials.parse_plan_materials(content)
    assert any("素材表の直後に要求表" in error for error in errors), errors


@pytest.mark.parametrize("material_id", ["P-001（ユーザー発言）:", "P-001: ユーザー発言"])
def test_material_id_annotation_is_rejected_near_the_invalid_line(material_id: str) -> None:
    content = _LEGACY_CONTENT.replace("P-001:", material_id, 1)

    errors = plan_sections.check_plan_structure(content)

    assert f"提示素材の素材ID行に注記を含めない: {material_id}" in errors


def test_bug_section_rejects_missing_record_line() -> None:
    content = _BUG_CONTENT.replace(f"{_plan_fixture.cause_record_line('類似見直し')}\n", "")
    errors = plan_sections.check_plan_structure(content)
    assert any("`類似見直し:`の行が無い" in error for error in errors), errors


def test_bug_section_is_required_only_for_bug_work_type() -> None:
    missing = _BUG_CONTENT.replace(_BUG_SECTION, "\n")
    assert any("固定H2" in error for error in plan_sections.check_plan_structure(missing))
    extra = _VALID_CONTENT.replace("\n## 恒久化・リファクタリング内容", f"{_BUG_SECTION}\n## 恒久化・リファクタリング内容")
    assert any("作業種別が`通常変更`" in error for error in plan_sections.check_plan_structure(extra))


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
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert metadata is None
    assert errors


def test_duplicate_headings_accepts_same_text_under_different_parents() -> None:
    content = "# 計画\n\n## 親A\n\n### 子\n\n## 親B\n\n### 子\n"

    assert not plan_markdown.check_duplicate_headings(content)


@pytest.mark.parametrize("track", plan_constants.PLAN_HISTORY_TRACK_VALUES)
def test_main_history_accepts_review_table_track_values(track: str) -> None:
    content = _canonical_main_content().replace(
        _plan_fixture.HISTORY_USER_ROW,
        f"| R1-{track} | レビュー指摘 | 主要な指摘。 | 1件を採用した。 | `## 実施内容` |",
        1,
    )
    _work_type, errors = plan_sections.check_plan_main_structure(content)
    assert not errors, errors


@pytest.mark.parametrize("review_id", ["C-002", "R1-planreview", "R2-planconformance"])
def test_new_main_rejects_legacy_history_review_identifier(review_id: str) -> None:
    content = _canonical_main_content().replace(
        _plan_fixture.HISTORY_USER_ROW,
        f"| {review_id} | レビュー指摘 | 主要な指摘。 | 1件を採用した。 | `## 実施内容` |",
        1,
    )
    _work_type, errors = plan_sections.check_plan_main_structure(content)
    assert any("レビュー指摘行の`ID`は`R<正の整数>-<系統名>`形式にする" in error for error in errors), errors


def test_human_detail_structure_rejects_ascii_comma_in_unit_name() -> None:
    content = _HUMAN_DETAIL_CONTENT.replace("| 契約境界の更新 |", "| 契約境界,更新 |", 1)
    errors = plan_sections.check_plan_detail_structure(content, "通常変更")
    assert any("実装単位名へASCIIカンマを含めない" in error for error in errors), errors


def test_main_structure_requires_detail_metadata_field() -> None:
    content = _VALID_MAIN_CONTENT.replace(f"- {_plan_format.PLAN_METADATA_DETAIL_FIELD}: `sample.detail.md`\n", "")
    _work_type, errors = plan_sections.check_plan_main_structure(content)
    assert any("この順序で1行ずつ置く" in error for error in errors), errors


def test_main_structure_rejects_empty_termination_section() -> None:
    content = _VALID_MAIN_CONTENT.replace("## 公開工程\n\nなし\n", "## 公開工程\n\n")
    _work_type, errors = plan_sections.check_plan_main_structure(content)
    assert any(f"`## {_plan_format.PLAN_H2_TERMINATION}`は" in error for error in errors), errors


def test_detail_structure_permanence_rejects_free_h3() -> None:
    content = _VALID_DETAIL_CONTENT.replace("\n## 実装資料", "\n### 任意の補足\n\n補足する。\n\n## 実装資料")
    errors = plan_sections.check_plan_detail_structure(content, "通常変更")
    assert any("固定見出し以外のH3" in error for error in errors), errors


def test_origin_check_needs_no_queue_repository(tmp_path: pathlib.Path) -> None:
    absent = tmp_path / "absent"
    errors, notices, skips = _origin_check(absent)
    assert not errors, errors
    assert not notices, notices
    assert not skips, skips
    assert errors == plan_sections.check_plan_main_structure(_HUMAN_MAIN_CONTENT)[1]
