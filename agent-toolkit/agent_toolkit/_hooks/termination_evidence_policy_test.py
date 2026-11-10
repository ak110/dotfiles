"""完了報告の省略方針が、主作業ツリーの判定（`worktrees.list_worktrees`）に基づいて規範ファイルの所属を判定することを確かめる。

`termination_evidence_test.py`はWindowsで読み込めない依存を含むため、Windows CIの「Windows境界テスト」（`pytest -m windows`）で
実Gitに対して確かめる消費側の試験をこのファイルへ分ける。
"""

import pathlib

import pytest

from agent_toolkit._hooks import termination_evidence
from agent_toolkit._testing import git_repository

pytestmark = pytest.mark.windows


def test_omission_policy_accepts_main_root_policy_and_rejects_nested_worktree_policy(tmp_path: pathlib.Path) -> None:
    """主作業ツリー直下の規範を所属内として受理し、入れ子worktreeの規範は拒否する。"""
    repository = git_repository.init_repository(
        tmp_path / "主 repo", initial_branch="main", files={"CLAUDE.md": "完了報告を省く。\n"}, commit_message="規範を保存"
    )
    nested = repository / ".claude/worktrees/nested"
    git_repository.run_git(repository, "worktree", "add", "-b", "nested", str(nested))
    work = {"deliverables": ["commit"], "repositories": [str(repository)]}
    document = {"policy_file": str(repository / "CLAUDE.md"), "target_repo": str(repository)}
    assert termination_evidence._omission_policy(document, work) == "完了報告を省く。\n"  # pylint: disable=protected-access
    document["policy_file"] = str(nested / "CLAUDE.md")
    with pytest.raises(ValueError, match="policy_fileは対象リポジトリ内"):
        termination_evidence._omission_policy(document, work)  # pylint: disable=protected-access
