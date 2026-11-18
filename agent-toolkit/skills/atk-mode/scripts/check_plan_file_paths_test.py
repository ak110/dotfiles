# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""作業中計画のファイル名を検証する。"""

import pathlib

import check_plan_file
import pytest

from agent_toolkit._testing.plan_file_check_support import _TWO_FILE_MIGRATION, _plan, human_new_format_plan
from agent_toolkit._testing.plan_file_check_support import fixture_repo as fixture_repo

_FILENAME_ERROR_MARKER = "`~/.claude/plans`直下の計画ファイル名が保存工程の受理条件を満たさない"


@pytest.mark.parametrize(
    "relative",
    [pathlib.Path("30-計画保存先移行-d4f9.md"), pathlib.Path("2026/08/30-計画保存先移行-d4f9.md")],
)
def test_accepts_direct_and_date_hierarchy_working_paths(
    repo: tuple[pathlib.Path, str],
    tmp_path: pathlib.Path,
    relative: pathlib.Path,
) -> None:
    """直下形式と既存の日付階層形式の作業計画を同じ構造の判定条件で受理する。"""
    work_dir, _base = repo
    home = tmp_path / "home"
    main_path = home / ".claude/plans" / relative
    detail_path = main_path.with_name(main_path.stem + ".detail.md")
    main_path.parent.mkdir(parents=True, exist_ok=True)
    main_content, detail_content = human_new_format_plan(work_dir)
    main_path.write_text(main_content, encoding="utf-8")
    detail_path.write_text(detail_content, encoding="utf-8")

    errors, warnings = check_plan_file.check(main_path, work_dir, home=home)

    assert not errors, errors
    assert warnings == [_TWO_FILE_MIGRATION]


@pytest.mark.parametrize(
    ("filename", "rejected"),
    [
        ("example-plan.md", True),
        ("13-1310_計画名検査.md", False),
        ("13-1310_計画名検査-a1b2.md", False),
        ("13-計画名検査-a1b2.md", False),
        ("13-legacy.md", False),
    ],
)
def test_working_plan_filename_follows_save_stage_condition(
    repo: tuple[pathlib.Path, str],
    tmp_path: pathlib.Path,
    filename: str,
    rejected: bool,
) -> None:
    """`~/.claude/plans`直下の計画ファイル名が保存工程と同じ受理条件を満たすか確かめる。"""
    work_dir, base = repo
    home = tmp_path / "home"
    plan_path = home / ".claude/plans" / filename
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(_plan(work_dir, base), encoding="utf-8")

    errors, _warnings = check_plan_file.check(plan_path, work_dir, home=home)

    filename_errors = [error for error in errors if _FILENAME_ERROR_MARKER in error]
    assert bool(filename_errors) is rejected, errors
    if rejected:
        assert "dd-HHmm_<名称>.md" in filename_errors[0]


def test_working_plan_filename_is_not_checked_outside_working_root(
    repo: tuple[pathlib.Path, str],
    tmp_path: pathlib.Path,
) -> None:
    """`~/.claude/plans`直下に無い計画ファイルの名前が所定の形式であるかは判定しない。"""
    work_dir, base = repo
    home = tmp_path / "home"
    plan_path = home / ".claude/plans/2026/08/example-plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(_plan(work_dir, base), encoding="utf-8")

    errors, _warnings = check_plan_file.check(plan_path, work_dir, home=home)

    assert not [error for error in errors if _FILENAME_ERROR_MARKER in error], errors


__all__ = ["fixture_repo"]
