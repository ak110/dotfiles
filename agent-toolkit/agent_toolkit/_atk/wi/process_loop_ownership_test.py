"""専用起動・all-reposの共通所有と未統合成果の受渡しを検証する。"""

import concurrent.futures
import dataclasses
import json
import pathlib
import threading

import pytest

from agent_toolkit._atk.wi import process_loop_ownership as ownership
from agent_toolkit._atk.wi import process_loop_session as session
from agent_toolkit._testing import git_repository as git


def test_dedicated_and_all_repos(tmp_path: pathlib.Path) -> None:
    """同originの競合は所有を1件にし、別originは進行する。"""
    repo = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    other = git.init_repository(tmp_path / "other", origin="https://example.test/team/other.git")
    barrier = threading.Barrier(2)

    def acquire(mode: str) -> ownership.Ownership | None:
        barrier.wait(timeout=5)
        return ownership.try_acquire("example.test/team/repo", worktree=repo, mode=mode)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(acquire, mode) for mode in ("dedicated", "all-repos")]
        results = [future.result(timeout=10) for future in futures]
    acquired = [result for result in results if result is not None]
    assert len(acquired) == 1
    independent = ownership.try_acquire("example.test/team/other", worktree=other, mode="all-repos")
    assert independent is not None
    record = json.loads(acquired[0].path.read_text(encoding="utf-8"))
    assert pathlib.Path(record["common_dir"]) == repo / ".git"
    ownership.release(acquired[0])
    replacement = ownership.try_acquire("example.test/team/repo", worktree=repo)
    assert replacement is not None and replacement.previous is None
    ownership.release(replacement)
    ownership.release(independent)


def test_all_repos_resume_preserves_lane_owner(tmp_path: pathlib.Path) -> None:
    """異常終了した担当の会話とレーン所有を継続し、通常会話は混同しない。"""
    repo = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    first = ownership.try_acquire("example.test/team/repo", worktree=repo, mode="all-repos")
    assert first is not None
    first = ownership.update_session(first, "repository-session")
    ownership.release(first, completed=False)
    assert ownership.resume_repository("repository-session", (first.repo_id,)) == first.repo_id
    resumed = ownership.try_acquire(first.repo_id, worktree=repo, mode="all-repos")
    assert resumed is not None
    assert resumed.lane_session_id == first.lane_session_id
    assert session.all_repos_resume_session(resumed, "repository-session") == "repository-session"
    with pytest.raises(ValueError, match="all-repos"):
        session.all_repos_resume_session(resumed, "ordinary-session")
    dedicated = dataclasses.replace(resumed, previous={"mode": "dedicated", "session_id": "ordinary-session"})
    assert session.all_repos_resume_session(dedicated, None) is None
    ownership.release(resumed)


def test_dead_process_takeover(tmp_path: pathlib.Path) -> None:
    """PID再利用を生存と誤認せず、残存成果の記録を引き渡す。"""
    repo = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    first = ownership.try_acquire("example.test/team/repo", worktree=repo)
    assert first is not None
    record = json.loads(first.path.read_text(encoding="utf-8"))
    record["create_time"] = 0
    first.path.write_text(json.dumps(record), encoding="utf-8")
    replacement = ownership.try_acquire(first.repo_id, worktree=repo, mode="all-repos")
    assert replacement is not None and replacement.previous is not None
    assert replacement.previous["worktree"] == str(repo)
    ownership.release(replacement)
