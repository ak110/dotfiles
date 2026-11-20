"""WI登録と明示同期の要求がロック競合に遭った時の応答とサーバーログ。"""

import asyncio
import json
import os
import pathlib
import threading

import filelock
import pytest

from agent_toolkit._atk import git_sync
from agent_toolkit._atk.serve import config, state, wi_operations
from agent_toolkit._atk.serve.app import create_app
from agent_toolkit._atk.wi import sync as wi_sync
from agent_toolkit._testing.git_repository import init_repository
from agent_toolkit._testing.serve_app_support import _saved_files, _serve_app

_ADD = {"type": "awi", "messages": ["ロック競合の試験"], "target_repo": "github.com/example/test"}


@pytest.mark.asyncio
async def test_add_lock_conflict_can_be_retried(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notes = init_repository(tmp_path / "notes", commit_message="init")
    monkeypatch.setattr(wi_operations, "_WEB_LOCK_TIMEOUT", 0)
    client = _serve_app(notes).test_client()
    with git_sync.repo_lock(notes):
        response = await client.post("/api/entries", json=_ADD)
    assert response.status_code == 409
    body = await response.get_json()
    assert body["code"] == "lock_conflict"
    assert _saved_files(notes) == {}
    assert "再試行" in body["next_action"]

    retry = await client.post("/api/entries", json=_ADD)
    assert retry.status_code == 201
    [filename] = (await retry.get_json())["filenames"]
    assert list(_saved_files(notes)) == [filename]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/api/entries", "/api/sync"])
async def test_disconnected_worker_reports_lock_holder(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, route: str
) -> None:
    entered = threading.Event()
    proceed = threading.Event()

    def gated_lock(path: pathlib.Path, *, timeout: float) -> filelock.FileLock:
        entered.set()
        assert proceed.wait(5)
        return git_sync.repo_lock(path, timeout=timeout)

    monkeypatch.setattr(wi_sync, "repo_lock", gated_lock)
    monkeypatch.setattr(wi_operations, "_WEB_LOCK_TIMEOUT", 0)
    current_state = state.ServeState(tmp_path)
    app = create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), current_state, worker_limit=1)
    client = app.test_client()
    events: list[str] = []
    current_state.publish_listener = events.append
    with git_sync.repo_lock(tmp_path):
        try:
            request = client.request(route, method="POST", headers={"Content-Type": "application/json"})
            async with request as connection:  # ty: ignore[invalid-context-manager]
                await connection.send(json.dumps(_ADD if route == "/api/entries" else {}).encode())
                await connection.send_complete()
                assert await asyncio.to_thread(entered.wait, 5)
                await connection.disconnect()
        finally:
            proceed.set()
        # 同じ実行枠を使う次の要求が応答すれば、切断した要求の同期処理は終わっている。
        assert (await client.post("/api/entries", json=_ADD)).status_code == 409

    assert "Task exception was never retrieved" not in caplog.text
    assert f"PID {os.getpid()}" in caplog.text
    if route == "/api/sync":
        assert events == ["sync-error"]
    assert _saved_files(tmp_path) == {}
