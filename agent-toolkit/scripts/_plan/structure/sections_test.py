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


def test_human_readable_wi_and_units_do_not_expose_internal_ids() -> None:
    """人間向け形式は正本ファイル名と説明的な実装単位だけを解析する。"""
    metadata, metadata_errors = _plan_format.parse_plan_metadata(_HUMAN_MAIN_CONTENT)
    assert not metadata_errors, metadata_errors
    assert metadata is not None
    assert tuple(filename for filename, _summary in metadata.related_wi) == (
        "20260817-223603-001.md",
        "20260817-223603-002.md",
    )
    units, unit_errors = _plan_format.parse_plan_implementation_units(_HUMAN_DETAIL_CONTENT)
    assert not unit_errors, unit_errors
    assert units is not None
    assert tuple(unit.unit_id for unit in units) == ("契約境界の更新", "回帰検証の追加")


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


def test_agent_wi_adopted_action_accepts_rederived_scope() -> None:
    """エージェント由来のWIの採用行は再導出した適用範囲と根拠を受理する。"""
    row = (
        f"| 入力の境界を追加確認する | エージェント由来のWI ({_plan_fixture.WI_FILES[0][0]}) | 採用 | "
        "入力経路全体へ適用する。誤りの機構が由来の種類に依存しないため。 |"
    )
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.WI_ACTION_ROW, row, 1)
    assert not _plan_format.check_plan_main_structure(content)[1]


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


def test_human_readable_history_rejects_internal_identifier() -> None:
    """人間向け変更履歴は内部管理IDを含めず自然な記録を持つ。"""
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.HISTORY_REVIEW_BODY, "P-001", 1)
    errors = _plan_format.check_plan_main_structure(content)[1]
    assert any("`## 変更履歴`へ履歴・要求・実装単位の合成ID" in error for error in errors), errors


@pytest.mark.parametrize("external_id", ["TLS P-256", "TLSでP-256", "NIST P-256", "ECDSA P-256"])
def test_human_readable_main_accepts_ambiguous_external_identifier(external_id: str) -> None:
    """外部仕様名と旧素材IDを区別できない完全トークンは誤拒否しない。"""
    content = _HUMAN_MAIN_CONTENT.replace(_plan_fixture.USER_ACTION_SUBJECT, f"{external_id}を維持する", 1)
    assert not _plan_format.check_plan_main_structure(content)[1]


def test_human_readable_units_reject_ambiguous_exact_id() -> None:
    """構造化された実装単位名では曖昧な完全トークンも旧IDとして拒否する。"""
    content = _HUMAN_DETAIL_CONTENT.replace("| 契約境界の更新 |", "| P-256 |", 1)
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any("合成IDではない説明的な名前" in error for error in errors), errors


def test_bug_file_structure_accepts_canonical_sidecar() -> None:
    """H1直下のバグ単位と原因分析表・固定行の調査表を持つ付属ファイルを受理する。"""
    assert not _plan_format.check_bug_file_structure(_BUG_FILE_CONTENT)


def test_bug_file_structure_rejects_missing_cause_table() -> None:
    """原因分析表のない新形式のバグ単位を拒否する。"""
    content = _BUG_FILE_CONTENT.replace(f"{_BUG_CAUSE_TABLE}\n\n", "")
    errors = _plan_format.check_bug_file_structure(content)
    assert any("原因分析表を調査表より前に置く" in error for error in errors), errors


def test_bug_file_structure_rejects_mixed_new_and_legacy_tables() -> None:
    """同じバグ単位に新形式と旧14行表が混在した場合を拒否する。"""
    legacy_table = _LEGACY_BUG_FILE_CONTENT.split("\n\n", 2)[2]
    assert _plan_format.check_bug_file_structure(f"{_BUG_FILE_CONTENT}\n\n{legacy_table}")


def test_canonical_fixture_accepts_mixed_agreements_and_numeric_target() -> None:
    """実施・除外・保持の条項分解と数値目標を含む正規fixtureを受理する。"""
    assert "診断件数を2件から1件へ減らす" in _VALID_CONTENT
    assert "対象外の挙動を変更しない" in _VALID_CONTENT
    assert "基準値は診断2件、目標は1件" in _VALID_CONTENT
    assert not _plan_format.check_plan_structure(_VALID_CONTENT)


def test_legacy_plan_accepts_legacy_history_review_identifier() -> None:
    """旧形式の単一ファイルでは既存のレビュー指摘IDを読み取り互換として受理する。"""
    content = _VALID_CONTENT.replace(
        _plan_fixture.HISTORY_USER_ROW,
        "| C-002 | レビュー指摘 | 主要な指摘。 | 1件を採用した。 | `## 実施内容` |",
        1,
    )
    assert not _plan_format.check_plan_structure(content)


def test_permanence_rejects_free_h3() -> None:
    """恒久化領域では固定3見出し以外のH3を拒否する。"""
    content = _VALID_CONTENT.replace("\n## 実装資料", "\n### 任意の補足\n\n補足する。\n\n## 実装資料")
    assert any("固定見出し以外のH3" in error for error in _plan_format.check_plan_structure(content))


def test_progress_table_rejects_empty_cells_when_a_row_exists() -> None:
    """進捗表に内容行がある場合は従来どおり全cellの値を要求する。"""
    marker = "| 日時 | 完了した工程 | 結果・特記事項 |\n| --- | --- | --- |\n"
    content = _VALID_CONTENT.replace(marker, marker + "| 2026-08-09 12:00 | 実装 |  |\n", 1)
    errors = _plan_format.check_plan_structure(content)
    assert any("空cell" in error for error in errors), errors


def test_permanence_table_accepts_no_candidate_phrase_within_finding() -> None:
    """予約値を含む通常の知見と別の知見を併記した表を受理する。"""
    row = _plan_fixture.PERMANENCE_ROW
    additional = "| 候補なし表記の検査を追加する | P-001 | 対象ファイル | 誤った記載を拒否するため。 |"
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


def test_action_decision_rejects_unknown_value() -> None:
    """実施内容表の未定義な採否値を拒否する。"""
    content = _VALID_CONTENT.replace(
        _plan_fixture.TWO_FILE_ACTION_ROW,
        "| 診断件数を2件から1件へ減らす | 未定義 | 指示どおり | R-P-001-001 |",
        1,
    )
    errors = _plan_format.check_plan_structure(content)
    assert any("採否" in error and "未定義" in error for error in errors), errors


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


def test_structured_material_contract_rejects_duplicate_feedback_queue_id() -> None:
    """異なる素材IDから同じフィードバックを重複参照できない。"""
    content = _VALID_CONTENT.replace(
        "| P-002 | 利用者合意 | 非該当 | 本セッション | 全文 |",
        "| P-002 | フィードバック | 20260817-223603-001.md | 値なし | 本文全文 |",
        1,
    )
    _materials, errors = _plan_format.parse_plan_materials(content)
    assert any("フィードバック素材のキューIDが重複している" in error for error in errors), errors


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


def test_agent_document_target_paths() -> None:
    """配布規範とagent定義をエージェント向け文書として判定する。"""
    assert _plan_format.is_agent_doc_target_file("agent-toolkit/skills/example/SKILL.md")
    assert _plan_format.is_agent_doc_target_file("agent-toolkit/agents/example.md")
    assert _plan_format.is_agent_doc_target_file("agent-toolkit/share/rules-main.md")
    assert not _plan_format.is_agent_doc_target_file("agent-toolkit/share/plan-review.parent.md")
    assert not _plan_format.is_agent_doc_target_file("pytools/example.py")


def test_main_structure_requires_none_when_no_agent_proposal_exists() -> None:
    """提案行が無い判断節へ任意の説明文を置かない。"""
    judgment = f"## {_plan_format.PLAN_H2_AGENT_JUDGMENT}"
    content = _canonical_main_content().replace(f"{judgment}\n\nなし", f"{judgment}\n\n調査結果を記載する", 1)
    _work_type, errors = _plan_format.check_plan_main_structure(content)
    assert any("エージェント提案が無い場合は`なし`" in error for error in errors), errors


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


def test_detail_structure_accepts_multiple_units_with_dependency() -> None:
    """複数単位と先行依存を持つ正規形を受理する。"""
    second = "| U-002 | 回帰検証を追加する | U-001 | 2 | `pytest check_plan_file_test.py` |\n"
    content = _VALID_DETAIL_CONTENT.replace(
        f"{_plan_fixture.TWO_FILE_UNIT_ROW}\n",
        f"{_plan_fixture.TWO_FILE_UNIT_ROW}\n" + second,
    )
    assert not _plan_format.check_plan_detail_structure(content, "通常変更")


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


def test_main_structure_accepts_termination_placeholder() -> None:
    """`## 終端工程`は終端工程が無い場合`なし`の記載を受理する。"""
    assert "\n## 終端工程\n\nなし\n" in _VALID_MAIN_CONTENT
    _work_type, errors = _plan_format.check_plan_main_structure(_VALID_MAIN_CONTENT)
    assert not any(f"`## {_plan_format.PLAN_H2_TERMINATION}`は" in error for error in errors), errors


def test_detail_structure_rejects_bug_section_for_normal_work_type() -> None:
    """detail側は作業種別が`通常変更`の場合`## バグ調査結果`を拒否する。"""
    content = "## バグ調査結果\n\n未使用。\n\n" + _VALID_DETAIL_CONTENT
    errors = _plan_format.check_plan_detail_structure(content, "通常変更")
    assert any(f"`## {_plan_format.PLAN_H2_BUG}`は置かない" in error for error in errors), errors


def test_origin_check_skips_when_source_is_unresolvable(tmp_path: pathlib.Path) -> None:
    """正本を解決できない場合は照合だけを省略し、他の検査の結果を変えない。"""
    (tmp_path / "inbox").mkdir(parents=True)
    errors, notices, skips = _origin_check(tmp_path)
    assert not errors, errors
    assert not notices, notices
    assert any(_plan_fixture.WI_FILES[0][0] in skip for skip in skips), skips
