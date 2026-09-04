"""private-notes共通Git同期の対象限定commitとpush保留を検証する。"""

import pathlib
import subprocess

import _atk_git_sync
import _atk_wi_common
import _atk_wi_mutations
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
    source.write_text("---\ntype: awi\n---\n\n同じ項目\n", encoding="utf-8")
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


def _create_matching_tree_divergence(local: pathlib.Path, peer: pathlib.Path) -> str:
    """異なるcommitで同じ木を持つ分岐を作成する。"""
    for repo, message in ((local, "local equivalent"), (peer, "peer equivalent")):
        (repo / "equivalent.txt").write_text("same\n", encoding="utf-8")
        _git(repo, "add", "equivalent.txt")
        _git(repo, "commit", "-m", message)
    _git(peer, "push")
    return _git(peer, "rev-parse", "HEAD").stdout.strip()


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


def test_worktree_dirty_can_limit_status_to_target_paths(tmp_path: pathlib.Path) -> None:
    """対象外の差分を無視し、指定したpathだけの変更を判定する。"""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "unrelated.txt").write_text("changed\n", encoding="utf-8")

    assert not _atk_git_sync.is_worktree_dirty(repo, paths=["target.txt"])
    assert _atk_git_sync.is_worktree_dirty(repo, paths=["unrelated.txt"])
    assert _atk_git_sync.is_worktree_dirty(repo)


def test_pending_commit_count_distinguishes_remote_and_upstream_states(tmp_path: pathlib.Path) -> None:
    """remoteなし、upstream不明及び未push件数を別の結果で返す。"""
    local_only = tmp_path / "local-only"
    local_only.mkdir()
    (local_only / _atk_git_sync.LOCAL_ONLY_MARKER).touch()
    assert _atk_git_sync.pending_commit_count(local_only) == 0

    unresolved = tmp_path / "unresolved"
    unresolved.mkdir()
    assert _atk_git_sync.pending_commit_count(unresolved) is None

    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "--initial-branch=main")
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    (repo / "target.txt").write_text("after\n", encoding="utf-8")
    _git(repo, "add", "target.txt")
    _git(repo, "commit", "-m", "local")

    assert _atk_git_sync.pending_commit_count(repo) == 1


@pytest.mark.parametrize("operation", ["pull", "push"])
def test_sync_failure_reports_recovery_steps_and_preserves_exception(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    """pullとpushの失敗は対応手順を示して元の例外を送出する。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    expected = subprocess.CalledProcessError(1, ["git", operation])

    def run_git(args: list[str], cwd: pathlib.Path) -> None:
        del cwd
        if operation == "pull" or args == ["push"]:
            raise expected
        raise subprocess.CalledProcessError(1, ["git", *args])

    sync = _atk_git_sync.pull if operation == "pull" else _atk_git_sync.push_pending_commits
    with _atk_git_sync.repo_lock(repo), pytest.raises(subprocess.CalledProcessError) as exc_info:
        sync(repo, run_git=run_git)

    assert exc_info.value is expected
    stderr = capsys.readouterr().err
    assert f"private-notesの{operation}に失敗しました: {repo.resolve()}" in stderr
    assert f"確認: `git -C {repo.resolve()} status`" in stderr
    assert "失敗した`atk`操作を再実行" in stderr


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
        if args == ["diff", "--quiet", "HEAD", "@{u}"]:
            return subprocess.CompletedProcess(["git", *args], 1, "", "")
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
@pytest.mark.parametrize("dirty", [False, True])
def test_sync_recovers_matching_tree_divergence(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    operation: str,
    dirty: bool,
) -> None:
    """木が一致する分岐は未コミット差分を保ってpull・pushできる。"""
    local, peer = _init_diverged_mq_repos(tmp_path)
    upstream = _create_matching_tree_divergence(local, peer)
    if dirty:
        source = local / "processing" / "20260831-101752-001.md"
        source.write_text(source.read_text(encoding="utf-8") + "\nstaged\n", encoding="utf-8")
        _git(local, "add", str(source.relative_to(local)))
        (local / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    status_before = _git(local, "status", "--porcelain").stdout

    with _atk_git_sync.repo_lock(local):
        if operation == "pull":
            _atk_wi_common.pull(local)
        else:
            _atk_git_sync.push_pending_commits(local)

    assert _git(local, "rev-parse", "HEAD").stdout.strip() == upstream
    assert _git(local, "status", "--porcelain").stdout == status_before
    assert "HEADとupstreamの内容が一致" in capsys.readouterr().err


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
            _atk_wi_common.pull(local)
    else:
        assert _atk_wi_mutations.commit_entries(local) is False

    assert _git(local, "rev-parse", "HEAD").stdout.strip() == upstream
    assert _git(local, "status", "--porcelain").stdout == ""
    assert "自動同期しました" in capsys.readouterr().err


def test_mq_pull_rebases_clean_divergence(
    tmp_path: pathlib.Path,
) -> None:
    """cleanな履歴分岐はローカルcommitを保持してupstreamへ載せ替える。"""
    local, peer = _init_diverged_mq_repos(tmp_path)
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    _git(local, "add", "local.txt")
    _git(local, "commit", "-m", "local change")
    (peer / "remote.txt").write_text("remote\n", encoding="utf-8")
    _git(peer, "add", "remote.txt")
    _git(peer, "commit", "-m", "remote change")
    _git(peer, "push")
    upstream = _git(peer, "rev-parse", "HEAD").stdout.strip()

    with _atk_git_sync.repo_lock(local):
        _atk_wi_common.pull(local)

    assert _git(local, "merge-base", "--is-ancestor", upstream, "HEAD").returncode == 0
    assert _git(local, "log", "-1", "--format=%s").stdout.strip() == "local change"
    assert _git(local, "status", "--porcelain").stdout == ""


def test_mq_pull_reports_dirty_divergence_without_rewriting(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """dirtyな履歴分岐はrebaseせず、原因と手動回復手順を表示する。"""
    local, peer = _init_diverged_mq_repos(tmp_path)
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    _git(local, "add", "local.txt")
    _git(local, "commit", "-m", "local change")
    local_head = _git(local, "rev-parse", "HEAD").stdout.strip()
    (peer / "remote.txt").write_text("remote\n", encoding="utf-8")
    _git(peer, "add", "remote.txt")
    _git(peer, "commit", "-m", "remote change")
    _git(peer, "push")
    (local / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    with _atk_git_sync.repo_lock(local), pytest.raises(subprocess.CalledProcessError):
        _atk_wi_common.pull(local)

    assert _git(local, "rev-parse", "HEAD").stdout.strip() == local_head
    assert not _atk_git_sync.is_rebase_in_progress(local)
    stderr = capsys.readouterr().err
    assert "Git履歴が分岐しています（ローカルのみ1件、upstreamのみ1件）" in stderr
    assert "local.txt" in stderr
    assert "remote.txt" in stderr
    assert "git rebase @{u}" in stderr
    assert "git rebase --skip" in stderr
    assert "git rebase --abort" in stderr


def test_mq_pull_reports_rebase_failure_and_preserves_state(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """rebase競合時は中間状態を保持し、競合解消手順を表示する。"""
    local, peer = _init_diverged_mq_repos(tmp_path)
    _finish_entry(local, "2026-08-31T20:42:20+00:00", note="local")
    local_head = _git(local, "rev-parse", "HEAD").stdout.strip()
    _finish_entry(peer, "2026-08-31T20:48:40+00:00", note="remote")
    _git(peer, "push")

    with _atk_git_sync.repo_lock(local), pytest.raises(subprocess.CalledProcessError) as exc_info:
        _atk_wi_common.pull(local)

    assert exc_info.value.cmd[-3:] == ["merge", "--ff-only", "@{u}"]
    assert _atk_git_sync.is_rebase_in_progress(local)
    assert _git(local, "rev-parse", "ORIG_HEAD").stdout.strip() == local_head
    stderr = capsys.readouterr().err
    assert "rebaseに失敗したため、rebase状態を保持しています" in stderr
    assert "Git履歴が分岐しています" not in stderr
