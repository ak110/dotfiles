"""監視開始に失敗したserveの後始末を検証する。"""

import asyncio
import contextlib
import queue
import threading
from typing import Any

import pytest

from pytools.media_remote import _window_move


@pytest.mark.asyncio
async def test_async_monitor_preparation_and_join_keep_loop_running() -> None:
    """UIAの開始と終了を待つ間もloopが進み、終了後に専用threadを残さない。"""
    loop = asyncio.get_running_loop()
    ready, finishing = asyncio.Event(), asyncio.Event()
    allow_start, allow_stop, hooks_stopped = threading.Event(), threading.Event(), threading.Event()

    @contextlib.contextmanager
    def uia() -> Any:
        loop.call_soon_threadsafe(ready.set)
        assert allow_start.wait(5)
        try:
            yield None
        finally:
            loop.call_soon_threadsafe(finishing.set)
            assert allow_stop.wait(5)

    class API:
        def physical_coordinates(self) -> Any:
            return contextlib.nullcontext()

        def run_hooks(self, mouse: Any, window: Any, key: Any, prepared: Any) -> None:
            del mouse, window, key
            prepared()
            assert hooks_stopped.wait(5)

        def stop_hooks(self) -> None:
            hooks_stopped.set()

    monitor = _window_move.WindowMover(api=API(), uia_factory=uia)
    entered_context, leave_context = asyncio.Event(), asyncio.Event()

    async def lifecycle() -> None:
        async with monitor:
            entered_context.set()
            await leave_context.wait()

    task = asyncio.create_task(lifecycle())
    try:
        await asyncio.wait_for(ready.wait(), 5)
        await asyncio.sleep(0)
        assert not entered_context.is_set()
        allow_start.set()
        await asyncio.wait_for(entered_context.wait(), 5)
        leave_context.set()
        await asyncio.wait_for(finishing.wait(), 5)
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        allow_start.set()
        leave_context.set()
        allow_stop.set()
        await task
    assert monitor.worker is not None and not monitor.worker.is_alive()
    assert monitor.hooks is not None and not monitor.hooks.is_alive()


@pytest.mark.parametrize("fail_worker", [False, True])
def test_monitor_start_failure_releases_worker_and_selection(fail_worker: bool) -> None:
    closed = threading.Event()
    events: queue.Queue[Any] = queue.Queue()

    @contextlib.contextmanager
    def uia() -> Any:
        try:
            if fail_worker:
                raise OSError("UIA initialization failed")
            yield None
        finally:
            closed.set()

    class FailedAPI:
        def physical_coordinates(self) -> Any:
            return contextlib.nullcontext()

        def run_hooks(self, *args: Any) -> None:
            del args
            raise OSError("hook registration failed")

        def stop_hooks(self) -> None:
            pass

    monitor = _window_move.WindowMover(api=FailedAPI(), uia_factory=uia, events=events)
    with pytest.raises(OSError), monitor:
        pytest.fail("開始に失敗した監視をserveへ渡した")
    assert closed.is_set()
    assert not monitor.on_mouse(0x207, (1, 2), 1, "")
    assert not any(thread.name.startswith("media-window-") for thread in threading.enumerate())
