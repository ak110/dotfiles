from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolRequestParams, CallToolResult

from agent_toolkit._agents_server import mcp_tools, service_context
from agent_toolkit._common import function_hook_requests, runtime_environment, send_misdetection, session_record_paths

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
                    assert isinstance(response, CallToolResult)
                    assert response.structured_content == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("meta", [{"claudecode/toolUseId": "toolu_01"}, None])
async def test_send_to_user_carries_tool_use_id_from_request_meta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, meta: dict[str, str] | None
) -> None:
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
    # 呼出しを識別できない要求では、案内を送信結果へ残す（識別できる場合はPostToolUseの添付で届ける）。 pyfltr: allow-comment
    if consulted:
        guidance = response.structured_content["guidance"]
        assert "改善点報告の区分名が、報告行の書式と一致しない。" in guidance
        assert "報告の案内を取得できなかった" not in guidance
    else:
        assert "guidance" not in response.structured_content
    request.assert_awaited_once_with("launcher-main", "send_to_user", {"message": message})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "inputs"),
    [
        ("apply_runtime_changes", {"targets": ["agents_server", "plugins"]}),
        ("compact_conversation", {}),
        ("send_to_user", {"message": "本文"}),
    ],
)
@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("bound", [False, True], ids=["stdio", "shared"])
async def test_ui_timeout_returns_caller_host_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    operation: str,
    inputs: dict[str, object],
    host: str,
    bound: bool,
) -> None:
    from agent_toolkit._agents_server.manager import AgentsServerManager  # pylint: disable=import-outside-toplevel

    codex_id = "019a0000-0000-7000-8000-000000000001"
    claude_id = "launcher-main"
    identifier = claude_id if host == "claude" else codex_id
    environment = {"CODEX_THREAD_ID": codex_id, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    manager = AgentsServerManager(None, launcher_environment=environment)
    if host == "claude" or bound:
        environment["CLAUDE_CODE_SESSION_ID"] = claude_id
    if host == "claude":
        monkeypatch.setattr(manager, "launcher_session_id", lambda: identifier)
    timeout = TimeoutError("会話内modの確認応答が期限内にない。表示・予約は成功として扱わない。")
    request = AsyncMock(side_effect=timeout)
    monkeypatch.setattr(function_hook_requests, "request", request)
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", None)
    monkeypatch.setattr(mcp_tools, "_UI_DELEGATED", False)
    caller = runtime_environment.CallerContext(environment, tmp_path)

    @contextlib.asynccontextmanager
    async def no_lifespan(*_args, **_kwargs):
        yield

    async with contextlib.AsyncExitStack() as stack:
        if bound:
            pool = service_context.ServiceManagers(state_root=tmp_path)
            monkeypatch.setattr(service_context, "_manager_lifespan", no_lifespan)
            monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", pool)
            stack.enter_context(runtime_environment.bind(caller))
            metadata = {"_meta": {"threadId": codex_id}} if host == "codex" else {}
            params = CallToolRequestParams.model_validate({"name": operation, "arguments": inputs, **metadata})
            await stack.enter_async_context(mcp_tools._request_scope(params))  # pylint: disable=protected-access
        with pytest.raises(ToolError) as raised:
            await mcp_tools.mcp.call_tool(operation, inputs)
    request.assert_awaited_once_with(identifier, operation, inputs)
    message = str(raised.value)
    assert "成功として扱わない" in message
    assert ("Codexには会話内modが無く" in message) == (host == "codex")
    assert mcp_tools._UI_UNAVAILABLE_NEXT_ACTIONS[operation][host] in message  # pylint: disable=protected-access


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "inputs", "expected"),
    [
        ("send_to_user", {"message": "本文"}, "同じ本文を最終応答に書く"),
        ("apply_runtime_changes", {"targets": ["agents_server"]}, "現在のターンを終え、先行の予約の結果の通知を待つ"),
    ],
)
async def test_conversation_mod_rejection_returns_operation_recovery(
    monkeypatch: pytest.MonkeyPatch, operation: str, inputs: dict[str, object], expected: str
) -> None:
    from agent_toolkit._agents_server.manager import AgentsServerManager  # pylint: disable=import-outside-toplevel

    manager = AgentsServerManager(None, launcher_environment={})
    monkeypatch.setattr(manager, "launcher_session_id", lambda: "launcher-main")
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", None)
    monkeypatch.setattr(mcp_tools, "_UI_DELEGATED", False)
    monkeypatch.setattr(
        function_hook_requests, "request", AsyncMock(side_effect=function_hook_requests.HookRequestRejected("modの拒否理由"))
    )
    with pytest.raises(ToolError) as raised:
        await mcp_tools.mcp.call_tool(operation, inputs)
    message = str(raised.value)
    assert "modの拒否理由" in message and expected in message
    assert "引数の受理形式" not in message


@pytest.mark.parametrize("case", ("format", "language", "unwritable", "unavailable", "delegated"))
@pytest.mark.asyncio
async def test_send_rejection_preserves_body_only_when_resend_can_recover(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, case: str
) -> None:
    from agent_toolkit._atk import managed_temp  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(mcp_tools, "_MANAGER", SimpleNamespace(launcher_session_id=lambda: "launcher-main"))
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", None)
    monkeypatch.setattr(mcp_tools, "_UI_DELEGATED", case == "delegated")

    def resolve(session_id: str) -> pathlib.Path:
        assert session_id == "launcher-main"
        if case == "unwritable":
            raise OSError("保存先が使えない")
        return tmp_path

    def create(prefix: str, root: pathlib.Path) -> pathlib.Path:
        assert root == tmp_path
        area = root / prefix
        area.mkdir()
        return area

    monkeypatch.setattr(managed_temp, "resolve_session_temp", resolve)
    monkeypatch.setattr(managed_temp, "create_session_temp", create)
    reason = "再発防止の行が無い"
    mod = AsyncMock(side_effect=function_hook_requests.HookRequestRejected(reason, resend=case != "unavailable"))
    monkeypatch.setattr(function_hook_requests, "request", mod)
    body = "The description must be provided in Japanese." if case == "language" else "期待: 期待した結果\r\n末尾。"
    args = {"message": body, "kind": "completion"}
    params = CallToolRequestParams.model_validate(
        {"name": "send_to_user", "arguments": args, "_meta": {"claudecode/toolUseId": "repl_rejected"}}
    )
    with pytest.raises(ToolError) as raised:
        async with mcp_tools._request_scope(params):  # pylint: disable=protected-access
            await mcp_tools.mcp.call_tool("send_to_user", args)
    rejection = str(raised.value)
    if case in {"format", "language"}:
        saved = re.search(r"本文の保存先: `([^`]+)`", rejection)
        assert saved is not None
        assert pathlib.Path(saved[1]).read_bytes() == body.encode("utf-8")
        assert rejection.index("`Read`") < rejection.index("`edit()`") < rejection.index(".text()")
        assert 'kind: "completion"' in rejection
        assert "最終応答に書く" not in rejection
    else:
        assert not list(tmp_path.iterdir())
    if case == "language":
        assert "日本語で書き直す" in rejection
    elif case != "delegated":
        assert reason in rejection
    if case == "unwritable":
        assert "本文を書き出せなかった" in rejection
    if case == "unavailable":
        assert "最終応答に書く" in rejection
    assert mod.await_count == (0 if case in {"language", "delegated"} else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("stashed", [True, False], ids=["stashed", "stash-failed"])
async def test_send_to_user_keeps_warnings_in_result_when_stash_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, *, stashed: bool
) -> None:
    """預けられた警告は結果から除き、預けられなかった警告は結果に残す（警告を消さない）。 pyfltr: allow-comment"""
    warning = '<atk-auto source="send-to-user" kind="warn">\n本文は表示済みである。\n</atk-auto>'
    request = AsyncMock(side_effect=lambda *_args: {"status": "confirmed", "warnings": [warning]})
    monkeypatch.setattr(function_hook_requests, "request", request)
    monkeypatch.setattr(mcp_tools, "_MANAGER", SimpleNamespace(launcher_session_id=lambda: "launcher-main"))
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", None)
    monkeypatch.setattr(mcp_tools, "_UI_DELEGATED", False)
    calls: list[tuple[str, str, list[str]]] = []

    def stash(session_id: str, tool_use_id: str, warnings: list[str]) -> bool:
        calls.append((session_id, tool_use_id, warnings))
        return stashed

    monkeypatch.setattr(mcp_tools.sent_warnings, "stash", stash)
    params = CallToolRequestParams.model_validate(
        {"name": "send_to_user", "arguments": {"message": "確認が完了した。"}, "_meta": {"claudecode/toolUseId": "toolu_01"}}
    )
    with runtime_environment.bind(runtime_environment.CallerContext({}, tmp_path)):
        async with mcp_tools._request_scope(params):  # pylint: disable=protected-access
            response = await mcp_tools.mcp.call_tool("send_to_user", {"message": "確認が完了した。"})
    assert calls == [("launcher-main", "toolu_01", [warning])]
    assert isinstance(response, CallToolResult)
    assert response.structured_content is not None
    assert "warnings" not in response.structured_content
    if stashed:
        assert "guidance" not in response.structured_content
    else:
        assert warning in response.structured_content["guidance"]


@pytest.mark.asyncio
async def test_send_to_user_misdetection_does_not_rewrite_message(monkeypatch, tmp_path):
    identifier = "a" * 32
    request = AsyncMock(return_value={"ok": True, "id": identifier})
    monkeypatch.setattr(function_hook_requests, "request", request)
    monkeypatch.setattr(mcp_tools, "_MANAGER", SimpleNamespace(launcher_session_id=lambda: "misdetection-mcp"))
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", None)
    monkeypatch.setattr(session_record_paths, "stop_log_path", lambda sid: tmp_path / f"{sid}.log")
    message = "引用の例を提示する。"

    environment = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    with runtime_environment.bind(runtime_environment.CallerContext(environment, tmp_path)):
        send_misdetection.remember_rejection("misdetection-mcp", "missing-prevention", message, identifier)
        with pytest.raises(ToolError, match="申告できる判定"):
            await mcp_tools.mcp.call_tool(
                "send_to_user",
                {
                    "message": message,
                    "misdetection": [{"check": "authorization", "reason": "例"}],
                },
            )
        request.assert_not_awaited()
        result = await mcp_tools.mcp.call_tool(
            "send_to_user",
            {
                "message": message,
                "misdetection": [{"check": "missing-prevention", "reason": "説明用の引用である。"}],
            },
        )

    assert isinstance(result, CallToolResult)
    assert result.structured_content == {"ok": True, "id": identifier}
    request.assert_awaited_once_with(
        "misdetection-mcp",
        "send_to_user",
        {
            "message": message,
            "misdetection_checks": ["missing-prevention"],
        },
    )
    summary = send_misdetection.summary("misdetection-mcp")
    assert "missing-prevention: 1件" in summary
    assert identifier in summary
