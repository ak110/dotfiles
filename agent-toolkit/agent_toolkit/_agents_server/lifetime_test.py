import asyncio
import contextlib

import pytest

from agent_toolkit._agents_server import lifetime


@pytest.mark.asyncio
async def test_shutdown_wait_is_bounded_when_task_ignores_cancellation() -> None:
    """taskの取消し受理を完了待機の前提にせず、後続の所有資源回収へ進む。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def unresponsive() -> None:
        started.set()
        while not release.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await release.wait()

    task = asyncio.create_task(unresponsive())
    try:
        await started.wait()
        [result] = await asyncio.wait_for(lifetime.wait_for_shutdown((task,), timeout=0.01), 1)
        assert isinstance(result, TimeoutError)
        assert not task.done()
    finally:
        release.set()
        await task
