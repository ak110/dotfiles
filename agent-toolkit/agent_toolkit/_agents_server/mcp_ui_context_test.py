"""共有MCPのUI認可がプロセス環境ではなく要求元に従うことを検証する。"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from agent_toolkit._agents_server import mcp_tools
from agent_toolkit._common import function_hook_requests, runtime_environment

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_ui_authorization_uses_bound_caller(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """前置拒否と共通UI境界の両方で、逆のglobal値による権限漏れを防ぐ。"""
    request = AsyncMock(side_effect=lambda *_args: {"status": "confirmed"})
    monkeypatch.setattr(function_hook_requests, "request", request)
    monkeypatch.setattr(mcp_tools, "_MANAGER", SimpleNamespace(launcher_session_id=lambda: "launcher-main"))
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", None)

    for delegated in (True, False):
        # global判定への退行は、拒否側でも許可側でも失敗として観測する。
        monkeypatch.setattr(mcp_tools, "_UI_DELEGATED", not delegated)
        environment = {"AGENT_TOOLKIT_OWNER_SESSION": "owner"} if delegated else {}
        caller = runtime_environment.CallerContext(environment, tmp_path)
        with runtime_environment.bind(caller):
            for name, arguments in (
                ("send_to_user", {"message": "確認が完了した。"}),
                ("compact_conversation", {}),
            ):
                request.reset_mock()
                if delegated:
                    with pytest.raises(ToolError, match="メイン専用ツール"):
                        await mcp_tools.mcp.call_tool(name, arguments)
                    request.assert_not_awaited()
                else:
                    response = await mcp_tools.mcp.call_tool(name, arguments)
                    request.assert_awaited_once_with("launcher-main", name, arguments)
                    expected = {"status": "confirmed"}
                    if name == "send_to_user":
                        expected["guidance"] = ""
                    assert isinstance(response, CallToolResult)
                    assert response.structured_content == expected
