"""plansの契約を検証する。"""

from __future__ import annotations

import pathlib
import types

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk import plans as _atk_plans
from agent_toolkit._atk import plans_storage as _plans_storage
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import web_input as _wi_web_input
from agent_toolkit._plan import locations as _plan_file
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.plans_support import (
    _assert_preserved_birth,
    _assert_preserved_times,
    _clone_notes,
    _create_saved_plan,
    _init_local_notes,
    _init_remote_notes,
    _isolate_state_directory,
    _set_stable_mtime,
)

__all__ = ["_isolate_state_directory"]


def test_commit_updates_checked_out_plan_and_preserves_saved_creation_time(tmp_path: pathlib.Path) -> None:
    """取得済み計画を同じinodeへ更新し、保存側の作成日時を維持する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-作成日時維持-d4f9.md")
    saved_main, saved_detail = _create_saved_plan(notes, relative)
    saved_inode = saved_main.stat().st_ino
    saved_birth = _plan_file._creation_epoch(saved_main)  # pylint: disable=protected-access
    _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    working_main = _plan_file.working_plans_root(home) / saved_main.name
    working_main.write_text("# updated\n", encoding="utf-8")
    working_detail = working_main.with_name(saved_detail.name)
    working_detail.unlink()
    working_attachment = working_main.with_name(working_main.stem + ".supplement.md")
    working_attachment.write_text("# supplement\n", encoding="utf-8")
    _set_stable_mtime(working_main)

    _atk_plans.commit_plan(notes, saved_main.name, home=home)

    assert saved_main.read_text(encoding="utf-8") == "# updated\n"
    assert saved_main.stat().st_ino == saved_inode
    _assert_preserved_birth(saved_main, saved_birth)
    assert not working_main.exists()
    assert saved_detail.read_text(encoding="utf-8") == "# saved detail\n"
    assert not (saved_main.parent / working_attachment.name).exists()
    assert working_attachment.read_text(encoding="utf-8") == "# supplement\n"


def test_commit_uses_recorded_relative_main_instead_of_birth_month(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取得済み計画は記録した取得元へ戻す。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2024/02/03-取得元維持-d4f9.md")
    saved_main, _detail = _create_saved_plan(notes, relative)
    _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    working_main = _plan_file.working_plans_root(home) / saved_main.name
    working_main.write_text("# updated\n", encoding="utf-8")
    monkeypatch.setattr(_plans_storage, "birth_date", lambda _path: "2030/12/31")

    result = _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert result["plan_file"] == relative.as_posix()
    assert saved_main.read_text(encoding="utf-8") == "# updated\n"
    assert not (notes / "plans/2030/12" / saved_main.name).exists()


def test_commit_resumes_checked_out_plan_after_push_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存更新後のpush失敗では作業側と記録を保持し、再実行で完了する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)
    relative = pathlib.Path("2026/08/30-push再開-d4f9.md")
    saved_main, _detail = _create_saved_plan(notes, relative)
    git_repository.run_git(notes, "push")
    _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    working_main = _plan_file.working_plans_root(home) / saved_main.name
    working_main.write_text("# updated\n", encoding="utf-8")
    original = _atk_git_sync.commit_and_push

    def fail_after_commit(private_notes, message, paths, *, skip_push: bool = False):  # pylint: disable=unused-argument
        original(private_notes, message, paths, skip_push=True)
        raise RuntimeError("push failure")

    monkeypatch.setattr(_atk_git_sync, "commit_and_push", fail_after_commit)
    with pytest.raises(RuntimeError, match="push failure"):
        _atk_plans.commit_plan(notes, saved_main.name, home=home)
    assert working_main.is_file()
    assert _atk_plans._read_checkout_record(pathlib.Path(saved_main.name)) is not None  # pylint: disable=protected-access

    monkeypatch.setattr(_atk_git_sync, "commit_and_push", original)
    _atk_plans.commit_plan(notes, saved_main.name, home=home)

    assert not working_main.exists()
    assert _atk_plans._read_checkout_record(pathlib.Path(saved_main.name)) is None  # pylint: disable=protected-access
    assert (
        git_repository.run_git(notes, "rev-parse", "HEAD").stdout == git_repository.run_git(notes, "rev-parse", "@{u}").stdout
    )


def test_commit_keeps_checkout_when_diverged_push_is_deferred(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """remote分岐と無関係なdirty差分でpushを保留した場合は取得状態を保持する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    other = tmp_path / "other-notes"
    _init_remote_notes(notes, remote)
    relative = pathlib.Path("2026/08/30-push保留-d4f9.md")
    saved_main, _detail = _create_saved_plan(notes, relative)
    git_repository.run_git(notes, "push")
    _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    working_main = _plan_file.working_plans_root(home) / saved_main.name
    working_main.write_text("# working\n", encoding="utf-8")
    unrelated = notes / "unrelated.tmp"
    unrelated.write_text("dirty\n", encoding="utf-8")
    _clone_notes(remote, other)
    other_main = other / "plans" / relative
    original = _atk_git_sync.commit_and_push

    def diverge_then_commit(private_notes, message, paths, *, skip_push: bool = False):
        other_main.write_text("# concurrent\n", encoding="utf-8")
        git_repository.run_git(other, "add", "plans")
        git_repository.run_git(other, "commit", "-m", "concurrent plan update")
        git_repository.run_git(other, "push")
        original(private_notes, message, paths, skip_push=skip_push)

    monkeypatch.setattr(_atk_git_sync, "commit_and_push", diverge_then_commit)

    with pytest.raises(_wi_web_input.WebInputError, match="remote branch") as error_info:
        _atk_plans.commit_plan(notes, saved_main.name, home=home)

    # 到達を確認できない場合は、ローカルに残ったcommitをpushする手段を案内する。
    assert "atk wi commit" in error_info.value.next_action

    assert working_main.read_text(encoding="utf-8") == "# working\n"
    assert _atk_plans._read_checkout_record(pathlib.Path(saved_main.name)) is not None  # pylint: disable=protected-access
    assert saved_main.read_text(encoding="utf-8") == "# working\n"
    assert unrelated.read_text(encoding="utf-8") == "dirty\n"
    assert (
        git_repository.run_git(notes, "rev-parse", "HEAD").stdout != git_repository.run_git(notes, "rev-parse", "@{u}").stdout
    )


def test_commit_recovers_checkout_record_without_working_bundle(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公開CLIは作業バンドルが無い取得記録だけを回収する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-取得取消-d4f9.md")
    saved_main, saved_detail = _create_saved_plan(notes, relative)
    copied = _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    for path in copied:
        path.unlink()
    saved_before = {path.name: path.read_bytes() for path in (saved_main, saved_detail)}
    head_before = git_repository.run_git(notes, "rev-parse", "HEAD").stdout
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["plans", "commit", saved_main.name], home=home)

    assert {path.name: path.read_bytes() for path in (saved_main, saved_detail)} == saved_before
    assert git_repository.run_git(notes, "rev-parse", "HEAD").stdout == head_before
    assert _atk_plans._read_checkout_record(pathlib.Path(saved_main.name)) is None  # pylint: disable=protected-access


def test_commit_plan_only_commits_selected_bundle(tmp_path: pathlib.Path) -> None:
    """指定mainと同stemの付属だけをcommitし、別stemと除外拡張子を残す。"""
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-計画保存先移行-d4f9.md")
    main = notes / _plan_file.NEW_PLANS_DIRECTORY / relative
    detail = main.with_name(main.stem + ".detail.md")
    plan_review = main.with_name(main.stem + ".plan-review.tsv")
    implementation_review = main.with_name(main.stem + ".exec-review.tsv")
    unrelated = main.with_name("30-別計画-a1b2.md")
    excluded = main.with_name(main.name + ".tmp")
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    detail.write_text("# detail\n", encoding="utf-8")
    plan_review.write_text('1\t"plan-review"\n', encoding="utf-8")
    implementation_review.write_text('1\t"implementation-review"\n', encoding="utf-8")
    unrelated.write_text("# unrelated\n", encoding="utf-8")
    excluded.write_text("temporary\n", encoding="utf-8")
    git_repository.run_git(notes, "add", "plans/2026/08/30-別計画-a1b2.md")

    result = _atk_plans.commit_plan(notes, relative.as_posix())

    assert result["plan_file"] == relative.as_posix()
    committed = git_repository.run_git(notes, "show", "--format=", "--name-only", "HEAD").stdout.splitlines()
    staged = git_repository.run_git(notes, "diff", "--cached", "--name-only").stdout.splitlines()
    assert committed == [
        "plans/2026/08/30-計画保存先移行-d4f9.detail.md",
        "plans/2026/08/30-計画保存先移行-d4f9.exec-review.tsv",
        "plans/2026/08/30-計画保存先移行-d4f9.md",
        "plans/2026/08/30-計画保存先移行-d4f9.plan-review.tsv",
    ]
    assert staged == ["plans/2026/08/30-別計画-a1b2.md"]
    assert excluded.is_file()


def test_commit_plan_moves_working_bundle_and_removes_source_after_commit(tmp_path: pathlib.Path) -> None:
    """現行作業バンドルを`private-notes/plans/`へ移し、旧添付は作業側へ残す。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-計画保存先移行-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    detail = main.with_name(main.stem + ".detail.md")
    review = main.with_name(main.stem + ".exec-review.tsv")
    unrelated = main.with_name(main.stem + ".unrelated.md")
    unrelated_contents = b"working notes\x00\xff\n"
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    detail.write_text("# detail\n", encoding="utf-8")
    review.write_text('1\t"implementation-review"\n', encoding="utf-8")
    unrelated.write_bytes(unrelated_contents)

    result = _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    saved_main = notes / "plans" / relative
    assert result["paths"] == (
        "plans/2026/08/30-計画保存先移行-d4f9.exec-review.tsv",
        "plans/2026/08/30-計画保存先移行-d4f9.md",
    )
    assert saved_main.read_text(encoding="utf-8") == "# main\n"
    assert not main.exists()
    assert detail.read_text(encoding="utf-8") == "# detail\n"
    assert unrelated.read_bytes() == unrelated_contents
    assert not (saved_main.parent / unrelated.name).exists()
    assert not review.exists()


def test_commit_and_checkout_carry_wi_commit_mapping(tmp_path: pathlib.Path) -> None:
    """WI実装commitの対応記録ファイルを計画と同じstemの付属ファイルとして保存し、取得で作業側へ戻す。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/10/07-対応記録-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    mapping = main.with_name(main.stem + ".wi-commits.jsonl")
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    mapping.write_text('{"commits":["abc1234"],"awi":["20261007-002735-001.md"]}\n', encoding="utf-8")
    expected = mapping.read_bytes()

    result = _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert result["paths"] == (
        "plans/2026/10/07-対応記録-d4f9.md",
        "plans/2026/10/07-対応記録-d4f9.wi-commits.jsonl",
    )
    saved_mapping = notes / "plans" / relative.parent / mapping.name
    assert saved_mapping.read_bytes() == expected
    assert not mapping.exists()

    copied = _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)

    working_mapping = _plan_file.working_plans_root(home) / mapping.name
    assert working_mapping in copied
    assert working_mapping.read_bytes() == expected


def test_commit_preserves_source_creation_and_modification_time(tmp_path: pathlib.Path) -> None:
    """保存確定は作業側の作成日時と更新日時を移動先へ維持する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-日時維持-d4f9.md")
    source = _plan_file.working_plans_root(home) / relative
    source.parent.mkdir(parents=True)
    source.write_text("# main\n", encoding="utf-8")
    expected_times = _set_stable_mtime(source)

    _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    destination = notes / "plans" / relative
    _assert_preserved_times(destination, expected_times)
    assert not source.exists()


def test_commit_fails_and_keeps_source_when_replace_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存の確定に失敗した場合は例外を返して作業側を残す。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-確定失敗-d4f9.md")
    source = _plan_file.working_plans_root(home) / relative
    source.parent.mkdir(parents=True)
    source.write_text("# main\n", encoding="utf-8")

    def fail_replace(_source: pathlib.Path, _destination: pathlib.Path) -> None:
        raise OSError("想定した確定失敗")

    monkeypatch.setattr(_atk_plans.os, "replace", fail_replace)

    with pytest.raises(OSError, match="想定した確定失敗"):
        _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert source.read_text(encoding="utf-8") == "# main\n"


def test_commit_resumes_after_partial_bundle_move_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """メイン計画を最後に移すため、途中失敗後も再実行で全バンドルを回収する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-部分確定-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    bugs = main.with_name(main.stem + ".bugs.md")
    review = main.with_name(main.stem + ".exec-review.tsv")
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    bugs.write_text("# bugs\n", encoding="utf-8")
    review.write_text('1\t"implementation-review"\n', encoding="utf-8")
    expected_times = {path.name: _set_stable_mtime(path) for path in (main, bugs, review)}
    original_replace = _atk_plans.os.replace

    def fail_review_replace(source: pathlib.Path, destination: pathlib.Path) -> None:
        if pathlib.Path(source) == review:
            raise OSError("想定したバンドル途中失敗")
        original_replace(source, destination)

    monkeypatch.setattr(_atk_plans.os, "replace", fail_review_replace)

    with pytest.raises(OSError, match="想定したバンドル途中失敗"):
        _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert main.is_file()
    assert any(path.is_file() for path in (bugs, review))
    monkeypatch.setattr(_atk_plans.os, "replace", original_replace)

    _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    for source in (main, bugs, review):
        assert not source.exists()
        _assert_preserved_times(notes / "plans" / relative.parent / source.name, expected_times[source.name])


def test_dispatch_reports_saved_relative_path_for_direct_working_plan(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """直下計画のcommit出力は作成月を付けた保存先相対パスを示す。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("30-計画保存先移行-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    year, month, _day = _atk_plans._birth_date(main).split("/")  # pylint: disable=protected-access
    args = types.SimpleNamespace(plans_subcommand="commit", plan_file=relative.as_posix(), skip_push=True)

    result = _atk_plans.dispatch(args, notes, home)

    assert result == 0
    assert capsys.readouterr().out == (
        f"成功: 計画bundleを`private-notes/plans/`へ移動してcommitした: {year}/{month}/{relative.name}\n"
    )


def test_commit_plan_skip_push_commits_locally_without_changing_remote(tmp_path: pathlib.Path) -> None:
    """push省略時も保存とcommitを完了し、remote branchは変更しない。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)
    remote_head_before = git_repository.run_git(notes, "rev-parse", "@{u}").stdout.strip()
    relative = pathlib.Path("2026/08/30-ローカル確定-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")

    _atk_plans.commit_plan(notes, relative.as_posix(), home=home, skip_push=True)

    assert not main.exists()
    assert (notes / "plans" / relative).read_text(encoding="utf-8") == "# main\n"
    assert git_repository.run_git(notes, "rev-parse", "HEAD").stdout.strip() != remote_head_before
    assert git_repository.run_git(notes, "rev-parse", "@{u}").stdout.strip() == remote_head_before


def test_commit_plan_retries_identical_destination_after_commit_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存先複製後の失敗では作業側を保持し、同内容の再実行で完了する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-再実行-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    original = _atk_git_sync.commit_and_push

    def fail_commit(*_args, **_kwargs) -> None:
        raise RuntimeError("commit failure")

    monkeypatch.setattr(_atk_git_sync, "commit_and_push", fail_commit)

    with pytest.raises(RuntimeError, match="commit failure"):
        _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    saved = notes / "plans" / relative
    assert main.is_file()
    assert saved.read_bytes() == main.read_bytes()
    monkeypatch.setattr(_atk_git_sync, "commit_and_push", original)

    _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert not main.exists()
    assert not git_repository.run_git(notes, "status", "--porcelain").stdout


def test_commit_plan_rejects_different_saved_content_without_removing_source(tmp_path: pathlib.Path) -> None:
    """同じ相対パスの保存済み内容が異なる場合は双方を保持し、回復手順を案内して停止する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-競合-d4f9.md")
    working = _plan_file.working_plans_root(home) / relative
    saved = notes / "plans" / relative
    working.parent.mkdir(parents=True)
    saved.parent.mkdir(parents=True)
    working.write_text("working\n", encoding="utf-8")
    saved.write_text("saved\n", encoding="utf-8")

    with pytest.raises(_wi_web_input.WebInputError, match="内容の異なる") as error_info:
        _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert "作業側を退避" in error_info.value.next_action
    assert "別名の新しい計画" in error_info.value.next_action
    assert working.read_text(encoding="utf-8") == "working\n"
    assert saved.read_text(encoding="utf-8") == "saved\n"


def test_commit_saved_bundle_rejects_working_root_residue(tmp_path: pathlib.Path) -> None:
    """保存済み計画と同じstemのファイルが`~/.claude/plans`直下に残っていれば失敗を返す。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-残骸-d4f9.md")
    saved, _detail = _create_saved_plan(notes, relative)
    residue = _plan_file.working_plans_root(home) / relative.name
    residue.parent.mkdir(parents=True)
    residue.write_text("# unsaved residue\n", encoding="utf-8")

    with pytest.raises(_wi_web_input.WebInputError, match="同じstemのファイルが残っています") as error_info:
        _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert "別名の新しい計画" in error_info.value.next_action
    assert residue.read_text(encoding="utf-8") == "# unsaved residue\n"
    assert saved.read_text(encoding="utf-8") == "# saved main\n"


def test_commit_plan_includes_deleted_bundle_when_parent_directory_is_gone(tmp_path: pathlib.Path) -> None:
    """親ディレクトリが消えた削除済み計画もGitの追跡情報からcommitする。"""
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-削除計画-d4f9.md")
    main = notes / "plans" / relative
    detail = main.with_name(main.stem + ".detail.md")
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    detail.write_text("# detail\n", encoding="utf-8")
    git_repository.run_git(notes, "add", "plans")
    git_repository.run_git(notes, "commit", "-m", "add plan")
    main.unlink()
    detail.unlink()
    month = main.parent
    year = month.parent
    plans = year.parent
    month.rmdir()
    year.rmdir()
    plans.rmdir()

    _atk_plans.commit_plan(notes, relative.as_posix())

    assert git_repository.run_git(notes, "show", "--format=", "--name-only", "HEAD").stdout.splitlines() == [
        "plans/2026/08/30-削除計画-d4f9.detail.md",
        "plans/2026/08/30-削除計画-d4f9.md",
    ]
