"""private-notes共通Git同期の対象限定commitとpush保留を検証する。"""

import pathlib
import subprocess

import _atk_git_sync
import pytest


def _git(root: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """テスト用Gitコマンドを実行する。"""
    return subprocess.run(["git", *args], cwd=root, check=check, capture_output=True, text=True)


def _init_repo(root: pathlib.Path) -> None:
    """commit検証用のGit repositoryを初期化する。"""
    root.mkdir()
    _git(root, "init", "--initial-branch=main")
    _git(root, "config", "user.name", "sync-test")
    _git(root, "config", "user.email", "sync-test@example.invalid")
    (root / "target.txt").write_text("before\n", encoding="utf-8")
    (root / "unrelated.txt").write_text("before\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")


def test_commit_and_push_keeps_unrelated_staged_change_out_of_commit(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """対象外のstage済み差分をindexへ残したまま、対象だけをcommitする。"""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "target.txt").write_text("after\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    _git(repo, "add", "unrelated.txt")

    with _atk_git_sync.repo_lock(repo):
        _atk_git_sync.commit_and_push(repo, "target only", ["target.txt"], skip_push=True)

    committed = _git(repo, "show", "--format=", "--name-only", "HEAD").stdout.splitlines()
    staged = _git(repo, "diff", "--cached", "--name-only").stdout.splitlines()
    assert committed == ["target.txt"]
    assert staged == ["unrelated.txt"]
    assert "未pushのcommit" in capsys.readouterr().err


def test_push_pending_defers_diverged_history_when_worktree_is_dirty(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """履歴分岐と無関係なdirty差分が同時にある場合、rebaseせずpushを保留する。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / _atk_git_sync.LOCAL_ONLY_MARKER).unlink(missing_ok=True)
    calls: list[list[str]] = []

    def run_git(args: list[str], cwd: pathlib.Path) -> None:
        del cwd
        calls.append(args)
        if args == ["push"]:
            raise subprocess.CalledProcessError(1, ["git", *args])
        if args == ["merge-base", "--is-ancestor", "HEAD", "@{u}"]:
            raise subprocess.CalledProcessError(1, ["git", *args])
        if args == ["merge-base", "--is-ancestor", "@{u}", "HEAD"]:
            raise subprocess.CalledProcessError(1, ["git", *args])

    def result_runner(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        del cwd
        if args == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(["git", *args], 0, " M unrelated.txt\n", "")
        return subprocess.CompletedProcess(["git", *args], 0, "", "")

    with _atk_git_sync.repo_lock(repo):
        _atk_git_sync.push_pending_commits(repo, run_git=run_git, result_runner=result_runner)

    assert ["rebase", "@{u}"] not in calls
    assert capsys.readouterr().err == _atk_git_sync.PUSH_DEFERRED_MESSAGE + "\n"
