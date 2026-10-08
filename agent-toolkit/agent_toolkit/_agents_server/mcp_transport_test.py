"""`_agents_server/mcp_transport.py`の振る舞いを検証する。"""

from __future__ import annotations

import contextlib

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import os
import pathlib
import shutil
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import anyio
import claude_agent_sdk
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCRequest, JSONRPCResponse

import agent_toolkit.agents_server_mcp as entry_script
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import (
    mcp_tools,
    mcp_transport,
    session_errors,
)
from agent_toolkit._common import state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX, ActionableError

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@contextlib.asynccontextmanager
async def _stdio_client(tmp_path: pathlib.Path) -> AsyncGenerator[ClientSession]:
    """pluginと同じlocked起動を、専用の状態領域へ分離する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[2]
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"AGENT_TOOLKIT_OWNER_SESSION", "CLAUDE_CODE_SESSION_ID", "VIRTUAL_ENV"}
    }
    env.update(XDG_STATE_HOME=str(tmp_path), LOCALAPPDATA=str(tmp_path))
    # 分離HOMEではmiseのshimが親のtrust状態を読めないため、uv本体を選ぶ。
    search_path = os.pathsep.join(directory for directory in os.get_exec_path() if pathlib.Path(directory).name != "shims")
    command = shutil.which("uv", path=search_path)
    assert command is not None
    params = StdioServerParameters(
        command=command,
        args=[
            "run",
            "--project",
            str(plugin_root),
            "--locked",
            "--no-default-groups",
            str(plugin_root / "agent_toolkit" / "agents_server_mcp.py"),
        ],
        env=env,
    )
    with anyio.fail_after(20):
        async with stdio_client(params) as streams:
            read, write = streams
            async with ClientSession(read, write) as client:
                initialized = await client.initialize()
                assert initialized.protocol_version == "2025-11-25"
                yield client


@pytest.mark.asyncio
async def test_stdio_initializes_and_lists_tools(tmp_path: pathlib.Path) -> None:
    """公開stdioで登録schemaを取得でき、終了時の診断とManager.closeが残る。"""
    async with _stdio_client(tmp_path) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == {"start", "send_message", "kill", "stop", "list", "show"}
        for tool in tools.values():
            assert tool.description
            assert tool.input_schema["type"] == "object"
            assert all(field.get("description") for field in tool.input_schema["properties"].values())
        assert tools["send_message"].input_schema["required"] == ["session_id", "prompt"]
    log = (tmp_path / "agent-toolkit" / "agents-server.log").read_text(encoding="utf-8")
    assert "MCP initializeを受信しました" in log
    assert "MCP initialize応答が完了しました" in log
    assert "manager closeが完了しました" in log


@pytest.mark.asyncio
async def test_stdio_tool_results(tmp_path: pathlib.Path) -> None:
    """SDK接続が構造化成功結果と、修正可能な不成立入力のエラーを区別する。"""
    async with _stdio_client(tmp_path) as client:
        result = await client.call_tool("list", {})
        assert not result.is_error
        assert result.structured_content is not None
        assert result.structured_content["sessions"] == []
        for name, arguments in [("show", {"session_id": "missing"}), ("start", {})]:
            result = await client.call_tool(name, arguments)
            assert result.is_error
            body = "\n".join(part.text for part in result.content if part.type == "text")
            assert NEXT_ACTION_PREFIX in body


@pytest.mark.asyncio
async def test_stdio_processing_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """処理例外も公開transportで理由と次の操作を持つエラーへ変わる。"""

    def fail_list(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("injected processing failure")

    monkeypatch.setattr(mcp_tools._MANAGER, "list_sessions", fail_list)
    client_write, server_read = anyio.create_memory_object_stream[SessionMessage | Exception](10)
    server_write, client_read = anyio.create_memory_object_stream[SessionMessage](10)

    @contextlib.asynccontextmanager
    async def transport() -> AsyncGenerator[Any]:
        async with server_read, server_write:
            yield server_read, server_write

    monkeypatch.setattr(mcp_transport, "stdio_server", transport)
    async with anyio.create_task_group() as group:
        group.start_soon(mcp_tools.mcp.run_stdio_async)
        async with client_read, client_write, ClientSession(client_read, client_write) as client:
            await client.initialize()
            result = await client.call_tool("list", {})
            assert result.is_error
            assert any(
                "injected processing failure" in part.text and NEXT_ACTION_PREFIX in part.text
                for part in result.content
                if part.type == "text"
            )


def test_main_persists_mcp_initialize_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """stdio起動でinitializeの受信、応答完了および失敗を永続化する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    # SDK 2.3.0のJSON-RPC型から、受信と送信の公式構造を生成する。
    requests = [SessionMessage(JSONRPCRequest(jsonrpc="2.0", method="initialize", id=i)) for i in range(1, 5)]

    async def send(message: SessionMessage) -> None:
        if getattr(message.message, "id", None) == 3:
            raise OSError("write failed")

    @contextlib.asynccontextmanager
    async def transport() -> AsyncGenerator[Any]:
        yield SimpleNamespace(receive=AsyncMock(side_effect=requests)), SimpleNamespace(send=send)

    async def run(_server: Any, read: Any, write: Any, _options: Any) -> None:
        for i in range(1, 5):
            await read.receive()
            if i == 4:
                return
            response = (
                JSONRPCError(jsonrpc="2.0", id=i, error=ErrorData(code=-32602, message="invalid initialize"))
                if i == 2
                else JSONRPCResponse(jsonrpc="2.0", id=i, result={})
            )
            with contextlib.suppress(OSError):
                await write.send(SessionMessage(response))

    monkeypatch.setattr(mcp_transport, "stdio_server", transport)
    monkeypatch.setattr(mcp_transport.Server, "run", run)

    assert entry_script.main([]) == 0

    content = (tmp_path / "agents-server.log").read_text(encoding="utf-8")
    assert "MCP initializeを受信しました: request_id=1" in content
    assert "MCP initialize応答が完了しました: request_id=1" in content
    assert "MCP initialize応答が失敗しました: request_id=2" in content
    assert "exception_type=ErrorData exception=invalid initialize" in content
    assert "MCP initialize応答の送信に失敗しました: request_id=3" in content
    assert "MCP initializeが未完了のままtransportが終了しました: request_id=4" in content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "operation"),
    [
        (ActionableError("理由の本文", next_action="`list`で保持状態を確かめる"), "`list`で保持状態を確かめる"),
        (session_errors.SessionInitializationTimeoutError("initialize timed out"), "atk agents logs"),
        (codex_backend.AppServerError("failed to start codex app-server"), "導入と認証"),
        (RuntimeError("unexpected failure"), "`show`"),
        (ValueError("unexpected value"), "受理形式"),
        (claude_agent_sdk.CLINotFoundError("Claude Code not found"), "導入と認証"),
    ],
    ids=["actionable", "initialization-timeout", "backend-start", "other", "plain-value-error", "claude-sdk-cli-missing"],
)
async def test_tool_error_body_carries_next_action(
    error: Exception,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCPツールのエラー本文は理由の後に`次の操作: `の行を持ち、失敗の種類に応じた操作を示す。

    MCPServerは例外の`str()`をエラー本文へ使うため、共通の例外型の次の操作も想定外の例外の案内も、
    登録の共通層が加えない限り委譲元へ届かない。
    """

    def raise_error(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise error

    async def keep_registry(_session_id: str) -> None:
        return None

    monkeypatch.setattr(
        mcp_tools, "_MANAGER", SimpleNamespace(take_over_orphaned_session=keep_registry, show_session=raise_error)
    )

    with pytest.raises(ToolError) as raised:
        await mcp_tools.mcp.call_tool("show", {"session_id": "3468feae-b2bf-4d67-ac55-3c40207e8b5b"})

    body = str(raised.value)
    reason, next_action = body.split(f"\n{NEXT_ACTION_PREFIX}", 1)
    assert reason.endswith(str(error))
    assert operation in next_action
