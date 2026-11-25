import concurrent.futures
import datetime
import queue
import subprocess
import threading

import pytest

from agent_toolkit._atk.wi import background_sync, sync
from agent_toolkit._atk.wi.mutations import transitions
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_entry_files import write_awi


@pytest.mark.parametrize(("during_fetch", "during_push"), [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize("peer_after_fetch", [False, True])
def test_worker_pushes_locally_committed_changes_without_holding_git_lock_during_network(
    tmp_path, monkeypatch, during_fetch, during_push, peer_after_fetch
):
    remote = tmp_path / "remote.git"
    notes = tmp_path / "notes"
    monkeypatch.setattr(background_sync.state_paths, "state_dir", lambda: tmp_path / "state")

    def git(path, *args):
        return subprocess.run(["git", *args], cwd=path, text=True, capture_output=True, check=True).stdout.strip()

    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "clone", str(remote), str(notes))
    git(notes, "config", "user.name", "WI Sync Test")
    git(notes, "config", "user.email", "wi@example.invalid")
    git(notes, "commit", "--allow-empty", "-m", "initialize")
    git(notes, "push", "-u", "origin", "HEAD")
    git(notes, "commit", "--allow-empty", "-m", "pending")
    peer = tmp_path / "peer"
    if peer_after_fetch:
        git(tmp_path, "clone", str(remote), str(peer))
        git(peer, "config", "user.name", "WI Sync Test")
        git(peer, "config", "user.email", "wi@example.invalid")
        (peer / "other.md").write_text("別のcloneの更新\n", encoding="utf-8")
        git(peer, "add", "other.md")
        git(peer, "commit", "-m", "peer")

    waiting = queue.Queue()
    release = {"fetch": threading.Event(), "push": threading.Event()}
    git_run = background_sync.git.run

    def delayed(args, *positional, **kwargs):
        operation = args[0]
        if operation in release and not release[operation].is_set():
            waiting.put(operation)
            assert release[operation].wait(5)
        return git_run(args, *positional, **kwargs)

    monkeypatch.setattr(background_sync.git, "run", delayed)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(background_sync.run, notes)
        try:
            for operation, add in (("fetch", during_fetch), ("push", during_push)):
                assert waiting.get(timeout=5) == operation
                with sync.repo_lock(notes, timeout=0):
                    if add:
                        git(notes, "commit", "--allow-empty", "-m", f"local during {operation}")
                if peer_after_fetch and operation == "push":
                    git(peer, "push")
                release[operation].set()
        finally:
            for event in release.values():
                event.set()
        assert worker.result(timeout=5) is True

    assert git(notes, "rev-parse", "HEAD") == git(remote, "rev-parse", "HEAD")
    assert background_sync.read_status(notes)["state"] == "synced"
    subjects = git(remote, "log", "--format=%s").splitlines()
    assert "pending" in subjects
    assert ("local during fetch" in subjects) is during_fetch
    assert ("local during push" in subjects) is during_push
    if peer_after_fetch:
        assert (notes / "other.md").read_text(encoding="utf-8") == "別のcloneの更新\n"


def test_default_read_schedules_and_explicit_modes_do_not(tmp_path, monkeypatch):
    requests = []
    monkeypatch.setattr(background_sync.sync, "has_remote", lambda _notes: True)
    monkeypatch.setattr(background_sync, "read_status", lambda _notes: {})
    monkeypatch.setattr(background_sync, "schedule", requests.append)
    background_sync.prepare_read(tmp_path, skip=True, force=False)
    assert requests == []
    background_sync.prepare_read(tmp_path, skip=False, force=False)
    assert requests == [tmp_path]


def test_same_start_claim_returns_success_to_only_one_clone(tmp_path, monkeypatch):
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    left = git_repository.init_repository(tmp_path / "left", initial_branch="main", origin=str(remote), files={".gitkeep": ""})
    filename = "20261125-090001-001.md"
    write_awi(left, filename)
    git_repository.commit_all(left, "inbox")
    git_repository.run_git(left, "push", "-u", "origin", "main")
    right = tmp_path / "right"
    git_repository.run_git(tmp_path, "clone", "--branch", "main", str(remote), str(right))
    for notes in (left, right):
        git_repository.run_git(notes, "config", "user.name", "WI Claim Test")
        git_repository.run_git(notes, "config", "user.email", "wi-claim@example.invalid")
    joined = threading.Barrier(2, timeout=5)
    push = sync._push_claim

    def concurrent_push(notes):
        joined.wait()
        return push(notes)

    monkeypatch.setattr(sync, "_push_claim", concurrent_push)

    def claim(notes):
        try:
            return transitions.transition_entries(
                notes,
                action="start-processing",
                filenames=[filename],
                now=datetime.datetime(2026, 11, 25, tzinfo=datetime.UTC),
            ) == [filename]
        except subprocess.CalledProcessError:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (left, right)))
    assert sorted(results) == [False, True]
