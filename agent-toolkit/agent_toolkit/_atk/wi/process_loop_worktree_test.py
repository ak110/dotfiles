# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""専用worktreeの上流追随と起動前のclean判定を検証する。"""

import pathlib

import pytest

from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
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
