"""`worktrees.list_worktrees`が実Gitの登録を、Gitの版によらず元の登録と一致する形で復元することを確かめる。

環境変数`AGENT_TOOLKIT_TEST_GIT`で最低版のGitを指定すると、同じテストを最低版で実行できる
（`agent_toolkit/_testing/isolation.py`）。
"""

import pathlib
import shutil
import sys

import pytest

from agent_toolkit._git import worktrees
from agent_toolkit._testing import git_repository


def _porcelain_z(repo: pathlib.Path) -> dict[pathlib.Path, dict[str, str]] | None:
    """`-z`を受理するGitでは、Git自身の列挙をパスごとの属性へ変換して返す。受理しない版では`None`を返す。"""
    result = git_repository.run_git(repo, "worktree", "list", "--porcelain", "-z", check=False)
    if result.returncode:
        return None
    entries: dict[pathlib.Path, dict[str, str]] = {}
    current: dict[str, str] = {}
    for field in result.stdout.split("\0"):
        if not field:
            if current:
                entries[pathlib.Path(current.pop("worktree"))] = current
            current = {}
            continue
        name, _, value = field.partition(" ")
        current[name] = value
    return entries


def _summary(entry: worktrees.Worktree) -> dict[str, str]:
    summary: dict[str, str] = {}
    if entry.bare:
        summary["bare"] = ""
    elif entry.detached:
        summary["detached"] = ""
    elif entry.head_ref is not None:
        summary["branch"] = entry.head_ref
    if entry.locked:
        summary["locked"] = ""
    if entry.prunable:
        summary["prunable"] = ""
    return summary


def _assert_matches_git(repo: pathlib.Path, listed: list[worktrees.Worktree]) -> None:
    expected = _porcelain_z(repo)
    if expected is None:
        return
    observed = {entry.path: _summary(entry) for entry in listed}
    # Gitの出力は種別ごとに理由やOIDを値に持つ。比べるのは属性の有無とbranchの参照名に限る。
    normalized = {
        path: {name: (value if name == "branch" else "") for name, value in fields.items() if name != "HEAD"}
        for path, fields in expected.items()
    }
    assert observed == normalized


def test_lists_special_paths_and_states(tmp_path: pathlib.Path) -> None:
    """空白・日本語・改行を含むパスとdetached・locked・prunableの登録を、元の登録と同じパス・branchで返す。"""
    repo = git_repository.init_repository(tmp_path / "主 repo", initial_branch="main", commit_message="初期")
    names = ["with space", "日本語の作業ツリー"]
    if sys.platform != "win32":
        names.append("line\nbreak")
    created: dict[pathlib.Path, str] = {}
    for index, name in enumerate(names):
        path = tmp_path / name
        git_repository.run_git(repo, "worktree", "add", "-b", f"branch-{index}", str(path))
        created[path] = f"branch-{index}"
    detached = tmp_path / "detached tree"
    git_repository.run_git(repo, "worktree", "add", "--detach", str(detached))
    locked = tmp_path / "locked tree"
    git_repository.run_git(repo, "worktree", "add", "-b", "locked-branch", str(locked))
    git_repository.run_git(repo, "worktree", "lock", "--reason", "保持", str(locked))
    prunable = tmp_path / "removed tree"
    git_repository.run_git(repo, "worktree", "add", "-b", "removed-branch", str(prunable))
    shutil.rmtree(prunable)

    for start in (repo, *created):
        listed = worktrees.list_worktrees(start)
        by_path = {entry.path.resolve(): entry for entry in listed}
        assert listed[0].path.resolve() == repo.resolve()
        assert listed[0].branch == "main" and not listed[0].bare
        for path, branch in created.items():
            entry = by_path[path.resolve()]
            assert (entry.branch, entry.detached, entry.locked, entry.prunable) == (branch, False, False, False)
        assert by_path[detached.resolve()].detached and by_path[detached.resolve()].branch is None
        assert by_path[locked.resolve()].locked and not by_path[locked.resolve()].prunable
        assert by_path[locked.resolve()].branch == "locked-branch"
        assert by_path[prunable.resolve()].prunable and by_path[prunable.resolve()].branch == "removed-branch"
        assert len(listed) == len(created) + 4
        _assert_matches_git(start, listed)


def test_lists_bare_repository_and_its_linked_worktree(tmp_path: pathlib.Path) -> None:
    """bareリポジトリの主項目をbareとして返し、そこから作成したworktreeのbranchを返す。"""
    seed = git_repository.init_repository(tmp_path / "seed", initial_branch="main", commit_message="初期")
    bare = tmp_path / "bare.git"
    git_repository.run_git(tmp_path, "clone", "--quiet", "--bare", str(seed), str(bare))
    linked = tmp_path / "linked tree"
    git_repository.run_git(bare, "worktree", "add", "-b", "feature", str(linked))

    for start in (bare, linked):
        listed = worktrees.list_worktrees(start)
        assert listed[0].path.resolve() == bare.resolve()
        assert listed[0].bare and listed[0].branch is None and not listed[0].detached
        assert [(entry.path.resolve(), entry.branch) for entry in listed[1:]] == [(linked.resolve(), "feature")]
        _assert_matches_git(start, listed)


def test_reports_failure_outside_repository(tmp_path: pathlib.Path) -> None:
    """Gitリポジトリでない場所では列挙の失敗を例外で返す。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(worktrees.WorktreeListError):
        worktrees.list_worktrees(outside)
