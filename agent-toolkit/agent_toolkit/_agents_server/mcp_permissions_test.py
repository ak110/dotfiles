from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agent_toolkit._agents_server import mcp_tools
from agent_toolkit._atk.wi import operation_permissions


@pytest.mark.asyncio
async def test_adopt_rejects_unowned_source_before_storage(monkeypatch):
    manager = SimpleNamespace(
        launcher_session_id=lambda: "parent",
        list_sessions=AsyncMock(return_value={"sessions": [{"session_id": "child"}]}),
    )
    adopted = []
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    monkeypatch.setattr(operation_permissions, "adopt_request", lambda *args: adopted.append(args))
    with pytest.raises(ToolError, match="所有する子"):
        await mcp_tools.mcp.call_tool("permissions", {"action": "adopt", "request_id": "request", "source_session": "outsider"})
    assert not adopted


@pytest.mark.asyncio
async def test_public_schema_rejects_approval_write_action():
    with pytest.raises(ToolError):
        await mcp_tools.mcp.call_tool("permissions", {"action": "approve", "request_id": "request"})
