# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""現行の計画ファイル（要求を書く節を通常AWIの本文と同じ見出しで持つ形式）の検査を検証する。"""

import pathlib

import append_progress_log
import check_plan_file
import pytest

from agent_toolkit._plan import structure as _plan_format
from agent_toolkit._plan.structure import verification as _verification
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.atk_support import _FIXED_DT, _setup_notes
from agent_toolkit._testing.plan_file_check_support import fixture_repo as fixture_repo
from agent_toolkit._testing.wi_add_support import _cmd_add_args, _patch_cmd_add_operations, _run_public_add

_EVIDENCE = f"## 調査結果と判断根拠\n\n{_plan_fixture.EVIDENCE_BODY}\n\n"
_COMPLETION = f"## 完成条件\n\n{_plan_fixture.COMPLETION_BODY}\n\n"
_REQUEST = f"## {_plan_format.REQUEST_H2_REQUEST}\n\n{_plan_fixture.REQUEST_BODY}\n\n"
_QUOTATION = f"## {_plan_format.REQUEST_H2_QUOTATION}\n\n{_plan_fixture.QUOTATION_SECTION}\n\n"
_SUMMARY = "対象の公開契約を更新する。\n\n"


def _check(work_dir: pathlib.Path, content: str) -> tuple[list[str], list[str]]:
    """計画を保存し、新規作成と同じく移行警告と起草時の進捗行を拒否する条件で確かめる。"""
    path = work_dir / "plan.md"
    path.write_text(content, encoding="utf-8")
    return check_plan_file.check(path, work_dir, reject_migration_warnings=True, reject_progress_log_rows=True)


@pytest.mark.parametrize(
    ("work_type", "related_wi", "cause"),
    [
        ("通常変更", (), False),
        ("通常変更", _plan_fixture.WI_FILES, False),
        ("バグ対応", _plan_fixture.WI_FILES, False),
        ("バグ対応", (), True),
    ],
    ids=["no-wi", "related-wi", "bug-with-wi", "bug-without-wi"],
)
def test_accepts_request_plan(
    repo: tuple[pathlib.Path, str], work_type: str, related_wi: tuple[tuple[str, str], ...], cause: bool
) -> None:
    """WIの有無と作業種別ごとの正常系を受理する。関連WIがあるバグ対応は`## 原因分析`を省ける。"""
    work_dir, _base = repo
    content = _plan_fixture.request_plan(repo=work_dir.resolve(), work_type=work_type, related_wi=related_wi, cause=cause)
    assert _check(work_dir, content) == ([], [])


def test_rejects_draft_without_required_request_section(repo: tuple[pathlib.Path, str]) -> None:
    """`## 反映内容と反映先`を欠く原稿は、旧形式と誤認せず、欠けた見出し名を示して不合格にする。"""
    work_dir, _base = repo
    content = _plan_fixture.request_plan(repo=work_dir.resolve()).replace(_REQUEST, "", 1)
    errors, _warnings = _check(work_dir, content)
    assert errors == [f"要求を書く節: 非空の必須節がありません: {_plan_format.REQUEST_H2_REQUEST}"]


def _swap_order(content: str) -> str:
    return content.replace(_EVIDENCE + _COMPLETION, _COMPLETION + _EVIDENCE, 1)


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (
            lambda c: c.replace(_EVIDENCE, "## 調査結果と判断根拠\n\n", 1),
            "要求を書く節: 本文が空の節があります: 調査結果と判断根拠",
        ),
        (_swap_order, "要求を書く節: H2の順序が規定と異なります: 反映内容と反映先、完成条件、調査結果と判断根拠、"),
        (lambda c: c.replace(_QUOTATION, _QUOTATION + "## 補足\n\n補足する。\n\n", 1), "実際=['補足']"),
        (
            lambda c: c.replace(_QUOTATION, _QUOTATION + "   ## 補足\n\n補足する。\n\n", 1),
            "H2の`##`は行頭に置く",
        ),
        (lambda c: c.replace("## 実施内容\n\n", "## 実施内容\n\n### 補足\n\n", 1), "`## 実施内容`の配下に見出しは置かない"),
        (lambda c: c.replace("## 検証\n\n", "## 検証\n\n### 補足\n\n", 1), "`## 検証`の配下に見出しは置かない"),
        (lambda c: c.replace("## 進捗ログ\n\n", "## 進捗ログ\n\n### 補足\n\n", 1), "`## 進捗ログ`の配下に見出しは置かない"),
        (lambda c: c.replace(_SUMMARY, "", 1), "H1直下に要約（全体像、目的、対象範囲）を書く"),
        (
            lambda c: c.replace(_SUMMARY, f"{_SUMMARY}## {_plan_format.PLAN_H2_OVERVIEW}\n\n{_SUMMARY}", 1),
            "計画メタ情報はH1直下へ置く: 実際=`## 概要`直下",
        ),
    ],
    ids=[
        "empty",
        "order",
        "unknown-h2",
        "indented-h2",
        "action-child",
        "verification-child",
        "progress-child",
        "summary",
        "overview",
    ],
)
def test_rejects_invalid_layout(repo: tuple[pathlib.Path, str], edit, expected: str) -> None:
    """置いた節の空、順序違い、未知のH2、行頭に空白を置いたH2、固定H2配下の見出し、要約の欠落、計画メタ情報の配置違いを拒否する。"""
    work_dir, _base = repo
    content = edit(_plan_fixture.request_plan(repo=work_dir.resolve()))
    errors, _warnings = _check(work_dir, content)
    assert any(expected in error for error in errors), errors


def test_bug_plan_without_related_wi_requires_cause_analysis(repo: tuple[pathlib.Path, str]) -> None:
    """関連WIが無いバグ対応の計画は、`## 原因分析`が無ければ不合格にする。"""
    work_dir, _base = repo
    content = _plan_fixture.request_plan(repo=work_dir.resolve(), work_type="バグ対応")
    errors, _warnings = _check(work_dir, content)
    assert any(f"関連WIが無いバグ対応の計画には`## {_plan_format.REQUEST_H2_CAUSE}`が必要" in error for error in errors), errors


def test_normal_change_rejects_cause_analysis(repo: tuple[pathlib.Path, str]) -> None:
    """作業種別が`通常変更`の計画に`## 原因分析`を置くと不合格にする。"""
    work_dir, _base = repo
    content = _plan_fixture.request_plan(repo=work_dir.resolve(), cause=True)
    errors, _warnings = _check(work_dir, content)
    assert errors == [f"作業種別が`通常変更`の計画に`## {_plan_format.REQUEST_H2_CAUSE}`は置かない"]


def test_cause_analysis_reports_missing_label(repo: tuple[pathlib.Path, str]) -> None:
    """`## 原因分析`の行頭語の欠落を、欠けた行頭語を示して不合格にする。"""
    work_dir, _base = repo
    expectation = _plan_fixture.cause_record_line("期待") + "\n"
    content = _plan_fixture.request_plan(repo=work_dir.resolve(), work_type="バグ対応", cause=True)
    assert expectation in content
    errors, _warnings = _check(work_dir, content.replace(expectation, "", 1))
    assert len(errors) == 1, errors
    assert errors[0].startswith(f"`## {_plan_format.REQUEST_H2_CAUSE}`: `期待:`の行が無い"), errors


def test_adopted_user_instruction_requires_quotation(repo: tuple[pathlib.Path, str]) -> None:
    """`ユーザー指示`の採用行を持つ計画は、`## ユーザー指摘の逐語引用`が無ければ不合格にする。"""
    work_dir, _base = repo
    content = _plan_fixture.request_plan(repo=work_dir.resolve()).replace(_QUOTATION, "", 1)
    errors, _warnings = _check(work_dir, content)
    assert len(errors) == 1, errors
    assert f"`## {_plan_format.REQUEST_H2_QUOTATION}`が必要" in errors[0]


def test_awi_and_plan_report_same_request_section_problems(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じ要求を書く節を持つAWIと計画へ、`atk wi add`と計画の検査が同じ診断文を返す。"""
    work_dir, _base = repo
    defective = "## 完成条件\n\n" + _EVIDENCE + _QUOTATION
    problems = _plan_format.request_section_problems(_plan_format.request_sections(defective))
    assert len(problems) == 3, problems

    _patch_cmd_add_operations(monkeypatch)
    args = _cmd_add_args(work_dir, f"# 表題\n\n説明\n\n{defective}", source="test", dry_run=True)
    with pytest.raises(SystemExit) as exc_info:
        _run_public_add(args, _setup_notes(work_dir), _FIXED_DT, work_dir)
    assert exc_info.value.code == 1
    awi_error = capsys.readouterr().err

    plan = _plan_fixture.request_plan(repo=work_dir.resolve())
    plan = plan.replace(_REQUEST + _EVIDENCE + _COMPLETION + _QUOTATION, defective, 1)
    plan_errors, _warnings = _check(work_dir, plan)

    for problem in problems:
        assert problem in awi_error
        assert f"要求を書く節: {problem}" in plan_errors


def test_request_sections_use_structure_check_directly() -> None:
    """構造の検査関数も`check_plan_file.check`と同じく正常系を受理し、作業種別を返す。"""
    content = _plan_fixture.request_plan(work_type="バグ対応", cause=True)
    assert _plan_format.check_plan_request_structure(content) == ("バグ対応", [])
    missing = content.replace(_REQUEST, "", 1)
    assert _plan_format.check_plan_request_structure(missing)[1] == [
        f"要求を書く節: 非空の必須節がありません: {_plan_format.REQUEST_H2_REQUEST}"
    ]


def test_detects_each_plan_format(tmp_path: pathlib.Path) -> None:
    """現行の形式、8節形式、旧二ファイル形式、旧単一ファイル形式を判別する。"""
    path = tmp_path / "plan.md"
    request = _plan_fixture.request_plan()
    assert check_plan_file.detect_plan_format(path, request) == check_plan_file.FORMAT_REQUEST
    # 必須節を欠いた現行の形式の原稿も、計画メタ情報の配置から現行の形式として扱う。
    assert check_plan_file.detect_plan_format(path, request.replace(_REQUEST, "", 1)) == check_plan_file.FORMAT_REQUEST
    assert check_plan_file.detect_plan_format(path, _plan_fixture.eight_section_plan()) == check_plan_file.FORMAT_EIGHT_SECTION
    assert check_plan_file.detect_plan_format(path, _plan_fixture.single_file_plan()) == check_plan_file.FORMAT_LEGACY
    (tmp_path / "plan.detail.md").write_text(_plan_fixture.two_file_detail(), encoding="utf-8")
    assert check_plan_file.detect_plan_format(path, _plan_fixture.two_file_main()) == check_plan_file.FORMAT_TWO_FILE


def test_progress_and_verification_commands_read_request_plan(tmp_path: pathlib.Path) -> None:
    """現行の形式の計画へ進捗を追記でき、`## 検証`の検証コマンドを取り出せる。"""
    path = tmp_path / "plan.md"
    path.write_text(_plan_fixture.request_plan(), encoding="utf-8")
    assert append_progress_log.main([str(path), "--completed-step", "実装", "--result", "成功"]) == 0
    content = path.read_text(encoding="utf-8")
    rows = _plan_format.progress_log_rows(content)
    assert [row[1:] for row in rows] == [("実装", "成功")]
    assert _verification.plan_commands(content) == [("pytest", ["pytest"])]


__all__ = ["fixture_repo"]
