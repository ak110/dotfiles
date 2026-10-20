"""共有fake backend自身の文脈寿命と背景処理の回収を検証する。"""

import asyncio

import pytest

from agent_toolkit._testing.agents_server_support import FakeBackend


@pytest.mark.parametrize("exit_kind", ["success", "exception", "cancel"])
@pytest.mark.asyncio
async def test_fake_backend_context_reclaims_pending_tasks(exit_kind: str) -> None:
    """正常・例外・取消の全経路で、文脈が所有する背景処理を終了させる。"""
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def background() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    backend = FakeBackend({}, "codex")

    async def run() -> None:
        async with backend as entered:
            assert entered is backend
            backend.pending.append(asyncio.create_task(background()))
            await started.wait()
            if exit_kind == "exception":
                raise RuntimeError("本文失敗")
            if exit_kind == "cancel":
                raise asyncio.CancelledError

    if exit_kind == "success":
        await run()
    else:
        error = RuntimeError if exit_kind == "exception" else asyncio.CancelledError
        with pytest.raises(error):
            await run()
    assert stopped.is_set()
    assert all(task.done() for task in backend.pending)
