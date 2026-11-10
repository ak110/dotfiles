"""専用起動・all-reposの共通所有と未統合成果の受渡しを検証する。"""

import concurrent.futures
import itertools
import json
import pathlib
import threading

import pytest

from agent_toolkit._atk.wi import process_loop_ownership as ownership
from agent_toolkit._atk.wi import snapshot as wi_snapshot
from agent_toolkit._common import session_registry
from agent_toolkit._testing import git_repository as git


def test_snapshot_survives_incomplete_restarts(tmp_path: pathlib.Path) -> None:
    """同じ実行の多段再開では初回集合だけを渡し、正常完了後だけ保存物を回収する。"""
    repo = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    owner = ownership.try_acquire("example.test/team/repo", worktree=repo, mode="coordinator")
    assert owner is not None
    entries = [(tmp_path / "old.md", owner.repo_id, "本文", "adopted", "awi")]
    baseline = ownership.save_snapshot(owner, entries)
    assert wi_snapshot.read(baseline) == {(owner.repo_id, "old.md")}
    for _ in range(2):
        ownership.release(owner, completed=False)
        owner = ownership.try_acquire(owner.repo_id, worktree=repo, mode="coordinator")
        assert owner is not None
        assert ownership.save_snapshot(owner, []) == baseline
        assert wi_snapshot.read(baseline) == {(owner.repo_id, "old.md")}
    baseline.write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError, match="開始時集合"):
        ownership.save_snapshot(owner, entries)
    assert baseline.read_text(encoding="utf-8") == "broken"
    ownership.release(owner)
    assert not baseline.exists()


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
    """元の集合とレーン所有を継続し、台帳に無い会話も明示resumeの対象にする。"""
    repo = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    first = ownership.try_acquire("example.test/team/repo", worktree=repo, mode="coordinator")
    assert first is not None
    first = ownership.update_session(first, "repository-session")
    ownership.release(first, completed=False)
    assert ownership.resume_repositories("repository-session", (first.repo_id,)) == {first.repo_id}
    resumed = ownership.try_acquire(first.repo_id, worktree=repo, mode="coordinator")
    assert resumed is not None
    assert resumed.lane_session_id == first.lane_session_id
    assert ownership.resume_repositories("ordinary-session", (first.repo_id,)) == {first.repo_id}
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
    session_registry.publish("root", state=session_registry.PublicationState.RESULT_COMPLETED)
    session_registry.publish(
        "writer", state=session_registry.PublicationState.PENDING_RUNNING, processes=(process,), launcher_session_id="root"
    )
    record = json.loads(first.path.read_text(encoding="utf-8"))
    record["create_time"] = 0
    first.path.write_text(json.dumps(record), encoding="utf-8")
    assert ownership.try_acquire(first.repo_id, worktree=repository, mode="coordinator") is None
    session_registry.publish("writer", state=session_registry.PublicationState.RESULT_COMPLETED, launcher_session_id="root")
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


@pytest.mark.parametrize("flags", list(itertools.product((False, True), repeat=3)))
def test_saved_stage_combinations(tmp_path: pathlib.Path, flags: tuple[bool, bool, bool]) -> None:
    """保存された8組を境界で読み、4段階だけを受理する。"""
    repo = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    owner = ownership.try_acquire("example.test/team/repo", worktree=repo, mode="coordinator")
    assert owner is not None
    record = json.loads(owner.path.read_text(encoding="utf-8"))
    record.update(zip(("completed", "released", "held"), flags, strict=True))
    owner.path.write_text(json.dumps(record), encoding="utf-8")
    valid = {(False, False, False), (False, False, True), (False, True, False), (True, True, False)}
    if flags in valid:
        assert ownership.unfinished_repository_ids((owner.repo_id,)) == (set() if flags[0] else {owner.repo_id})
    else:
        with pytest.raises(ValueError, match="段階の組合せ"):
            ownership.unfinished_repository_ids((owner.repo_id,))


@pytest.mark.parametrize("key", ["completed", "released", "held"])
@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_saved_stage_requires_bool(tmp_path: pathlib.Path, key: str, value: object) -> None:
    """JSONの数値・文字列・nullをboolとして受け入れない。"""
    repo = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    owner = ownership.try_acquire("example.test/team/repo", worktree=repo)
    assert owner is not None
    record = json.loads(owner.path.read_text(encoding="utf-8"))
    record[key] = value
    owner.path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="bool"):
        ownership.try_acquire(owner.repo_id, worktree=repo)


@pytest.mark.parametrize("residual", [{}, [{}], [{"pid": "1"}], [{"command": "codex exec"}]])
def test_invalid_residual_processes_name_record_and_pid_independent_check(tmp_path: pathlib.Path, residual: object) -> None:
    """残存子孫の項目が不正なら、記録のパスと、PIDが無い項目でも実行できる確認手順を示して拒否する。"""
    repo = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    owner = ownership.try_acquire("example.test/team/repo", worktree=repo)
    assert owner is not None
    record = json.loads(owner.path.read_text(encoding="utf-8"))
    record["residual_processes"] = residual
    owner.path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError) as raised:
        ownership.try_acquire(owner.repo_id, worktree=repo)
    message = str(raised.value)
    assert str(owner.path) in message
    assert "PIDが無い項目" in message
    assert "ps -eo pid,lstart,args" in message
    assert "Get-CimInstance Win32_Process" in message


@pytest.mark.parametrize("completed", [False, True])
def test_transitions_preserve_data_and_reject_late_updates(tmp_path: pathlib.Path, completed: bool) -> None:
    """更新・保留・解放の全段階で準備情報を保ち、解放後の更新を拒否する。"""
    repo = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    owner = ownership.try_acquire("example.test/team/repo", worktree=repo, mode="coordinator")
    assert owner is not None
    owner = ownership.update_session(owner, "session")
    manifest = tmp_path / "manifest.json"
    ownership.update_prepared(owner, repo, manifest)
    candidates = ownership.save_candidates(owner, [{"filename": "work.md"}])
    process = session_registry.owner_process()
    assert process is not None
    ownership.update_process(owner, process)
    ownership.hold(owner, writers=["writer"])
    held = json.loads(owner.path.read_text(encoding="utf-8"))
    assert (held["completed"], held["released"], held["held"]) == (False, False, True)
    assert held["writer_sessions"] == ["writer"]
    ownership.release(owner, completed=completed)
    released = json.loads(owner.path.read_text(encoding="utf-8"))
    assert (released["completed"], released["released"], released["held"]) == (completed, True, False)
    assert released["manifest"] == str(manifest)
    assert released["executor_process"] == session_registry.process_payload(process)
    assert candidates.exists() is not completed
    with pytest.raises(ValueError, match="解放済み"):
        ownership.update_session(owner, "late-session")
    with pytest.raises(ValueError, match="解放済み"):
        ownership.hold(owner)
    assert json.loads(owner.path.read_text(encoding="utf-8")) == released
