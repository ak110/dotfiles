"""Codex App Server MCPの公開契約と状態集約を検証する。"""

# テストでは内部状態集約も直接検証する。
# pylint: disable=protected-access

import asyncio
import pathlib
from typing import Any, cast

import codex_app_server_mcp as subject
import pytest


class FakeClient:
    """App Server要求を記録する偽クライアント。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self._closed = False

    @property
    def closed(self) -> bool:
        """テスト用の接続状態を返す。"""
        return self._closed

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.requests.append((method, params or {}))
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "thread/resume":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            return {"turn": {"id": f"turn-{len(self.requests)}"}}
        return self.responses.pop(0) if self.responses else {}

    async def _send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def send(self, message: dict[str, Any]) -> None:
        """テスト用のJSON-RPC応答を記録する。"""
        await self._send(message)


def test_tools_are_exactly_the_five_async_operations() -> None:
    assert set(subject.mcp._tool_manager._tools) == {  # noqa: SLF001  # pylint: disable=protected-access
        "codex_start",
        "codex_status",
        "codex_wait",
        "codex_result",
        "codex_start_reply",
    }
    start_schema = subject.mcp._tool_manager._tools["codex_start"].parameters  # noqa: SLF001
    assert start_schema["required"] == ["prompt", "cwd"]
    wait_schema = subject.mcp._tool_manager._tools["codex_wait"].parameters  # noqa: SLF001
    assert wait_schema["properties"]["timeout"]["default"] == subject.DEFAULT_WAIT_TIMEOUT


def test_validation_rejects_invalid_cwd_and_partial_model_effort(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        subject._validate_cwd("relative")  # noqa: SLF001
    with pytest.raises(ValueError, match="existing directory"):
        subject._validate_cwd(str(tmp_path / "missing"))  # noqa: SLF001
    with pytest.raises(ValueError, match="together"):
        subject._validate_model_effort("model", None)  # noqa: SLF001


@pytest.mark.asyncio
async def test_start_passes_fixed_noninteractive_policy_and_returns_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    manager = subject.AppServerManager()
    client = FakeClient()

    async def ensure_client() -> FakeClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    response = await manager.start("調査", str(tmp_path), "gpt-test", "high")

    assert response["session_id"] == "thread-1"
    assert response["status"] == "running"
    assert [method for method, _ in client.requests] == ["thread/start", "turn/start"]
    thread_params = client.requests[0][1]
    assert thread_params["approvalPolicy"] == "never"
    assert thread_params["sandbox"] == "danger-full-access"
    turn_params = client.requests[1][1]
    assert turn_params["sandboxPolicy"] == {"type": "dangerFullAccess"}
    assert turn_params["model"] == "gpt-test"
    assert turn_params["effort"] == "high"


@pytest.mark.asyncio
async def test_notifications_complete_turn_and_result_then_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    manager = subject.AppServerManager()
    client = FakeClient()

    async def ensure_client() -> FakeClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "item/started",
            "params": {"threadId": "thread-1", "turnId": "turn-2", "item": {"type": "commandExecution"}},
        }
    )
    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-2",
                "item": {"type": "agentMessage", "text": "最終結果"},
            },
        }
    )
    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-2", "status": "completed", "error": None},
            },
        }
    )
    assert manager.status("thread-1")["status"] == "completed"
    result = manager.result("thread-1")
    assert result["agent_message"] == "最終結果"
    await manager.start_reply("thread-1", "続行")
    assert [method for method, _ in client.requests][-2:] == ["thread/resume", "turn/start"]
    assert client.requests[-2][1]["approvalPolicy"] == "never"


@pytest.mark.asyncio
async def test_all_server_requests_are_replied_and_noninteractive_requests_fail(
    tmp_path: pathlib.Path,
) -> None:
    manager = subject.AppServerManager()
    client = FakeClient()
    manager.client = cast(subject.JsonRpcProcess, client)
    manager.sessions["thread-1"] = subject.SessionState("thread-1", str(tmp_path), turn_id="turn-1")

    await manager._handle_server_request(  # noqa: SLF001
        {"id": "elicitation-1", "method": "mcpServer/elicitation/request", "params": {"threadId": "thread-1"}}
    )
    assert client.sent[-1] == {"id": "elicitation-1", "result": {"action": "cancel", "content": None, "_meta": None}}
    assert manager.status("thread-1")["status"] == "running"

    await manager._handle_server_request(  # noqa: SLF001
        {"id": 2, "method": "item/tool/requestUserInput", "params": {"threadId": "thread-1"}}
    )
    assert client.sent[-1]["id"] == 2
    assert "error" in client.sent[-1]
    assert manager.status("thread-1")["status"] == "failed"
    await asyncio.sleep(0)
    assert client.requests[-1][0] == "turn/interrupt"


@pytest.mark.asyncio
async def test_unknown_request_without_identifier_fails_all_active_sessions(tmp_path: pathlib.Path) -> None:
    manager = subject.AppServerManager()
    client = FakeClient()
    manager.client = cast(subject.JsonRpcProcess, client)
    manager.sessions["thread-1"] = subject.SessionState("thread-1", str(tmp_path), turn_id="turn-1")
    manager.sessions["thread-2"] = subject.SessionState("thread-2", str(tmp_path), turn_id="turn-2")

    await manager._fail_for_request({}, "unknown/server/request")  # noqa: SLF001

    assert {manager.status("thread-1")["status"], manager.status("thread-2")["status"]} == {"failed"}


@pytest.mark.asyncio
async def test_wait_timeout_returns_current_state_without_failing(tmp_path: pathlib.Path) -> None:
    manager = subject.AppServerManager()
    manager.sessions["thread-1"] = subject.SessionState("thread-1", str(tmp_path), turn_id="turn-1")
    result = await manager.wait("thread-1", timeout=0)
    assert result["status"] == "running"
    with pytest.raises(ValueError, match="non-negative"):
        await manager.wait("thread-1", timeout=-1)
