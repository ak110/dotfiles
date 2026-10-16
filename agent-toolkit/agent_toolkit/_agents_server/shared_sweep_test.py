"""解放要求の一時記録が既存session掃引へ統合されることを確かめる。"""

import os
import pathlib
import time

from agent_toolkit._agents_server import shared_sweep
from agent_toolkit._common import session_registry


def test_orphaned_release_files_follow_existing_session_sweep(tmp_path: pathlib.Path) -> None:
    """異常終了で残った要求・応答を既存session掃引が回収し、現行登録を保持する。"""
    session_registry.publish("active", terminal=False, cwd=str(tmp_path), state_root=tmp_path)
    directory = session_registry.registry_directory(tmp_path)
    now = time.time()
    records = [directory / "orphan.release-request", directory / "orphan.release-response"]
    for path in records:
        path.write_text("{}\n", encoding="utf-8")
        old = now - 8 * 24 * 60 * 60
        os.utime(path, (old, old))
    shared_sweep.sweep_stale_shared_state(keep_root_session_id=None, state_root=tmp_path, now=now)
    assert all(not path.exists() for path in records)
    assert session_registry.resolve("active", state_root=tmp_path).state is session_registry.Resolution.RUNNING
