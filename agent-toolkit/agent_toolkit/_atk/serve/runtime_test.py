"""serveの各監視の開始・join待機と、同じloopの独立taskの進行を検証する。"""

import asyncio
import pathlib
import threading
from typing import Any

import pytest
import watchdog.observers.api

from agent_toolkit._atk.serve import plans, runtime, sessions, state, wi_operations


@pytest.mark.asyncio
async def test_failed_sync_retries_and_clears_error_without_remote_changes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """明示同期の通信失敗を自動で再試行し、差分のない成功でも表示を復旧する。"""
    operations = wi_operations.Operations(tmp_path)
    calls: list[str] = []
    recovered = asyncio.Event()
    notifications: list[str] = []

    def fail() -> bool:
        raise OSError("一時的な通信失敗")

    def retry() -> bool:
        calls.append("retry")
        if len(calls) == 1:
            raise OSError("一時的な通信失敗")
        return False

    def publish(event: str) -> None:
        notifications.append(event)
        if event == "sync-ok":
            recovered.set()

    current_state = state.ServeState(tmp_path)
    monkeypatch.setattr(current_state, "publish", publish)
    monkeypatch.setattr(operations, "sync", fail)
    monkeypatch.setattr(operations, "background_sync", retry)
    monkeypatch.setattr(runtime, "_SYNC_RETRY_INTERVAL_SECONDS", 0.001)
    current = runtime.ServeRuntime(operations, runtime.BoundedWorkers(1), current_state)
    with pytest.raises(OSError, match="通信失敗"):
        await current.synchronize()
    task = asyncio.create_task(current.background_sync_loop())
    try:
        await asyncio.wait_for(recovered.wait(), timeout=5)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert calls == ["retry", "retry"]
    assert notifications == ["sync-error", "sync-error", "sync-ok"]


@pytest.mark.parametrize("owner", ["wi", "plans", "sessions"])
@pytest.mark.parametrize("stage", ["start", "stop"])
@pytest.mark.asyncio
async def test_observer_lifecycle_waits_do_not_stall_event_loop(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, owner: str, stage: str
) -> None:
    root = tmp_path / "plans"
    root.mkdir()
    wi = state.ServeState(tmp_path)
    plan_context = plans.create_context(root=root, hostname="local")
    session_context = sessions.create_context(hostname="local", claude_home=tmp_path / "claude", codex_home=tmp_path / "codex")
    for path in (session_context.claude_home / "projects", session_context.codex_home / "sessions"):
        path.mkdir(parents=True)

    async def start() -> None:
        if owner == "wi":
            await wi.start_async(asyncio.get_running_loop())
        elif owner == "plans":
            await plans.start_local_watchers_async(plan_context)
        else:
            await sessions.start_local_watch_async(session_context)

    async def stop() -> None:
        if owner == "wi":
            await wi.stop_async()
        elif owner == "plans":
            await plans.stop_local_watchers_async(plan_context)
        else:
            await sessions.stop_local_watch_async(session_context)

    if stage == "stop":
        await start()
    entered, release = asyncio.Event(), threading.Event()
    loop, loop_thread = asyncio.get_running_loop(), threading.get_ident()
    observer_class = watchdog.observers.api.BaseObserver
    method = "start" if stage == "start" else "join"
    original = getattr(observer_class, method)

    def block(observer: Any, *args: Any, **kwargs: Any) -> Any:
        assert threading.get_ident() != loop_thread
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return original(observer, *args, **kwargs)

    monkeypatch.setattr(observer_class, method, block)
    task = asyncio.create_task(start() if stage == "start" else stop())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        await task
        await stop()
    if owner == "wi":
        assert not wi.observer.is_alive()
    elif owner == "plans":
        assert plan_context.state.local_observer is None
    else:
        assert session_context.state.record_watch is None
