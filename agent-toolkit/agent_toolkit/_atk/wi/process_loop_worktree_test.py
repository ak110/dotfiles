# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""専用worktreeの上流追随と起動前のclean判定を検証する。"""

import pathlib
import signal
import subprocess
import sys

import pytest

from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._git import command as git_command
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.process_loop_worktree_support import (
    _prepare_process_loop_environment as _prepare_process_loop_environment,
)
from agent_toolkit._testing.process_loop_worktree_support import (
    _process_loop_commands_isolated as _process_loop_commands_isolated,
)


def _upstream_repository(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """先行commitを独立して作れるローカルremoteと対象cloneを返す。"""
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    peer = git_repository.init_repository(
        tmp_path / "peer", initial_branch="main", origin=str(remote), files={"shared.md": "base\n"}, commit_message="base"
    )
    git_repository.run_git(peer, "push", "-u", "origin", "main")
    local = tmp_path / "repo"
    git_repository.run_git(tmp_path, "clone", str(remote), str(local))
    return local, peer


@pytest.mark.parametrize("tracking", ["origin", "upstream", "fallback"])
@pytest.mark.parametrize("existing", [False, True])
def test_worktree_tracks_upstream_and_preserves_local_commit(
    tmp_path: pathlib.Path,
    tracking: str,
    existing: bool,
) -> None:
    """mainと異なる追跡branch・別名remote・origin/HEAD fallbackで双方の成果を保持する。"""
    local, peer = _upstream_repository(tmp_path)
    worktree = local / ".claude/worktrees/process-loop"
    if tracking != "fallback":
        git_repository.run_git(peer, "switch", "-c", "feature")
        (peer / "feature.md").write_text("非既定branch固有の成果\n", encoding="utf-8")
        git_repository.commit_all(peer, "feature work")
        git_repository.run_git(peer, "push", "-u", "origin", "feature")
        git_repository.run_git(local, "fetch", "origin")
        git_repository.run_git(local, "switch", "-c", "feature", "--track", "origin/feature")
    if tracking == "upstream":
        git_repository.run_git(local, "remote", "rename", "origin", "upstream")
    if tracking == "fallback":
        git_repository.run_git(local, "branch", "--unset-upstream")
    else:
        # origin/HEADが無くても明示された追跡branchを選ぶ。
        git_repository.run_git(local, "symbolic-ref", "--delete", f"refs/remotes/{tracking}/HEAD")
    if existing:
        git_repository.run_git(local, "worktree", "add", "-b", "worktree-process-loop", str(worktree))
        (worktree / "local.md").write_text("ローカル成果\n", encoding="utf-8")
        git_repository.commit_all(worktree, "local work")
    (peer / "remote.md").write_text("先行成果\n", encoding="utf-8")
    upstream = git_repository.commit_all(peer, "remote work")
    git_repository.run_git(peer, "push")
    assert _pl_worktree._sync_worktree_with_upstream(local, "process-loop") == worktree  # pylint: disable=protected-access
    assert git_repository.run_git(worktree, "merge-base", "--is-ancestor", upstream, "HEAD", check=False).returncode == 0
    assert (worktree / "remote.md").read_text(encoding="utf-8") == "先行成果\n"
    if tracking != "fallback":
        assert (worktree / "feature.md").read_text(encoding="utf-8") == "非既定branch固有の成果\n"
    if existing:
        assert (worktree / "local.md").read_text(encoding="utf-8") == "ローカル成果\n"
        assert git_repository.git_output(worktree, "log", "-1", "--format=%s") == "local work"
    assert git_repository.git_output(worktree, "branch", "--show-current") == "worktree-process-loop"
    assert git_repository.git_output(worktree, "status", "--porcelain") == ""


@pytest.mark.parametrize("dirty", ["unstaged", "staged", "untracked"])
def test_dirty_worktree_keeps_user_changes_and_head(
    tmp_path: pathlib.Path,
    dirty: str,
) -> None:
    """各種のユーザー差分があるworktreeは起動先にせず、差分とHEADを保持する。"""
    local, _peer = _upstream_repository(tmp_path)
    worktree = _pl_worktree._sync_worktree_with_upstream(local, "process-loop")  # pylint: disable=protected-access
    assert worktree is not None
    path = worktree / ("untracked.md" if dirty == "untracked" else "shared.md")
    path.write_text("保存するユーザー差分\n", encoding="utf-8")
    if dirty == "staged":
        git_repository.run_git(worktree, "add", path.name)
    before = git_repository.git_output(worktree, "rev-parse", "HEAD")
    status = git_repository.git_output(worktree, "status", "--porcelain")
    assert (
        _pl_worktree.prepare_session_target(
            local, "github.com/example/repo", "prompt", worktree_name="process-loop", resume_pending=False
        )
        is None
    )
    assert path.read_text(encoding="utf-8") == "保存するユーザー差分\n"
    assert git_repository.git_output(worktree, "rev-parse", "HEAD") == before
    assert git_repository.git_output(worktree, "status", "--porcelain") == status


def test_worktree_rebase_conflict_aborts_without_losing_commits(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実競合では起動先を返さず、rebaseを中止して双方のcommitと本文を保持する。"""
    local, peer = _upstream_repository(tmp_path)
    worktree = _pl_worktree._sync_worktree_with_upstream(local, "process-loop")  # pylint: disable=protected-access
    assert worktree is not None
    (worktree / "shared.md").write_text("ローカル変更\n", encoding="utf-8")
    before = git_repository.commit_all(worktree, "local conflict")
    (peer / "shared.md").write_text("remote変更\n", encoding="utf-8")
    remote_commit = git_repository.commit_all(peer, "remote conflict")
    git_repository.run_git(peer, "push")
    assert (
        _pl_worktree.prepare_session_target(
            local, "github.com/example/repo", "prompt", worktree_name="process-loop", resume_pending=False
        )
        is None
    )
    assert git_repository.git_output(worktree, "rev-parse", "HEAD") == before
    assert (worktree / "shared.md").read_text(encoding="utf-8") == "ローカル変更\n"
    assert git_repository.git_output(worktree, "show", f"{remote_commit}:shared.md") == "remote変更"
    assert git_repository.git_output(worktree, "status", "--porcelain") == ""
    git_dir = pathlib.Path(git_repository.git_output(worktree, "rev-parse", "--absolute-git-dir"))
    assert not (git_dir / "rebase-merge").exists()
    assert not (git_dir / "rebase-apply").exists()
    error = capsys.readouterr().err
    assert "追随に失敗したため実装セッションを起動しません" in error
    assert "rebase refs/remotes/origin/main`を手作業で実行" in error


def test_registered_branch_elsewhere_is_preserved_when_target_worktree_is_missing(tmp_path: pathlib.Path) -> None:
    """別worktreeで使用中の専用branchを勝手に解除せず、作成失敗として保持する。"""
    local, _peer = _upstream_repository(tmp_path)
    other = tmp_path / "existing"
    git_repository.run_git(local, "worktree", "add", "-b", "worktree-process-loop", str(other))
    (other / "user.md").write_text("ユーザー差分\n", encoding="utf-8")
    before = git_repository.git_output(other, "rev-parse", "HEAD")
    assert _pl_worktree._sync_worktree_with_upstream(local, "process-loop") is None  # pylint: disable=protected-access
    assert (other / "user.md").read_text(encoding="utf-8") == "ユーザー差分\n"
    assert git_repository.git_output(other, "rev-parse", "HEAD") == before
    assert not (local / ".claude/worktrees/process-loop").exists()


def test_fetch_failure_preserves_worktree_and_prevents_session_target(tmp_path: pathlib.Path) -> None:
    """取得不能なremoteは正常なworktreeを変更せず、起動先の準備を失敗させる。"""
    local, _peer = _upstream_repository(tmp_path)
    worktree = _pl_worktree._sync_worktree_with_upstream(local, "process-loop")  # pylint: disable=protected-access
    assert worktree is not None
    before = git_repository.git_output(worktree, "rev-parse", "HEAD")
    git_repository.run_git(local, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    assert (
        _pl_worktree.prepare_session_target(
            local, "github.com/example/repo", "prompt", worktree_name="process-loop", resume_pending=False
        )
        is None
    )
    assert git_repository.git_output(worktree, "rev-parse", "HEAD") == before
    assert (worktree / "shared.md").read_text(encoding="utf-8") == "base\n"
    assert git_repository.git_output(worktree, "status", "--porcelain") == ""


def test_unresolved_upstream_preserves_repository_and_guides_setting(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """追跡先もorigin/HEADも無い場合、本文とHEADを保ち設定方法を案内する。"""
    local, _peer = _upstream_repository(tmp_path)
    git_repository.run_git(local, "branch", "--unset-upstream")
    git_repository.run_git(local, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    before = git_repository.git_output(local, "rev-parse", "HEAD")
    assert _pl_worktree._sync_worktree_with_upstream(local, "process-loop") is None  # pylint: disable=protected-access
    assert git_repository.git_output(local, "rev-parse", "HEAD") == before
    assert (local / "shared.md").read_text(encoding="utf-8") == "base\n"
    assert "branch -u <remote>/<branch>" in capsys.readouterr().err


_SYNC_IN_CHILD = (
    "import os,pathlib,sys; os.environ['ATK_TEST_SYNC_PID']=str(os.getpid()); "
    "from agent_toolkit._atk.wi import process_loop_worktree as m; "
    "m._sync_worktree_with_upstream(pathlib.Path(sys.argv[1]),'process-loop')"
)


def _diverged_worktree(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path, str, str]:
    """未公開のローカルcommitと先行した上流を持つ専用worktreeを用意し、両commitのOIDを返す。"""
    local, peer = _upstream_repository(tmp_path)
    worktree = _pl_worktree._sync_worktree_with_upstream(local, "process-loop")  # pylint: disable=protected-access
    assert worktree is not None
    (worktree / "local.md").write_text("ローカル成果\n", encoding="utf-8")
    local_commit = git_repository.commit_all(worktree, "local work")
    (peer / "remote.md").write_text("先行成果\n", encoding="utf-8")
    upstream = git_repository.commit_all(peer, "remote work")
    git_repository.run_git(peer, "push")
    return local, worktree, local_commit, upstream


def _kill_worker_during_sync_rebase(local: pathlib.Path) -> None:
    """process-loopのworkerを、追随のrebaseがHEADを切り離した時点でrebaseごと強制終了する。

    常駐の停止時に`process_tree`がworkerの木を止める場面を、post-checkout hookからのSIGKILLで再現する。
    """
    hook = local / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text('#!/bin/sh\nkill -KILL "$ATK_TEST_SYNC_PID" "$PPID"\n', encoding="utf-8")
    hook.chmod(0o755)
    try:
        child = subprocess.run(
            [sys.executable, "-c", _SYNC_IN_CHILD, str(local)], capture_output=True, text=True, timeout=60, check=False
        )
    finally:
        hook.unlink()
    assert child.returncode == -signal.SIGKILL, child.stderr


def _in_progress(worktree: pathlib.Path, name: str) -> bool:
    return pathlib.Path(git_repository.git_output(worktree, "rev-parse", "--path-format=absolute", "--git-path", name)).exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIXのhookとシグナルで中断を再現する")
@pytest.mark.cpu_limit(3.0, reason="workerの強制終了を再現するため、製品モジュールをimportする子プロセスを起動する")
def test_rebase_interrupted_by_worker_kill_is_recovered_on_next_iteration(tmp_path: pathlib.Path) -> None:
    """追随のrebase中にworkerが止められても、次の反復で元へ戻してから追随し直し、双方の成果を保つ。"""
    local, worktree, _local_commit, upstream = _diverged_worktree(tmp_path)
    _kill_worker_during_sync_rebase(local)
    assert git_repository.run_git(worktree, "symbolic-ref", "-q", "HEAD", check=False).returncode != 0
    assert _in_progress(worktree, "rebase-merge")

    assert _pl_worktree._sync_worktree_with_upstream(local, "process-loop") == worktree  # pylint: disable=protected-access
    assert git_repository.git_output(worktree, "branch", "--show-current") == "worktree-process-loop"
    assert git_repository.run_git(worktree, "merge-base", "--is-ancestor", upstream, "HEAD", check=False).returncode == 0
    assert git_repository.git_output(worktree, "log", "-1", "--format=%s") == "local work"
    assert (worktree / "local.md").read_text(encoding="utf-8") == "ローカル成果\n"
    assert (worktree / "remote.md").read_text(encoding="utf-8") == "先行成果\n"
    assert git_repository.git_output(worktree, "status", "--porcelain") == ""
    assert not _in_progress(worktree, "rebase-merge")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIXのhookとシグナルで中断を再現する")
@pytest.mark.cpu_limit(3.0, reason="workerの強制終了を再現するため、製品モジュールをimportする子プロセスを起動する")
def test_interrupted_rebase_with_user_change_is_kept_and_reported(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """中断後に作業ツリーへ変更が加わったrebaseは自動で中止せず、変更と途中状態を保って対処を案内する。"""
    local, worktree, local_commit, _upstream = _diverged_worktree(tmp_path)
    _kill_worker_during_sync_rebase(local)
    (worktree / "user.md").write_text("中断後のユーザー変更\n", encoding="utf-8")
    capsys.readouterr()

    assert _pl_worktree._sync_worktree_with_upstream(local, "process-loop") is None  # pylint: disable=protected-access
    assert (worktree / "user.md").read_text(encoding="utf-8") == "中断後のユーザー変更\n"
    assert _in_progress(worktree, "rebase-merge")
    assert git_repository.git_output(worktree, "rev-parse", "refs/heads/worktree-process-loop") == local_commit
    error = capsys.readouterr().err
    assert "rebaseが途中のまま残っている" in error
    assert f"`git -C {worktree} -c submodule.recurse=false rebase --abort`" in error


@pytest.mark.skipif(sys.platform == "win32", reason="POSIXのhookで時間切れを再現する")
@pytest.mark.cpu_limit(1.0, reason="実Gitのrebaseを時間上限で打ち切り、中止まで確かめる")
def test_rebase_timeout_is_aborted_and_reported(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """追随のrebaseが時間上限に達しても例外で抜けず、rebaseを中止して専用ブランチと成果を元へ戻す。"""
    local, worktree, local_commit, _upstream = _diverged_worktree(tmp_path)
    once = tmp_path / "hook-ran"
    hook = local / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f'#!/bin/sh\n[ -e "{once}" ] && exit 0\ntouch "{once}"\nsleep 30\n', encoding="utf-8")
    hook.chmod(0o755)
    monkeypatch.setattr(git_command, "MUTATION_TIMEOUT_SECONDS", 2.0)
    capsys.readouterr()

    assert _pl_worktree._sync_worktree_with_upstream(local, "process-loop") is None  # pylint: disable=protected-access
    assert git_repository.git_output(worktree, "branch", "--show-current") == "worktree-process-loop"
    assert git_repository.git_output(worktree, "rev-parse", "HEAD") == local_commit
    assert not _in_progress(worktree, "rebase-merge")
    assert git_repository.git_output(worktree, "status", "--porcelain") == ""
    assert "時間上限" in capsys.readouterr().err


def _leave_rebase_conflict(worktree: pathlib.Path, peer: pathlib.Path) -> None:
    (worktree / "shared.md").write_text("ローカル変更\n", encoding="utf-8")
    git_repository.commit_all(worktree, "local conflict")
    (peer / "shared.md").write_text("remote変更\n", encoding="utf-8")
    git_repository.commit_all(peer, "remote conflict")
    git_repository.run_git(peer, "push")
    git_repository.run_git(worktree, "fetch", "origin")


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("rebase", ["rebaseが途中のまま残っている", "-c submodule.recurse=false rebase --continue`", "rebase --abort`"]),
        ("merge", ["mergeが途中のまま残っている", " merge --abort`"]),
        ("broken-gitdir", ["rev-parse --git-common-dir`が終了コード128で失敗した", " worktree repair "]),
        (
            "unreachable-detached",
            ["ローカルブランチにも上流にも含まれない", " branch <退避先の名前> HEAD`", " switch worktree-process-loop`"],
        ),
        ("remote-only-detached", ["ローカルブランチにも上流にも含まれない", " switch worktree-process-loop`"]),
        ("dirty-other-branch", ["`refs/heads/other`にあり、未commitの変更がある", " switch worktree-process-loop`"]),
    ],
)
def test_unusable_existing_worktree_reports_failed_condition_and_keeps_state(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], state: str, expected: list[str]
) -> None:
    """既存worktreeが使えない場合は、成り立たなかった条件と状態ごとの対処を示し、途中の作業を変更しない。"""
    local, peer = _upstream_repository(tmp_path)
    worktree = _pl_worktree._sync_worktree_with_upstream(local, "process-loop")  # pylint: disable=protected-access
    assert worktree is not None
    if state in ("rebase", "merge"):
        _leave_rebase_conflict(worktree, peer)
        command = ["rebase", "origin/main"] if state == "rebase" else ["merge", "--no-edit", "origin/main"]
        assert git_repository.run_git(worktree, *command, check=False).returncode != 0
    elif state == "broken-gitdir":
        (worktree / ".git").write_text(f"gitdir: {tmp_path / 'moved' / 'process-loop'}\n", encoding="utf-8")
    elif state == "unreachable-detached":
        git_repository.run_git(worktree, "switch", "--detach", "HEAD")
        git_repository.commit_all(worktree, "detached work")
    elif state == "remote-only-detached":
        # 公開用ブランチをpushした後にローカルのブランチだけ消した状態。リモートで消されると次のfetchで辿れなくなりうる。
        git_repository.run_git(worktree, "switch", "-c", "feature-x")
        git_repository.commit_all(worktree, "feature work")
        git_repository.run_git(worktree, "push", "origin", "feature-x")
        git_repository.run_git(worktree, "switch", "--detach", "feature-x")
        git_repository.run_git(worktree, "branch", "-D", "feature-x")
    else:
        git_repository.run_git(worktree, "switch", "-c", "other")
        (worktree / "user.md").write_text("ユーザー変更\n", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in worktree.iterdir() if path.is_file()}
    head = git_repository.run_git(worktree, "rev-parse", "HEAD", check=False).stdout
    capsys.readouterr()

    assert _pl_worktree._sync_worktree_with_upstream(local, "process-loop") is None  # pylint: disable=protected-access
    error = capsys.readouterr().err
    for fragment in expected:
        assert fragment in error
    assert {path.name: path.read_bytes() for path in worktree.iterdir() if path.is_file()} == before
    assert git_repository.run_git(worktree, "rev-parse", "HEAD", check=False).stdout == head
    if state in ("rebase", "merge"):
        assert _in_progress(worktree, "rebase-merge" if state == "rebase" else "MERGE_HEAD")


@pytest.mark.parametrize("state", ["detached-at-upstream", "branch-deleted", "detached-inside-branch", "other-branch"])
def test_worktree_off_dedicated_branch_without_unique_work_is_returned(tmp_path: pathlib.Path, state: str) -> None:
    """HEADが専用ブランチから外れていても、失うcommitと変更が無ければ専用ブランチへ戻して追随し、全ての成果を保つ。

    `detached-at-upstream`と`branch-deleted`は、MRで公開する実装セッションが上流の追跡refを取り出して終えた状態である。
    """
    local, worktree, local_commit, upstream = _diverged_worktree(tmp_path)
    git_repository.run_git(worktree, "fetch", "origin")
    kept: dict[str, str] = {}
    if state in ("detached-at-upstream", "branch-deleted"):
        git_repository.run_git(worktree, "checkout", "origin/main")
        if state == "branch-deleted":
            git_repository.run_git(local, "branch", "-D", "worktree-process-loop")
    elif state == "detached-inside-branch":
        (worktree / "second.md").write_text("2件目の成果\n", encoding="utf-8")
        kept["worktree-process-loop"] = git_repository.commit_all(worktree, "second work")
        git_repository.run_git(worktree, "switch", "--detach", "HEAD~1")
    else:
        git_repository.run_git(worktree, "switch", "-c", "feature-x")
        (worktree / "feature.md").write_text("公開用ブランチの成果\n", encoding="utf-8")
        kept["feature-x"] = git_repository.commit_all(worktree, "feature work")

    assert _pl_worktree._sync_worktree_with_upstream(local, "process-loop") == worktree  # pylint: disable=protected-access
    assert git_repository.git_output(worktree, "branch", "--show-current") == "worktree-process-loop"
    assert git_repository.git_output(worktree, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/main"
    assert git_repository.run_git(worktree, "merge-base", "--is-ancestor", upstream, "HEAD", check=False).returncode == 0
    if state != "branch-deleted":
        assert git_repository.run_git(worktree, "cherry", "HEAD", local_commit, "origin/main").stdout.startswith("-")
    if "worktree-process-loop" in kept:
        assert git_repository.git_output(worktree, "log", "-1", "--format=%s") == "second work"
    if "feature-x" in kept:
        assert git_repository.git_output(worktree, "rev-parse", "refs/heads/feature-x") == kept["feature-x"]
    assert git_repository.git_output(worktree, "status", "--porcelain") == ""
