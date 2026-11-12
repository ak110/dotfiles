"""`_agents_server/tool_descriptions.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import json
import pathlib
import re
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import mcp_tools, state
from agent_toolkit._testing.agents_server_support import (
    _manager_with_fake,
    _observed_input_params,
    _recording_candidates,
    _start_tool,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_start_description_selects_mode_and_lists_minimal_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """公開説明の最小例はschemaと起動条件を通り、記載されたmodeでsessionを生成する。"""
    tool = _start_tool()
    description = tool.description + tool.parameters["properties"]["mode"]["description"]
    examples = dict(re.findall(r"^- ([^:]+): `(\{[^`]+\})`", description, re.MULTILINE))
    assert set(examples) == {"通常task", "軽量task", "delegate", "explore", "write", "shell"}
    contract = tmp_path / "work-contract.md"
    contract.write_text("# 全体作業契約\n", encoding="utf-8")
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        for name, example in examples.items():
            arguments = json.loads(example.replace("{...}", "{}"))
            arguments["cwd"] = str(tmp_path)
            if "whole_contract_path" in arguments:
                arguments["whole_contract_path"] = str(contract)
            if name.endswith("task"):
                role = "exec" if name == "通常task" else "external-write-review"
                arguments["subagent_md_path"] = role
                arguments["extra_params"] = _observed_input_params(f"{role}.subagent.md", tmp_path)
            response = await mcp_tools.mcp.call_tool("start", arguments)
            assert isinstance(response, CallToolResult)
            assert response.structured_content is not None
            shown = await manager.show_session(response.structured_content["session_id"])
            assert shown["launch_kind"] == ({"通常task": "delegate", "軽量task": "explore"}.get(name, name))


@pytest.mark.asyncio
async def test_tool_descriptions_fit_claude_code_truncation_and_describe_every_argument() -> None:
    """Claude Codeが初期送信で切り詰める4,096文字に説明を収め、全引数へ説明を付けて単体で呼び出せるようにする。

    初期送信の上限内なら、ツール検索で後から読む説明の上限16,384文字も満たす。
    出典はClaude Codeのv2.1.295（検索後16,384文字）とv2.1.296（初期送信とinstructions4,096文字）のリリースノート。
    """
    assert len(mcp_tools.mcp.instructions or "") <= 4096
    for tool in await mcp_tools.mcp.list_tools():
        assert len(tool.description or "") <= 4096, tool.name
        for name, schema in tool.input_schema.get("properties", {}).items():
            assert schema.get("description"), f"{tool.name}.{name}"


@pytest.mark.asyncio
async def test_tool_input_schema_is_unchanged_by_error_wrapping() -> None:
    """例外を包む共通層を通しても、ツールの引数と説明文はツール関数の定義から生成される。"""
    tools = {tool.name: tool for tool in await mcp_tools.mcp.list_tools()}

    assert set(tools["send_message"].input_schema["properties"]) == {
        "session_id",
        "prompt",
        "timeout",
        "instruction",
        "extra_params",
    }
    assert tools["send_message"].input_schema["required"] == ["session_id"]
    assert "reply_failed" in (tools["send_message"].description or "")


@pytest.mark.asyncio
async def test_tool_list_publishes_title_and_side_effect_annotations() -> None:
    """`tools/list`の応答が全ツールの表示名と、副作用の性質を示す注釈をホストへ渡す。

    期待値はMCP仕様2025-11-25の`ToolAnnotations`の定義と各ツールの副作用から決めた。
    Claude Code 2.1.312は`readOnlyHint`が真のツールを、実行済みの可能性がある接続断の後にも再試行する。
    読み取り専用でないツールで`destructiveHint`を省略すると仕様上は真として扱うため、追加だけを行うツールには偽を明示する。
    """
    expected = {
        "start": (None, False),
        "send_message": (None, False),
        "kill": (None, True),
        "stop": (None, True),
        "list": (True, None),
        "show": (True, None),
    }
    tools = {tool.name: tool for tool in await mcp_tools.mcp.list_tools()}

    assert set(tools) == set(expected)
    for name, (read_only, destructive) in expected.items():
        tool = tools[name]
        assert tool.title, name
        assert tool.annotations is not None, name
        assert tool.annotations.read_only_hint is read_only, name
        assert tool.annotations.destructive_hint is destructive, name


@pytest.mark.parametrize("operation", ["send_message", "kill"])
@pytest.mark.asyncio
async def test_public_timeout_description_matches_effective_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, operation: str
) -> None:
    """公開操作の省略時に実装が使う待機予算が、schemaと説明の秒数に一致する。"""
    tools = {tool.name: tool for tool in await mcp_tools.mcp.list_tools()}
    tool = tools[operation]
    timeout_schema = tool.input_schema["properties"]["timeout"]
    observed: list[float] = []
    real_wait_for = asyncio.wait_for
    real_timeout = asyncio.timeout

    def observe_timeout(seconds: float) -> asyncio.Timeout:
        observed.append(seconds)
        return real_timeout(0.001)

    async def observe_wait_for(awaitable: Any, timeout: float | None, **_kwargs: Any) -> Any:
        assert timeout is not None
        observed.append(timeout)
        return await real_wait_for(awaitable, timeout=0.001)

    # 期限の減算だけを固定し、実際のロック待ちと取消・公開エラーへの変換は動かす。
    proxy = SimpleNamespace(
        **{
            **vars(asyncio),
            "timeout": observe_timeout,
            "wait_for": observe_wait_for,
            "get_running_loop": lambda: SimpleNamespace(time=lambda: 0.0),
        }
    )
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        session = state.SessionState("timeout-contract", str(tmp_path), engine="codex", turn_id="turn")
        manager.sessions[session.session_id] = session
        async with session.turn_control_lock:
            with monkeypatch.context() as scoped:
                scoped.setattr(server_manager, "asyncio", proxy)
                with pytest.raises(ToolError, match="timed out"):
                    await mcp_tools.mcp.call_tool(
                        operation,
                        {"session_id": session.session_id, **({"prompt": "追加指示"} if operation == "send_message" else {})},
                    )
        assert backend.send_calls == backend.interrupt_calls == 0
        assert session.session_id in manager.sessions

    assert observed == [timeout_schema["default"]]
    for description in (tool.description or "", timeout_schema["description"]):
        seconds = {float(value) for value in re.findall(r"(\d+(?:\.\d+)?)秒", description)}
        assert observed[0] in seconds, (operation, description, observed)
