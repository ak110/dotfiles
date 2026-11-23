from __future__ import annotations

from typing import Any

import pytest

from agent_toolkit._agents_server import mcp_tools
from agent_toolkit._testing.agents_server_tool_contract import EXPECTED_TOOL_SCHEMAS

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def _without_prose(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _without_prose(item) for key, item in value.items() if key not in ("description", "title")}
    if isinstance(value, list):
        return [_without_prose(item) for item in value]
    return value


@pytest.mark.asyncio
async def test_tool_schemas_keep_names_types_defaults_and_return_shapes() -> None:
    published = {
        tool.name: {
            "input_schema": _without_prose(tool.input_schema),
            "output_schema": tool.output_schema,
            "annotations": tool.annotations.model_dump(exclude_none=True) if tool.annotations else None,
        }
        for tool in await mcp_tools.mcp.list_tools()
    }

    assert published == EXPECTED_TOOL_SCHEMAS
