import pathlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_toolkit._atk import git_sync as _atk_git_sync


@pytest.mark.parametrize("previous_use", ["timeout", "released", "timeout-in-other-thread"])
def test_force_release_does_not_clear_another_lock(tmp_path: pathlib.Path, previous_use: str) -> None:
    other = _atk_git_sync.repo_lock(tmp_path, timeout=0)
    if previous_use == "released":
        with other:
            _atk_git_sync.assert_repo_lock_held(tmp_path)
        with pytest.raises(RuntimeError):
            _atk_git_sync.assert_repo_lock_held(tmp_path)

    lock = _atk_git_sync.repo_lock(tmp_path)
    with lock:
        if previous_use == "timeout":
            with pytest.raises(_atk_git_sync.RepoLockTimeoutError):
                other.acquire()
        if previous_use == "timeout-in-other-thread":
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(other.acquire)
                with pytest.raises(_atk_git_sync.RepoLockTimeoutError):
                    pending.result(timeout=5)
        with lock:
            other.release(force=True)
            _atk_git_sync.assert_repo_lock_held(tmp_path)
        _atk_git_sync.assert_repo_lock_held(tmp_path)

        lock.acquire()
        lock.release(force=True)
        assert not lock.is_locked
        with pytest.raises(RuntimeError):
            _atk_git_sync.assert_repo_lock_held(tmp_path)
