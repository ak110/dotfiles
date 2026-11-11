# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""保留中の追送と待機対象の維持を検証する。"""

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access

import asyncio
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._common import session_registry
from agent_toolkit._testing.agents_server_resume_support import (
    _RESUME_WAIT_TIMEOUT,
    AssistantMessage,
    ControlledClaudeClient,
    ResultMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    UserMessage,
    _await_state,
    _emit_child_start,
    _manager,
    _start,
    _wait_with_timeout,
)
from agent_toolkit._testing.agents_server_resume_support import _isolate_session_registry as _isolate_session_registry
from agent_toolkit._testing.helpers import delivery_payload


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
async def test_send_message_preserves_background_and_mixed_waits(
    mixed: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保留中の追送で背景taskを維持し、古い待機表明を結果として渡さない。"""
    client = ControlledClaudeClient("claude-send")
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        if mixed:
            session_registry.publish("mixed-child", state=session_registry.PublicationState.PENDING_RUNNING)
            _emit_child_start(client, "mixed-child")
        client.emit(TaskStartedMessage("task-1"))
        client.emit(ResultMessage("初回結果"))
        await _await_state(lambda: session.awaiting_auto_resume)

        response = await manager.send_message(session.session_id, "続行", timeout=1)

        assert response["delivery"] == "reply_started"
        assert "previous_result" not in response
        assert session.status == "running"
        assert set(session.live_tasks) == {"task-1"}
        assert [delivery_payload(value) for value in client.queries] == ["調査", "続行"]

        client.emit(ResultMessage("追送後も待機"))
        await _await_state(lambda: session.awaiting_auto_resume)
        client.emit(TaskUpdatedMessage("task-1", "completed"))
        if mixed:
            session_registry.publish("mixed-child", state=session_registry.PublicationState.RESULT_COMPLETED)
        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        if mixed:
            await _await_state(lambda: len(client.queries) == 3)
        client.emit(ResultMessage("reply結果", origin={"kind": "task-notification"}))
        result = await wait_task
        assert result["agent_message"] == "reply結果"
        assert "error" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("completion_order", ["before", "during", "after"])
async def test_send_message_wait_survives_failure_and_completion_order(
    completion_order: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """失敗した追送を再試行しても子と背景wait出力を保持し、到着順によらず完了を一度だけ届ける。"""
    client = ControlledClaudeClient("claude-send-retry")
    async with _manager(client, monkeypatch) as (manager, _backend):
        child = "send-retry-child"
        output = tmp_path / "wait-output.txt"
        output.write_text("", encoding="utf-8")
        session = await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(child, state=session_registry.PublicationState.PENDING_RUNNING)
        _emit_child_start(client, child)
        client.emit(AssistantMessage([SimpleNamespace(id="wait", name="Bash", input={"command": "atk agents wait"})]))
        client.emit(
            UserMessage(
                [
                    SimpleNamespace(
                        tool_use_id="wait",
                        content=f"Command running in background with ID: wait-job. Output is being written to: {output}.",
                    )
                ]
            )
        )
        client.emit(ResultMessage("待機中"))
        await _await_state(lambda: session.awaiting_auto_resume)
        original_query = client.query
        monkeypatch.setattr(client, "query", AsyncMock(side_effect=RuntimeError("配送拒否")))
        failed = await manager.send_message(session.session_id, "追加指示", timeout=1)
        assert failed["delivery"] == "reply_failed"
        assert "previous_result" not in failed
        assert session.awaiting_auto_resume and not session.result_available
        assert session.live_child_session_ids == {child}
        assert session.agents_wait_background_outputs == {str(output)}

        async def query(prompt: str) -> None:
            if completion_order == "during":
                session_registry.publish(child, state=session_registry.PublicationState.RESULT_COMPLETED)
            await original_query(prompt)

        monkeypatch.setattr(client, "query", query)
        if completion_order == "before":
            session_registry.publish(child, state=session_registry.PublicationState.RESULT_COMPLETED)
        for _ in range(2):
            response = await manager.send_message(session.session_id, "追加指示", timeout=1)
            assert "previous_result" not in response
            assert session.live_child_session_ids == {child}
            assert session.agents_wait_background_outputs == {str(output)}
            client.emit(ResultMessage("追送後の待機"))
            await _await_state(lambda: session.awaiting_auto_resume)
        if completion_order == "after":
            session_registry.publish(child, state=session_registry.PublicationState.RESULT_COMPLETED)
        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        await _await_state(lambda: len(client.queries) == 4)
        client.emit(ResultMessage("子の結果を確認した"))
        result = await wait_task
        assert result["agent_message"] == "子の結果を確認した"
        assert "error" not in result
        assert len(client.queries) == 4
        assert "agent_message" not in await _wait_with_timeout(manager, 0)
        assert session.result_delivered
