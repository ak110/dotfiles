"""同期操作の回収と、成功の反映を行うthreadを検証する。"""

import asyncio
import threading

import pytest

from agent_toolkit._common.async_operations import run_sync_operation


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.asyncio
async def test_worker_finishes_before_cancellation_and_reflection_stays_on_loop(cancel: bool) -> None:
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    owner_thread = threading.get_ident()
    reflected: list[tuple[int, int]] = []

    def operation() -> int:
        assert threading.get_ident() != owner_thread
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        return 7

    def reflect(result: int) -> None:
        reflected.append((result, threading.get_ident()))

    task = asyncio.create_task(run_sync_operation(operation, on_success=reflect))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if cancel:
            task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task == 7
    assert reflected == [(7, owner_thread)]
