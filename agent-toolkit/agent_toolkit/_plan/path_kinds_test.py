"""計画rootの配下にあるパスの種別判定（`_plan/path_kinds.py`）を検証する。"""

import pathlib

import pytest

from agent_toolkit._plan import path_kinds as _path_kinds


@pytest.fixture
def _plans_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """`~/.claude/plans/`を`tmp_path`配下に振り替える。"""
    home = tmp_path / "home"
    plans = home / ".claude" / "plans"
    plans.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return plans


def test_is_plan_main_file_normal_md_returns_true(_plans_home: pathlib.Path) -> None:
    """`~/.claude/plans/`直下の`.md`は計画ファイル（メイン）として真になる。"""
    plan = _plans_home / "sample.md"
    plan.write_text("# t\n", encoding="utf-8")
    assert _path_kinds.is_plan_main_file(str(plan)) is True


def test_is_plan_main_file_detail_md_returns_false(_plans_home: pathlib.Path) -> None:
    """計画ファイル（詳細）は計画ファイル（メイン）述語では偽になる。"""
    plan = _plans_home / "sample.detail.md"
    plan.write_text("# t\n", encoding="utf-8")
    assert _path_kinds.is_plan_main_file(str(plan)) is False


def test_is_plan_main_file_bugs_md_returns_false(_plans_home: pathlib.Path) -> None:
    """計画ファイル（バグ）は計画ファイル（メイン）述語では偽になる。"""
    path = _plans_home / "sample.bugs.md"
    path.write_text("# t\n", encoding="utf-8")
    assert _path_kinds.is_plan_main_file(str(path)) is False


def test_is_plan_main_file_review_md_excluded(_plans_home: pathlib.Path) -> None:
    """`.review.md`サフィックスは副次ファイルとして除外される。"""
    path = _plans_home / "sample.review.md"
    path.write_text("x\n", encoding="utf-8")
    assert _path_kinds.is_plan_main_file(str(path)) is False


def test_is_plan_main_file_codex_log_excluded(_plans_home: pathlib.Path) -> None:
    """`.codex.log`サフィックスは副次ファイルとして除外される。"""
    path = _plans_home / "sample.codex.log"
    path.write_text("x\n", encoding="utf-8")
    assert _path_kinds.is_plan_main_file(str(path)) is False


def test_is_plan_main_file_workaround_check_excluded(_plans_home: pathlib.Path) -> None:
    """`-workaround-check.md`サフィックスは副次ファイルとして除外される。"""
    path = _plans_home / "sample-workaround-check.md"
    path.write_text("x\n", encoding="utf-8")
    assert _path_kinds.is_plan_main_file(str(path)) is False


def test_is_plan_main_file_subdirectory_excluded(_plans_home: pathlib.Path) -> None:
    """サブディレクトリ配下は対象外。"""
    subdir = _plans_home / "sub"
    subdir.mkdir()
    path = subdir / "sample.md"
    path.write_text("x\n", encoding="utf-8")
    assert _path_kinds.is_plan_main_file(str(path)) is False


def test_is_plan_main_file_empty_path_returns_false() -> None:
    assert _path_kinds.is_plan_main_file("") is False


def test_is_plan_handoff_file_returns_true(_plans_home: pathlib.Path) -> None:
    """引き継ぎ記録は専用の述語でだけ真になる。"""
    path = _plans_home / "sample.handoff.md"
    path.write_text("# t\n", encoding="utf-8")
    assert _path_kinds.is_plan_handoff_file(str(path)) is True
    assert _path_kinds.is_plan_main_file(str(path)) is False
    assert _path_kinds.is_plan_adjunct_file(str(path)) is False


@pytest.mark.parametrize("name", ["sample.md", "sample.bugs.md", "sample.detail.md"])
def test_is_plan_handoff_file_other_names_return_false(_plans_home: pathlib.Path, name: str) -> None:
    """引き継ぎ記録以外の計画ファイルは専用の述語で偽になる。"""
    path = _plans_home / name
    path.write_text("# t\n", encoding="utf-8")
    assert _path_kinds.is_plan_handoff_file(str(path)) is False


def test_is_plan_adjunct_file_bugs_md_returns_true(_plans_home: pathlib.Path) -> None:
    """`~/.claude/plans/`直下の`.bugs.md`は計画付属ファイルとして真になる。"""
    path = _plans_home / "sample.bugs.md"
    path.write_text("# t\n", encoding="utf-8")
    assert _path_kinds.is_plan_adjunct_file(str(path)) is True


@pytest.mark.parametrize("name", ["sample.md", "sample.detail.md", "sample.review.md", "sample.bugs.txt"])
def test_is_plan_adjunct_file_non_bugs_files_return_false(_plans_home: pathlib.Path, name: str) -> None:
    """計画ファイル（メイン）、詳細および副次ファイルは付属ファイル述語で偽になる。"""
    path = _plans_home / name
    path.write_text("x\n", encoding="utf-8")
    assert _path_kinds.is_plan_adjunct_file(str(path)) is False


def test_is_plan_adjunct_file_subdirectory_excluded(_plans_home: pathlib.Path) -> None:
    """サブディレクトリ配下の`.bugs.md`は対象外。"""
    subdir = _plans_home / "sub"
    subdir.mkdir()
    path = subdir / "sample.bugs.md"
    path.write_text("x\n", encoding="utf-8")
    assert _path_kinds.is_plan_adjunct_file(str(path)) is False


def test_is_plan_adjunct_file_empty_path_returns_false() -> None:
    assert _path_kinds.is_plan_adjunct_file("") is False


def test_new_plan_predicates_recognize_main_and_bugs_only(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """新plans rootではmain・bugsだけを現行hook向け述語で分類する。"""
    private_notes = tmp_path / "private-notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))
    main = private_notes / "plans/2026/08/30-計画保存先移行-d4f9.md"
    detail = main.with_name(main.stem + ".detail.md")
    bugs = main.with_name(main.stem + ".bugs.md")
    main.parent.mkdir(parents=True)
    for path in (main, detail, bugs):
        path.write_text("# 計画\n", encoding="utf-8")

    assert _path_kinds.is_plan_main_file(str(main)) is True
    assert _path_kinds.is_plan_main_file(str(detail)) is False
    assert _path_kinds.is_plan_adjunct_file(str(bugs)) is True
    assert _path_kinds.is_plan_main_file(str(bugs)) is False
