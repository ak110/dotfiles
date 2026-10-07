"""`_agents_server/mcp_transport.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import pathlib
from types import SimpleNamespace
from typing import Any, cast

import claude_agent_sdk
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.message import SessionMessage

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


def test_main_persists_mcp_initialize_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """stdio起動でinitializeの受信、応答完了および失敗を永続化する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    def run(**_kwargs: Any) -> None:
        def message(root: SimpleNamespace) -> SessionMessage:
            return cast(SessionMessage, SimpleNamespace(message=SimpleNamespace(root=root)))

        tracker = mcp_transport._InitializationLogTracker()
        tracker.receive(message(SimpleNamespace(method="initialize", id=1)))
        tracker.sent(message(SimpleNamespace(id=1, error=None)))
        tracker.receive(message(SimpleNamespace(method="initialize", id=2)))
        tracker.sent(message(SimpleNamespace(id=2, error=ValueError("invalid initialize"))))

    monkeypatch.setattr(mcp_tools.mcp, "run", run)

    assert entry_script.main([]) == 0

    content = (tmp_path / "agents-server.log").read_text(encoding="utf-8")
    assert "FastMCP initializeを受信しました: request_id=1" in content
    assert "FastMCP initialize応答が完了しました: request_id=1" in content
    assert "FastMCP initialize応答が失敗しました: request_id=2" in content
    assert "exception_type=ValueError exception=invalid initialize" in content


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

    FastMCPは例外の`str()`をエラー本文へ使うため、共通の例外型の次の操作も想定外の例外の案内も、
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
