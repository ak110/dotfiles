"""未送達記録を予約し、表示の成否に応じて使い切る。 pyfltr: allow-comment"""

from collections.abc import Awaitable, Callable
from typing import Any

from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._common import function_hook_requests, send_misdetection
from agent_toolkit._common.next_action import ActionableError


async def deliver(
    request: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]],
    inputs: dict[str, Any],
    session_id: str | None,
    retry: set[str],
    message: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    if not retry:
        return await request("send_to_user", inputs), {}
    sources = await run_file_operation(send_misdetection.take_rejections, session_id or "", retry, message)
    if not session_id or retry - sources.keys():
        raise ValueError("同じ本文と判定の未送達記録を確認できない。")
    try:
        return await request("send_to_user", inputs), sources
    except ActionableError as error:
        if isinstance(error.__cause__, function_hook_requests.HookRequestRejected):
            await run_file_operation(send_misdetection.restore_rejections, session_id, message, sources)
        raise
