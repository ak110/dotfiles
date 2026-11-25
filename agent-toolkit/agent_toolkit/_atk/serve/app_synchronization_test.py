# pylint: disable=protected-access

import asyncio
import os
import pathlib
import subprocess

import pytest

from agent_toolkit._atk.serve import state, wi_operations
from agent_toolkit._atk.wi import background_sync
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing.serve_support import _BlockingSync, _sync_app


@pytest.mark.asyncio
async def test_sse_heartbeat(tmp_path: pathlib.Path) -> None:
    events = state.ServeState(tmp_path).events(heartbeat=0.001)
    assert await anext(events) == "event: subscribed\ndata: {}\n\n"
    assert await anext(events) == "event: heartbeat\ndata: {}\n\n"
    await events.aclose()


@pytest.mark.asyncio
async def test_concurrent_sync_requests_share_one_pull(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同時同期要求は未送信commitの反映とpullを1回だけ実行する。"""
    synchronize = _BlockingSync()
    monkeypatch.setattr(_wi_sync, "synchronize", synchronize)
    app = _sync_app(tmp_path, wi_operations.Operations(tmp_path))
    tasks = [asyncio.create_task(app.test_client().post("/api/sync")) for _ in range(4)]
    await asyncio.to_thread(synchronize.started.wait)
    await asyncio.sleep(0)
    synchronize.release.set()
    responses = await asyncio.gather(*tasks)
    assert synchronize.calls == 1
    assert [await response.get_json() for response in responses] == [{"synced": True}] * 4


@pytest.mark.asyncio
async def test_sync_api_git_failure_returns_check_and_retry_action(tmp_path: pathlib.Path) -> None:
    """原因を判別できないGitコマンドの失敗は、理由を保ち、保存先の確認と再試行の操作を別項目で返す。

    保存先は空白を含むパスとし、案内するコマンドが保存先を引数1個として渡せる引用を持つことも確かめる。
    """
    root = tmp_path / "notes with space"
    root.mkdir()

    class _FailingSyncOperations(wi_operations.Operations):
        def sync(self) -> bool:
            raise subprocess.CalledProcessError(1, ["git", "pull"])

    response = await _sync_app(root, _FailingSyncOperations(root)).test_client().post("/api/sync", json={})

    assert response.status_code == 500
    body = await response.get_json()
    assert set(body) == {"error", "next_action"}
    assert body["error"] == "Git同期に失敗しました"
    assert body["next_action"] == wi_operations.git_failure_next_action(root)
    quoted_root = subprocess.list2cmdline([str(root.resolve())]) if os.name == "nt" else f"'{root.resolve()}'"
    assert f"`git -C {quoted_root} status`" in body["next_action"]
    assert "「今すぐ同期」で再試行する" in body["next_action"]
    assert "dotfilesの作者へ報告する" in body["next_action"]


@pytest.mark.parametrize("result", [True, None])
def test_background_sync_uses_shared_worker(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, result: bool | None
) -> None:
    calls: list[pathlib.Path] = []

    def run(path: pathlib.Path) -> bool | None:
        calls.append(path)
        return result

    monkeypatch.setattr(background_sync, "run", run)
    assert wi_operations.Operations(tmp_path).background_sync() is result
    assert calls == [tmp_path]
