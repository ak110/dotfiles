"""loopの状態を渡さず、ファイル操作の終了をキャンセル時にも回収する。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


async def run_file_operation[T](
    operation: Callable[..., T],
    *args: Any,
    on_success: Callable[[T], None] | None = None,
    **kwargs: Any,
) -> T:
    """ファイル操作だけをworkerで実行し、workerを残さず呼出元へ戻る。"""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    return await finish_task(task, on_success=on_success)


async def finish_task[T](task: asyncio.Task[T], *, on_success: Callable[[T], None] | None = None) -> T:
    """taskの終了と成功反映を完了してから、呼出元のキャンセルを伝える。"""
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
