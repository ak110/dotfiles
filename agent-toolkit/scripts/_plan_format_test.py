"""計画形式の共通解析を検証する。"""

import pathlib
import sys

import pytest
from pyfltr.colloquial import check as _colloquial_check

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import _plan_fixture  # noqa: E402  # pylint: disable=wrong-import-position
import _plan_format  # noqa: E402  # pylint: disable=wrong-import-position

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

_HUMAN_MAIN_CONTENT = _plan_fixture.human_main(related_feedback=_plan_fixture.FEEDBACK_FILES)
_HUMAN_DETAIL_CONTENT = _plan_fixture.human_detail()
_HUMAN_PARTIAL_ROW = _plan_fixture.FEEDBACK_ACTION_ROW
_HUMAN_PARTIAL_REASON = _plan_fixture.FEEDBACK_ACTION_REASON

_VALID_MAIN_CONTENT = _plan_fixture.two_file_main()
_VALID_DETAIL_CONTENT = _plan_fixture.two_file_detail()


def test_canonical_plan_passes_structure_check() -> None:
    """通常変更とバグ対応の正規形はいずれも構造検査を通過する。"""
    assert not _plan_format.check_plan_structure(_VALID_CONTENT)
    assert not _plan_format.check_plan_structure(_BUG_CONTENT)


def test_human_readable_main_and_detail_pass_structure_check() -> None:
    """新規の人間向け計画ファイル（メイン）と計画ファイル（詳細）がIDなしの判断・実装契約を満たす。"""
    work_type, main_errors = _plan_format.check_plan_main_structure(_HUMAN_MAIN_CONTENT)
    assert work_type == "通常変更"
    assert not main_errors, main_errors
    assert not _plan_format.check_plan_detail_structure(_HUMAN_DETAIL_CONTENT, work_type)


def test_human_readable_main_accepts_satisfied_action() -> None:
    """新書式の実施内容表は裏付けを持つ`充足済み`を受理する。"""
    source = _HUMAN_PARTIAL_ROW
    replacement = (
        "| 入力の境界を追加確認する | 人間由来のフィードバック (20260817-223603-001.md) | 充足済み | "
        "対象実装と要求を突合して充足を確認した。 |"
    )
    content = _HUMAN_MAIN_CONTENT.replace(
        source,
        replacement,
        1,
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert not errors, errors


def test_human_readable_feedback_and_units_do_not_expose_internal_ids() -> None:
    """人間向け形式は正本ファイル名と説明的な実装単位だけを解析する。"""
    metadata, metadata_errors = _plan_format.parse_plan_metadata(_HUMAN_MAIN_CONTENT)
    assert not metadata_errors, metadata_errors
    assert metadata is not None
    assert tuple(filename for filename, _summary in metadata.related_feedback) == (
        "20260817-223603-001.md",
        "20260817-223603-002.md",
    )
    units, unit_errors = _plan_format.parse_plan_implementation_units(_HUMAN_DETAIL_CONTENT)
    assert not unit_errors, unit_errors
    assert units is not None
    assert tuple(unit.unit_id for unit in units) == ("契約境界の更新", "回帰検証の追加")


def test_human_readable_action_rejects_non_adopted_empty_reason() -> None:
    """人間向け形式の採用以外の行は自足した理由を持つ。"""
    content = _HUMAN_MAIN_CONTENT.replace(
        _plan_fixture.PROPOSAL_ACTION_ROW,
        "| 類似するが対象外の記述は変更しない | エージェント提案 | 対象外 | - |",
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any("エージェント提案行" in error for error in errors), errors


def test_human_readable_history_requires_canonical_user_heading() -> None:
    """新規書式の直接入力は連番のユーザー発言見出しと空でない逐語本文を持つ。"""
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_EVENT_HEADING, "### 利用者からの確認", 1)
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any(f"`### {_plan_format.PLAN_HISTORY_USER_EVENT_PREFIX}1`見出し" in error for error in errors), errors


def test_human_readable_history_accepts_legacy_user_heading() -> None:
    """要旨を見出しへ書く旧書式のユーザー発言を読み取り互換として受理する。"""
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_EVENT_HEADING, _plan_fixture.LEGACY_USER_EVENT_HEADING, 1)
    assert not _plan_format.check_plan_main_structure(content)[1]
    assert _plan_format.has_legacy_history_user_event(content)
    assert not _plan_format.has_legacy_history_user_event(_HUMAN_MAIN_CONTENT)


@pytest.mark.parametrize("heading", ["### ユーザー発言0", "### ユーザー発言2"])
def test_human_readable_history_rejects_invalid_user_event_sequence(heading: str) -> None:
    """連番が1から欠番なく昇順に並ばないユーザー発言見出しを拒否する。"""
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_EVENT_HEADING, heading, 1)
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert errors, errors


def test_human_readable_history_accepts_sequential_user_events() -> None:
    """連番のユーザー発言見出しを複数置いた変更履歴を受理する。"""
    second = f"### {_plan_format.PLAN_HISTORY_USER_EVENT_PREFIX}2\n\n```text\n追加の指示。\n```\n\n"
    content = _HUMAN_MAIN_CONTENT.replace(
        f"{_plan_fixture.HISTORY_REVIEW_HEADING}\n", f"{second}{_plan_fixture.HISTORY_REVIEW_HEADING}\n", 1
    )
    assert not _plan_format.check_plan_main_structure(content)[1]


def test_human_readable_partial_user_instruction_counts_as_adopted() -> None:
    """ユーザー指示の部分採用も採用済みとして扱う。"""
    content = _HUMAN_MAIN_CONTENT.replace(
        _plan_fixture.USER_ACTION_ROW,
        "| 公開契約に必要な変更を実装する | ユーザー指示 | 部分採用 | - |",
        1,
    )
    assert _plan_format.has_adopted_human_user_instruction(content)


def test_human_readable_action_accepts_review_origin_with_matching_round(tmp_path: pathlib.Path) -> None:
    """計画レビュー由来の採用行は絶対パスのTSVと同じ正のラウンドを指定する。"""
    review_path = tmp_path / "review.tsv"
    review_path.write_text("2\tplan-review\t指摘\n", encoding="utf-8")
    content = _HUMAN_MAIN_CONTENT.replace(
        _HUMAN_PARTIAL_ROW,
        f"| 入力の境界を追加確認する | 計画レビュー第2ラウンド | 採用 | {review_path.as_posix()}のround 2 |",
        1,
    )
    errors = _plan_format.check_plan_main_structure(content)[1]
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


@pytest.mark.parametrize("decision", _plan_format.PLAN_ACTION_DECISIONS)
def test_human_readable_review_origin_applies_root_rule_before_decision(tmp_path: pathlib.Path, decision: str) -> None:
    """計画レビュー由来は採否より先に判定し、採用以外ではTSV参照に理由を続ける。"""
    review_path = tmp_path / "review.tsv"
    review_path.write_text("2\tplan-review\t指摘\n", encoding="utf-8")
    root = f"{review_path.as_posix()}のround 2"
    if decision != "採用":
        root += "。実施しない範囲と理由を記録する。"
    content = _HUMAN_MAIN_CONTENT.replace(
        _HUMAN_PARTIAL_ROW,
        f"| 入力の境界を追加確認する | 計画レビュー第2ラウンド | {decision} | {root} |",
        1,
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


@pytest.mark.parametrize("decision", _plan_format.PLAN_ACTION_DECISIONS)
def test_human_readable_user_origin_applies_general_decision_rule(decision: str) -> None:
    """一般由来は採用だけハイフンとし、ほかの採否では理由を要求する。"""
    root = "-" if decision == "採用" else "実施しない範囲と理由。"
    original = _plan_fixture.USER_ACTION_ROW
    accepted = _HUMAN_MAIN_CONTENT.replace(
        original,
        f"| 公開契約に必要な変更を実装する | ユーザー指示 | {decision} | {root} |",
        1,
    )
    assert not _plan_format.check_plan_main_structure(accepted)[1]

    invalid_root = "理由がある。" if decision == "採用" else "-"
    rejected = _HUMAN_MAIN_CONTENT.replace(
        original,
        f"| 公開契約に必要な変更を実装する | ユーザー指示 | {decision} | {invalid_root} |",
        1,
    )
    errors = _plan_format.check_plan_main_structure(rejected)[1]
    assert any("`根拠`" in error for error in errors), errors


def test_human_readable_action_rejects_independent_exclusion_table() -> None:
    """人間向けメイン側は実施内容と別の除外・保持表を持たない。"""
    content = _HUMAN_MAIN_CONTENT.replace(
        f"\n## {_plan_format.PLAN_H2_AGENT_JUDGMENT}\n",
        f"\n### 合意済みの除外・保持\n\n対象外の類似箇所は維持する。\n\n## {_plan_format.PLAN_H2_AGENT_JUDGMENT}\n",
        1,
    )
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("直下にH3を置かない" in error for error in errors), errors


def test_human_readable_action_rejects_arbitrary_h3() -> None:
    """実施内容直下へ置いた任意のH3を、拒否する対象をH3と述べるメッセージで拒否する。"""
    content = _HUMAN_MAIN_CONTENT.replace(
        f"\n## {_plan_format.PLAN_H2_AGENT_JUDGMENT}\n",
        f"\n### 補足の観点\n\n対象範囲の補足を述べる。\n\n## {_plan_format.PLAN_H2_AGENT_JUDGMENT}\n",
        1,
    )
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("実施内容" in error and "H3を置かない" in error for error in errors), errors


def test_human_readable_action_rejects_feedback_missing_from_metadata() -> None:
    """フィードバック由来の正本は関連フィードバックから逆照合できる。"""
    content = _HUMAN_MAIN_CONTENT.replace("(20260817-223603-001.md)", "(20260817-223603-999.md)", 1)
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("フィードバック由来が関連フィードバックに無い" in error for error in errors), errors


def test_feedback_origin_diagnostic_shows_expected_format() -> None:
    """受理形式に一致しない`由来`欄の診断は、半角丸括弧を用いた期待書式の実例を示す。"""
    example = "`エージェント由来のフィードバック (20260831-000000-001.md)`"
    origin = f"人間由来のフィードバック ({_plan_fixture.FEEDBACK_FILES[0][0]})"
    full_width = _HUMAN_MAIN_CONTENT.replace(origin, f"人間由来のフィードバック（{_plan_fixture.FEEDBACK_FILES[0][0]}）", 1)
    errors = _plan_format.check_plan_main_structure(full_width)[1]
    assert any(example in error for error in errors), errors
    without_name = _HUMAN_MAIN_CONTENT.replace(origin, "人間由来のフィードバック", 1)
    errors = _plan_format.check_plan_main_structure(without_name)[1]
    assert any(example in error for error in errors), errors


def test_related_feedback_rejects_invalid_filename() -> None:
    """関連フィードバックは正本ファイル名だけを受理する。"""
    content = _HUMAN_MAIN_CONTENT.replace("20260817-223603-001.md", "docs/notes.md", 1)
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("ファイル名が不正" in error for error in errors), errors


@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ("- 関連フィードバック:\n  - 20260817-223603-001.md:", "1行要約が空"),
        (
            "- 関連フィードバック:\n  - 20260817-223603-001.md: 入力の境界を追加確認する\n"
            "  - 20260817-223603-001.md: 重複した要求",
            "ファイル名が重複",
        ),
        (
            "- 関連フィードバック: なし\n  - 20260817-223603-001.md: 入力の境界を追加確認する",
            "なし`と子項目",
        ),
    ],
)
def test_related_feedback_rejects_invalid_children(replacement: str, expected: str) -> None:
    """関連フィードバックの要約欠落、重複及び`なし`との併記を拒否する。"""
    content = _HUMAN_MAIN_CONTENT.replace(
        "- 関連フィードバック:\n  - 20260817-223603-001.md: 入力の境界を追加確認する",
        replacement,
        1,
    )
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any(expected in error for error in errors), errors


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("関連フィードバック", "ファイル名が不正"),
        (
            "| 対象外の類似するが対象外の記述は変更しない |",
            "`## 実施内容`へ素材・要求・履歴・実装単位の合成IDを記載しない",
        ),
    ],
)
def test_human_readable_main_rejects_internal_identifiers(mutation: str, expected: str) -> None:
    """人間向けメイン側に内部管理IDを持ち込まない。"""
    if mutation == "関連フィードバック":
        content = _HUMAN_MAIN_CONTENT.replace("20260817-223603-001.md", "P-001.md", 1)
    else:
        content = _HUMAN_MAIN_CONTENT.replace(
            _plan_fixture.PROPOSAL_ACTION_ROW,
            "| P-001 | エージェント提案 | 対象外 | 当初目的と公開契約への影響が無いため。 |",
            1,
        )
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any(expected in error for error in errors), errors


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


def test_human_readable_history_rejects_internal_identifier() -> None:
    """人間向け変更履歴は内部管理IDを含めず自然な記録を持つ。"""
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.HISTORY_REVIEW_BODY, "P-001", 1)
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("`## 変更履歴`へ履歴・要求・実装単位の合成ID" in error for error in errors), errors


def test_human_readable_main_accepts_external_identifiers_and_verbatim_ids() -> None:
    """通常の外部識別子と利用者発言の逐語文は合成IDとして誤拒否しない。"""
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_ACTION_SUBJECT, "MCP-toolとTLSのP-256を維持する", 1)
    content = content.replace(_plan_fixture.USER_EVENT_TEXT, "P-001という入力を変更しない。", 1)
    assert not _plan_format.check_plan_main_structure(content)[1]


@pytest.mark.parametrize("external_id", ["TLS P-256", "TLSでP-256", "NIST P-256", "ECDSA P-256"])
def test_human_readable_main_accepts_ambiguous_external_identifier(external_id: str) -> None:
    """外部仕様名と旧素材IDを区別できない完全トークンは誤拒否しない。"""
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_ACTION_SUBJECT, f"{external_id}を維持する", 1)
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


def test_human_readable_units_reject_duplicate_descriptive_name() -> None:
    """人間向けdetail側の実装単位名は重複させない。"""
    content = _HUMAN_DETAIL_CONTENT.replace(
        "| 回帰検証の追加 | 更新後の挙動を検証する | 契約境界の更新 | 2 | `pytest` |",
        "| 契約境界の更新 | 更新後の挙動を検証する | 契約境界の更新 | 2 | `pytest` |",
        1,
    )
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any("表内で一意の説明的な名前" in error for error in errors), errors


def test_human_readable_units_reject_ambiguous_exact_id() -> None:
    """構造化された実装単位名では曖昧な完全トークンも旧IDとして拒否する。"""
    content = _HUMAN_DETAIL_CONTENT.replace("| 契約境界の更新 |", "| P-256 |", 1)
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any("合成IDではない説明的な名前" in error for error in errors), errors


def test_bug_file_structure_accepts_canonical_sidecar() -> None:
    """H1直下のバグ単位と原因分析表・固定行の調査表を持つ付属ファイルを受理する。"""
    assert not _plan_format.check_bug_file_structure(_BUG_FILE_CONTENT)


def test_bug_cause_table_rows_pass_colloquial_check() -> None:
    """許容語彙表から対象項目が失われた場合に失敗し、固定行名の改称を判断する契機とする。"""
    deny_patterns = _colloquial_check.load_patterns(_colloquial_check.DENY_PATH)
    allow_patterns = _colloquial_check.load_patterns(_colloquial_check.ALLOW_PATH)

    for row_name in _plan_format.PLAN_BUG_CAUSE_TABLE_ROWS:
        diagnostics = _colloquial_check.scan_text(row_name, deny_patterns, allow_patterns)
        assert not diagnostics, (row_name, diagnostics)


def test_bug_file_structure_rejects_missing_fixed_row() -> None:
    """付属ファイルの固定行の調査表から行が欠けた場合を拒否する。"""
    content = _BUG_FILE_CONTENT.replace(f"{_plan_fixture.bug_row(_plan_format.PLAN_BUG_TABLE_ROWS[0])}\n", "")
    errors = _plan_format.check_bug_file_structure(content)
    assert any(f"固定{len(_plan_format.PLAN_BUG_TABLE_ROWS)}行" in error for error in errors), errors


def test_bug_file_structure_rejects_empty_content_cell() -> None:
    """付属ファイルの固定表で`内容`が空欄の場合を拒否する。"""
    content = _BUG_FILE_CONTENT.replace(
        _plan_fixture.bug_row("直接的原因"),
        "| 直接的原因 |  |",
    )
    errors = _plan_format.check_bug_file_structure(content)
    assert any("空の`内容`" in error for error in errors), errors


def test_bug_file_structure_rejects_missing_cause_table() -> None:
    """原因分析表のない新形式のバグ単位を拒否する。"""
    content = _BUG_FILE_CONTENT.replace(f"{_BUG_CAUSE_TABLE}\n\n", "")
    errors = _plan_format.check_bug_file_structure(content)
    assert any("原因分析表を調査表より前に置く" in error for error in errors), errors


def test_bug_file_structure_rejects_cause_table_after_investigation_table() -> None:
    """原因分析表が調査表の後にある新形式のバグ単位を拒否する。"""
    content = _BUG_FILE_CONTENT.replace(f"{_BUG_CAUSE_TABLE}\n\n", "") + f"\n{_BUG_CAUSE_TABLE}\n"
    errors = _plan_format.check_bug_file_structure(content)
    assert any("原因分析表を調査表より前に置く" in error for error in errors), errors


def test_bug_file_structure_rejects_duplicate_cause_table() -> None:
    """同じバグ単位の原因分析表が重複した場合を拒否する。"""
    content = _BUG_FILE_CONTENT.replace(_BUG_CAUSE_TABLE, f"{_BUG_CAUSE_TABLE}\n\n{_BUG_CAUSE_TABLE}", 1)
    assert _plan_format.check_bug_file_structure(content)


def test_bug_file_structure_rejects_duplicate_investigation_table() -> None:
    """同じバグ単位の調査表が重複した場合を拒否する。"""
    content = _BUG_FILE_CONTENT.replace(
        _BUG_INVESTIGATION_TABLE,
        f"{_BUG_INVESTIGATION_TABLE}\n\n{_BUG_INVESTIGATION_TABLE}",
        1,
    )
    assert _plan_format.check_bug_file_structure(content)


def test_bug_file_structure_rejects_mixed_new_and_legacy_tables() -> None:
    """同じバグ単位に新形式と旧14行表が混在した場合を拒否する。"""
    legacy_table = _LEGACY_BUG_FILE_CONTENT.split("\n\n", 2)[2]
    assert _plan_format.check_bug_file_structure(f"{_BUG_FILE_CONTENT}\n\n{legacy_table}")


def test_bug_file_structure_rejects_additional_table() -> None:
    """正規の2表に追加表が置かれた場合を拒否する。"""
    extra_table = "| 補足 | 内容 |\n| --- | --- |\n| 任意 | 値 |"
    assert _plan_format.check_bug_file_structure(f"{_BUG_FILE_CONTENT}\n\n{extra_table}")


def test_bug_file_structure_accepts_legacy_standalone_table() -> None:
    """原因分析表を持たない旧調査表を読み取り互換で受理する。"""
    assert not _plan_format.check_bug_file_structure(_LEGACY_BUG_FILE_CONTENT)


def test_bug_file_structure_accepts_legacy_row_layout() -> None:
    """統廃合前の行構成を持つ調査表を読み取り互換で受理する。"""
    assert not _plan_format.check_bug_file_structure(_LEGACY_ROWS_BUG_FILE_CONTENT)
    assert _plan_format.has_legacy_bug_investigation_table(_LEGACY_ROWS_BUG_FILE_CONTENT)
    assert not _plan_format.has_legacy_bug_investigation_table(_BUG_FILE_CONTENT)


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


def test_canonical_fixture_accepts_mixed_agreements_and_numeric_target() -> None:
    """実施・除外・保持の条項分解と数値目標を含む正規fixtureを受理する。"""
    assert "診断件数を2件から1件へ減らす" in _VALID_CONTENT
    assert "対象外の挙動を変更しない" in _VALID_CONTENT
    assert "基準値は診断2件、目標は1件" in _VALID_CONTENT
    assert not _plan_format.check_plan_structure(_VALID_CONTENT)


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


def test_history_review_rows_reject_duplicate_track_and_round() -> None:
    """異なる表記でも同じ系統・ラウンドを表すレビュー指摘行を拒否する。"""
    review_rows = (
        "| R1-conformance | レビュー指摘 | 主要な指摘。 | 1件を採用した。 | `## 実施内容` |\n"
        "| R01-conformance | レビュー指摘 | 追加の指摘。 | 1件を採用した。 | `## 変更履歴` |\n"
    )
    content = _VALID_CONTENT.replace(
        f"{_plan_fixture.HISTORY_USER_ROW}\n",
        review_rows,
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("レビュー指摘行は系統・ラウンドを重複させない: conformance, 1" in error for error in errors), errors


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


def test_legacy_plan_accepts_legacy_history_review_identifier() -> None:
    """旧形式の単一ファイルでは既存のレビュー指摘IDを読み取り互換として受理する。"""
    content = _VALID_CONTENT.replace(
        _plan_fixture.HISTORY_USER_ROW,
        "| C-002 | レビュー指摘 | 主要な指摘。 | 1件を採用した。 | `## 実施内容` |",
        1,
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


def test_legacy_bug_table_predicate_requires_valid_fixed_table() -> None:
    """バグ調査表は固定行の調査表を満たす場合だけ検出する。"""
    content = _BUG_CONTENT
    assert _plan_format.has_legacy_bug_table(content)
    invalid = content.replace(f"{_plan_fixture.bug_row(_plan_format.PLAN_BUG_TABLE_ROWS[0])}\n", "")
    assert not _plan_format.has_legacy_bug_table(invalid)


def test_implementation_materials_allows_free_h3_composition() -> None:
    """実装資料配下では自由なH3構成を受理する。"""
    content = _VALID_CONTENT.replace(f"### {_plan_fixture.IMPLEMENTATION_H3}", "### 実行方法\n\n手順。\n\n### 変更説明")
    assert not _plan_format.check_plan_structure(content)


def test_permanence_rejects_free_h3() -> None:
    """恒久化領域では固定3見出し以外のH3を拒否する。"""
    content = _VALID_CONTENT.replace("\n## 実装資料", "\n### 任意の補足\n\n補足する。\n\n## 実装資料")
    assert any("固定見出し以外のH3" in error for error in _plan_format.check_plan_structure(content))


def test_duplicate_fixed_table_is_rejected() -> None:
    """同一H2内に複製した固定表を拒否する。"""
    duplicate = """| 実施内容 | 採否 | ユーザー指示との関係 | 根拠 |
| --- | --- | --- | --- |
| 追加の変更 | 採用 | 具体化 | R-P-001-001 |

"""
    content = _VALID_CONTENT.replace("### 合意済みの除外・保持", duplicate + "### 合意済みの除外・保持", 1)
    errors = _plan_format.check_plan_structure(content)
    assert any("固定表は1件必要" in error for error in errors), errors


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


def test_progress_table_rejects_empty_cells_when_a_row_exists() -> None:
    """進捗表に内容行がある場合は従来どおり全cellの値を要求する。"""
    marker = "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n"
    content = _VALID_CONTENT.replace(marker, marker + "| 2026-08-09 12:00 | 実装 |  |\n", 1)
    errors = _plan_format.check_plan_structure(content)
    assert any("空cell" in error for error in errors), errors


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


def test_permanence_table_accepts_no_candidate_phrase_within_finding() -> None:
    """予約値を含む通常の知見と別の知見を併記した表を受理する。"""
    row = _plan_fixture.PERMANENCE_ROW
    additional = "| 候補なし表記の検査を追加する | P-001 | 対象ファイル | 誤った記載を拒否するため。 |"
    assert not _plan_format.check_plan_structure(_VALID_CONTENT.replace(row, f"{row}\n{additional}"))


def test_permanence_table_accepts_multiple_findings() -> None:
    """恒久化表は知見を複数行で記載できる。"""
    row = _plan_fixture.PERMANENCE_ROW
    additional = "| 公開契約を維持する | P-001 | 計画限り | 既存文書へ記載済みのため。 |"
    assert not _plan_format.check_plan_structure(_VALID_CONTENT.replace(row, f"{row}\n{additional}"))


@pytest.mark.parametrize(
    "row",
    [
        "| 更新経路を恒久化する | エージェント提案詳細 |  | 後続の更新でも参照するため。 |",
        "| 更新経路を恒久化する | エージェント提案詳細 | 対象ファイル |",
    ],
)
def test_permanence_table_rejects_empty_cells_and_column_mismatch(row: str) -> None:
    """恒久化表の空セルと列数不一致を拒否する。"""
    original = _plan_fixture.PERMANENCE_ROW
    errors = _plan_format.check_plan_structure(_VALID_CONTENT.replace(original, row))
    assert any("空cellまたは列数不一致" in error for error in errors), errors


def test_permanence_sections_reject_conclusion_words_only() -> None:
    """恒久化等の結論語だけの記載を検討の省略として拒否する。"""
    content = _VALID_CONTENT.replace(_plan_fixture.REFACTORING_TABLE, "該当なし", 1)
    errors = _plan_format.check_plan_structure(content)
    assert any("結論語だけの記載は成立しない" in error for error in errors), errors


def test_permanence_sections_accept_and_omit_retired_similar_review_heading() -> None:
    """廃止した`### 類似見直し`を持つ既存計画と、置かない計画のいずれも受理する。"""
    assert _plan_fixture.LEGACY_SIMILAR_REVIEW_SECTION in _VALID_CONTENT
    assert not _plan_format.check_plan_structure(_VALID_CONTENT)
    without = _VALID_CONTENT.replace(_plan_fixture.LEGACY_SIMILAR_REVIEW_SECTION, "", 1)
    assert not _plan_format.check_plan_structure(without), _plan_format.check_plan_structure(without)


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


def test_extract_tables_keeps_row_column_count() -> None:
    """列数が見出しと異なる行を切り詰めずに返し、列数不一致を後段で検出できるようにする。"""
    table = "| A | B |\n| --- | --- |\n| 1 | 2 | 3 |\n| 4 |"
    lines: list[tuple[int, str]] = list(enumerate(table.splitlines(), start=1))
    assert _plan_format.extract_tables(lines)[0].rows == (("1", "2", "3"), ("4",))


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


def test_parse_plan_materials_returns_structured_ids_and_legacy_flag() -> None:
    """新旧形式の素材と要求を識別し、ID集合を返す。"""
    materials, errors = _plan_format.parse_plan_materials(_VALID_CONTENT)
    assert not errors
    assert materials == _plan_format.PlanMaterials(
        frozenset({"P-001", "P-002"}),
        frozenset({"R-P-001-001", "R-P-001-002", "R-P-002-001"}),
        False,
        frozenset({"R-P-001-001", "R-P-002-001"}),
        feedback_queue_ids=frozenset({"20260817-223603-001.md"}),
    )

    legacy_materials, legacy_errors = _plan_format.parse_plan_materials(_LEGACY_CONTENT)
    assert not legacy_errors
    assert legacy_materials == _plan_format.PlanMaterials(frozenset({"P-001"}), frozenset(), True)


def test_new_material_tables_take_priority_over_legacy_fence() -> None:
    """新形式の表と旧形式の素材記法が混在する場合は新形式を解析する。"""
    legacy_tail = """
P-999:

```text
旧形式の本文。
```
"""
    content = _VALID_CONTENT.replace("\n## 変更履歴", f"{legacy_tail}\n## 変更履歴", 1)
    materials, errors = _plan_format.parse_plan_materials(content)
    assert not errors
    assert materials is not None
    assert not materials.is_legacy
    assert materials.material_ids == frozenset({"P-001", "P-002"})


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


def test_action_references_rejected_requirement_are_rejected() -> None:
    """採用系の実施内容の根拠に不採用要求を指定できない。"""
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 採用 | 指示どおり | R-P-001-002 |",
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("不採用要求を参照できない: R-P-001-002" in error for error in errors), errors


@pytest.mark.parametrize("decision", _plan_format.PLAN_ACTION_DECISIONS)
def test_action_decisions_accept_all_declared_values(decision: str) -> None:
    """実施内容表が定義する7種類の採否値を受理する。"""
    relation = "指示どおり" if decision in {"採用", "部分採用"} else "非該当"
    root = "R-P-001-001" if decision in {"採用", "部分採用"} else "R-P-001-002を採用しない理由を記載する。"
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
    errors = _plan_format.check_plan_structure(content)
    assert not errors, errors


def test_action_decision_rejects_unknown_value() -> None:
    """実施内容表の未定義な採否値を拒否する。"""
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 未定義 | 指示どおり | R-P-001-001 |",
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("採否" in error and "未定義" in error for error in errors), errors


def test_non_adopted_action_requires_free_text_reason() -> None:
    """非採用系の実施内容は要求IDでなく理由を根拠へ記載する。"""
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 不採用 | 非該当 |  |",
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("非採用系の`根拠`は理由を記載する" in error for error in errors), errors


def test_non_adopted_action_may_reference_rejected_requirement_in_reason() -> None:
    """非採用系の理由に存在する不採用要求IDを含めても拒否しない。"""
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 不採用 | 非該当 | R-P-001-002を不採用とする理由を記載する。 |",
        1,
    )
    content = content.replace(
        "| 公開契約を維持する | 対象の公開API | P-002, R-P-002-001 |",
        "| 公開契約を維持する | 対象の公開API | P-002, R-P-002-001, R-P-001-001 |",
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert not errors, errors


def test_adopted_action_rejects_non_queue_relation() -> None:
    """採用系の実施内容に非該当の関係を指定できない。"""
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 採用 | 非該当 | R-P-001-001 |",
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("ユーザー指示との関係" in error and "非該当" in error for error in errors), errors


def test_requirement_coverage_accepts_content_where_every_adopted_requirement_is_referenced() -> None:
    """採用要求が`根拠`又は合意表の`素材・要求参照`のいずれかで被覆されていれば検出しない。"""
    errors = _plan_format.check_plan_structure(_VALID_CONTENT)
    assert not any("採用要求を被覆しない" in error for error in errors), errors


def test_requirement_coverage_rejects_adopted_requirement_referenced_by_neither_action_nor_exclusion() -> None:
    """採用要求が`根拠`にも合意表の`素材・要求参照`にも現れない場合を検出する。"""
    content = _VALID_CONTENT.replace("P-002, R-P-002-001", "P-001, R-P-001-002")
    errors = _plan_format.check_plan_structure(content)
    assert any("採用要求を被覆しない: R-P-002-001" in error for error in errors), errors


_UNCOVERED_REQUIREMENT_ROW = (
    "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |"
)


def _plan_with_uncovered_requirement(adopted_scope: str) -> str:
    """採用要求R-P-002-001を被覆しない計画本文を、指定した`採用範囲`で返す。

    合意表の`素材・要求参照`を別要求へ差し替えることで、R-P-002-001は`根拠`と
    `素材・要求参照`のいずれからも参照されない状態になる。
    """
    content = _VALID_CONTENT.replace("P-002, R-P-002-001", "P-001, R-P-001-002", 1)
    replaced = _UNCOVERED_REQUIREMENT_ROW.replace("| 公開APIの維持 |", f"| {adopted_scope} |")
    return content.replace(_UNCOVERED_REQUIREMENT_ROW, replaced, 1)


def test_requirement_coverage_excludes_terminal_only_adopted_requirement() -> None:
    """`採用範囲`が`終端工程のみ`で始まる採用要求は被覆されていなくても受理する。"""
    assert not _plan_format.check_plan_structure(_plan_with_uncovered_requirement("終端工程のみ適用する"))


def test_requirement_coverage_keeps_checking_adopted_requirement_outside_terminal_only() -> None:
    """`終端工程のみ`で始まらない採用要求は除外の影響を受けず被覆検査の対象に残る。"""
    errors = _plan_format.check_plan_structure(_plan_with_uncovered_requirement("公開APIの維持"))
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


def test_structured_material_contract_rejects_duplicate_material_id() -> None:
    """素材IDの重複を拒否する。"""
    content = _VALID_CONTENT.replace(
        "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        "| P-001 | 利用者合意 | 非該当 | 本セッション | 全文 |\n| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        1,
    )
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("素材IDが重複している" in error for error in errors), errors


def test_structured_material_contract_rejects_duplicate_feedback_queue_id() -> None:
    """異なる素材IDから同じフィードバックを重複参照できない。"""
    content = _VALID_CONTENT.replace(
        "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        "| P-002 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |",
        1,
    )
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("フィードバック素材のキューIDが重複している" in error for error in errors), errors


def test_structured_material_contract_rejects_requirement_order_and_gap() -> None:
    """要求表のID順序と素材内連番の欠落を拒否する。"""
    content = _VALID_CONTENT.replace("R-P-002-001 | P-002 |", "R-P-002-003 | P-002 |", 1)
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("末尾連番が001から欠番なく続かない" in error for error in errors), errors


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


def test_structured_material_contract_requires_adjacent_tables() -> None:
    """素材表と要求表の間に説明文又は別表を置かない。"""
    content = _VALID_CONTENT.replace(
        "\n| 要求ID | 素材参照 |",
        "\n説明文を配置する。\n\n| 要求ID | 素材参照 |",
        1,
    )
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("素材表の直後に要求表" in error for error in errors), errors


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
    """フィードバック以外の素材種別も投入元と引用範囲を保持して受理する。"""
    content = _VALID_CONTENT.replace(
        "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        row,
        1,
    )
    materials, errors = _plan_format.parse_plan_materials(content)
    assert not errors
    assert materials is not None
    assert not materials.is_legacy


@pytest.mark.parametrize("material_type", ["参考素材", "処理対象資料", "起動事実"])
def test_structured_material_types_may_be_unreferenced(material_type: str) -> None:
    """参考素材、処理対象資料及び起動事実は要求を直接持たなくても受理する。"""
    row = {
        "参考素材": "| P-002 | 参考素材 | 非該当 | docs/reference.md | 節1 |",
        "処理対象資料": "| P-002 | 処理対象資料 | 非該当 | input.json | $.items |",
        "起動事実": "| P-002 | 起動事実 | 非該当 | 常駐自動起動 | 非該当 |",
    }[material_type]
    content = _VALID_CONTENT.replace(
        "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        row,
        1,
    )
    content = content.replace("P-001, P-002", "P-001", 1)
    content = content.replace(
        "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |\n",
        "",
        1,
    )
    materials, errors = _plan_format.parse_plan_materials(content)
    assert not errors
    assert materials is not None


def test_structured_material_types_reject_unreferenced_user_agreement() -> None:
    """利用者指示又は利用者合意を要求表から未参照にしない。"""
    content = _VALID_CONTENT.replace("P-001, P-002", "P-001", 1).replace(
        "| R-P-002-001 | P-002 | 公開契約を維持する。 | 採用 | 公開APIの維持 | 非該当 | 利用者合意を反映するため。 |\n",
        "",
        1,
    )
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("要求表から参照されていない" in error for error in errors), errors


@pytest.mark.parametrize("material_id", ["P-001（利用者発言）:", "P-001: 利用者発言"])
def test_material_id_annotation_is_rejected_near_the_invalid_line(material_id: str) -> None:
    content = _LEGACY_CONTENT.replace("P-001:", material_id, 1)

    errors = _plan_format.check_plan_structure(content)

    assert f"提示素材の素材ID行に注記を含めない: {material_id}" in errors


def test_material_id_candidate_check_ignores_normal_notes_and_fenced_text() -> None:
    content = _LEGACY_CONTENT.replace(
        "P-001:\n\n```text\n対象を更新してほしい。",
        "注記: 提示素材の説明\nSource: user transcript\n\nP-001:\n\n```text\nP-999: fence内の文字列\n対象を更新してほしい。",
    )

    errors = _plan_format.check_plan_structure(content)

    assert not any("素材ID行に注記" in error for error in errors)


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


def test_metadata_prefers_canonical_placement() -> None:
    """正規配置がある計画では旧配置を無視する。"""
    content = _VALID_CONTENT.replace(
        f"### {_plan_fixture.IMPLEMENTATION_H3}",
        f"### 計画メタ情報\n\n- ベースコミット: `{'b' * 40}`\n\n### ファイル群別の変更説明",
    )
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert not errors
    assert metadata is not None
    assert metadata.is_canonical
    assert metadata.values["ベースコミット"] == _BASE


def test_metadata_falls_back_to_legacy_placement() -> None:
    """正規配置が無い既存計画は旧配置を読み取り互換で解析する。"""
    content = f"## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`\n"
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert not errors
    assert metadata is not None
    assert metadata.parent == "背景"
    assert metadata.base_commit_candidates == ("a" * 40,)


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


def test_metadata_rejects_conflicting_values() -> None:
    """同じ項目に異なる値を持つ計画は競合として拒否する。"""
    content = _VALID_CONTENT.replace(
        "- 作業種別: 通常変更\n",
        "- 作業種別: 通常変更\n- 作業種別: バグ対応\n",
    )
    metadata, errors = _plan_format.parse_plan_metadata(content)
    assert metadata is None
    assert any("競合する値" in error for error in errors)


def test_markdown_body_lines_exclude_frontmatter_fences_and_comments() -> None:
    """フロントマター、コードフェンス、複数行HTMLコメントの行は本文行に含めない。"""
    content = """---
title: x
---
## 実在

```text
## フェンス内
```

<!--
## コメント内
-->
"""
    headings = [line for _lineno, line in _plan_format.iter_markdown_body_lines(content) if line.startswith("## ")]
    assert headings == ["## 実在"]


def test_duplicate_headings_rejects_same_text_under_same_parent() -> None:
    """同じ親の下に同じ文言の見出しが現れた場合は文言と両方の行番号を返す。"""
    content = "# 計画\n\n## 親\n\n### 子\n\n### 子\n"

    assert _plan_format.check_duplicate_headings(content) == ["同じ見出しが重複している: `### 子`（計画/親配下、5行目と7行目）"]


def test_duplicate_headings_accepts_same_text_under_different_parents() -> None:
    """異なる親の下にある同じ文言の見出しは受理する。"""
    content = "# 計画\n\n## 親A\n\n### 子\n\n## 親B\n\n### 子\n"

    assert not _plan_format.check_duplicate_headings(content)


def test_duplicate_headings_checks_deep_headings_and_ignores_fences() -> None:
    """H4以深の重複を検出し、コードフェンス内の見出し記法は除外する。"""
    content = "# 計画\n\n## 親\n\n### 子\n\n#### 深部\n\n```md\n#### 深部\n```\n\n#### 深部\n"

    errors = _plan_format.check_duplicate_headings(content)

    assert len(errors) == 1
    assert "`#### 深部`" in errors[0]
    assert "7行目と13行目" in errors[0]


_PLAN_FILE_STANDARDS = (
    pathlib.Path(__file__).resolve().parents[1] / "skills" / "plan-mode" / "references" / "plan-file-standards.md"
)


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


_ROOT_CAUSE_ANALYSIS = (
    pathlib.Path(__file__).resolve().parents[1] / "skills" / "bugfix" / "references" / "root-cause-analysis.md"
)


@pytest.mark.parametrize("row_name", _plan_format.PLAN_BUG_TABLE_ROWS)
def test_root_cause_analysis_states_every_bug_table_row(row_name: str) -> None:
    """調査表の固定行名を原因分析契約の集約表が明記する。

    行名の正本を構造定数に置くため、集約表の追随漏れをここで検出する。
    """
    assert f"| {row_name} | " in _ROOT_CAUSE_ANALYSIS.read_text(encoding="utf-8")


def test_agent_document_target_paths() -> None:
    """配布規範とagent定義をエージェント向け文書として判定する。"""
    assert _plan_format.is_agent_doc_target_file("agent-toolkit/skills/example/SKILL.md")
    assert _plan_format.is_agent_doc_target_file("agent-toolkit/agents/example.md")
    assert not _plan_format.is_agent_doc_target_file("pytools/example.py")


# --- 新書式（計画2ファイル）の構造検査 ---


def _canonical_main_content() -> str:
    """新しい固定H2と、エージェント提案が無い場合の提案詳細節を持つメイン本文を返す。"""
    return _plan_fixture.to_canonical_main(_VALID_MAIN_CONTENT)


def _canonical_human_main_content() -> str:
    """新しい固定H2とエージェント提案の判断表を持つ人間向けメイン本文を返す。"""
    return _HUMAN_MAIN_CONTENT


def test_main_and_detail_canonical_pass_structure_check() -> None:
    """新書式のメイン側・detail側の正規形はいずれも構造検査を通過する。"""
    work_type, main_errors = _plan_format.check_plan_main_structure(_canonical_main_content())
    assert work_type == "通常変更"
    assert not main_errors
    assert not _plan_format.check_plan_detail_structure(_VALID_DETAIL_CONTENT, work_type)


def test_main_structure_accepts_new_fixed_headings_and_empty_judgment() -> None:
    """新しい固定H2と、エージェント提案が無い場合の`なし`を受理する。"""
    work_type, errors = _plan_format.check_plan_main_structure(_canonical_main_content())
    assert work_type == "通常変更"
    assert not errors, errors


def test_main_structure_requires_judgment_for_canonical_headings() -> None:
    """新しい固定H2を使う計画では判断節を省略できない。"""
    content = _VALID_MAIN_CONTENT.replace("## 変更履歴", "## 変更履歴（計画時）", 1).replace(
        "## 進捗ログ", "## 進捗ログ（実行時）", 1
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any(f"`## {_plan_format.PLAN_H2_AGENT_JUDGMENT}`が無い" in error for error in errors), errors


def test_main_structure_requires_none_when_no_agent_proposal_exists() -> None:
    """提案行が無い判断節へ任意の説明文を置かない。"""
    judgment = f"## {_plan_format.PLAN_H2_AGENT_JUDGMENT}"
    content = _canonical_main_content().replace(f"{judgment}\n\nなし", f"{judgment}\n\n調査結果を記載する", 1)
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any("エージェント提案が無い場合は`なし`" in error for error in errors), errors


def test_human_main_structure_requires_one_judgment_row_per_agent_proposal() -> None:
    """人間向け実施内容のエージェント提案と判断表を一対一で対応させる。"""
    _work_type, errors = _plan_format.check_plan_main_structure(_canonical_human_main_content())
    assert not errors, errors

    missing = _canonical_human_main_content().replace(
        f"{_plan_fixture.JUDGMENT_ROW}\n",
        "",
        1,
    )
    errors = _plan_format.check_plan_main_structure(missing)[1]
    assert any("実施内容`はエージェント提案行と同じ順序" in error for error in errors), errors


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


def test_legacy_history_keeps_old_track_compatibility_but_rejects_unknown_track() -> None:
    """旧形式では旧trackを残せるが、正規値に無いハイフン付き系統名は拒否する。"""
    accepted = _VALID_CONTENT.replace(
        _plan_fixture.HISTORY_USER_ROW,
        "| R1-conformance | レビュー指摘 | 主要な指摘。 | 1件を採用した。 | `## 実施内容` |",
        1,
    )
    assert not _plan_format.check_plan_structure(accepted)

    rejected = accepted.replace("R1-conformance", "R1-unknown-track", 1)
    errors = _plan_format.check_plan_structure(rejected)
    assert any("系統名は" in error and "正規値" not in error for error in errors), errors


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


def test_detail_structure_requires_implementation_units() -> None:
    """detail側の`## 実装資料`直下に実装単位表を必須とする。"""
    start = _VALID_DETAIL_CONTENT.index("### 実装単位")
    end = _VALID_DETAIL_CONTENT.index(f"### {_plan_fixture.IMPLEMENTATION_H3}")
    content = _VALID_DETAIL_CONTENT[:start] + _VALID_DETAIL_CONTENT[end:]
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any("`### 実装単位`を1件置く" in error for error in errors), errors


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


def test_detail_structure_accepts_multiple_units_with_dependency() -> None:
    """複数単位と先行依存を持つ正規形を受理する。"""
    second = "| U-002 | 回帰検証を追加する | U-001 | 2 | `pytest check_plan_file_test.py` |\n"
    content = _VALID_DETAIL_CONTENT.replace(
        f"{_plan_fixture.TWO_FILE_UNIT_ROW}\n",
        f"{_plan_fixture.TWO_FILE_UNIT_ROW}\n" + second,
    )
    assert not _plan_format.check_plan_detail_structure(content, "通常変更")


def test_human_detail_structure_accepts_multiple_ascii_comma_dependencies() -> None:
    """人間向け実装単位は複数の先行依存をASCIIカンマで列挙できる。"""
    second = "| 調査結果の整理 | 既存の判断材料を整理する | なし | 2 | `pytest` |\n"
    content = _HUMAN_DETAIL_CONTENT.replace(
        "| 回帰検証の追加 | 更新後の挙動を検証する | 契約境界の更新 | 2 | `pytest` |",
        second + "| 回帰検証の追加 | 更新後の挙動を検証する | 契約境界の更新, 調査結果の整理 | 3 | `pytest` |",
        1,
    )
    units, errors = _plan_format.parse_plan_implementation_units(content)
    assert not errors, errors
    assert units is not None
    assert units[-1].dependencies == ("契約境界の更新", "調査結果の整理")


def test_human_detail_structure_rejects_non_ascii_dependency_separator() -> None:
    """先行依存の全角読点を区切りとして扱わず、未解決の依存として拒否する。"""
    content = _HUMAN_DETAIL_CONTENT.replace("契約境界の更新 | 2 |", "契約境界の更新、回帰検証の追加 | 2 |", 1)
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any("先行依存`が実装単位表に無い" in error for error in errors), errors


def test_human_detail_structure_rejects_ascii_comma_in_unit_name() -> None:
    """説明的な実装単位名へASCIIカンマを含めない。"""
    content = _HUMAN_DETAIL_CONTENT.replace("| 契約境界の更新 |", "| 契約境界,更新 |", 1)
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any("実装単位名へASCIIカンマを含めない" in error for error in errors), errors


def test_detail_structure_rejects_dependency_not_preceding_integration_order() -> None:
    """先行依存が依存元より前の統合順に無い場合を拒否する。"""
    first = "| U-001 | 診断件数を更新する | U-002 | 1 | `pytest _plan_format_test.py` |\n"
    second = "| U-002 | 回帰検証を追加する | なし | 2 | `pytest check_plan_file_test.py` |\n"
    content = _VALID_DETAIL_CONTENT.replace(
        f"{_plan_fixture.TWO_FILE_UNIT_ROW}\n",
        first + second,
    )
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any("`統合順`より前にない" in error for error in errors), errors


def test_detail_structure_accepts_legacy_implementation_unit_column() -> None:
    """既存計画の6列表を読み取り互換として受理する。"""
    legacy = _VALID_DETAIL_CONTENT.replace(
        _plan_fixture.TWO_FILE_UNITS_TABLE,
        "| 単位ID | 目的 | 対象の実施内容 | 先行依存 | 統合順 | 近接検証 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| U-001 | 診断件数を更新する | 任意の旧値 | なし | 1 | {_plan_fixture.VERIFICATION_COMMAND} |",
    )
    assert not _plan_format.check_plan_detail_structure(legacy, "通常変更")


def test_main_structure_requires_detail_metadata_field() -> None:
    """計画ファイル（メイン）の計画メタ情報は詳細参照を末尾へ含む5項目にする。"""
    content = _VALID_MAIN_CONTENT.replace(f"- {_plan_format.PLAN_METADATA_DETAIL_FIELD}: `sample.detail.md`\n", "")
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any("この順序で1行ずつ置く" in error for error in errors), errors


def test_main_structure_rejects_missing_verification_table() -> None:
    """メイン側の`## 検証区分`は固定2行2列表にする。"""
    content = _VALID_MAIN_CONTENT.replace(
        _plan_fixture.VERIFICATION_TABLE,
        "",
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any(f"`## {_plan_format.PLAN_H2_VERIFICATION}`は" in error for error in errors), errors


def test_main_structure_rejects_empty_verification_command() -> None:
    """`## 検証区分`の各行に空の検証コマンドを置けない。"""
    content = _VALID_MAIN_CONTENT.replace(
        f"| {_plan_format.PLAN_VERIFICATION_TABLE_ROWS[1]} | {_plan_fixture.INTEGRATION_COMMAND} |", "| 統合後検証 |  |"
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any("空の検証コマンドがある" in error for error in errors), errors


def test_main_structure_accepts_termination_placeholder() -> None:
    """`## 終端工程`は終端工程が無い場合`なし`の記載を受理する。"""
    assert "\n## 終端工程\n\nなし\n" in _VALID_MAIN_CONTENT
    _work_type, errors = _plan_format.check_plan_main_structure(_VALID_MAIN_CONTENT)
    assert not any(f"`## {_plan_format.PLAN_H2_TERMINATION}`は" in error for error in errors), errors


def test_main_structure_rejects_empty_termination_section() -> None:
    """`## 終端工程`は空欄を拒否する（無い場合は`なし`と書く）。"""
    content = _VALID_MAIN_CONTENT.replace("## 終端工程\n\nなし\n", "## 終端工程\n\n")
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any(f"`## {_plan_format.PLAN_H2_TERMINATION}`は" in error for error in errors), errors


def test_main_structure_rejects_bug_section() -> None:
    """メイン側に`## バグ調査結果`は置かない（detail側専用）。"""
    content = _VALID_MAIN_CONTENT.replace(
        "## 検証区分",
        "## バグ調査結果\n\n未使用。\n\n## 検証区分",
    )
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any("固定H2は" in error for error in errors), errors


def test_detail_structure_requires_bug_section_for_bug_work_type() -> None:
    """detail側は作業種別が`バグ対応`の場合だけ`## バグ調査結果`を先頭へ要求する。"""
    errors = _plan_format.check_plan_detail_structure(_VALID_DETAIL_CONTENT, "バグ対応")
    assert any(f"固定H2`## {_plan_format.PLAN_H2_BUG}`は1件必要" in error for error in errors), errors


def test_detail_structure_rejects_bug_section_for_normal_work_type() -> None:
    """detail側は作業種別が`通常変更`の場合`## バグ調査結果`を拒否する。"""
    content = "## バグ調査結果\n\n未使用。\n\n" + _VALID_DETAIL_CONTENT
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any(f"`## {_plan_format.PLAN_H2_BUG}`は置かない" in error for error in errors), errors


def test_detail_structure_permanence_rejects_free_h3() -> None:
    """detail側の恒久化領域でも固定3見出し以外のH3を拒否する。"""
    content = _VALID_DETAIL_CONTENT.replace("\n## 実装資料", "\n### 任意の補足\n\n補足する。\n\n## 実装資料")
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any("固定見出し以外のH3" in error for error in errors), errors


def _feedback_source(*, source: bool, trailing_user_comment: bool = False, answer: bool = False) -> str:
    """フィードバック正本の本文を組み立てる。"""
    frontmatter = ["---", "status: inbox"]
    if source:
        frontmatter.append(f"{_plan_format.PLAN_FEEDBACK_SOURCE_KEY}: agent-toolkit:session-review")
    frontmatter.append("---")
    sections = ["# 要求", "", "本文。"]
    if answer:
        sections += ["", f"## {_plan_format.PLAN_FEEDBACK_ANSWER_HEADING}", "", "回答本文。"]
    if trailing_user_comment:
        sections += ["", f"## {_plan_format.PLAN_FEEDBACK_USER_COMMENT_HEADING}", "", "ユーザーの記入。"]
    return "\n".join([*frontmatter, "", *sections, ""])


def _origin_check(private_notes: pathlib.Path, content: str = _HUMAN_MAIN_CONTENT) -> tuple[list[str], list[str], list[str]]:
    """由来照合を有効にして(違反, 移行の指摘, 省略の事実)を返す。"""
    notices: list[str] = []
    skips: list[str] = []
    _work_type, errors = _plan_format.check_plan_main_structure(
        content, origin_notices=notices, origin_skips=skips, private_notes=private_notes
    )
    return errors, notices, skips


def _write_feedback(private_notes: pathlib.Path, body: str, name: str = _plan_fixture.FEEDBACK_FILES[0][0]) -> None:
    """キュー管理リポジトリの状態ディレクトリへ正本を作成する。"""
    inbox = private_notes / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / name).write_text(body, encoding="utf-8")


def test_origin_check_reports_notice_for_agent_sourced_feedback(tmp_path: pathlib.Path) -> None:
    """`source`を持ち機械判定できる明示由来が無い正本を移行の指摘として報告する。"""
    _write_feedback(tmp_path, _feedback_source(source=True))
    errors, notices, skips = _origin_check(tmp_path)
    assert not errors, errors
    assert not skips, skips
    assert any(_plan_fixture.FEEDBACK_FILES[0][0] in notice for notice in notices), notices


@pytest.mark.parametrize(
    "body",
    [
        _feedback_source(source=False),
        _feedback_source(source=True, trailing_user_comment=True),
        _feedback_source(source=True, answer=True),
    ],
)
def test_origin_check_accepts_human_origin(tmp_path: pathlib.Path, body: str) -> None:
    """`source`の欠落と機械判定できる明示由来を持つ正本は指摘の対象にしない。"""
    _write_feedback(tmp_path, body)
    errors, notices, skips = _origin_check(tmp_path)
    assert not errors, errors
    assert not notices, notices
    assert not skips, skips


def test_origin_check_skips_conversational_note(tmp_path: pathlib.Path) -> None:
    """`[対話由来]`注記のある行は書式として受理し、照合の対象から除く。"""
    _write_feedback(tmp_path, _feedback_source(source=True))
    content = _HUMAN_MAIN_CONTENT.replace(
        f"({_plan_fixture.FEEDBACK_FILES[0][0]})",
        f"({_plan_fixture.FEEDBACK_FILES[0][0]}) [対話由来]",
    )
    errors, notices, skips = _origin_check(tmp_path, content)
    assert not errors, errors
    assert not notices, notices
    assert not skips, skips


def test_origin_check_skips_when_source_is_unresolvable(tmp_path: pathlib.Path) -> None:
    """正本を解決できない場合は照合だけを省略し、他の検査の結果を変えない。"""
    (tmp_path / "inbox").mkdir(parents=True)
    errors, notices, skips = _origin_check(tmp_path)
    assert not errors, errors
    assert not notices, notices
    assert any(_plan_fixture.FEEDBACK_FILES[0][0] in skip for skip in skips), skips


def test_origin_check_skips_when_queue_repository_is_absent(tmp_path: pathlib.Path) -> None:
    """キュー管理リポジトリのルートが実在しない環境では照合だけを省略する。"""
    absent = tmp_path / "absent"
    errors, notices, skips = _origin_check(absent)
    assert not errors, errors
    assert not notices, notices
    assert any(str(absent) in skip for skip in skips), skips
    assert errors == _plan_format.check_plan_main_structure(_HUMAN_MAIN_CONTENT)[1]


def test_origin_check_is_inactive_without_collectors(tmp_path: pathlib.Path) -> None:
    """収集用の一覧を渡さない既存の呼び出しでは照合を行わない。"""
    _write_feedback(tmp_path, _feedback_source(source=True))
    _work_type, errors = _plan_format.check_plan_main_structure(_HUMAN_MAIN_CONTENT)
    assert not errors, errors
