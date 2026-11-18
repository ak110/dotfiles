"""停止taskの完了待ちを上限で打ち切り、取り消しを無視するtaskが後続の回収を妨げないようにする。"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

SHUTDOWN_TIMEOUT = 15.0


async def wait_for_shutdown(tasks: Iterable[asyncio.Task[Any]], *, timeout: float = SHUTDOWN_TIMEOUT) -> list[Any]:
    """取り消しを無視するtaskの完了待ちを上限で打ち切り、他の回収を継続する。"""
    tasks = tuple(tasks)
    if not tasks:
        return []
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    outcomes: list[Any] = []
    for task in tasks:
        if task in pending:
            task.cancel()
            task.add_done_callback(_consume_exception)
            outcomes.append(TimeoutError("停止taskが期限内に終了しませんでした"))
        elif task.cancelled():
            outcomes.append(asyncio.CancelledError())
        elif (error := task.exception()) is not None:
            outcomes.append(error)
        else:
            outcomes.append(task.result())
    return outcomes


def _consume_exception(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()
