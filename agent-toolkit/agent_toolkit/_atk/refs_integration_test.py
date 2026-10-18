"""同名のtagとローカルbranchを持つリポジトリで、worktree準備とCI収集先を検証する。"""

import pathlib

import pytest

from agent_toolkit._atk.wi import alerts, process_loop_worktree
from agent_toolkit._common import worktree_hooks
from agent_toolkit._testing import git_repository as git


@pytest.mark.parametrize("collision", ["none", "worktree-tag", "tag", "branch", "both"])
@pytest.mark.parametrize("hook", [False, True])
def test_worktree_and_alerts_ignore_other_ref_namespaces(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, collision: str, hook: bool
) -> None:
    """作成、hook接続、再利用、追跡先の収集が、他の名前空間の同名refに左右されない。"""
    repo = git.init_repository(tmp_path / "repo", initial_branch="main", files={"file": "main"}, commit_message="main")
    git.run_git(repo, "switch", "-c", "feature")
    (repo / "file").write_text("feature", encoding="utf-8")
    feature = git.commit_all(repo, "feature")
    git.run_git(repo, "remote", "add", "origin", str(repo))
    git.run_git(repo, "fetch", "origin")
    git.run_git(repo, "branch", "--set-upstream-to=origin/feature", "feature")
    git.run_git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    if collision in {"tag", "both"}:
        for name in ("feature", "worktree-fixed", "origin/feature"):
            git.run_git(repo, "tag", name)
    if collision == "worktree-tag":
        git.run_git(repo, "tag", "worktree-fixed")
    if collision in {"branch", "both"}:
        git.run_git(repo, "branch", "--no-track", "origin/feature", "HEAD")
    if hook:
        monkeypatch.setattr(worktree_hooks, "configured", lambda _path: True)

        def create(_repo: pathlib.Path, _name: str, _owner: str, **_kwargs: object) -> pathlib.Path:
            destination = tmp_path / "hook-created"
            git.run_git(repo, "worktree", "add", "-b", "worktree-fixed", str(destination), "refs/heads/feature")
            return destination

        monkeypatch.setattr(worktree_hooks, "create", create)
    first = process_loop_worktree.prepare_session_target(
        repo, "example/repo", "prompt", worktree_name="fixed", resume_pending=False
    )
    assert first is not None
    assert git.git_output(first[0], "rev-parse", "HEAD") == feature
    second = process_loop_worktree.prepare_session_target(
        repo, "example/repo", "prompt", worktree_name="fixed", resume_pending=False
    )
    assert second is not None and second[0] == first[0]
    assert alerts.resolve_target_branch(repo) == "feature"


@pytest.mark.parametrize("collision", ["none", "tag", "branch"])
def test_alerts_tracking_branch_ignores_ref_collisions(tmp_path: pathlib.Path, collision: str) -> None:
    """追跡先がorigin/HEADと異なる場合も、完全名から追跡branchを収集する。"""
    repo = git.init_repository(tmp_path / "repo", initial_branch="feature", commit_message="base")
    git.run_git(repo, "branch", "main")
    git.run_git(repo, "remote", "add", "origin", str(repo))
    git.run_git(repo, "fetch", "origin")
    git.run_git(repo, "branch", "--set-upstream-to=origin/feature", "feature")
    git.run_git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    if collision == "tag":
        git.run_git(repo, "tag", "origin/feature")
    if collision == "branch":
        git.run_git(repo, "branch", "origin/feature")
    assert alerts.resolve_target_branch(repo) == "feature"
