"""plans checkoutの契約を検証する。"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import runpy

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import plans as _atk_plans
from agent_toolkit._atk import run_script
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._testing import git_repository
from agent_toolkit._testing import plan_fixture as _plan_fixture
from agent_toolkit._testing.plans_support import (
    _clone_notes,
    _create_saved_plan,
    _init_local_notes,
    _init_remote_notes,
    _isolate_state_directory,
)

__all__ = ["_isolate_state_directory"]


def test_checkout_copies_saved_bundle_into_working_root(tmp_path: pathlib.Path) -> None:
    """保存済みバンドルをbytes保持で`~/.claude/plans`へ取得し、取得時点を記録する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-再実装-d4f9.md")
    main, detail = _create_saved_plan(notes, relative)

    copied = _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)

    working_root = _plan_file.working_plans_root(home)
    assert copied == (working_root / detail.name, working_root / main.name)
    assert {path.name: path.read_bytes() for path in copied} == {
        detail.name: detail.read_bytes(),
        main.name: main.read_bytes(),
    }
    record = _atk_plans._read_checkout_record(pathlib.Path(main.name))  # pylint: disable=protected-access
    assert record is not None
    recorded_relative, snapshots = record
    assert recorded_relative == relative
    assert snapshots == {detail.name: detail.read_bytes(), main.name: main.read_bytes()}


@pytest.mark.parametrize("input_source", ["name", "checkout", "list"])
def test_cli_checkout_edit_and_commit_saved_bundle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], input_source: str
) -> None:
    """公開CLIで保存済み計画を取得し、編集内容を元の保存先へ反映する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_remote_notes(notes, tmp_path / "origin.git")
    relative = pathlib.Path("2026/08/30-1310_再編集.md")
    saved_main, _ = _create_saved_plan(notes, relative)
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["plans", "checkout", str(saved_main)], home=home, _output_capture_active=True)

    checkout_output = capsys.readouterr().out

    working_main = _plan_file.working_plans_root(home) / saved_main.name
    working_main.write_text("# updated main\n", encoding="utf-8")

    argument = saved_main.name
    if input_source == "checkout":
        argument = checkout_output.strip().rsplit(": ", maxsplit=1)[1]
    elif input_source == "list":
        with pytest.raises(SystemExit, match="0"):
            atk.main(["plans", "list"], home=home, _output_capture_active=True)
        argument = capsys.readouterr().out.splitlines()[0].split("\t")[0]
    with pytest.raises(SystemExit, match="0"):
        atk.main(["plans", "commit", argument, "--skip-push"], home=home, _output_capture_active=True)

    assert saved_main.read_text(encoding="utf-8") == "# updated main\n"
    assert not working_main.exists()


@pytest.mark.parametrize("operation", ["commit", "checkout"])
def test_plan_absolute_path_outside_roots_is_rejected_without_changes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operation: str
) -> None:
    """許可root外の絶対パスを、状態変更前に受理範囲の案内付きで拒否する。"""
    notes = tmp_path / "notes"
    _init_local_notes(notes)
    outside = tmp_path / "14-1310_計画.md"
    outside.write_text("# 保持\n", encoding="utf-8")
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    before = git_repository.git_output(notes, "rev-parse", "HEAD")
    with pytest.raises(SystemExit) as rejected:
        atk.main(["plans", operation, str(outside)], home=tmp_path / "home", _output_capture_active=True)
    assert rejected.value.code != 0
    diagnostic = capsys.readouterr().err
    assert "絶対パス" in diagnostic and "yyyy/MM" in diagnostic and "~/.claude/plans" in diagnostic
    assert outside.read_text(encoding="utf-8") == "# 保持\n"
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == before
    assert not git_repository.git_output(notes, "status", "--short")


def test_commit_accepts_legacy_dated_working_absolute_path(tmp_path: pathlib.Path) -> None:
    """日付階層に残る旧作業ファイルも、listが返す絶対パスのまま保存できる。"""
    notes = tmp_path / "notes"
    home = tmp_path / "home"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-旧計画-1a2b.md")
    working = _plan_file.working_plans_root(home) / relative
    working.parent.mkdir(parents=True)
    working.write_text("# 旧計画\n", encoding="utf-8")
    listed = _atk_plans.list_working_plans(home)
    result = _atk_plans.commit_plan(notes, str(listed[0]["path"]), home=home, skip_push=True)
    assert result["plan_file"] == relative.as_posix()
    assert (_plan_file.new_plans_root(notes) / relative).read_text(encoding="utf-8") == "# 旧計画\n"
    assert not working.exists()


def test_checkout_syncs_remote_before_reading_saved_bundle(tmp_path: pathlib.Path) -> None:
    """remoteだけが新しい場合も同期後の保存元を取得する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    other = tmp_path / "other-notes"
    _init_remote_notes(notes, remote)
    relative = pathlib.Path("2026/08/30-remote同期-d4f9.md")
    saved_main, _detail = _create_saved_plan(notes, relative)
    git_repository.run_git(notes, "push")
    _clone_notes(remote, other)
    other_main = other / "plans" / relative
    other_main.write_text("# remote latest\n", encoding="utf-8")
    git_repository.run_git(other, "add", "plans")
    git_repository.run_git(other, "commit", "-m", "update saved plan")
    git_repository.run_git(other, "push")

    copied = _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)

    working_main = next(path for path in copied if path.name == saved_main.name)
    assert working_main.read_text(encoding="utf-8") == "# remote latest\n"
    assert saved_main.read_text(encoding="utf-8") == "# remote latest\n"


def test_checkout_rejection_reports_recovery_commands(tmp_path: pathlib.Path) -> None:
    """取得済みのため拒否する場合は、取得記録の回収と再取得のコマンドを示す。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-再取得-d4f9.md")
    main, _detail = _create_saved_plan(notes, relative)
    _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)

    with pytest.raises(_wi_web_input.WebInputError) as exc_info:
        _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)

    # 次の操作は理由と別の行で届くため、2行の本文から回収コマンドを確かめる。
    message = exc_info.value.message
    assert "\n次の操作: " in message
    assert f"atk plans commit {main.name}" in exc_info.value.next_action
    assert "取得記録を回収" in exc_info.value.next_action


@pytest.mark.parametrize("conflict", ["working", "record", "missing-main"])
def test_checkout_rejects_conflicting_working_file_and_existing_record(
    tmp_path: pathlib.Path,
    conflict: str,
) -> None:
    """作業側の同名ファイル、取得済み、メイン計画の欠落のいずれでも、保存側と作業側を変えずに拒否する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-取得拒否-d4f9.md")
    main, detail = _create_saved_plan(notes, relative)
    working_main = _plan_file.working_plans_root(home) / main.name
    if conflict == "working":
        working_main.parent.mkdir(parents=True)
        working_main.write_text("existing\n", encoding="utf-8")
    elif conflict == "record":
        _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    else:
        main.unlink()
    saved_before = {path.name: path.read_bytes() for path in (main, detail) if path.exists()}
    working_before = {path.name: path.read_bytes() for path in _plan_file.working_plans_root(home).glob("*") if path.is_file()}

    with pytest.raises(_wi_web_input.WebInputError):
        _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)

    assert {path.name: path.read_bytes() for path in (main, detail) if path.exists()} == saved_before
    assert {
        path.name: path.read_bytes() for path in _plan_file.working_plans_root(home).glob("*") if path.is_file()
    } == working_before


@pytest.mark.parametrize("saved_change", ["changed-main", "added-file"])
def test_commit_rejects_checked_out_plan_when_saved_bundle_changed(
    tmp_path: pathlib.Path,
    saved_change: str,
) -> None:
    """取得後の保存側の変更とファイル追加を競合として扱い、双方を変更せずに拒否する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-並行変更-d4f9.md")
    main, _detail = _create_saved_plan(notes, relative)
    _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    working_main = _plan_file.working_plans_root(home) / main.name
    working_main.write_text("# working\n", encoding="utf-8")
    if saved_change == "changed-main":
        main.write_text("# concurrent\n", encoding="utf-8")
    else:
        main.with_name(main.stem + ".bugs.md").write_text("# concurrent\n", encoding="utf-8")
    saved_before = _atk_plans._bundle_contents(  # pylint: disable=protected-access
        _atk_plans._saved_plan_bundle(notes, relative)  # pylint: disable=protected-access
    )

    with pytest.raises(_wi_web_input.WebInputError, match="取得後に保存元") as exc_info:
        _atk_plans.commit_plan(notes, main.name, home=home)

    assert "相違した対象" in str(exc_info.value)
    assert "`~/.claude/plans`の外へ退避" in exc_info.value.next_action
    assert "別名の新しい計画" in exc_info.value.next_action

    assert (
        _atk_plans._bundle_contents(  # pylint: disable=protected-access
            _atk_plans._saved_plan_bundle(notes, relative)  # pylint: disable=protected-access
        )
        == saved_before
    )
    assert working_main.read_text(encoding="utf-8") == "# working\n"


@pytest.mark.parametrize("remote_change", ["changed-main", "added-file"])
def test_commit_rejects_remote_saved_bundle_change_after_checkout(
    tmp_path: pathlib.Path,
    remote_change: str,
) -> None:
    """取得後にremoteで更新された同一ファイルと付属ファイルの追加を同期してから拒否する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    other = tmp_path / "other-notes"
    _init_remote_notes(notes, remote)
    relative = pathlib.Path("2026/08/30-remote並行変更-d4f9.md")
    saved_main, _detail = _create_saved_plan(notes, relative)
    git_repository.run_git(notes, "push")
    _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    working_main = _plan_file.working_plans_root(home) / saved_main.name
    working_main.write_text("# working\n", encoding="utf-8")
    _clone_notes(remote, other)
    other_main = other / "plans" / relative
    if remote_change == "changed-main":
        other_main.write_text("# concurrent\n", encoding="utf-8")
    else:
        other_main.with_name(other_main.stem + ".exec-review.tsv").write_text(
            "# concurrent\n",
            encoding="utf-8",
        )
    git_repository.run_git(other, "add", "plans")
    git_repository.run_git(other, "commit", "-m", "concurrent plan update")
    git_repository.run_git(other, "push")
    saved_before = _atk_plans._bundle_contents(  # pylint: disable=protected-access
        _atk_plans._saved_plan_bundle(notes, relative)  # pylint: disable=protected-access
    )

    with pytest.raises(_wi_web_input.WebInputError, match="取得後に保存元"):
        _atk_plans.commit_plan(notes, saved_main.name, home=home)

    actual = _atk_plans._bundle_contents(  # pylint: disable=protected-access
        _atk_plans._saved_plan_bundle(notes, relative)  # pylint: disable=protected-access
    )
    assert actual == saved_before
    assert working_main.read_text(encoding="utf-8") == "# working\n"
    assert _atk_plans._read_checkout_record(pathlib.Path(saved_main.name)) is not None  # pylint: disable=protected-access
    assert (
        git_repository.run_git(notes, "rev-parse", "HEAD").stdout != git_repository.run_git(notes, "rev-parse", "@{u}").stdout
    )


def test_commit_rejects_broken_checkout_record_with_recovery_steps(tmp_path: pathlib.Path) -> None:
    """壊れた取得記録は、記録を削除して再取得する手順を次の操作として返す。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-記録破損-d4f9.md")
    _create_saved_plan(notes, relative)
    _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    record = _atk_plans._checkout_record_root(pathlib.Path(relative.name))  # pylint: disable=protected-access
    (record / "meta.json").write_text("{", encoding="utf-8")

    with pytest.raises(_wi_web_input.WebInputError, match="取得記録を読み取れません") as error_info:
        _atk_plans.commit_plan(notes, relative.name, home=home)

    assert str(record) in error_info.value.next_action
    assert "atk plans checkout" in error_info.value.next_action


def test_public_save_all_then_restore_unfinished_plan(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """順序付きの全計画保存を終えてから、未終了だけをcheckoutで再開できる場所へ戻す。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    working = _plan_file.working_plans_root(home)
    working.mkdir(parents=True)
    names = ["04-0100_first-abcd.md", "04-0200_second-bcde.md"]
    original = {}
    for name, wi in zip(names, ("20260930-175957-001.md", "20261004-044541-001.md"), strict=True):
        path = working / name
        text = _plan_fixture.current_plan(related_wi=((wi, name),))
        path.write_text(text, encoding="utf-8")
        args = argparse.Namespace(
            script_name="plan-progress",
            script_args=[str(path), "--completed-step", "観測の再開", "--result", "反映後の観測だけが残る"],
        )
        assert run_script.dispatch(args) == 0
        original[name] = path.read_bytes()
    saved = []
    for name in names:
        with pytest.raises(SystemExit, match="0"):
            atk.main(["plans", "commit", name, "--skip-push"], home=home)
        assert not (working / name).exists()
        output = capsys.readouterr().out
        assert name in output
        matches = list((notes / "plans").rglob(name))
        assert len(matches) == 1 and matches[0].read_bytes() == original[name]
        saved.append(matches[0].relative_to(notes / "plans").as_posix())
    assert [pathlib.Path(item).name for item in saved] == names
    with pytest.raises(SystemExit, match="0"):
        atk.main(["plans", "checkout", saved[0]], home=home)
    assert (working / names[0]).read_bytes() == original[names[0]]
    assert not (working / names[1]).exists()
    assert "20260930-175957-001.md" in (working / names[0]).read_text(encoding="utf-8")
    assert "反映後の観測だけが残る" in (working / names[0]).read_text(encoding="utf-8")


def test_generated_plan_names_are_current_and_help_shares_the_format(capsys: pytest.CaptureFixture[str]) -> None:
    """作成側の生成名を保存検査が現行として受理し、現行名の拒否にも同じ形式を案内する。"""
    creator = runpy.run_path(str(run_script.PLUGIN_ROOT / "skills/write-plan/scripts/create_plan_files.py"))
    now = datetime.datetime(2026, 10, 14, 13, 10, tzinfo=datetime.UTC)
    stems = [creator["process_lane_plan_name"]("lane-01", now=now), creator["named_plan_name"]("計画", now=now)]
    for stem in stems:
        for suffix in ("", "-abcd"):
            name = f"{stem}{suffix}.md"
            assert _plan_file.validate_working_plan_relative_path(name) == pathlib.Path(name)
            assert _plan_file.validate_plan_relative_path(f"2026/10/{name}") == pathlib.Path("2026/10") / name
        with pytest.raises(ActionableError) as invalid:
            _plan_file.validate_working_plan_relative_path(f"{stem}:禁止.md")
        assert "移行済み" not in str(invalid.value)
        assert _plan_file.PLAN_NAME_FORMAT in invalid.value.next_action
        with pytest.raises(ActionableError):
            _plan_file.validate_working_plan_relative_path(f"{stem}.detail.md")
    for subcommand in ("commit", "checkout"):
        with pytest.raises(SystemExit, match="0"):
            atk.main(["plans", subcommand, "--help"], _output_capture_active=True)
        assert _plan_file.PLAN_NAME_FORMAT in capsys.readouterr().out
