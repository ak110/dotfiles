"""共有MCPのUI認可がプロセス環境ではなく要求元に従うことを検証する。"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolRequestParams, CallToolResult

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


@pytest.mark.asyncio
@pytest.mark.parametrize("meta", [{"claudecode/toolUseId": "toolu_01"}, None])
async def test_send_to_user_carries_tool_use_id_from_request_meta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, meta: dict[str, str] | None
) -> None:
    """MCP要求の`_meta`にある呼出しIDを表示要求へ添え、無い場合は本文だけを渡す。"""
    request = AsyncMock(side_effect=lambda *_args: {"status": "confirmed"})
    monkeypatch.setattr(function_hook_requests, "request", request)
    monkeypatch.setattr(mcp_tools, "_MANAGER", SimpleNamespace(launcher_session_id=lambda: "launcher-main"))
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", None)
    monkeypatch.setattr(mcp_tools, "_UI_DELEGATED", False)
    params = CallToolRequestParams.model_validate(
        {"name": "send_to_user", "arguments": {"message": "確認が完了した。"}, **({"_meta": meta} if meta else {})}
    )
    with runtime_environment.bind(runtime_environment.CallerContext({}, tmp_path)):
        async with mcp_tools._request_scope(params):  # pylint: disable=protected-access
            await mcp_tools.mcp.call_tool("send_to_user", {"message": "確認が完了した。"})
    expected = {"message": "確認が完了した。", **({"tool_use_id": "toolu_01"} if meta else {})}
    request.assert_awaited_once_with("launcher-main", "send_to_user", expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [True, False], ids=["shared", "stdio"])
@pytest.mark.parametrize(
    ("message", "consulted"),
    [
        ("気付いた改善点: 対象。AWI化ステータス: 不要 原因不明", True),
        ("作業中に自分の手順漏れを見つけて直した。", False),
        ("確認が完了した。", False),
    ],
)
async def test_send_to_user_consults_report_guidance_for_improvement_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, message: str, consulted: bool, shared: bool
) -> None:
    """改善点の行を含む報告だけ送達前の案内を照会し、結果を`guidance`で返す。

    逸脱の報告に欠けた記録の行は、送達後に`hooks/send_to_user.tsx`が案内する。
    """
    request = AsyncMock(side_effect=lambda *_args: {"status": "confirmed"})
    monkeypatch.setattr(function_hook_requests, "request", request)
    monkeypatch.setattr(mcp_tools, "_MANAGER", SimpleNamespace(launcher_session_id=lambda: "launcher-main"))
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", None)
    monkeypatch.setattr(mcp_tools, "_UI_DELEGATED", False)
    monkeypatch.chdir(tmp_path)
    caller_cwd = tmp_path / "caller"
    caller_cwd.mkdir()
    payloads: list[dict] = []
    run_hook = subprocess.run

    def run(argv: list[str], **kwargs: str) -> subprocess.CompletedProcess[str]:
        payload = json.loads(kwargs["input"])
        payloads.append(payload)
        if not pathlib.Path(payload.get("cwd", "")).is_absolute():
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="cwdがありません")
        root = pathlib.Path(argv[1]).parents[1]
        return run_hook(
            [argv[0], str(root / argv[2]), *argv[3:]],
            cwd=payload["cwd"],
            env={**os.environ, "PYTHONPATH": str(root)},
            input=kwargs["input"],
            capture_output=True,
            encoding="utf-8",
            check=False,
            timeout=10,
        )

    monkeypatch.setattr(mcp_tools.subprocess, "run", run)
    caller = runtime_environment.bind(runtime_environment.CallerContext({}, caller_cwd))
    with caller if shared else contextlib.nullcontext():
        response = await mcp_tools.mcp.call_tool("send_to_user", {"message": message})
    expected = (
        [{"session_id": "launcher-main", "last_assistant_message": message, "cwd": str(caller_cwd if shared else tmp_path)}]
        if consulted
        else []
    )
    assert payloads == expected
    assert isinstance(response, CallToolResult)
    assert response.structured_content is not None
    assert response.structured_content["status"] == "confirmed"
    guidance = response.structured_content["guidance"]
    if consulted:
        assert "改善点報告の区分名が、報告行の書式と一致しない。" in guidance
        assert "報告の案内を取得できなかった" not in guidance
    else:
        assert guidance == ""
    request.assert_awaited_once_with("launcher-main", "send_to_user", {"message": message})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment", "expected", "absent"),
    [
        ({}, ("`/mcp`", "`/reload-plugins`"), "codex resume"),
        ({"CODEX_THREAD_ID": "019a0000-0000-7000-8000-000000000001"}, ("`codex resume`",), "/reload-plugins"),
    ],
    ids=["claude", "codex"],
)
async def test_runtime_changes_without_conversation_mod_returns_host_recovery(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    expected: tuple[str, ...],
    absent: str,
) -> None:
    """会話内modが応答しないホストでは、予約していないことと、ホストに応じた再接続の操作を返す。"""
    from agent_toolkit._agents_server.manager import AgentsServerManager  # pylint: disable=import-outside-toplevel

    manager = AgentsServerManager(None, launcher_environment=environment)
    if not environment:
        monkeypatch.setattr(manager, "launcher_session_id", lambda: "launcher-main")
    timeout = TimeoutError("会話内modの確認応答が期限内にない。表示・予約は成功として扱わない。")
    monkeypatch.setattr(function_hook_requests, "request", AsyncMock(side_effect=timeout))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", None)
    monkeypatch.setattr(mcp_tools, "_UI_DELEGATED", False)
    with pytest.raises(ToolError) as raised:
        await mcp_tools.mcp.call_tool("apply_runtime_changes", {"targets": ["agents_server", "plugins"]})
    message = str(raised.value)
    assert "成功として扱わない" in message and "予約" in message
    assert all(text in message for text in expected) and absent not in message
    assert "`list`か`show`" not in message
