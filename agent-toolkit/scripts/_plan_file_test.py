"""_plan_file.pyのis_plan_file判定挙動を検証する。"""

import pathlib

import _plan_file
import pytest


@pytest.fixture
def _plans_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """`~/.claude/plans/`を`tmp_path`配下に振り替える。"""
    home = tmp_path / "home"
    plans = home / ".claude" / "plans"
    plans.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return plans


def test_is_plan_file_normal_md_returns_true(_plans_home: pathlib.Path) -> None:
    """`~/.claude/plans/`直下の`.md`は計画ファイル判定される。"""
    plan = _plans_home / "sample.md"
    plan.write_text("# t\n", encoding="utf-8")
    assert _plan_file.is_plan_file(str(plan)) is True


def test_is_plan_file_review_md_excluded(_plans_home: pathlib.Path) -> None:
    """`.review.md`サフィックスは副次ファイルとして除外される。"""
    path = _plans_home / "sample.review.md"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_file(str(path)) is False


def test_is_plan_file_codex_log_excluded(_plans_home: pathlib.Path) -> None:
    """`.codex.log`サフィックスは副次ファイルとして除外される。"""
    path = _plans_home / "sample.codex.log"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_file(str(path)) is False


def test_is_plan_file_workaround_check_excluded(_plans_home: pathlib.Path) -> None:
    """`-workaround-check.md`サフィックスは副次ファイルとして除外される。"""
    path = _plans_home / "sample-workaround-check.md"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_file(str(path)) is False


def test_is_plan_file_subdirectory_excluded(_plans_home: pathlib.Path) -> None:
    """サブディレクトリ配下は対象外。"""
    subdir = _plans_home / "sub"
    subdir.mkdir()
    path = subdir / "sample.md"
    path.write_text("x\n", encoding="utf-8")
    assert _plan_file.is_plan_file(str(path)) is False


def test_is_plan_file_empty_path_returns_false() -> None:
    assert _plan_file.is_plan_file("") is False
