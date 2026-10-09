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

from agent_toolkit._atk import worktree_stash as stash  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._common import file_lock as _file_lock  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._testing import git_repository

_SCRIPT = pathlib.Path(stash.__file__).resolve().parents[1] / "atk.py"


def _make_repository(tmp_path: pathlib.Path, name: str = "repo") -> pathlib.Path:
    repo = tmp_path / name
    git_repository.init_repository(repo, initial_branch="main")
    git_repository.run_git(repo, "config", "user.name", "test")
    git_repository.run_git(repo, "config", "user.email", "test@example.invalid")
    (repo / "state.txt").write_text("base\n", encoding="utf-8")
    git_repository.run_git(repo, "add", "state.txt")
    git_repository.run_git(repo, "commit", "-m", "base")
    return repo


def _make_worktrees(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    repo = _make_repository(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    git_repository.run_git(repo, "worktree", "add", "-b", "first", str(first), "main")
    git_repository.run_git(repo, "worktree", "add", "-b", "second", str(second), "main")
    for worktree in (first, second):
        git_repository.run_git(worktree, "config", "user.name", "test")
        git_repository.run_git(worktree, "config", "user.email", "test@example.invalid")
    return repo, first, second


def _make_changes(worktree: pathlib.Path, marker: str) -> None:
    (worktree / "state.txt").write_text(f"{marker}-staged\n", encoding="utf-8")
    git_repository.run_git(worktree, "add", "state.txt")
    (worktree / "state.txt").write_text(f"{marker}-unstaged\n", encoding="utf-8")
    (worktree / "untracked.txt").write_text(f"{marker}-untracked\n", encoding="utf-8")


def _save(worktree: pathlib.Path, label: str, *, timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = str(worktree / "gitconfig-global")
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "worktree-stash", "save", "--label", label],
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
        [sys.executable, str(_SCRIPT), "worktree-stash", "drop", identifier],
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
    assert git_repository.run_git(repo, "rev-parse", "--verify", "refs/stash", check=False).returncode != 0

    for worktree, label, marker in (
        (first, "first-save", "first"),
        (second, "second-save", "second"),
    ):
        assert git_repository.run_git(worktree, "stash", "apply", "--index", f"refs/worktree/{label}").returncode == 0
        assert git_repository.run_git(worktree, "show", ":state.txt").stdout == f"{marker}-staged\n"
        assert (worktree / "state.txt").read_text(encoding="utf-8") == f"{marker}-unstaged\n"
        assert (worktree / "untracked.txt").read_text(encoding="utf-8") == f"{marker}-untracked\n"


def test_existing_stash_is_preserved(tmp_path: pathlib.Path) -> None:
    """既存の共有stashは新規退避分のdropで変化しない。"""
    repo, first, _second = _make_worktrees(tmp_path)
    (repo / "existing.txt").write_text("existing\n", encoding="utf-8")
    git_repository.run_git(repo, "stash", "push", "--include-untracked", "-m", "existing")
    before = git_repository.run_git(repo, "rev-parse", "--verify", "refs/stash").stdout.strip()
    _make_changes(first, "first")

    result = _save(first, "new-save")

    assert result.returncode == 0, result.stderr
    assert git_repository.run_git(repo, "rev-parse", "--verify", "refs/stash").stdout.strip() == before
    assert git_repository.run_git(first, "rev-parse", "--verify", "refs/worktree/new-save").returncode == 0


@pytest.mark.parametrize("label", ["../bad", "", "-leading"])
def test_invalid_label_is_rejected_before_changes(tmp_path: pathlib.Path, label: str) -> None:
    """不正ラベルではstashもrefも変更しない。"""
    _repo, first, _second = _make_worktrees(tmp_path)
    _make_changes(first, "first")
    before = git_repository.run_git(first, "status", "--short").stdout

    result = _save(first, label)

    assert result.returncode == 2
    assert git_repository.run_git(first, "status", "--short").stdout == before
    assert (
        git_repository.run_git(first, "show-ref", "--verify", "--quiet", f"refs/worktree/{label}", check=False).returncode != 0
    )


def test_duplicate_label_and_no_changes_are_rejected(tmp_path: pathlib.Path) -> None:
    """同名refと退避対象なしは作業状態を変えずに終了コード2となる。"""
    _repo, first, _second = _make_worktrees(tmp_path)
    assert _save(first, "empty").returncode == 2
    _make_changes(first, "first")
    assert _save(first, "same").returncode == 0
    (first / "new.txt").write_text("keep\n", encoding="utf-8")
    before = git_repository.run_git(first, "status", "--short").stdout

    duplicate = _save(first, "same")

    assert duplicate.returncode == 2
    assert git_repository.run_git(first, "status", "--short").stdout == before
    assert git_repository.run_git(first, "show-ref", "--verify", "--quiet", "refs/worktree/same", check=False).returncode == 0


def test_save_refuses_queue_repository_worktree(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """saveはprivate-notesを拒否し、別リポジトリでは成功する。"""
    queue_repository = _make_repository(tmp_path, "private-notes")
    target_repository = _make_repository(tmp_path, "target")
    _make_changes(queue_repository, "queue")
    _make_changes(target_repository, "target")
    queue_status = git_repository.run_git(queue_repository, "status", "--short").stdout
    args = argparse.Namespace(command="save", label="queue-save")

    monkeypatch.chdir(queue_repository)
    assert stash.dispatch(args, private_notes=queue_repository) == 2
    error = capsys.readouterr().err
    assert "private-notes" in error
    assert "次の操作: " in error
    assert "atk wi・atk plansのコマンドかatk serveの画面" in error
    assert "atk wi commit" in error
    assert git_repository.run_git(queue_repository, "status", "--short").stdout == queue_status
    assert (
        git_repository.run_git(
            queue_repository, "show-ref", "--verify", "--quiet", "refs/worktree/queue-save", check=False
        ).returncode
        == 1
    )
    monkeypatch.chdir(target_repository)
    args.label = "target-save"
    assert stash.dispatch(args, private_notes=queue_repository) == 0


def test_drop_refuses_queue_repository_worktree(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """dropはprivate-notesを拒否し、別リポジトリでは成功する。"""
    queue_repository = _make_repository(tmp_path, "private-notes")
    target_repository = _make_repository(tmp_path, "target")
    ref = "refs/worktree/drop-target"
    for repository in (queue_repository, target_repository):
        oid = git_repository.run_git(repository, "rev-parse", "HEAD").stdout.strip()
        git_repository.run_git(repository, "update-ref", ref, oid)
    args = argparse.Namespace(command="drop", identifier=ref)

    monkeypatch.chdir(queue_repository)
    assert stash.dispatch(args, private_notes=queue_repository) == 2
    error = capsys.readouterr().err
    assert "private-notes" in error
    assert "次の操作: " in error
    assert "atk wi・atk plansのコマンドかatk serveの画面" in error
    assert "atk wi commit" in error
    assert git_repository.run_git(queue_repository, "show-ref", "--verify", "--quiet", ref).returncode == 0
    monkeypatch.chdir(target_repository)
    assert stash.dispatch(args, private_notes=queue_repository) == 0
    assert git_repository.run_git(target_repository, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 1


def test_protect_refuses_queue_repository_worktree(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """protectはprivate-notesの状態を保って拒否し、別リポジトリでは保護する。"""
    queue_repository = _make_repository(tmp_path, "private-notes")
    target_repository = _make_repository(tmp_path, "target")
    ref = "refs/worktree/protect-target"
    for repository in (queue_repository, target_repository):
        oid = git_repository.run_git(repository, "rev-parse", "HEAD").stdout.strip()
        git_repository.run_git(repository, "update-ref", ref, oid)
        _make_changes(repository, repository.name)
    queue_status = git_repository.run_git(queue_repository, "status", "--short").stdout
    queue_refs = git_repository.run_git(queue_repository, "show-ref").stdout
    args = argparse.Namespace(command="protect", identifier=ref)

    monkeypatch.chdir(queue_repository)
    assert stash.dispatch(args, private_notes=queue_repository) == 2
    error = capsys.readouterr().err
    assert "private-notes" in error
    assert "次の操作: " in error
    assert "atk wi・atk plansのコマンドかatk serveの画面" in error
    assert "atk wi commit" in error
    assert git_repository.run_git(queue_repository, "status", "--short").stdout == queue_status
    assert git_repository.run_git(queue_repository, "show-ref").stdout == queue_refs
    monkeypatch.chdir(target_repository)
    assert stash.dispatch(args, private_notes=queue_repository) == 0
    protections = git_repository.run_git(
        target_repository, "for-each-ref", "--format=%(objectname)", "refs/atk/worktree-stash/"
    ).stdout.splitlines()
    assert protections == [git_repository.run_git(target_repository, "rev-parse", ref).stdout.strip()]


@pytest.mark.parametrize("failure", ["update-ref", "drop"])
def test_intermediate_failure_preserves_recovery_identifier(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    """ref記録またはdrop失敗時に退避OIDと復旧refの情報を失わない。"""
    worktree = _make_repository(tmp_path)
    _make_changes(worktree, "failure")
    ref = "refs/worktree/failure"
    original_run = stash._run_git  # pylint: disable=protected-access  # noqa: SLF001

    def fake_run(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        if (failure == "update-ref" and args[:2] == ["update-ref", ref]) or (
            failure == "drop" and args == ["stash", "drop", "stash@{0}"]
        ):
            return subprocess.CompletedProcess(args, 1, "", "injected failure")
        return original_run(args, cwd)

    monkeypatch.setattr(stash, "_run_git", fake_run)
    result = stash.save("failure", cwd=worktree)

    assert result == 1
    error = capsys.readouterr().err
    oid = git_repository.run_git(worktree, "rev-parse", "refs/stash").stdout.strip()
    assert f"stash_oid={oid}" in error
    assert ref in error
    next_action = next(line for line in error.splitlines() if line.startswith("次の操作: "))
    assert "git -C" in next_action
    assert "stash list" in next_action
    assert f"stash apply {oid}" in next_action
    git_repository.run_git(worktree, "stash", "apply", "--index", oid)
    assert git_repository.run_git(worktree, "show", ":state.txt").stdout == "failure-staged\n"
    assert (worktree / "untracked.txt").read_text(encoding="utf-8") == "failure-untracked\n"
    if failure == "update-ref":
        assert "共有refs/stashへ保持" in error
    else:
        assert "worktree固有refへ記録済み" in error


def test_fixed_lock_file_is_reused(tmp_path: pathlib.Path) -> None:
    """固定ロックファイルを削除せず、次回の排他取得へ再利用する。"""
    _repo, first, _second = _make_worktrees(tmp_path)
    _make_changes(first, "first")
    common = pathlib.Path(git_repository.run_git(first, "rev-parse", "--git-common-dir").stdout.strip()).resolve()

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
    assert (
        git_repository.run_git(first, "show-ref", "--verify", "--quiet", "refs/worktree/first-save", check=False).returncode
        == 1
    )
    assert git_repository.run_git(second, "show-ref", "--verify", "--quiet", "refs/worktree/second-save").returncode == 0


def test_drop_uses_the_oid_observed_under_the_lock(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ref削除はロック取得後に読んだOIDをupdate-refの旧値へ渡す。"""
    worktree = _make_repository(tmp_path)
    _make_changes(worktree, "saved")
    ref = "refs/worktree/drop-target"
    assert _save(worktree, "drop-target").returncode == 0
    original_run = stash._run_git  # pylint: disable=protected-access  # noqa: SLF001
    new_oid = git_repository.run_git(worktree, "rev-parse", "HEAD").stdout.strip()

    def fake_run(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["update-ref", "-d", ref]:
            git_repository.run_git(cwd, "update-ref", ref, new_oid)
        return original_run(args, cwd)

    monkeypatch.setattr(stash, "_run_git", fake_run)

    assert stash.drop(ref, cwd=worktree) == 1
    assert git_repository.run_git(worktree, "rev-parse", ref).stdout.strip() == new_oid


def test_drop_removes_shared_stash_by_identifier(tmp_path: pathlib.Path) -> None:
    """共有stashの回収にも同じ削除処理を使い、固定ロックの保持中に削除する。"""
    repo = _make_repository(tmp_path)
    (repo / "untracked.txt").write_text("temporary\n", encoding="utf-8")
    git_repository.run_git(repo, "stash", "push", "--include-untracked")

    result = _drop(repo, "stash@{0}")

    assert result.returncode == 0, result.stderr
    assert git_repository.run_git(repo, "rev-parse", "--verify", "refs/stash", check=False).returncode != 0


def _protect(worktree: pathlib.Path, ref: str) -> subprocess.CompletedProcess[str]:
    """公開CLIで旧退避を保護する。"""
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "worktree-stash", "protect", ref],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def _gc(worktree: pathlib.Path) -> None:
    git_repository.run_git(worktree, "reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all")
    git_repository.run_git(worktree, "gc", "--prune=now")


def _assert_restored(worktree: pathlib.Path, source: str, marker: str) -> None:
    git_repository.run_git(worktree, "stash", "apply", "--index", source)
    assert git_repository.run_git(worktree, "show", ":state.txt").stdout == f"{marker}-staged\n"
    assert (worktree / "state.txt").read_text(encoding="utf-8") == f"{marker}-unstaged\n"
    assert (worktree / "untracked.txt").read_text(encoding="utf-8") == f"{marker}-untracked\n"


def test_same_label_main_and_linked_survive_gc_fetch_and_restore(tmp_path: pathlib.Path) -> None:
    """他worktreeのGC・fetch・pull後も、同labelの双方を3状態へ復元できる。"""
    repo, first, second = _make_worktrees(tmp_path)
    remote = tmp_path / "remote.git"
    git_repository.run_git(repo, "clone", "--bare", str(repo), str(remote))
    for worktree, marker in ((repo, "main"), (first, "linked")):
        _make_changes(worktree, marker)
        saved = _save(worktree, "same")
        assert saved.returncode == 0, saved.stderr
        assert saved.stdout.strip() == "refs/worktree/same"
        assert saved.stderr.startswith("成功: ")
    _gc(second)
    for worktree, marker in ((repo, "main"), (first, "linked")):
        git_repository.run_git(worktree, "fetch", str(remote), "main")
        git_repository.run_git(worktree, "pull", "--ff-only", str(remote), "main")
        _assert_restored(worktree, "refs/worktree/same", marker)


def test_protect_legacy_ref_is_idempotent_and_preserves_missing_ref(tmp_path: pathlib.Path) -> None:
    """旧固有refを改名せず保護し、欠損OIDは失敗してrefを残す。"""
    repo, first, second = _make_worktrees(tmp_path)
    _make_changes(first, "legacy")
    git_repository.run_git(first, "stash", "push", "--include-untracked")
    oid = git_repository.run_git(first, "rev-parse", "refs/stash").stdout.strip()
    ref = "refs/worktree/legacy"
    git_repository.run_git(first, "update-ref", ref, oid)
    git_repository.run_git(first, "stash", "drop", "stash@{0}")
    for _ in range(2):
        result = _protect(first, ref)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ref
        assert result.stderr.startswith("成功: ")
    _gc(second)
    _assert_restored(first, ref, "legacy")
    missing_ref = "refs/worktree/missing"
    missing_path = pathlib.Path(git_repository.run_git(repo, "rev-parse", "--git-path", missing_ref).stdout.strip())
    if not missing_path.is_absolute():
        missing_path = repo / missing_path
    missing_path.parent.mkdir(parents=True, exist_ok=True)
    missing_path.write_text("1" * 40 + "\n", encoding="ascii")
    failed = _protect(repo, missing_ref)
    assert failed.returncode == 1
    assert missing_ref in failed.stderr
    assert missing_path.read_text(encoding="ascii") == "1" * 40 + "\n"
    absent = _protect(repo, "refs/worktree/absent")
    assert absent.returncode == 2
    assert "refs/worktree/absent" in absent.stderr


@pytest.mark.parametrize("operation", ["show-ref", "rev-parse"])
def test_protect_query_failure_preserves_existing_ref(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    """存在照会とOID照会の障害を不在と区別し、既存refを保持する。"""
    repo = _make_repository(tmp_path)
    ref = "refs/worktree/protect-target"
    oid = git_repository.run_git(repo, "rev-parse", "HEAD").stdout.strip()
    git_repository.run_git(repo, "update-ref", ref, oid)
    before = git_repository.run_git(repo, "show-ref").stdout
    original_run = stash._run_git  # pylint: disable=protected-access  # noqa: SLF001

    def fail_query(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        if args[0] == operation and args[-1] == ref:
            return subprocess.CompletedProcess(args, 128, "", "injected ref query failure")
        return original_run(args, cwd)

    monkeypatch.setattr(stash, "_run_git", fail_query)
    assert stash.protect(ref, cwd=repo) == 1
    assert "照会できない" in capsys.readouterr().err
    assert git_repository.run_git(repo, "show-ref").stdout == before


def test_drop_partial_failure_can_retry_without_losing_protection(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """共有保護の回収失敗後もGCから守り、同じ識別子で再試行できる。"""
    repo, first, second = _make_worktrees(tmp_path)
    (repo / "existing.txt").write_text("existing\n", encoding="utf-8")
    git_repository.run_git(repo, "stash", "push", "--include-untracked")
    existing = git_repository.run_git(repo, "rev-parse", "refs/stash").stdout.strip()
    for worktree, marker in ((repo, "main"), (first, "linked")):
        _make_changes(worktree, marker)
        assert _save(worktree, "same").returncode == 0
    ref = "refs/worktree/same"
    shared = git_repository.run_git(repo, "for-each-ref", "--format=%(refname)", "refs/atk/worktree-stash/main/").stdout.strip()
    original_run = stash._run_git  # pylint: disable=protected-access  # noqa: SLF001

    def fail_delete(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        if args[:3] == ["update-ref", "-d", shared]:
            return subprocess.CompletedProcess(args, 1, "", "injected protection deletion failure")
        return original_run(args, cwd)

    with monkeypatch.context() as patcher:
        patcher.setattr(stash, "_run_git", fail_delete)
        assert stash.drop(ref, cwd=repo) == 1
    error = capsys.readouterr().err
    assert ref in error and shared in error
    _gc(second)
    _assert_restored(repo, shared, "main")
    assert _drop(repo, ref).returncode == 0
    assert git_repository.run_git(repo, "for-each-ref", "--format=%(refname)", "refs/atk/worktree-stash/main/").stdout == ""
    _assert_restored(first, ref, "linked")
    assert git_repository.run_git(repo, "rev-parse", "refs/stash").stdout.strip() == existing
    assert git_repository.run_git(repo, "show", "stash@{0}^3:existing.txt").stdout == "existing\n"


def test_protection_failure_keeps_shared_stash(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共有保護成立前に失敗した場合は、stash先頭をdropせずGC後も復旧できる。"""
    repo, _first, second = _make_worktrees(tmp_path)
    _make_changes(repo, "failure")
    original_run = stash._run_git  # pylint: disable=protected-access  # noqa: SLF001

    def fail_protect(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        if args[0] == "update-ref" and args[1].startswith("refs/atk/worktree-stash/"):
            return subprocess.CompletedProcess(args, 1, "", "injected protection failure")
        return original_run(args, cwd)

    with monkeypatch.context() as patcher:
        patcher.setattr(stash, "_run_git", fail_protect)
        assert stash.save("failure", cwd=repo) == 1
    _gc(second)
    _assert_restored(repo, "refs/stash", "failure")
