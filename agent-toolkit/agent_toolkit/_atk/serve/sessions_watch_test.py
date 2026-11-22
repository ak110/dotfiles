# pylint: disable=protected-access

import asyncio
import json
import pathlib
import threading

import pytest
import quart

from agent_toolkit._atk.serve import (
    plans,
    runtime,
    session_watch,
    sessions,
    sessions_events,
    sessions_local,
    state,
    wi_operations,
)
from agent_toolkit._testing.serve_sessions_support import _context, _write


@pytest.mark.asyncio
@pytest.mark.parametrize("stopping", [False, True])
async def test_background_rescan_delivery_and_stop(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, stopping: bool
) -> None:
    context = _context(tmp_path)
    loop = asyncio.get_running_loop()
    app = quart.Quart(__name__)
    current = runtime.ServeRuntime(wi_operations.Operations(tmp_path), runtime.BoundedWorkers(1), state.ServeState(tmp_path))
    runtime.register_lifecycle(app, current, plans.create_context(root=tmp_path / "plans", hostname="local"), context)
    await app.startup()
    context.state.watch_loop = loop
    entered = asyncio.Event()
    release = threading.Event()
    calls: list[bool] = []

    def scan(*_args: object) -> list:
        calls.append(True)
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return []

    monkeypatch.setattr(sessions_local, "_scan_local_sessions", scan)
    queue = await sessions.subscribe(context.state)
    tracker = session_watch.RecordChangeTracker([], lambda *_args: None)
    sessions_local._start_background_rescan(context, tracker, changed=True)
    await asyncio.wait_for(entered.wait(), 5)
    thread = context.state.local_rescan_thread
    assert thread is not None
    task = None
    try:
        if stopping:
            task = asyncio.create_task(app.shutdown())
            while not context.state.local_rescan_stopping:
                await asyncio.sleep(0)
            sessions_local._start_background_rescan(context, tracker, changed=True)
            assert not task.done()
        release.set()
        if task is not None:
            await task
            assert queue.empty()
        else:
            assert json.loads(await asyncio.wait_for(queue.get(), 5)) == {"type": "refresh"}
            await app.shutdown()
    finally:
        release.set()
        await sessions.stop_local_watch_async(context)
    assert not thread.is_alive()
    assert context.state.local_rescan_thread is None
    assert calls == [True]


def test_closed_loop_does_not_create_delivery(tmp_path: pathlib.Path) -> None:
    context = _context(tmp_path)
    loop = asyncio.new_event_loop()
    loop.close()

    def unexpected():
        pytest.fail("予約拒否後にcoroutineを生成した")

    sessions_events.schedule_delivery(context.state, loop, unexpected)


@pytest.mark.asyncio
async def test_refresh_notification_is_delivered_to_subscribers(tmp_path: pathlib.Path) -> None:
    context = _context(tmp_path)
    queue = await sessions.subscribe(context.state)
    try:
        await sessions.deliver_refresh(context.state)
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=1)) == {"type": "refresh"}

        await sessions.deliver_refresh(context.state)
        await sessions.deliver_refresh(context.state)
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=1)) == {"type": "refresh"}
        assert queue.empty()
    finally:
        await sessions.unsubscribe(context.state, queue)
    assert context.state.subscribers == set()


@pytest.mark.asyncio
async def test_local_watch_notifies_new_records_and_appends(tmp_path: pathlib.Path) -> None:

    (tmp_path / "state").mkdir()
    context = _context(tmp_path, state_dir=tmp_path / "state")
    (tmp_path / "claude" / "projects").mkdir(parents=True)
    sessions.start_local_watch(context)
    queue = await sessions.subscribe(context.state)
    try:
        path = _write(tmp_path / "claude" / "projects" / "repo" / "new.jsonl", [{"type": "user", "message": {"content": "a"}}])
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=5)) == {"type": "refresh"}
        await asyncio.to_thread(sessions.list_local_sessions, context)
        while not queue.empty():
            queue.get_nowait()

        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "assistant", "message": {"content": "b"}}) + "\n")

        message = json.loads(await asyncio.wait_for(queue.get(), timeout=5))
        assert message == {"type": "record", "host": "local-host", "engine": "claude", "path": str(path)}
    finally:
        await sessions.unsubscribe(context.state, queue)
        sessions.stop_local_watch(context)
    assert context.state.record_watch is None
