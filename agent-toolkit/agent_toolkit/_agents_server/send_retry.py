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
    try:
        sources = await run_file_operation(send_misdetection.take_rejections, session_id or "", retry, message)
    except send_misdetection.RejectionStateError as error:
        raise ActionableError(
            str(error), next_action="状態の保存先を確認し、元の本文は再掲せず未送達と確認できた場合だけ再試行する"
        ) from error
    if sources is None:
        raise ValueError("同じ本文と判定の未送達記録を確認できない。")
    try:
        result = await request("send_to_user", inputs)
    except ActionableError as error:
        if sources and session_id and isinstance(error.__cause__, function_hook_requests.HookRequestRejected):
            await run_file_operation(send_misdetection.finish_rejection, session_id, message, failed=True)
        raise
    if sources and session_id:
        await run_file_operation(send_misdetection.finish_rejection, session_id, message, failed=False)
    return result, sources
