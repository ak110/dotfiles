# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""二ファイル計画とバグ調査の参照を検証する。"""

import pathlib
import subprocess
import sys

import check_plan_file
import pytest

from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._plan import structure as _plan_format
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.plan_file_check_support import _check, _check_new, _new_format_plan, _plan
from agent_toolkit._testing.plan_file_check_support import fixture_repo as fixture_repo


def _replace_action_table(content: str, rows: list[str], *, legacy: bool = False) -> str:
    """fixtureの現行列構成を前提とせず、実施内容表を指定した新旧形式へ置き換える。"""
    header = (
        f"| {' | '.join(_plan_format.PLAN_LEGACY_ACTION_TABLE_HEADER)} |\n| --- | --- | --- |"
        if legacy
        else f"| {' | '.join(_plan_format.PLAN_ACTION_TABLE_HEADER)} |\n| --- | --- | --- | --- |"
    )
    start = content.index(f"## {_plan_format.PLAN_H2_ACTION}")
    end = content.index(f"\n## {_plan_format.PLAN_H2_MATERIALS}", start)
    rows_text = "\n".join(rows)
    section = f"## {_plan_format.PLAN_H2_ACTION}\n\n{header}\n{rows_text}\n"
    return content[:start] + section + content[end:]


def test_new_format_reports_one_diagnostic_for_one_duplicate_heading(
    repo: tuple[pathlib.Path, str],
) -> None:
    """一件の重複見出しに対する診断を二ファイルの形式を判定するときに一回だけ返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    detail_content = detail_content.replace(
        "## 完了条件",
        "### 重複見出し\n\n一つ目。\n\n### 重複見出し\n\n二つ目。\n\n## 完了条件",
    )

    errors, _warnings = _check_new(work_dir, main_content, detail_content)

    duplicate_errors = [error for error in errors if "同じ見出しが重複している" in error]
    assert len(duplicate_errors) == 1, errors
    assert "`### 重複見出し`" in duplicate_errors[0]


def test_new_format_detected_by_detail_file_presence(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（詳細）が存在しない同名の計画ファイル（メイン）は旧形式として判定される。"""
    work_dir, base = repo
    errors, _warnings = _check(work_dir, _plan(work_dir, base))
    assert not errors, errors


def test_old_two_file_format_ignores_detail_reference_value(repo: tuple[pathlib.Path, str]) -> None:
    """旧二ファイル形式の詳細参照値はstem導出へ移行したため対応判定に使わない。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace("- 計画ファイル（詳細）: `plan.detail.md`", "- 計画ファイル（詳細）: `other.detail.md`")
    errors, warnings = _check_new(work_dir, main_content, detail_content)
    assert not errors, errors
    assert any("stemから対応付ける" in warning for warning in warnings), warnings


def test_new_format_requires_related_wi_metadata_field(repo: tuple[pathlib.Path, str]) -> None:
    """詳細参照を除いた新書式には`関連WI`が必要である。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace("- 計画ファイル（詳細）: `plan.detail.md`\n", "")
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("`関連WI`" in error for error in errors), errors


def test_new_format_rejects_missing_verification_section(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（メイン）に`## 検証区分`が無い新書式を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace(
        f"## {_plan_format.PLAN_H2_VERIFICATION}\n\n{_plan_fixture.VERIFICATION_TABLE}\n",
        "",
    )
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("固定H2" in error for error in errors), errors


def test_new_format_rejects_bug_section_placed_in_main(repo: tuple[pathlib.Path, str]) -> None:
    """`## バグ調査結果`は計画ファイル（詳細）専用であり計画ファイル（メイン）に置くと拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = main_content.replace(
        "## 検証区分",
        "## バグ調査結果\n\n未使用。\n\n## 検証区分",
    )
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("固定H2は" in error for error in errors), errors


def test_new_format_rejects_missing_bug_sidecar(repo: tuple[pathlib.Path, str]) -> None:
    """バグ対応の計画ファイル（詳細）が参照する計画ファイル（バグ）が無い場合を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    errors, _warnings = _check_new(work_dir, main_content, detail_content, create_bug_file=False)
    assert any("バグ調査ファイルが実在しない" in error for error in errors), errors


def test_new_format_rejects_bug_sidecar_stem_mismatch(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（バグ）のstemが計画ファイル（メイン）と異なる場合を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    detail_content = detail_content.replace("plan.bugs.md", "other.bugs.md")
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("計画stemと一致しない" in error for error in errors), errors


def test_new_format_rejects_bug_sidecar_structure_violation(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（バグ）の固定行の調査表欠落を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    invalid_bug_file = _plan_fixture.bug_file().replace(f"{_plan_fixture.bug_row(_plan_format.PLAN_BUG_TABLE_ROWS[0])}\n", "")
    errors, _warnings = _check_new(work_dir, main_content, detail_content, bug_file_content=invalid_bug_file)
    assert any(f"固定{len(_plan_format.PLAN_BUG_TABLE_ROWS)}行" in error for error in errors), errors


@pytest.mark.parametrize(
    "variant",
    [
        _plan_fixture.BUG_VARIANT_BEFORE_HARM_ROWS,
        _plan_fixture.BUG_VARIANT_PREVIOUS_ROWS,
        _plan_fixture.BUG_VARIANT_LEGACY_ROWS,
    ],
)
def test_creation_rejects_legacy_bug_table_rows(repo: tuple[pathlib.Path, str], variant: str) -> None:
    """新規作成では現行より前の行構成を持つ調査表をエラーにする。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    legacy_bug_file = _plan_fixture.bug_file(variant=variant)
    errors, _warnings = _check_new(
        work_dir,
        main_content,
        detail_content,
        bug_file_content=legacy_bug_file,
        reject_migration_warnings=True,
    )
    assert any("現行より前の行構成" in error for error in errors), errors


@pytest.mark.parametrize("row", ["害の型と反復", "再発防止策の採否"])
def test_new_format_rejects_empty_bug_sidecar_content(repo: tuple[pathlib.Path, str], row: str) -> None:
    """計画ファイル（バグ）の`内容`空欄を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    invalid_bug_file = _plan_fixture.bug_file().replace(
        _plan_fixture.bug_row(row),
        f"| {row} |  |",
    )
    errors, _warnings = _check_new(work_dir, main_content, detail_content, bug_file_content=invalid_bug_file)
    assert any("空の`内容`" in error for error in errors), errors


def test_new_format_rejects_detail_structure_violation(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイル（詳細）の固定H2欠落も基準に違反すると判定される。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    detail_content = detail_content.replace(
        "## 完了条件\n\n基準値は診断2件、目標は1件とし、CLIを再実行して標準エラーの行数を測定する。\n",
        "",
    )
    errors, _warnings = _check_new(work_dir, main_content, detail_content)
    assert any("固定H2" in error for error in errors), errors


def test_new_format_reports_short_action_row_without_index_error(repo: tuple[pathlib.Path, str]) -> None:
    """列不足の新4列表は例外を送出せず、既存の構造診断として拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base)
    main_content = _replace_action_table(
        main_content,
        ["| 診断件数を2件から1件へ減らす |"],
    )

    errors, _warnings = _check_new(work_dir, main_content, detail_content)

    assert any("実施内容`の表の列数が一致しない" in error and "`\\|`へエスケープ" in error for error in errors), errors


def test_new_format_warns_for_legacy_inline_bug_table(repo: tuple[pathlib.Path, str]) -> None:
    """2ファイル書式でも本文内の旧バグ調査表を受理し、移行warningを返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    reference = _plan_format.extract_bug_file_reference(detail_content)
    if reference is not None:
        detail_content = detail_content.replace(
            _plan_fixture.bug_reference_section(reference),
            _plan_fixture.inline_bug_section(variant=_plan_fixture.BUG_VARIANT_LEGACY_STANDALONE),
        )
    errors, warnings = _check_new(work_dir, main_content, detail_content)
    assert not errors, errors
    expected = ["バグ調査結果が旧形式の本文内表である。新規作成・改訂ではバグ調査ファイルへ移行する"]
    if _plan_format.has_legacy_action_table(main_content):
        expected.append("実施内容表が旧3列表である。新規作成・改訂では4列表へ移行する")
    expected.extend(
        [
            "計画メタ情報の`計画ファイル（詳細）`が旧形式である。新規作成・改訂ではstemから対応付ける",
            "`## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連WI`へ移行する",
        ]
    )
    expected.append("二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する")
    assert warnings == expected


def _adjunct_reference_plan(repo: pathlib.Path, base: str, *, stem: str = "plan") -> tuple[str, str]:
    """計画ファイル（バグ）を新しい参照値で指す計画を組み立てて返す。"""
    main, detail = _new_format_plan(repo, base, bug=True, detail_name=f"{stem}.detail.md")
    absolute = str((repo / f"{stem}.bugs.md").resolve())
    reference = f"{_plan_file.PLAN_ADJUNCT_REFERENCE_PREFIX}{stem}.bugs.md"
    return main, detail.replace(absolute, reference)


def test_new_format_accepts_adjunct_bug_file_reference(repo: tuple[pathlib.Path, str]) -> None:
    """計画ファイルと同じディレクトリを基準に新しい参照値を解決する。"""
    work_dir, base = repo
    main_content, detail_content = _adjunct_reference_plan(work_dir, base)
    errors, warnings = _check_new(work_dir, main_content, detail_content)
    assert not errors, errors
    assert not any("付属ファイル参照が旧表記" in warning for warning in warnings), warnings


def test_adjunct_bug_file_reference_resolves_from_any_plan_directory(
    repo: tuple[pathlib.Path, str], tmp_path: pathlib.Path
) -> None:
    """同じ参照値が、計画を置いたどちらのディレクトリでも同じ計画の実体を指す。"""
    work_dir, base = repo
    main_content, detail_content = _adjunct_reference_plan(work_dir, base)
    saved_directory = tmp_path / "saved" / "2026" / "09"
    saved_directory.mkdir(parents=True)
    main_path = saved_directory / "plan.md"
    main_path.write_text(main_content, encoding="utf-8")
    (saved_directory / "plan.detail.md").write_text(detail_content, encoding="utf-8")
    (saved_directory / "plan.bugs.md").write_text(_plan_fixture.bug_file(), encoding="utf-8")
    errors, _warnings = check_plan_file.check(main_path, work_dir)
    assert not errors, errors


def test_new_format_rejects_adjunct_reference_with_path_separator(repo: tuple[pathlib.Path, str]) -> None:
    """新しい参照値にパス区切り文字を含む場合を拒否する。"""
    work_dir, base = repo
    main_content, detail_content = _adjunct_reference_plan(work_dir, base)
    detail_content = detail_content.replace(
        f"{_plan_file.PLAN_ADJUNCT_REFERENCE_PREFIX}plan.bugs.md",
        f"{_plan_file.PLAN_ADJUNCT_REFERENCE_PREFIX}2026/09/plan.bugs.md",
    )
    errors, _warnings = _check_new(work_dir, main_content, detail_content, create_bug_file=False)
    assert any("参照値が不正です" in error for error in errors), errors


def test_new_format_warns_for_legacy_adjunct_reference_notation(repo: tuple[pathlib.Path, str]) -> None:
    """絶対パスの参照は読み取りで受理し、新しい参照値への移行warningを返す。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True)
    errors, warnings = _check_new(work_dir, main_content, detail_content)
    assert not errors, errors
    assert any("付属ファイル参照が旧表記" in warning for warning in warnings), warnings


def test_new_format_accepts_portable_bug_file_reference(
    repo: tuple[pathlib.Path, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """可搬表記の参照から、新しい保存先にある計画ファイル（バグ）の構造を確かめられる。"""
    work_dir, base = repo
    private_notes = work_dir / "private-notes"
    stem = "30-計画保存先移行-d4f9"
    detail_name = f"{stem}.detail.md"
    main_content, detail_content = _new_format_plan(work_dir, base, bug=True, detail_name=detail_name)
    absolute_bug_path = (work_dir / f"{stem}.bugs.md").resolve()
    portable_bug_path = f"$(atk config get private_notes)/plans/2026/08/{stem}.bugs.md"
    detail_content = detail_content.replace(str(absolute_bug_path), portable_bug_path)

    plan_directory = private_notes / "plans/2026/08"
    plan_directory.mkdir(parents=True)
    main_path = plan_directory / f"{stem}.md"
    main_path.write_text(main_content, encoding="utf-8")
    (plan_directory / detail_name).write_text(detail_content, encoding="utf-8")
    (plan_directory / f"{stem}.bugs.md").write_text(_plan_fixture.bug_file(), encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))

    errors, _warnings = check_plan_file.check(main_path, work_dir, private_notes=private_notes)

    assert not errors, errors


def test_cli_accepts_new_format_plan(repo: tuple[pathlib.Path, str]) -> None:
    """CLI経由でも新書式の計画ファイル（メイン）・計画ファイル（詳細）の組を受理する。"""
    work_dir, base = repo
    main_content, detail_content = _new_format_plan(work_dir, base, detail_name="new-format-plan.detail.md")
    path = work_dir / "new-format-plan.md"
    path.write_text(main_content, encoding="utf-8")
    (work_dir / "new-format-plan.detail.md").write_text(detail_content, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(pathlib.Path(check_plan_file.__file__)), "--work-dir", str(work_dir), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == (
        "[warn] 計画メタ情報の`計画ファイル（詳細）`が旧形式である。新規作成・改訂ではstemから対応付ける\n"
        "[warn] `## 提示素材`が旧形式である。新規作成・改訂では計画メタ情報の`関連WI`へ移行する\n"
        "[warn] 二ファイル計画が旧ID形式である。新規作成・改訂では人間向け書式へ移行する\n"
    )


__all__ = ["fixture_repo"]
