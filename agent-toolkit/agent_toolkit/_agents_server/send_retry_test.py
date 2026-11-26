"""再送資格の消費を、送達の全入口に共通するUI境界で確かめる。 pyfltr: allow-comment"""

from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import send_retry
from agent_toolkit._common import function_hook_requests, send_misdetection
from agent_toolkit._common.next_action import ActionableError


@pytest.mark.asyncio
@pytest.mark.parametrize("check", sorted(send_misdetection.RETRY))
@pytest.mark.parametrize(("first_claimed", "second_claimed"), [(True, False), (False, True), (True, True), (False, False)])
async def test_one_ui_success_per_rejection(tmp_path, monkeypatch, check, first_claimed, second_claimed):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sid, body = "retry-case", "同じ本文"
    request = AsyncMock(return_value={"ok": True})
    assert send_misdetection.remember_rejection(sid, check, body, "original-call")

    async def deliver(claimed):
        return await send_retry.deliver(request, {"message": body}, sid, {check} if claimed else set(), body)

    request.side_effect = ActionableError("配置できなかった", next_action="原稿を保持する")
    request.side_effect.__cause__ = function_hook_requests.HookRequestRejected("配置不可")
    with pytest.raises(ActionableError):
        await deliver(first_claimed)
    request.side_effect = None
    _, sources = await deliver(first_claimed)
    assert sources == {check: "original-call"}
    with pytest.raises(ValueError, match="未送達記録を確認できない"):
        await deliver(second_claimed)
    assert request.await_count == 2
    assert send_misdetection.remember_rejection(sid, check, body, "new-call")
    _, sources = await deliver(second_claimed)
    assert sources == {check: "new-call"}
    assert request.await_count == 3
