"""専用起動・all-reposの共通所有と未統合成果の受渡しを検証する。"""

import concurrent.futures
import json
import pathlib
import threading

import pytest

from agent_toolkit._atk.wi import process_loop_ownership as ownership
from agent_toolkit._common import session_registry
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
    independent = ownership.try_acquire("example.test/team/other", worktree=other, mode="coordinator")
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
    first = ownership.try_acquire("example.test/team/repo", worktree=repo, mode="coordinator")
    assert first is not None
    first = ownership.update_session(first, "repository-session")
    ownership.release(first, completed=False)
    assert ownership.resume_repositories("repository-session", (first.repo_id,)) == {first.repo_id}
    resumed = ownership.try_acquire(first.repo_id, worktree=repo, mode="coordinator")
    assert resumed is not None
    assert resumed.lane_session_id == first.lane_session_id
    with pytest.raises(ValueError, match="統括"):
        ownership.resume_repositories("ordinary-session", (first.repo_id,))
    ownership.release(resumed)


def test_executor_keeps_ownership_before_session_id(tmp_path: pathlib.Path) -> None:
    """会話ID未確定のまま親を失っても、統括CLIの生存中は新しいwriterを許可しない。"""
    repository = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    first = ownership.try_acquire("example.test/team/repo", worktree=repository, mode="coordinator")
    assert first is not None and first.session_id is None
    process = session_registry.owner_process()
    assert process is not None
    ownership.update_process(first, process)
    ownership.hold(first)
    record = json.loads(first.path.read_text(encoding="utf-8"))
    record["create_time"] = 0
    first.path.write_text(json.dumps(record), encoding="utf-8")
    assert ownership.executor_running(first)
    assert ownership.try_acquire(first.repo_id, worktree=repository, mode="coordinator") is None
    record["executor_process"]["create_time"] = 0
    first.path.write_text(json.dumps(record), encoding="utf-8")
    recovered = ownership.try_acquire(first.repo_id, worktree=repository, mode="coordinator")
    assert recovered is not None and recovered.lane_session_id == first.lane_session_id
    ownership.release(recovered)


def test_live_descendant_blocks_takeover_after_parent_loss(tmp_path: pathlib.Path) -> None:
    """終了処理へ到達する前の親喪失でも、残存する配下writerを所有として保持する。"""
    repository = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    first = ownership.try_acquire("example.test/team/repo", worktree=repository, session_id="root", mode="coordinator")
    assert first is not None
    process = session_registry.owner_process()
    assert process is not None
    session_registry.publish("root", terminal=True)
    session_registry.publish("writer", terminal=False, processes=(process,), launcher_session_id="root")
    record = json.loads(first.path.read_text(encoding="utf-8"))
    record["create_time"] = 0
    first.path.write_text(json.dumps(record), encoding="utf-8")
    assert ownership.try_acquire(first.repo_id, worktree=repository, mode="coordinator") is None
    session_registry.publish("writer", terminal=True, launcher_session_id="root")
    recovered = ownership.try_acquire(first.repo_id, worktree=repository, mode="coordinator")
    assert recovered is not None
    ownership.release(recovered)


def test_dead_process_takeover(tmp_path: pathlib.Path) -> None:
    """PID再利用を生存と誤認せず、残存成果の記録を引き渡す。"""
    repo = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    first = ownership.try_acquire("example.test/team/repo", worktree=repo)
    assert first is not None
    record = json.loads(first.path.read_text(encoding="utf-8"))
    record["create_time"] = 0
    first.path.write_text(json.dumps(record), encoding="utf-8")
    replacement = ownership.try_acquire(first.repo_id, worktree=repo, mode="coordinator")
    assert replacement is not None and replacement.previous is not None
    assert replacement.previous["worktree"] == str(repo)
    ownership.release(replacement)


@pytest.mark.parametrize(
    "state", [session_registry.Resolution.RUNNING, session_registry.Resolution.UNREADABLE, session_registry.Resolution.TERMINAL]
)
def test_held_ownership_live_uses_session_only(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    state: session_registry.Resolution,
) -> None:
    """保持中の所有は再起動後も担当sessionだけで判定し、非生存ならpreviousへ残して取得する。"""
    repo = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    first = ownership.try_acquire("example.test/team/repo", worktree=repo, mode="coordinator")
    assert first is not None
    first = ownership.update_session(first, "held-session")
    # holdが保存する形式を再起動前から残す。実装前も同じ保存入力で判定を比較できる。
    record = json.loads(first.path.read_text(encoding="utf-8"))
    record["held"] = True
    first.path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(session_registry, "resolve", lambda *_args, **_kwargs: session_registry.SessionResolution(state))
    replacement = ownership.try_acquire(first.repo_id, worktree=repo, mode="coordinator")
    if state in {session_registry.Resolution.RUNNING, session_registry.Resolution.UNREADABLE}:
        assert replacement is None
    else:
        assert replacement is not None and replacement.previous is not None
        assert replacement.previous["session_id"] == "held-session"
        assert replacement.previous["released"]
        ownership.release(replacement)
