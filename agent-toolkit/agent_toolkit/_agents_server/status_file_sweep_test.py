"""agents_server共有状態の期限掃引テスト。"""

from __future__ import annotations

import os
import pathlib

import pytest

from agent_toolkit._agents_server import status_file


def _aged_file(path: pathlib.Path, timestamp: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    os.utime(path, (timestamp, timestamp))


def test_sweep_stale_shared_state_removes_expired_and_keeps_current(tmp_path: pathlib.Path) -> None:
    """root・登録簿・計測記録を回収し、現行rootと新しい状態を残す。"""
    now = 1_000_000.0
    old = now - status_file.STALE_SHARED_STATE_SECONDS - 1
    current = tmp_path / "agents-server" / "current"
    stale = tmp_path / "agents-server" / "stale"
    fresh = tmp_path / "agents-server" / "fresh"
    _aged_file(current / "root.json", old)
    _aged_file(stale / "results" / "child.json", old)
    _aged_file(fresh / "root.json", now)
    _aged_file(tmp_path / "agents-server" / "sessions" / "old.json", old)
    _aged_file(tmp_path / "agents-server" / "sessions" / "new.json", now)
    record = tmp_path / "agents-server" / "compaction" / "thread.jsonl"
    _aged_file(record, old)
    _aged_file(record.with_name("thread.jsonl.lock"), old)
    orphan_lock = tmp_path / "agents-server" / "compaction" / "orphan.jsonl.lock"
    _aged_file(orphan_lock, old)

    status_file.sweep_stale_shared_state(
        keep_root_session_id="current",
        state_root=tmp_path,
        now=now,
    )

    assert current.is_dir()
    assert not stale.exists()
    assert fresh.is_dir()
    assert not (tmp_path / "agents-server" / "sessions" / "old.json").exists()
    assert (tmp_path / "agents-server" / "sessions" / "new.json").is_file()
    assert not record.exists()
    assert not record.with_name("thread.jsonl.lock").exists()
    assert not orphan_lock.exists()


def test_sweep_stale_shared_state_continues_after_root_removal_failure(
    tmp_path: pathlib.Path,
    monkeypatch,
) -> None:
    """root削除の個別失敗が登録簿の掃引を妨げない。"""
    now = 1_000_000.0
    old = now - status_file.STALE_SHARED_STATE_SECONDS - 1
    _aged_file(tmp_path / "agents-server" / "stale" / "root.json", old)
    registry = tmp_path / "agents-server" / "sessions" / "old.json"
    _aged_file(registry, old)
    monkeypatch.setattr(status_file.shutil, "rmtree", lambda _path: (_ for _ in ()).throw(OSError("busy")))

    status_file.sweep_stale_shared_state(keep_root_session_id=None, state_root=tmp_path, now=now)

    assert not registry.exists()


@pytest.mark.asyncio
async def test_status_writer_activate_continues_when_sweep_fails(tmp_path: pathlib.Path, monkeypatch) -> None:
    """manager起動経路は掃引APIのOSErrorを記録して書込を開始する。"""
    monkeypatch.setattr(status_file, "sweep_stale_shared_state", lambda **_kwargs: (_ for _ in ()).throw(OSError("x")))
    writer = status_file.StatusFileWriter(
        {},
        status_file.StatusFileIdentity("current", "root.json", None),
        state_root=tmp_path,
    )

    writer.activate()

    assert writer.path.is_file()
    writer.deactivate()
