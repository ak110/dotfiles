"""plans ci reviewの契約を検証する。"""

from __future__ import annotations

import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import plans as _atk_plans
from agent_toolkit._atk import review_table as _review_table
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._testing import git_repository, review_bodies
from agent_toolkit._testing.plans_support import (
    _SERIES_HEAD,
    _assert_guides_series_head_name,
    _clone_notes,
    _init_local_notes,
    _init_remote_notes,
    _isolate_state_directory,
)

__all__ = ["_isolate_state_directory"]


def test_ci_review_table_round_trip_commits_pushes_and_cleans(tmp_path: pathlib.Path) -> None:
    """対応する計画なしの再帰的CI失敗で同じ表を取得、更新、再保存できる。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    remote = tmp_path / "private-notes.git"
    clone = tmp_path / "private-notes-clone"
    _init_remote_notes(notes, remote)
    first_cause_oid = "0123456"
    second_cause_oid = "89abcde"
    name = f"ci-{first_cause_oid}.exec-review.tsv"
    working = _plan_file.working_plans_root(home) / name
    working.parent.mkdir(parents=True)
    _review_table.init(working)
    _review_table.add(working, "1", "exec-review", "sample.py:1", "初回指摘", "仕様")

    first = _atk_plans.commit_plan(notes, str(working), home=home)

    saved_relative = pathlib.Path("ci") / name
    saved = _plan_file.new_plans_root(notes) / saved_relative
    assert first["plan_file"] == saved_relative.as_posix()
    assert first["kind"] == "ci-review"
    assert saved.is_file()
    assert not working.exists()
    assert git_repository.run_git(notes, "status", "--short").stdout == ""
    _clone_notes(remote, clone)
    assert (clone / "plans" / saved_relative).read_bytes() == saved.read_bytes()

    assert _atk_plans.checkout_plan(notes, str(saved), home=home) == (working,)
    assert _review_table.validate(working, require_responses=False) == 0
    _review_table.respond(
        working,
        "1",
        "exec-review",
        "sample.py:1",
        "初回指摘",
        review_bodies.response_body("修正済み"),
        "",
    )
    _review_table.add(
        working,
        "2",
        "exec-review",
        "sample.py:2",
        f"再帰失敗の指摘（原因commit: {second_cause_oid}）",
        "仕様",
    )
    _review_table.respond(
        working,
        "2",
        "exec-review",
        "sample.py:2",
        f"再帰失敗の指摘（原因commit: {second_cause_oid}）",
        review_bodies.response_body("2回目の修正済み"),
        "",
    )
    assert _review_table.validate(working) == 0

    second = _atk_plans.commit_plan(notes, name, home=home)

    assert second["plan_file"] == saved_relative.as_posix()
    assert not working.exists()
    assert _atk_plans._read_checkout_record(pathlib.Path(name)) is None  # pylint: disable=protected-access
    assert git_repository.run_git(notes, "status", "--short").stdout == ""
    git_repository.run_git(clone, "pull", "--ff-only")
    assert (clone / "plans" / saved_relative).read_bytes() == saved.read_bytes()
    assert saved.read_text().count("exec-review") == 2
    assert saved.name == f"ci-{first_cause_oid}.exec-review.tsv"


@pytest.mark.parametrize(
    "name",
    [
        "ci-0123456789abcdef.exec-review.tsv",
        "ci-0123456789ABCDEF0123456789ABCDEF01234567.exec-review.tsv",
        "ci-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0.exec-review.tsv",
        "other-0123456789abcdef0123456789abcdef01234567.exec-review.tsv",
        "nested/ci-0123456789abcdef0123456789abcdef01234567.exec-review.tsv",
    ],
)
def test_commit_ci_review_rejects_noncanonical_working_name(tmp_path: pathlib.Path, name: str) -> None:
    """7〜64文字の一意な短縮OIDから決まらない独立表名を拒否する。"""
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)

    with pytest.raises(_wi_web_input.WebInputError):
        _atk_plans.commit_plan(notes, name, home=tmp_path / "home")


def test_plans_commit_help_names_ci_review_table_by_series_head(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`atk plans commit --help`の位置引数の説明が、表を修正系列の開始時のHEAD由来の名前で案内する。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["plans", "commit", "--help"], home=tmp_path / "home")

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    _assert_guides_series_head_name(output[output.index("位置引数:") : output.index("オプション:")])
    _assert_guides_series_head_name(output)


def test_ci_review_name_errors_guide_series_head_format(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """書式に合わない名前の表を指定したときの失敗行と次の操作が、修正系列の開始時のHEAD由来の書式を示す。"""
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)

    for argv, prefix in (
        (["plans", "commit", "ci-zz.exec-review.tsv", "--skip-push"], "ci-<"),
        (["plans", "checkout", "ci/ci-zz.exec-review.tsv"], "ci/ci-<"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            atk.main(argv, home=tmp_path / "home")
        assert exc_info.value.code != 0
        error = capsys.readouterr().err
        failure = next(line for line in error.splitlines() if line.startswith("失敗: "))
        next_step = next(line for line in error.splitlines() if line.startswith("次の操作: "))
        for text in (failure, next_step):
            _assert_guides_series_head_name(text)
            assert f"{prefix}{_SERIES_HEAD}" in text


def test_commit_ci_review_rejects_saved_change_after_checkout(tmp_path: pathlib.Path) -> None:
    """独立表の取得後に保存元が変わった場合は作業側を保持して拒否する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    name = "ci-fedcba9876543210fedcba9876543210fedcba98.exec-review.tsv"
    saved_relative = pathlib.Path("ci") / name
    saved = _plan_file.new_plans_root(notes) / saved_relative
    saved.parent.mkdir(parents=True)
    _review_table.init(saved)
    git_repository.run_git(notes, "add", saved.relative_to(notes).as_posix())
    git_repository.run_git(notes, "commit", "-m", "add review")
    (working,) = _atk_plans.checkout_plan(notes, saved_relative.as_posix(), home=home)
    _review_table.add(working, "1", "exec-review", "sample.py:1", "作業側", "詳細")
    _review_table.add(saved, "1", "exec-review", "sample.py:2", "保存側", "詳細")

    with pytest.raises(_wi_web_input.WebInputError, match="取得後に保存元") as exc_info:
        _atk_plans.commit_plan(notes, name, home=home)

    _assert_guides_series_head_name(exc_info.value.next_action)
    assert working.is_file()
    assert _atk_plans._read_checkout_record(pathlib.Path(name)) is not None  # pylint: disable=protected-access


def test_commit_ci_review_accepts_checked_out_saved_forty_character_name(tmp_path: pathlib.Path) -> None:
    """保存済み40文字OID名はcheckout記録を条件に再commitできる。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    name = "ci-fedcba9876543210fedcba9876543210fedcba98.exec-review.tsv"
    saved_relative = pathlib.Path("ci") / name
    saved = _plan_file.new_plans_root(notes) / saved_relative
    saved.parent.mkdir(parents=True)
    _review_table.init(saved)
    git_repository.run_git(notes, "add", saved.relative_to(notes).as_posix())
    git_repository.run_git(notes, "commit", "-m", "add legacy review")
    (working,) = _atk_plans.checkout_plan(notes, saved_relative.as_posix(), home=home)
    _review_table.add(working, "1", "exec-review", "sample.py:1", "保存済み表の更新", "詳細")

    result = _atk_plans.commit_plan(notes, name, home=home, skip_push=True)

    assert result["plan_file"] == saved_relative.as_posix()
    assert saved.read_bytes() != b""
    assert not working.exists()
    assert _atk_plans._read_checkout_record(pathlib.Path(name)) is None  # pylint: disable=protected-access
