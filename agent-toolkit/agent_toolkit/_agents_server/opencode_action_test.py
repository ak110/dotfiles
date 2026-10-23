"""受信済みOpenCode tool partの入力と、活動表示の対応を検証する。"""

import json
from typing import Any

import pytest

from agent_toolkit._agents_server.opencode import OpenCodeManager
from agent_toolkit._agents_server.state import SessionState


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "error"])
async def test_received_input_changes_summary_without_unfinished_task(status: str) -> None:
    """同名ツールの別入力と原本を区別し、受信していない実行中状態を作らない。"""
    session = SessionState(session_id="ses_received", cwd=".", engine="opencode")
    async with OpenCodeManager() as backend:
        for command in ("printf 日本語", "printf 次の処理"):
            payload = {
                "type": "tool_use",
                "sessionID": session.session_id,
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "callID": "call_received",
                    "state": {"status": status, "input": {"command": command, "token": "received-example"}},
                },
            }
            original = json.dumps(payload, ensure_ascii=False)
            assert await backend.process_event(session, payload) is False
            assert session.last_action == f"bash: command={command} token=received-example"
            assert not session.pending_tool_uses
            assert session.current_item is None
            assert json.dumps(payload, ensure_ascii=False) == original
            assert session.status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [None, {}, {"input": {}}, {"input": "not-an-object"}])
async def test_absent_input_keeps_name_only(state: Any) -> None:
    """入力の無い通知から情報を補わず、既存の名前だけの表示を保つ。"""
    session = SessionState(session_id="ses_received", cwd=".", engine="opencode")
    async with OpenCodeManager() as backend:
        await backend.process_event(session, {"type": "tool_use", "part": {"tool": "read", "state": state}})
    assert session.last_action == "read"
    assert not session.pending_tool_uses


@pytest.mark.asyncio
async def test_input_uses_existing_single_line_and_length_limit() -> None:
    """入れ子・改行・長い入力を、共通の1行化と200文字上限で扱う。"""
    session = SessionState(session_id="ses_received", cwd=".", engine="opencode")
    async with OpenCodeManager() as backend:
        await backend.process_event(
            session,
            {"type": "tool_use", "part": {"tool": "read", "state": {"input": {"options": {"line": 2}, "path": "/資料\n本文"}}}},
        )
        assert session.last_action == 'read: options={"line":2} path=/資料 本文'
        await backend.process_event(
            session, {"type": "tool_use", "part": {"tool": "write", "state": {"input": {"content": "あ" * 300}}}}
        )
    assert session.last_action == "write: content=" + "あ" * 192 + "…"
