"""親だけの履歴・作業ツリー操作が、初期化済み子の状態を保持することを実Gitで確かめる。"""

import os
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit._atk import git_sync, lane
from agent_toolkit._testing import git_repository as git


def _fixture(path: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    source = git.init_repository(path / "source", initial_branch="main", files={"child": "A\n"}, commit_message="A")
    parent = git.init_repository(path / "parent", initial_branch="main")
    git.run_git(parent, "-c", "protocol.file.allow=always", "submodule", "add", str(source), "sub")
    base = git.commit_all(parent, "base")
    (source / "child").write_text("B\n", encoding="utf-8")
    git.commit_all(source, "B")
    child = parent / "sub"
    git.run_git(child, "pull", "--ff-only")
    target = git.commit_all(parent, "advance child")
    (parent / "change").write_text("fixup\n", encoding="utf-8")
    git.commit_all(parent, "fixup! advance child")
    git.run_git(parent, "branch", "old", base)
    git.run_git(parent, "config", "submodule.recurse", "true")
    (child / "untracked").write_text("review notes\n", encoding="utf-8")
    return parent, base, target


def _child_state(parent: pathlib.Path) -> tuple[str, str, str, str, str]:
    child = parent / "sub"
    return (
        git.git_output(child, "rev-parse", "HEAD"),
        git.run_git(child, "symbolic-ref", "--quiet", "HEAD", check=False).stdout,
        git.git_output(child, "status", "--porcelain=v1", "--untracked-files=all"),
        (child / "child").read_text(encoding="utf-8"),
        (child / "untracked").read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize("operation", ["autosquash", "rebase", "keep-reset", "switch", "checkout", "restore"])
@pytest.mark.parametrize("recurse", [True, False])
def test_parent_only_operation_preserves_child(tmp_path: pathlib.Path, operation: str, recurse: bool) -> None:
    """履歴内のgitlinkを変え、再帰設定だけが異なる対照で子のHEAD・branch・内容を比較する。"""
    parent, base, _target = _fixture(tmp_path)
    before = _child_state(parent)
    arguments = {
        "autosquash": ["-c", "rebase.updateRefs=false", "rebase", "-i", "--force-rebase", "--autosquash", base],
        "rebase": ["rebase", "--force-rebase", base],
        "keep-reset": ["reset", "--keep", base],
        "switch": ["switch", "old"],
        "checkout": ["checkout", base],
        "restore": ["restore", "--source", base, "--staged", "--worktree", "--", "sub"],
    }[operation]
    git.run_git(
        parent,
        "-c",
        f"submodule.recurse={str(recurse).lower()}",
        *arguments,
        env={**os.environ, "GIT_SEQUENCE_EDITOR": ":"},
    )
    after = _child_state(parent)
    print(f"{git.git_output(parent, '--version')}: {operation} recurse={recurse} child_preserved={after == before}")
    if not recurse:
        assert after == before
    elif operation not in {"autosquash", "rebase"}:
        assert after != before
    assert git.git_output(parent, "config", "submodule.recurse") == "true"


@pytest.mark.parametrize("redundant", [False, True])
def test_sync_rebase_preserves_child(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, redundant: bool) -> None:
    """private-notes同期のrebase・keep resetが環境の再帰設定を抑止する。"""
    parent, base, _target = _fixture(tmp_path)
    before = _child_state(parent)
    git.run_git(parent, "update-ref", "refs/remotes/origin/main", base)
    git.run_git(parent, "config", "branch.main.remote", "origin")
    git.run_git(parent, "config", "branch.main.merge", "refs/heads/main")
    git.run_git(parent, "config", "remote.origin.url", str(tmp_path / "unused"))
    git.run_git(parent, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    # 分岐検出と通信は境界で固定し、実際の同期rebaseを同じfixtureへ実行する。
    monkeypatch.setattr(git_sync, "has_remote", lambda _path: True)
    monkeypatch.setattr(git_sync, "_history_has_diverged", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(git_sync, "_recover_matching_tree_divergence", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(git_sync, "is_worktree_dirty", lambda *_args, **_kwargs: False)

    def run(args: list[str], cwd: pathlib.Path, **_kwargs: object) -> None:
        if args[0] == "fetch":
            return
        if args[0] == "merge":
            raise subprocess.CalledProcessError(1, args)
        assert args[:2] == ["-c", "submodule.recurse=false"]
        git.run_git(cwd, *args, env={**os.environ, "GIT_SEQUENCE_EDITOR": ":"})

    with git_sync.repo_lock(parent):
        git_sync.pull(parent, run_git=run, redundant_divergence=(lambda _path: True) if redundant else None)
    assert _child_state(parent) == before


def test_lane_detached_branch_connection_preserves_child(tmp_path: pathlib.Path) -> None:
    """準備済みdetached作業ツリーをレーンbranchへ接続しても子のレビュー状態を保持する。"""
    parent, base, _target = _fixture(tmp_path)
    git.run_git(parent, "-c", "submodule.recurse=false", "checkout", "--detach")
    before = _child_state(parent)
    record: dict[str, Any] = {
        "repo": str(parent),
        "worktree": str(parent),
        "branch": "old",
        "initial_head": base,
        "common_dir": git.git_output(parent, "rev-parse", "--path-format=absolute", "--git-common-dir"),
        "commands": {},
    }
    lane._prepare(record, tmp_path / "record.json")  # noqa: SLF001  # pylint: disable=protected-access
    assert record["ready"] is True
    assert _child_state(parent) == before
