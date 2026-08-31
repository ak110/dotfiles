"""private-notes共通Git同期の対象限定commitとpush保留を検証する。"""

import pathlib
import subprocess

import _atk_git_sync
import _atk_mq_common
import _atk_mq_mutations
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


def _init_diverged_mq_repos(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """同じMQ項目を別々に終端できる2つのcloneを作成する。"""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    local = tmp_path / "local"
    peer = tmp_path / "peer"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    _git(seed, "config", "user.name", "sync-test")
    _git(seed, "config", "user.email", "sync-test@example.invalid")
    source = seed / "processing" / "20260831-101752-001.md"
    source.parent.mkdir()
    source.write_text("---\ntype: feedback\n---\n\n同じ項目\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "base")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", "main")
    for clone in (local, peer):
        _git(tmp_path, "clone", "--branch", "main", str(origin), str(clone))
        _git(clone, "config", "user.name", "sync-test")
        _git(clone, "config", "user.email", "sync-test@example.invalid")
    return local, peer


def _finish_entry(repo: pathlib.Path, timestamp: str, *, note: str | None = None) -> None:
    """MQが生成する形式でprocessing項目をadoptedへ移してcommitする。"""
    source = repo / "processing" / "20260831-101752-001.md"
    destination = repo / "adopted" / source.name
    destination.parent.mkdir()
    body = source.read_text(encoding="utf-8")
    body += f"\n## 処理結果\n\n- 採否: adopted\n- 処理日時: {timestamp}\n"
    if note is not None:
        body += f"- メモ: {note}\n"
    destination.write_text(body, encoding="utf-8")
    source.unlink()
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", "chore: process 1 entry (adopted)")


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
    stderr = capsys.readouterr().err
    assert "Git履歴が分岐しています" in stderr
    assert stderr.endswith(_atk_git_sync.PUSH_DEFERRED_MESSAGE + "\n")


@pytest.mark.parametrize("operation", ["pull", "push"])
def test_mq_sync_recovers_duplicate_terminal_commit(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    """処理日時だけが異なる同一終端はpull・pushの両経路でupstreamへ揃える。"""
    local, peer = _init_diverged_mq_repos(tmp_path)
    _finish_entry(local, "2026-08-31T20:42:20+00:00")
    _finish_entry(peer, "2026-08-31T20:48:40+00:00")
    _git(peer, "push")
    upstream = _git(peer, "rev-parse", "HEAD").stdout.strip()

    if operation == "pull":
        with _atk_git_sync.repo_lock(local):
            _atk_mq_common.pull(local)
    else:
        assert _atk_mq_mutations.commit_entries(local) is False

    assert _git(local, "rev-parse", "HEAD").stdout.strip() == upstream
    assert _git(local, "status", "--porcelain").stdout == ""
    assert "自動同期しました" in capsys.readouterr().err


def test_mq_pull_reports_non_equivalent_divergence_without_rewriting(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """処理結果が異なる終端は保持し、原因と手動回復手順を表示する。"""
    local, peer = _init_diverged_mq_repos(tmp_path)
    _finish_entry(local, "2026-08-31T20:42:20+00:00", note="local")
    local_head = _git(local, "rev-parse", "HEAD").stdout.strip()
    _finish_entry(peer, "2026-08-31T20:48:40+00:00", note="remote")
    _git(peer, "push")

    with _atk_git_sync.repo_lock(local), pytest.raises(subprocess.CalledProcessError):
        _atk_mq_common.pull(local)

    assert _git(local, "rev-parse", "HEAD").stdout.strip() == local_head
    assert _git(local, "status", "--porcelain").stdout == ""
    stderr = capsys.readouterr().err
    assert "Git履歴が分岐しています（ローカルのみ1件、upstreamのみ1件）" in stderr
    assert "git rebase @{u}" in stderr
    assert "git rebase --skip" in stderr
    assert "git rebase --abort" in stderr
