"""`atk worktree-stash`の共有stash排他と退避refの契約を検証する。"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import threading
import typing

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_worktree_stash as stash  # noqa: E402  # pylint: disable=wrong-import-position
import _file_lock  # noqa: E402  # pylint: disable=wrong-import-position

_SCRIPT = pathlib.Path(stash.__file__).resolve()


def _git(args: list[str], cwd: pathlib.Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = str(cwd / "gitconfig-global")
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if check:
        assert result.returncode == 0, result.stderr
    return result


def _make_repository(tmp_path: pathlib.Path, name: str = "repo") -> pathlib.Path:
    repo = tmp_path / name
    _git(["init", "--initial-branch=main", str(repo)], tmp_path)
    _git(["config", "user.name", "test"], repo)
    _git(["config", "user.email", "test@example.invalid"], repo)
    (repo / "state.txt").write_text("base\n", encoding="utf-8")
    _git(["add", "state.txt"], repo)
    _git(["commit", "-m", "base"], repo)
    return repo


def _make_worktrees(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    repo = _make_repository(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    _git(["worktree", "add", "-b", "first", str(first), "main"], repo)
    _git(["worktree", "add", "-b", "second", str(second), "main"], repo)
    for worktree in (first, second):
        _git(["config", "user.name", "test"], worktree)
        _git(["config", "user.email", "test@example.invalid"], worktree)
    return repo, first, second


def _make_changes(worktree: pathlib.Path, marker: str) -> None:
    (worktree / "state.txt").write_text(f"{marker}-staged\n", encoding="utf-8")
    _git(["add", "state.txt"], worktree)
    (worktree / "state.txt").write_text(f"{marker}-unstaged\n", encoding="utf-8")
    (worktree / "untracked.txt").write_text(f"{marker}-untracked\n", encoding="utf-8")


def _save(worktree: pathlib.Path, label: str, *, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = str(worktree / "gitconfig-global")
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "save", "--label", label],
        cwd=worktree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _drop(worktree: pathlib.Path, identifier: str, *, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = str(worktree / "gitconfig-global")
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "drop", identifier],
        cwd=worktree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def test_two_worktrees_can_save_concurrently_and_restore_three_states(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aのref記録後からdropまでBを排他し、各refから3状態を復元する。"""
    repo, first, second = _make_worktrees(tmp_path)
    _make_changes(first, "first")
    _make_changes(second, "second")
    first_before_drop = threading.Event()
    release_first_drop = threading.Event()
    second_lock_attempt = threading.Event()
    second_push = threading.Event()
    results: dict[str, int] = {}
    original_run_git = stash._run_git  # pylint: disable=protected-access  # noqa: SLF001
    original_acquire_lock = _file_lock.acquire_lock

    def synchronized_run_git(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        if cwd == first and args == ["stash", "drop", "stash@{0}"]:
            first_before_drop.set()
            assert release_first_drop.wait(timeout=20)
        if cwd == second and args == ["stash", "push", "--include-untracked"]:
            second_push.set()
        return original_run_git(args, cwd)

    def observed_acquire_lock(lock_file: typing.IO) -> None:
        if threading.current_thread().name == "second-save":
            second_lock_attempt.set()
        original_acquire_lock(lock_file)

    def save(label: str, worktree: pathlib.Path) -> None:
        results[label] = stash.save(label, cwd=worktree)

    monkeypatch.setattr(stash, "_run_git", synchronized_run_git)
    monkeypatch.setattr(_file_lock, "acquire_lock", observed_acquire_lock)
    first_thread = threading.Thread(target=save, args=("first-save", first), name="first-save")
    second_thread = threading.Thread(target=save, args=("second-save", second), name="second-save")
    first_thread.start()
    assert first_before_drop.wait(timeout=20)
    second_thread.start()
    try:
        assert second_lock_attempt.wait(timeout=20)
        assert not second_push.is_set()
    finally:
        release_first_drop.set()
        first_thread.join(timeout=20)
        second_thread.join(timeout=20)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert results == {"first-save": 0, "second-save": 0}
    assert second_push.is_set()
    assert _git(["rev-parse", "--verify", "refs/stash"], repo, check=False).returncode != 0

    for worktree, label, marker in (
        (first, "first-save", "first"),
        (second, "second-save", "second"),
    ):
        assert _git(["stash", "apply", "--index", f"refs/worktree/{label}"], worktree).returncode == 0
        assert _git(["show", ":state.txt"], worktree).stdout == f"{marker}-staged\n"
        assert (worktree / "state.txt").read_text(encoding="utf-8") == f"{marker}-unstaged\n"
        assert (worktree / "untracked.txt").read_text(encoding="utf-8") == f"{marker}-untracked\n"


def test_existing_stash_is_preserved(tmp_path: pathlib.Path) -> None:
    """既存の共有stashは新規退避分のdropで変化しない。"""
    repo, first, _second = _make_worktrees(tmp_path)
    (repo / "existing.txt").write_text("existing\n", encoding="utf-8")
    _git(["stash", "push", "--include-untracked", "-m", "existing"], repo)
    before = _git(["rev-parse", "--verify", "refs/stash"], repo).stdout.strip()
    _make_changes(first, "first")

    result = _save(first, "new-save")

    assert result.returncode == 0, result.stderr
    assert _git(["rev-parse", "--verify", "refs/stash"], repo).stdout.strip() == before
    assert _git(["rev-parse", "--verify", "refs/worktree/new-save"], first).returncode == 0


@pytest.mark.parametrize("label", ["../bad", "", "-leading"])
def test_invalid_label_is_rejected_before_changes(tmp_path: pathlib.Path, label: str) -> None:
    """不正ラベルではstashもrefも変更しない。"""
    _repo, first, _second = _make_worktrees(tmp_path)
    _make_changes(first, "first")
    before = _git(["status", "--short"], first).stdout

    result = _save(first, label)

    assert result.returncode == 2
    assert _git(["status", "--short"], first).stdout == before
    assert _git(["show-ref", "--verify", "--quiet", f"refs/worktree/{label}"], first, check=False).returncode != 0


def test_duplicate_label_and_no_changes_are_rejected(tmp_path: pathlib.Path) -> None:
    """同名refと退避対象なしは作業状態を変えずに終了コード2となる。"""
    _repo, first, _second = _make_worktrees(tmp_path)
    assert _save(first, "empty").returncode == 2
    _make_changes(first, "first")
    assert _save(first, "same").returncode == 0
    (first / "new.txt").write_text("keep\n", encoding="utf-8")
    before = _git(["status", "--short"], first).stdout

    duplicate = _save(first, "same")

    assert duplicate.returncode == 2
    assert _git(["status", "--short"], first).stdout == before
    assert _git(["show-ref", "--verify", "--quiet", "refs/worktree/same"], first, check=False).returncode == 0


def test_save_refuses_queue_repository_worktree(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """saveはキュー管理リポジトリを拒否し、別リポジトリでは成功する。"""
    queue_repository = _make_repository(tmp_path, "private-notes")
    target_repository = _make_repository(tmp_path, "target")
    _make_changes(queue_repository, "queue")
    _make_changes(target_repository, "target")
    queue_status = _git(["status", "--short"], queue_repository).stdout
    args = argparse.Namespace(command="save", label="queue-save")

    monkeypatch.chdir(queue_repository)
    assert stash.dispatch(args, private_notes=queue_repository) == 2
    error = capsys.readouterr().err
    assert "キュー管理リポジトリ" in error
    assert "atk wi・atk plans・atk serve" in error
    assert "atk wi commit" in error
    assert _git(["status", "--short"], queue_repository).stdout == queue_status
    assert _git(["show-ref", "--verify", "--quiet", "refs/worktree/queue-save"], queue_repository, check=False).returncode == 1
    monkeypatch.chdir(target_repository)
    args.label = "target-save"
    assert stash.dispatch(args, private_notes=queue_repository) == 0


def test_drop_refuses_queue_repository_worktree(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """dropはキュー管理リポジトリを拒否し、別リポジトリでは成功する。"""
    queue_repository = _make_repository(tmp_path, "private-notes")
    target_repository = _make_repository(tmp_path, "target")
    ref = "refs/worktree/drop-target"
    for repository in (queue_repository, target_repository):
        oid = _git(["rev-parse", "HEAD"], repository).stdout.strip()
        _git(["update-ref", ref, oid], repository)
    args = argparse.Namespace(command="drop", identifier=ref)

    monkeypatch.chdir(queue_repository)
    assert stash.dispatch(args, private_notes=queue_repository) == 2
    error = capsys.readouterr().err
    assert "キュー管理リポジトリ" in error
    assert "atk wi・atk plans・atk serve" in error
    assert "atk wi commit" in error
    assert _git(["show-ref", "--verify", "--quiet", ref], queue_repository).returncode == 0
    monkeypatch.chdir(target_repository)
    assert stash.dispatch(args, private_notes=queue_repository) == 0
    assert _git(["show-ref", "--verify", "--quiet", ref], target_repository, check=False).returncode == 1


@pytest.mark.parametrize("failure", ["update-ref", "drop"])
def test_intermediate_failure_preserves_recovery_identifier(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    """ref記録またはdrop失敗時に退避OIDと復旧refの情報を失わない。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    common_dir = tmp_path / "common.git"
    common_dir.mkdir()
    ref = "refs/worktree/failure"
    stash_calls = {"oid": 0}

    def fake_run(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        del cwd
        if args == ["rev-parse", "--git-common-dir"]:
            return subprocess.CompletedProcess(args, 0, str(common_dir), "")
        if args == ["check-ref-format", ref]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args == ["show-ref", "--verify", "--quiet", ref]:
            return subprocess.CompletedProcess(args, 1, "", "")
        if args == ["rev-parse", "--verify", "refs/stash"]:
            stash_calls["oid"] += 1
            stdout = "" if stash_calls["oid"] == 1 else "0123456789abcdef\n"
            return subprocess.CompletedProcess(args, 1 if not stdout else 0, stdout, "")
        if args == ["stash", "push", "--include-untracked"]:
            return subprocess.CompletedProcess(args, 0, "saved\n", "")
        if args == ["update-ref", ref, "0123456789abcdef"]:
            return subprocess.CompletedProcess(args, 1 if failure == "update-ref" else 0, "", "update failed")
        if args == ["stash", "drop", "stash@{0}"]:
            return subprocess.CompletedProcess(args, 1, "", "drop failed")
        raise AssertionError(args)

    monkeypatch.setattr(stash, "_run_git", fake_run)
    result = stash.save("failure", cwd=worktree)

    assert result == 1
    error = capsys.readouterr().err
    assert "stash_oid=0123456789abcdef" in error
    assert ref in error
    if failure == "update-ref":
        assert "共有refs/stashへ保持" in error
    else:
        assert "worktree固有refへ記録済み" in error


def test_fixed_lock_file_is_reused(tmp_path: pathlib.Path) -> None:
    """固定ロックファイルを削除せず、次回の排他取得へ再利用する。"""
    _repo, first, _second = _make_worktrees(tmp_path)
    _make_changes(first, "first")
    common = pathlib.Path(_git(["rev-parse", "--git-common-dir"], first).stdout.strip()).resolve()

    assert _save(first, "lock-save").returncode == 0
    lock_path = common / "agent-toolkit-stash.lock"
    assert lock_path.is_file()
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        _file_lock.acquire_lock(lock_file)
        _file_lock.release_lock(lock_file)


def test_drop_removes_only_the_selected_worktree_ref(tmp_path: pathlib.Path) -> None:
    """dropは選択したrefの現行OIDだけを固定ロック下で削除する。"""
    _repo, first, second = _make_worktrees(tmp_path)
    _make_changes(first, "first")
    _make_changes(second, "second")
    assert _save(first, "first-save").returncode == 0
    assert _save(second, "second-save").returncode == 0

    result = _drop(first, "refs/worktree/first-save")

    assert result.returncode == 0, result.stderr
    assert _git(["show-ref", "--verify", "--quiet", "refs/worktree/first-save"], first, check=False).returncode == 1
    assert _git(["show-ref", "--verify", "--quiet", "refs/worktree/second-save"], second).returncode == 0


def test_drop_uses_the_oid_observed_under_the_lock(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ref削除はロック取得後に読んだOIDをupdate-refの旧値へ渡す。"""
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    common_dir = tmp_path / "common.git"
    common_dir.mkdir()
    ref = "refs/worktree/drop-target"
    oid = "0123456789abcdef"
    calls: list[list[str]] = []

    def fake_run(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        del cwd
        calls.append(args)
        if args == ["check-ref-format", ref]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args == ["rev-parse", "--git-common-dir"]:
            return subprocess.CompletedProcess(args, 0, str(common_dir), "")
        if args == ["rev-parse", "--verify", ref]:
            return subprocess.CompletedProcess(args, 0, f"{oid}\n", "")
        if args == ["update-ref", "-d", ref, oid]:
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)

    monkeypatch.setattr(stash, "_run_git", fake_run)

    assert stash.drop(ref, cwd=worktree) == 0
    assert calls[-2:] == [["rev-parse", "--verify", ref], ["update-ref", "-d", ref, oid]]


def test_drop_removes_shared_stash_by_identifier(tmp_path: pathlib.Path) -> None:
    """共有stashも固定ロックを使う同じ削除経路で回収する。"""
    repo = _make_repository(tmp_path)
    (repo / "untracked.txt").write_text("temporary\n", encoding="utf-8")
    _git(["stash", "push", "--include-untracked"], repo)

    result = _drop(repo, "stash@{0}")

    assert result.returncode == 0, result.stderr
    assert _git(["rev-parse", "--verify", "refs/stash"], repo, check=False).returncode != 0
