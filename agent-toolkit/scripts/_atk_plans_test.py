"""`atk plans checkout`・`commit`と旧計画root移行の実Git検証。"""

import datetime
import os
import pathlib
import subprocess
import types
import typing

import _atk_git_sync
import _atk_mq_common as _common
import _atk_plans
import _plan_file
import atk
import pytest


@pytest.fixture(autouse=True)
def _isolate_state_directory(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIXとWindowsの状態ディレクトリ解決をテスト固有の場所へ隔離する。"""
    state_root = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.setenv("LOCALAPPDATA", str(state_root))
    monkeypatch.setenv("APPDATA", str(state_root))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "profile"))


def _git(root: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """テスト用Gitコマンドを実行する。"""
    return subprocess.run(["git", *args], cwd=root, check=check, capture_output=True, text=True)


def _init_local_notes(root: pathlib.Path) -> None:
    """remote不要のlocal-only private-notesを初期化する。"""
    root.mkdir()
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.name", "plans-test")
    _git(root, "config", "user.email", "plans-test@example.invalid")
    _git(root, "config", "core.quotePath", "false")
    (root / ".agent-toolkit-local-only").touch()
    for state in ("inbox", "processing", "planning", "editing", "hold", "adopted", "rejected"):
        (root / state / ".gitkeep").parent.mkdir(parents=True)
        (root / state / ".gitkeep").touch()
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")


def _init_remote_notes(root: pathlib.Path, remote: pathlib.Path) -> None:
    """upstream付きprivate-notesを初期化する。"""
    _init_local_notes(root)
    (root / ".agent-toolkit-local-only").unlink()
    _git(root, "add", "-u")
    _git(root, "commit", "-m", "enable remote")
    _git(remote.parent, "init", "--bare", "--initial-branch=main", str(remote))
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "--set-upstream", "origin", "main")


def _clone_notes(remote: pathlib.Path, clone: pathlib.Path) -> None:
    """並行更新用のprivate-notes cloneを作成する。"""
    _git(remote.parent, "clone", str(remote), str(clone))
    _git(clone, "config", "user.name", "plans-test-clone")
    _git(clone, "config", "user.email", "plans-test-clone@example.invalid")
    _git(clone, "config", "core.quotePath", "false")


def _preserved_times(path: pathlib.Path) -> tuple[float | None, int]:
    """取得できる作成日時と更新日時を返す。"""
    birth = _plan_file._creation_epoch(path)  # pylint: disable=protected-access
    return birth, path.stat().st_mtime_ns


def _assert_preserved_times(path: pathlib.Path, expected: tuple[float | None, int]) -> None:
    """作成日時を取得できる環境では作成日時も含めて一致を確認する。"""
    birth, mtime_ns = expected
    assert path.stat().st_mtime_ns == mtime_ns
    if birth is not None:
        assert _plan_file._creation_epoch(path) == birth  # pylint: disable=protected-access


def _set_stable_mtime(path: pathlib.Path) -> tuple[float | None, int]:
    """日時維持の検査用に更新日時を固定して返す。"""
    timestamp_ns = 1_700_000_000_123_456_789
    os.utime(path, ns=(timestamp_ns, timestamp_ns))
    return _preserved_times(path)


def _assert_preserved_birth(path: pathlib.Path, expected_birth: float | None) -> None:
    """取得できる環境では作成日時だけの維持を確認する。"""
    if expected_birth is not None:
        assert _plan_file._creation_epoch(path) == expected_birth  # pylint: disable=protected-access


def _create_saved_plan(notes: pathlib.Path, relative: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """commit済みのメイン・詳細計画を作成する。"""
    main = notes / "plans" / relative
    detail = main.with_name(main.stem + ".detail.md")
    main.parent.mkdir(parents=True)
    main.write_text("# saved main\n", encoding="utf-8")
    detail.write_text("# saved detail\n", encoding="utf-8")
    _git(notes, "add", "plans")
    _git(notes, "commit", "-m", "add saved plan")
    return main, detail


def _prepare_migration(
    tmp_path: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path, pathlib.Path, bytes]:
    """本文変換が生じる旧計画とremote付き保存先を作成する。"""
    home = tmp_path / "home"
    legacy = home / ".claude" / "plans"
    legacy.mkdir(parents=True)
    source = legacy / "legacy.md"
    source.write_text(f"legacy: {source}\n", encoding="utf-8")
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)
    date = _atk_plans._birth_date(source)  # pylint: disable=protected-access
    year, month, day = date.split("/")
    destination = notes / "plans" / year / month / f"{day}-legacy.md"
    portable = _plan_file.to_portable_plan_file(destination, private_notes=notes)
    return home, source, notes, destination, f"legacy: {portable}\n".encode()


def test_preserved_times_ignores_unavailable_birth_time(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作成日時を取得できない場合も更新日時を検証対象として返す。"""
    source = tmp_path / "plan.md"
    source.write_text("# plan\n", encoding="utf-8")
    expected_mtime_ns = source.stat().st_mtime_ns

    def unavailable_creation(_path: pathlib.Path) -> None:
        return None

    monkeypatch.setattr(_plan_file, "_creation_epoch", unavailable_creation)

    assert _preserved_times(source) == (None, expected_mtime_ns)


def test_checkout_copies_saved_bundle_into_working_root(tmp_path: pathlib.Path) -> None:
    """保存済みバンドルをbytes保持で作業rootへ取得し、取得時点を記録する。"""
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


@pytest.mark.parametrize("conflict", ["working", "record", "missing-main"])
def test_checkout_rejects_conflicting_working_file_and_existing_record(
    tmp_path: pathlib.Path,
    conflict: str,
) -> None:
    """作業側衝突、取得済み、メイン欠落は保存側と作業側を変えず拒否する。"""
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

    with pytest.raises(_common.WebInputError):
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
    """取得後の保存側の変更とファイル追加を競合として双方無変更で拒否する。"""
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
        main.with_name(main.stem + ".new.md").write_text("# concurrent\n", encoding="utf-8")
    saved_before = _atk_plans._bundle_contents(  # pylint: disable=protected-access
        _atk_plans._saved_plan_bundle(notes, relative)  # pylint: disable=protected-access
    )

    with pytest.raises(_common.WebInputError, match="取得後に保存元"):
        _atk_plans.commit_plan(notes, main.name, home=home)

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
    """取得後にremoteで更新された同一ファイルと付属追加を同期して拒否する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    other = tmp_path / "other-notes"
    _init_remote_notes(notes, remote)
    relative = pathlib.Path("2026/08/30-remote並行変更-d4f9.md")
    saved_main, _detail = _create_saved_plan(notes, relative)
    _git(notes, "push")
    _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    working_main = _plan_file.working_plans_root(home) / saved_main.name
    working_main.write_text("# working\n", encoding="utf-8")
    _clone_notes(remote, other)
    other_main = other / "plans" / relative
    if remote_change == "changed-main":
        other_main.write_text("# concurrent\n", encoding="utf-8")
    else:
        other_main.with_name(other_main.stem + ".supplement.md").write_text(
            "# concurrent\n",
            encoding="utf-8",
        )
    _git(other, "add", "plans")
    _git(other, "commit", "-m", "concurrent plan update")
    _git(other, "push")
    saved_before = _atk_plans._bundle_contents(  # pylint: disable=protected-access
        _atk_plans._saved_plan_bundle(notes, relative)  # pylint: disable=protected-access
    )

    with pytest.raises(_common.WebInputError, match="取得後に保存元"):
        _atk_plans.commit_plan(notes, saved_main.name, home=home)

    actual = _atk_plans._bundle_contents(  # pylint: disable=protected-access
        _atk_plans._saved_plan_bundle(notes, relative)  # pylint: disable=protected-access
    )
    assert actual == saved_before
    assert working_main.read_text(encoding="utf-8") == "# working\n"
    assert _atk_plans._read_checkout_record(pathlib.Path(saved_main.name)) is not None  # pylint: disable=protected-access
    assert _git(notes, "rev-parse", "HEAD").stdout != _git(notes, "rev-parse", "@{u}").stdout


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
    assert not saved_detail.exists()
    assert (saved_main.parent / working_attachment.name).read_text(encoding="utf-8") == "# supplement\n"
    assert not working_attachment.exists()


def test_commit_uses_recorded_relative_main_instead_of_birth_month(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取得済み計画は作業側の作成月でなく記録した取得元へ戻す。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2024/02/03-取得元維持-d4f9.md")
    saved_main, _detail = _create_saved_plan(notes, relative)
    _atk_plans.checkout_plan(notes, relative.as_posix(), home=home)
    working_main = _plan_file.working_plans_root(home) / saved_main.name
    working_main.write_text("# updated\n", encoding="utf-8")
    monkeypatch.setattr(_atk_plans, "_birth_date", lambda _path: "2030/12/31")

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
    _git(notes, "push")
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
    assert _git(notes, "rev-parse", "HEAD").stdout == _git(notes, "rev-parse", "@{u}").stdout


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
    _git(notes, "push")
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
        _git(other, "add", "plans")
        _git(other, "commit", "-m", "concurrent plan update")
        _git(other, "push")
        original(private_notes, message, paths, skip_push=skip_push)

    monkeypatch.setattr(_atk_git_sync, "commit_and_push", diverge_then_commit)

    with pytest.raises(_common.WebInputError, match="remote branch"):
        _atk_plans.commit_plan(notes, saved_main.name, home=home)

    assert working_main.read_text(encoding="utf-8") == "# working\n"
    assert _atk_plans._read_checkout_record(pathlib.Path(saved_main.name)) is not None  # pylint: disable=protected-access
    assert saved_main.read_text(encoding="utf-8") == "# working\n"
    assert unrelated.read_text(encoding="utf-8") == "dirty\n"
    assert _git(notes, "rev-parse", "HEAD").stdout != _git(notes, "rev-parse", "@{u}").stdout


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
    head_before = _git(notes, "rev-parse", "HEAD").stdout
    monkeypatch.setattr(_common, "_ensure_environment", lambda _home: notes)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["plans", "commit", saved_main.name], home=home)

    assert {path.name: path.read_bytes() for path in (saved_main, saved_detail)} == saved_before
    assert _git(notes, "rev-parse", "HEAD").stdout == head_before
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
    _git(notes, "add", "plans/2026/08/30-別計画-a1b2.md")

    result = _atk_plans.commit_plan(notes, relative.as_posix())

    assert result["plan_file"] == relative.as_posix()
    committed = _git(notes, "show", "--format=", "--name-only", "HEAD").stdout.splitlines()
    staged = _git(notes, "diff", "--cached", "--name-only").stdout.splitlines()
    assert committed == [
        "plans/2026/08/30-計画保存先移行-d4f9.detail.md",
        "plans/2026/08/30-計画保存先移行-d4f9.exec-review.tsv",
        "plans/2026/08/30-計画保存先移行-d4f9.md",
        "plans/2026/08/30-計画保存先移行-d4f9.plan-review.tsv",
    ]
    assert staged == ["plans/2026/08/30-別計画-a1b2.md"]
    assert excluded.is_file()


def test_commit_plan_moves_working_bundle_and_removes_source_after_commit(tmp_path: pathlib.Path) -> None:
    """作業バンドル全体を保存rootへ移し、commit成功後だけ作業側を回収する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("2026/08/30-計画保存先移行-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    detail = main.with_name(main.stem + ".detail.md")
    review = main.with_name(main.stem + ".exec-review.tsv")
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    detail.write_text("# detail\n", encoding="utf-8")
    review.write_text('1\t"implementation-review"\n', encoding="utf-8")

    result = _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    saved_main = notes / "plans" / relative
    assert result["paths"] == (
        "plans/2026/08/30-計画保存先移行-d4f9.detail.md",
        "plans/2026/08/30-計画保存先移行-d4f9.exec-review.tsv",
        "plans/2026/08/30-計画保存先移行-d4f9.md",
    )
    assert saved_main.read_text(encoding="utf-8") == "# main\n"
    assert not main.exists()
    assert not detail.exists()
    assert not review.exists()


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
    detail = main.with_name(main.stem + ".detail.md")
    review = main.with_name(main.stem + ".plan-review.tsv")
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    detail.write_text("# detail\n", encoding="utf-8")
    review.write_text('1\t"plan-review"\n', encoding="utf-8")
    expected_times = {path.name: _set_stable_mtime(path) for path in (main, detail, review)}
    original_replace = _atk_plans.os.replace

    def fail_review_replace(source: pathlib.Path, destination: pathlib.Path) -> None:
        if pathlib.Path(source) == review:
            raise OSError("想定したバンドル途中失敗")
        original_replace(source, destination)

    monkeypatch.setattr(_atk_plans.os, "replace", fail_review_replace)

    with pytest.raises(OSError, match="想定したバンドル途中失敗"):
        _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert main.is_file()
    assert any(path.is_file() for path in (detail, review))
    monkeypatch.setattr(_atk_plans.os, "replace", original_replace)

    _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    for source in (main, detail, review):
        assert not source.exists()
        _assert_preserved_times(notes / "plans" / relative.parent / source.name, expected_times[source.name])


def test_commit_plan_moves_direct_working_bundle_to_birth_month(tmp_path: pathlib.Path) -> None:
    """直下の全付属ファイルを作成月の保存先へ移し、stemと内容を維持する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("30-計画保存先移行-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    contents = {
        main: "# main\n",
        main.with_name(main.stem + ".detail.md"): "# detail\n",
        main.with_name(main.stem + ".bugs.md"): "# bugs\n",
        main.with_name(main.stem + ".exec-review.tsv"): '1\t"implementation-review"\n',
        main.with_name(main.stem + ".supplement.md"): "# supplement\n",
    }
    for path, content in contents.items():
        path.write_text(content, encoding="utf-8")
    birth_date = _atk_plans._birth_date(main)  # pylint: disable=protected-access
    year, month, _day = birth_date.split("/")

    result = _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    saved_relative = pathlib.Path(year, month, relative.name)
    assert result["plan_file"] == saved_relative.as_posix()
    assert result["message"] == "chore: update plan 計画保存先移行"
    for source, content in contents.items():
        destination = notes / "plans" / year / month / source.name
        assert destination.read_text(encoding="utf-8") == content
        assert not source.exists()


def test_commit_plan_succeeds_without_creation_time(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作成日時を取得できない場合は更新日時の年月へ保存する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("04-作成日時なし-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    modified_epoch = datetime.datetime(2025, 4, 4, 12).timestamp()
    os.utime(main, (modified_epoch, modified_epoch))
    expected_date = datetime.datetime.fromtimestamp(modified_epoch).date()

    def unavailable_creation(_path: pathlib.Path) -> None:
        return None

    monkeypatch.setattr(_plan_file, "_creation_epoch", unavailable_creation)

    result = _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    expected_relative = pathlib.Path(f"{expected_date.year:04d}", f"{expected_date.month:02d}", relative.name)
    assert result["plan_file"] == expected_relative.as_posix()
    assert (notes / "plans" / expected_relative).read_text(encoding="utf-8") == "# main\n"
    assert not main.exists()


def test_commit_plan_prefers_creation_time_over_mtime(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作成日時を取得できる場合は更新日時より作成日時の年月を優先する。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)
    relative = pathlib.Path("03-作成日時優先-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    creation_epoch = datetime.datetime(2025, 3, 3, 12).timestamp()
    modified_epoch = datetime.datetime(2024, 2, 2, 12).timestamp()
    os.utime(main, (modified_epoch, modified_epoch))
    expected_date = datetime.datetime.fromtimestamp(creation_epoch).date()

    def fixed_creation(_path: pathlib.Path) -> float:
        return creation_epoch

    monkeypatch.setattr(_plan_file, "_creation_epoch", fixed_creation)

    result = _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    expected_relative = pathlib.Path(f"{expected_date.year:04d}", f"{expected_date.month:02d}", relative.name)
    assert result["plan_file"] == expected_relative.as_posix()
    assert (notes / "plans" / expected_relative).read_text(encoding="utf-8") == "# main\n"
    assert not main.exists()


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
    assert capsys.readouterr().out == (f"計画bundleを保存rootへ移動してcommitしました: {year}/{month}/{relative.name}\n")


def test_commit_plan_skip_push_commits_locally_without_changing_remote(tmp_path: pathlib.Path) -> None:
    """push省略時も保存とcommitを完了し、remote branchは変更しない。"""
    home = tmp_path / "home"
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)
    remote_head_before = _git(notes, "rev-parse", "@{u}").stdout.strip()
    relative = pathlib.Path("2026/08/30-ローカル確定-d4f9.md")
    main = _plan_file.working_plans_root(home) / relative
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")

    _atk_plans.commit_plan(notes, relative.as_posix(), home=home, skip_push=True)

    assert not main.exists()
    assert (notes / "plans" / relative).read_text(encoding="utf-8") == "# main\n"
    assert _git(notes, "rev-parse", "HEAD").stdout.strip() != remote_head_before
    assert _git(notes, "rev-parse", "@{u}").stdout.strip() == remote_head_before


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
    assert not _git(notes, "status", "--porcelain").stdout


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

    with pytest.raises(_common.WebInputError, match="内容の異なる") as error_info:
        _atk_plans.commit_plan(notes, relative.as_posix(), home=home)

    assert "作業root外へ退避" in str(error_info.value)
    assert "atk plans checkout" in str(error_info.value)
    assert working.read_text(encoding="utf-8") == "working\n"
    assert saved.read_text(encoding="utf-8") == "saved\n"


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
    _git(notes, "add", "plans")
    _git(notes, "commit", "-m", "add plan")
    main.unlink()
    detail.unlink()
    month = main.parent
    year = month.parent
    plans = year.parent
    month.rmdir()
    year.rmdir()
    plans.rmdir()

    _atk_plans.commit_plan(notes, relative.as_posix())

    assert _git(notes, "show", "--format=", "--name-only", "HEAD").stdout.splitlines() == [
        "plans/2026/08/30-削除計画-d4f9.detail.md",
        "plans/2026/08/30-削除計画-d4f9.md",
    ]


def test_migrate_plans_moves_bundle_references_and_deletes_after_remote_push(tmp_path: pathlib.Path) -> None:
    """旧rootのbundle・MQ参照を移行し、push後に列挙済み旧ファイルを削除する。"""
    home = tmp_path / "home"
    legacy = home / ".claude" / "plans"
    legacy.mkdir(parents=True)
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)

    main = legacy / "feature.md"
    detail = legacy / "feature.detail.md"
    supplement = legacy / "feature.supplement.md"
    orphan = legacy / "orphan.md"
    excluded = legacy / "ignored.tmp"
    main_token = str(main)
    main.write_text(
        "# feature\n\n通常文: " + main_token + "\n\n"
        "## 変更履歴（計画時）\n\n"
        "```text\n## 見出し様のユーザー発言\nユーザー発言: " + main_token + "\n```\n\n"
        "```bash\necho " + main_token + "\n```\n\n"
        "日本語文: " + main_token + "を使用する。\n"
        "対応表外: " + main_token + ".bak\n"
        "対応表外suffix: " + main_token + "suffix\n",
        encoding="utf-8",
    )
    detail.write_text("detail: " + str(detail) + "\n", encoding="utf-8")
    supplement.write_text("supplement\n", encoding="utf-8")
    orphan.write_text("orphan: " + str(orphan) + "\n", encoding="utf-8")
    excluded.write_bytes(b"ignored\xff\n")
    mq = notes / "inbox" / "queue.md"
    mq.write_text(
        "---\n# frontmatter comment\ntarget_repo: github.com/example/repo\n"
        'type: feedback\nplan_file: "' + main_token + '" # inline comment\n'
        "custom: '保持する値'\n---\n\n本文\n",
        encoding="utf-8",
    )
    _git(notes, "add", "inbox/queue.md")
    _git(notes, "commit", "-m", "queue")
    _git(notes, "push")

    main_date = _atk_plans._birth_date(main)  # pylint: disable=protected-access
    year, month, day = main_date.split("/")
    destination_main = notes / "plans" / year / month / f"{day}-feature.md"
    destination_detail = destination_main.with_name(destination_main.stem + ".detail.md")
    destination_supplement = destination_main.with_name(destination_main.stem + ".supplement.md")
    portable_main = _plan_file.to_portable_plan_file(destination_main, private_notes=notes)

    result = _atk_plans.migrate_plans(notes, home=home)

    assert result["migrated"] == 4
    assert result["deleted"] == 4
    assert not main.exists()
    assert not detail.exists()
    assert not supplement.exists()
    assert not orphan.exists()
    assert excluded.is_file()
    assert destination_main.is_file()
    assert destination_detail.is_file()
    assert destination_supplement.is_file()
    transformed = destination_main.read_text(encoding="utf-8")
    assert f"通常文: {portable_main}" in transformed
    assert f"ユーザー発言: {main_token}" in transformed
    assert f"echo {portable_main}" in transformed
    assert f"日本語文: {portable_main}を使用する。" in transformed
    assert f"対応表外: {main_token}.bak" in transformed
    assert f"対応表外suffix: {main_token}suffix" in transformed
    assert mq.read_text(encoding="utf-8") == (
        "---\n# frontmatter comment\ntarget_repo: github.com/example/repo\n"
        'type: feedback\nplan_file: "' + portable_main + '" # inline comment\n'
        "custom: '保持する値'\n---\n\n本文\n"
    )
    assert not _git(notes, "status", "--porcelain").stdout
    local_head = _git(notes, "rev-parse", "HEAD").stdout.strip()
    remote_head = _git(remote, "rev-parse", "refs/heads/main").stdout.strip()
    assert local_head == remote_head


def test_migrate_preserves_source_creation_and_modification_time(tmp_path: pathlib.Path) -> None:
    """本文変換後も旧計画の作成日時と更新日時を移行先へ維持する。"""
    home, source, notes, destination, transformed = _prepare_migration(tmp_path)
    expected_times = _set_stable_mtime(source)

    _atk_plans.migrate_plans(notes, home=home)

    assert destination.read_bytes() == transformed
    _assert_preserved_times(destination, expected_times)
    assert not source.exists()


@pytest.mark.parametrize("failure", ["write", "utime", "replace"])
def test_migrate_keeps_source_and_clean_destination_when_post_move_step_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """確定前の一過性障害では旧計画を復元し、再実行で移行を完了する。"""
    home, source, notes, destination, transformed = _prepare_migration(tmp_path)
    original_content = source.read_bytes()
    expected_times = _set_stable_mtime(source)
    original_write_bytes = pathlib.Path.write_bytes
    original_utime = _atk_plans.os.utime
    original_replace = _atk_plans.os.replace
    injected = False

    def write_bytes_once(path: pathlib.Path, content: bytes) -> int:
        nonlocal injected
        if failure == "write" and path == source and not injected:
            injected = True
            original_write_bytes(path, b"")
            raise OSError("想定した書込み失敗")
        return original_write_bytes(path, content)

    def utime_once(path: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> None:
        nonlocal injected
        if failure == "utime" and pathlib.Path(path) == source and not injected:
            injected = True
            raise OSError("想定した日時復元失敗")
        original_utime(path, *args, **kwargs)

    def replace_once(source_path: pathlib.Path, destination_path: pathlib.Path) -> None:
        nonlocal injected
        if failure == "replace" and pathlib.Path(source_path) == source and not injected:
            injected = True
            raise OSError("想定した確定失敗")
        original_replace(source_path, destination_path)

    monkeypatch.setattr(pathlib.Path, "write_bytes", write_bytes_once)
    monkeypatch.setattr(_atk_plans.os, "utime", utime_once)
    monkeypatch.setattr(_atk_plans.os, "replace", replace_once)

    with pytest.raises(OSError, match="想定した"):
        _atk_plans.migrate_plans(notes, home=home)

    assert source.read_bytes() == original_content
    _assert_preserved_times(source, expected_times)
    assert not tuple(source.parent.glob(f".{source.name}.*.tmp"))
    assert not _git(notes, "status", "--porcelain").stdout
    monkeypatch.setattr(pathlib.Path, "write_bytes", original_write_bytes)
    monkeypatch.setattr(_atk_plans.os, "utime", original_utime)
    monkeypatch.setattr(_atk_plans.os, "replace", original_replace)

    _atk_plans.migrate_plans(notes, home=home)

    assert not source.exists()
    assert destination.read_bytes() == transformed
    _assert_preserved_times(destination, expected_times)


def test_migrate_reports_leftover_backup_when_cleanup_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """確定後の複製削除失敗は移行成功と残存パスを別々に報告する。"""
    home, source, notes, destination, transformed = _prepare_migration(tmp_path)
    expected_times = _set_stable_mtime(source)
    original_unlink = pathlib.Path.unlink

    def fail_backup_unlink(path: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> None:
        if path.parent == source.parent and path.name.startswith(f".{source.name}.") and path.suffix == ".tmp":
            raise OSError("想定した複製削除失敗")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", fail_backup_unlink)

    result = _atk_plans.migrate_plans(notes, home=home)

    backups = tuple(source.parent.glob(f".{source.name}.*.tmp"))
    assert result["migrated"] == 1
    assert destination.read_bytes() == transformed
    _assert_preserved_times(destination, expected_times)
    assert not source.exists()
    assert len(backups) == 1
    error = capsys.readouterr().err
    assert str(backups[0]) in error
    assert "この複製は移行結果に影響しません" in error


def test_migrate_reports_manual_recovery_when_restore_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """自動復元も失敗した場合は残存パスと手作業を報告する。"""
    home, source, notes, destination, _transformed = _prepare_migration(tmp_path)
    original_write_bytes = pathlib.Path.write_bytes

    def fail_source_write(path: pathlib.Path, _content: bytes) -> int:
        if path == source:
            original_write_bytes(path, b"")
            raise OSError("想定した持続的な書込み失敗")
        return original_write_bytes(path, _content)

    monkeypatch.setattr(pathlib.Path, "write_bytes", fail_source_write)

    with pytest.raises(_common.WebInputError, match="自動復元"):
        _atk_plans.migrate_plans(notes, home=home)

    backups = tuple(source.parent.glob(f".{source.name}.*.tmp"))
    assert len(backups) == 1
    error = capsys.readouterr().err
    assert str(source) in error
    assert str(destination) in error
    assert str(backups[0]) in error
    assert "へ書き戻し" in error


def test_migrate_plans_pushes_pending_commit_before_deleting_legacy_files(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """再実行時は同内容の移行先があってもpending commitをpushしてから旧ファイルを削除する。"""
    home = tmp_path / "home"
    legacy = home / ".claude" / "plans"
    legacy.mkdir(parents=True)
    source = legacy / "legacy.md"
    source.write_text("legacy\n", encoding="utf-8")
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)
    destination = notes / "plans/2026/08/30-legacy.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("legacy\n", encoding="utf-8")
    _git(notes, "add", "plans")
    _git(notes, "commit", "-m", "migration pending push")
    pending_head = _git(notes, "rev-parse", "HEAD").stdout.strip()
    assert _git(remote, "rev-parse", "refs/heads/main").stdout.strip() != pending_head
    monkeypatch.setattr(_atk_plans, "_birth_date", lambda _path: "2026/08/30")

    result = _atk_plans.migrate_plans(notes, home=home)

    assert result["commit"] is None
    assert not source.exists()
    assert _git(remote, "rev-parse", "refs/heads/main").stdout.strip() == pending_head


@pytest.mark.parametrize("relative", [pathlib.Path("30-作業中計画-d4f9.md"), pathlib.Path("2026/08/30-作業中計画-d4f9.md")])
def test_migrate_plans_skips_canonical_working_bundle(tmp_path: pathlib.Path, relative: pathlib.Path) -> None:
    """直下形式と日付階層の正規作業バンドルは旧形式移行の対象にしない。"""
    home = tmp_path / "home"
    main = _plan_file.working_plans_root(home) / relative
    detail = main.with_name(main.stem + ".detail.md")
    main.parent.mkdir(parents=True)
    main.write_text("# main\n", encoding="utf-8")
    detail.write_text("# detail\n", encoding="utf-8")
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)

    result = _atk_plans.migrate_plans(notes, home=home)

    assert result["migrated"] == 0
    assert main.is_file()
    assert detail.is_file()
    assert not (notes / "plans" / relative).exists()


def test_migrate_plans_keeps_legacy_files_when_remote_does_not_contain_head(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """送信処理後もremoteがHEADを含まなければ旧ファイルを削除しない。"""
    home = tmp_path / "home"
    legacy = home / ".claude" / "plans"
    legacy.mkdir(parents=True)
    source = legacy / "legacy.md"
    source.write_text("legacy\n", encoding="utf-8")
    notes = tmp_path / "private-notes"
    remote = tmp_path / "origin.git"
    _init_remote_notes(notes, remote)
    destination = notes / "plans/2026/08/30-legacy.md"
    destination.parent.mkdir(parents=True)
    destination.write_text("legacy\n", encoding="utf-8")
    _git(notes, "add", "plans")
    _git(notes, "commit", "-m", "migration pending push")
    monkeypatch.setattr(_atk_plans, "_birth_date", lambda _path: "2026/08/30")
    monkeypatch.setattr(_atk_git_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(_atk_git_sync, "remote_contains_head", lambda _path: False)

    with pytest.raises(_common.WebInputError, match="remote branch"):
        _atk_plans.migrate_plans(notes, home=home)

    assert source.is_file()


def test_migrate_plans_rejects_remote_less_repository_before_writing(tmp_path: pathlib.Path) -> None:
    """remoteなしのprivate-notesでは旧rootも移行先も変更しない。"""
    home = tmp_path / "home"
    legacy = home / ".claude" / "plans"
    legacy.mkdir(parents=True)
    source = legacy / "legacy.md"
    source.write_text("legacy\n", encoding="utf-8")
    notes = tmp_path / "private-notes"
    _init_local_notes(notes)

    with pytest.raises(_common.WebInputError, match="remote"):
        _atk_plans.migrate_plans(notes, home=home)

    assert source.is_file()
    assert not (notes / "plans").exists()
