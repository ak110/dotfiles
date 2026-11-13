"""標準ロックの競合で失敗したgitの再実行の試験。"""

import subprocess
from pathlib import Path

import pytest
from agent_toolkit._git import stale_lock

from pytools._internal import git_lock_recovery


def _lock_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "file.txt").write_text("content\n", encoding="utf-8")
    monkeypatch.setattr(stale_lock.psutil, "process_iter", lambda *_args, **_kwargs: iter([]))
    monkeypatch.setattr(stale_lock, "POLL_INITIAL_SECONDS", 0.02)
    monkeypatch.setattr(stale_lock, "POLL_MAX_SECONDS", 0.05)
    monkeypatch.setattr(stale_lock, "DEFAULT_WAIT_SECONDS", 0.5)
    return repo


def test_run_git_with_lock_recovery_reclaims_orphan_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """所有者のいない`index.lock`で失敗したgitを、回収後に1回だけ再実行して成功させる。"""
    repo = _lock_repo(tmp_path, monkeypatch)
    lock = repo / ".git" / "index.lock"
    lock.write_bytes(b"")

    result = git_lock_recovery.run_git_with_lock_recovery(["git", "-C", str(repo), "add", "file.txt"], repo=repo)

    assert result is not None
    assert result.returncode == 0, result.stderr
    assert not lock.exists()


def test_run_git_with_lock_recovery_keeps_lock_of_live_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """生存中の所有者のロックは回収せず、元のエラーに判定結果を添えて返す。"""
    repo = _lock_repo(tmp_path, monkeypatch)
    lock = repo / ".git" / "index.lock"
    lock.write_bytes(b"")
    stale_lock.write_record(lock.parent, lock.with_name("index"), lock.stat())

    result = git_lock_recovery.run_git_with_lock_recovery(["git", "-C", str(repo), "add", "file.txt"], repo=repo)

    assert result is not None
    assert result.returncode != 0
    assert "回収しませんでした" in result.stderr
    assert lock.exists()
