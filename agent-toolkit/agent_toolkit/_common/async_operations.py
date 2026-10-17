"""同期操作をworkerで完了させ、状態の反映とキャンセルの通知をイベントループで行う。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


async def run_sync_operation[T](
    operation: Callable[..., T],
    *args: Any,
    on_success: Callable[[T], None] | None = None,
    **kwargs: Any,
) -> T:
    """同期操作の終了を待ち、workerを残さず呼出元へ戻る。"""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    return await finish_task(task, on_success=on_success)


async def finish_task[T](task: asyncio.Task[T], *, on_success: Callable[[T], None] | None = None) -> T:
    """taskの終了と成功時の反映を済ませ、呼出元へキャンセルを伝える。"""
    cancelled = False
    try:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        result = task.result()
    except Exception:
        if cancelled:
            raise asyncio.CancelledError from None
        raise
    if on_success is not None:
        on_success(result)
    if cancelled:
        raise asyncio.CancelledError
    return result
