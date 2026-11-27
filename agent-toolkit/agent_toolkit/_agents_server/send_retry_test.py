"""再送資格の消費を、送達の全入口に共通するUI境界で確かめる。 pyfltr: allow-comment"""

from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import send_retry
from agent_toolkit._common import function_hook_requests, send_misdetection, session_state
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


@pytest.mark.asyncio
async def test_initial_delivery_does_not_need_state_write(tmp_path, monkeypatch):

    state_parent = tmp_path / "not-directory"
    state_parent.write_text("検証用", encoding="utf-8")
    monkeypatch.setattr(session_state, "state_path", lambda _sid: state_parent / "state.json")
    body = "用語集を更新した。"
    request = AsyncMock(return_value={"ok": True})
    result, sources = await send_retry.deliver(request, {"message": body}, "initial-case", set(), body)
    assert result == {"ok": True} and not sources
    request.assert_awaited_once_with("send_to_user", {"message": body})


@pytest.mark.asyncio
async def test_independent_delivery_after_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    body = "確認した。"
    request = AsyncMock(side_effect=[TimeoutError("表示結果不明"), {"ok": True}])
    with pytest.raises(TimeoutError):
        await send_retry.deliver(request, {"message": body}, "ordinary-case", set(), body)
    result, sources = await send_retry.deliver(request, {"message": body}, "ordinary-case", set(), body)
    assert result == {"ok": True} and not sources
    assert request.await_count == 2


@pytest.mark.asyncio
async def test_rejected_delivery_with_unknown_result_is_not_replayed(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sid, body = "known-case", "同じ本文"
    assert send_misdetection.remember_rejection(sid, "response-language", body, "call")
    unknown = ActionableError("表示結果不明", next_action="表示の有無を先に確かめる")
    unknown.__cause__ = TimeoutError("modから応答なし")
    request = AsyncMock(side_effect=unknown)
    with pytest.raises(ActionableError):
        await send_retry.deliver(request, {"message": body}, sid, set(), body)
    with pytest.raises(ValueError, match="未送達記録を確認できない"):
        await send_retry.deliver(request, {"message": body}, sid, {"response-language"}, body)
    request.assert_awaited_once()


@pytest.mark.asyncio
async def test_known_rejection_cannot_be_retried_without_state_write(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    sid, body = "write-failure-case", "同じ本文"
    assert send_misdetection.remember_rejection(sid, "response-language", body, "call")
    monkeypatch.setattr(session_state, "update_state", lambda *_args: False)
    request = AsyncMock()
    with pytest.raises(ActionableError, match="未送達記録を更新できない"):
        await send_retry.deliver(request, {"message": body}, sid, set(), body)
    request.assert_not_awaited()
