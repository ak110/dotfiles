"""`_agents_server/tool_descriptions.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import (
    mcp_tools,
    tool_names,
)
from agent_toolkit._testing.agents_server_support import (
    _start_tool,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def test_start_parameter_descriptions_are_self_contained() -> None:
    """各引数の説明は外部の説明を参照せず、`mode`の説明は公開する全modeを挙げる。"""
    properties = _start_tool().parameters["properties"]
    for name, schema in properties.items():
        description = schema.get("description", "")
        assert "instructions" not in description, name
        assert "共通引数" not in description, name
        assert "agent-toolkit:delegation" not in description, name
    mode = properties["mode"]["description"]
    for value in tool_names.START_MODES:
        assert f"`{value}`" in mode, value


def test_start_description_selects_mode_and_lists_minimal_calls() -> None:
    """`start`の説明と`mode`の引数説明が、公開する全modeの最小呼び出し例を持つ。"""
    tool = _start_tool()
    description = tool.description + tool.parameters["properties"]["mode"]["description"]
    for mode in tool_names.START_MODES:
        assert f"- {mode}: `{{" in description, mode


@pytest.mark.asyncio
async def test_tool_descriptions_fit_claude_code_truncation_and_describe_every_argument() -> None:
    """Claude Codeが設定を変えない状態で切り詰める2,048文字に説明を収め、全引数へ説明を付けて単体で呼び出せるようにする。

    出典はClaude Code公式資料「Connect Claude Code to tools via MCP」の「For MCP server authors」。
    """
    assert len(mcp_tools.mcp.instructions or "") <= 2048
    for tool in await mcp_tools.mcp.list_tools():
        assert len(tool.description or "") <= 2048, tool.name
        for name, schema in tool.inputSchema.get("properties", {}).items():
            assert schema.get("description"), f"{tool.name}.{name}"


def test_instructions_keep_server_overview_without_argument_specification() -> None:
    """instructionsは撤去したmodeの名前を案内しない。"""
    instructions = mcp_tools.mcp.instructions or ""
    for legacy in tool_names.LEGACY_START_MODES:
        assert legacy not in instructions, legacy


def test_public_timeout_schemas_expose_unified_defaults() -> None:
    """公開schemaの待機系操作の説明が、timeoutを省略した場合の実装の上限秒数を示す。"""
    for tool_name, default in (
        ("send_message", server_manager.DEFAULT_SEND_MESSAGE_TIMEOUT),
        ("kill", server_manager.DEFAULT_KILL_TIMEOUT),
    ):
        tool = mcp_tools.mcp._tool_manager.get_tool(tool_name)
        assert tool is not None
        assert default.is_integer(), tool_name
        seconds = f"{int(default)}秒"
        assert seconds in tool.description, tool_name
        assert seconds in tool.parameters["properties"]["timeout"]["description"], tool_name


@pytest.mark.asyncio
async def test_tool_input_schema_is_unchanged_by_error_wrapping() -> None:
    """例外を包む共通層を通しても、ツールの引数と説明文はツール関数の定義から生成される。"""
    tools = {tool.name: tool for tool in await mcp_tools.mcp.list_tools()}

    assert set(tools["send_message"].inputSchema["properties"]) == {"session_id", "prompt", "timeout"}
    assert tools["send_message"].inputSchema["required"] == ["session_id", "prompt"]
    assert "reply_failed" in (tools["send_message"].description or "")
