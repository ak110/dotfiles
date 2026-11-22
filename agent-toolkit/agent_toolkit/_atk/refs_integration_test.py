import pathlib

import pytest

from agent_toolkit._atk.wi import alerts
from agent_toolkit._testing import git_repository as git


@pytest.mark.parametrize("collision", ["none", "tag", "branch"])
def test_alerts_tracking_branch_ignores_ref_collisions(tmp_path: pathlib.Path, collision: str) -> None:
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
