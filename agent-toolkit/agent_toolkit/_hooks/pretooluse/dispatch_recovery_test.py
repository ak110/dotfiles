"""正常拒否と、引用を保った質問・計画の再発行を検証する。"""

# 対象tool集合の追加漏れと内部出力の分離を契約として検証する。
# pylint: disable=protected-access

import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from agent_toolkit._agents_server import mcp_tools
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import function_hook_requests
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._hooks.pretooluse import dispatch, operation_skills, user_facing_checks
from agent_toolkit._hooks.pretooluse.decision import Decision


@pytest.fixture(autouse=True)
def _isolate_skill_notices(monkeypatch):
    monkeypatch.setattr(operation_skills, "notices", lambda *_args, **_kwargs: [])


def _reason(payload: dict, tool: str, inputs: dict, capsys) -> str | None:
    assert dispatch.main(json.dumps({**payload, "tool_name": tool, "tool_input": inputs})) == 0
    captured = capsys.readouterr()
    assert not captured.err
    specific = json.loads(captured.out)["hookSpecificOutput"] if captured.out else {}
    if specific.get("permissionDecision") == "deny":
        assert specific["permissionDecisionReason"]
        return specific["permissionDecisionReason"]
    return None


def _input(tool: str, text: str) -> dict:
    if tool == "ExitPlanMode":
        return {"plan": text}
    if tool == "AskUserQuestion":
        return {"questions": [{"question": text, "header": "確認", "options": []}]}
    raise AssertionError(f"言語判定対象の入力と回復試験を追加せよ: {tool}")


def test_structured_denial_separates_pending_notice(capsys) -> None:
    assert dispatch._emit(Decision(block="拒否理由", notices=("付随通知",)), ["保留通知"], is_codex=False) == 0
    captured = capsys.readouterr()
    assert not captured.err
    specific = json.loads(captured.out)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert specific["permissionDecisionReason"] == "拒否理由"
    assert "保留通知" in specific["additionalContext"]
    assert "付随通知" in specific["additionalContext"]


@pytest.mark.parametrize("tool", sorted(user_facing_checks.USER_FACING_TEXT_TOOL_NAMES))
def test_quoted_material_can_recover_with_identical_input(tool: str, capsys) -> None:

    text = '原文: "Now the requested action has failed because the remote process no longer exists."'
    payload = {"session_id": "recovery", "agent_id": "one"}
    inputs = _input(tool, text)
    first = _reason(payload, tool, inputs, capsys)
    assert first
    assert "引用" in first and "同一" in first
    assert _reason(payload, tool, inputs, capsys) is None


@pytest.mark.parametrize("tool", sorted(user_facing_checks.USER_FACING_TEXT_TOOL_NAMES))
@pytest.mark.parametrize("intervening_read", [False, True])
def test_recovery_uses_fingerprint_saved_before_move(tool: str, intervening_read: bool, capsys) -> None:
    session_id = "before-language-check-move"
    payload = {"session_id": session_id, "agent_id": "one"}
    inputs = _input(tool, '原文: "Now the remote process no longer exists and the requested action has failed."')
    saved_key = "response_language_retry_by_actor"
    encoded = json.dumps([tool, inputs], sort_keys=True, ensure_ascii=False).encode()
    saved_retry = {"one": hashlib.sha256(encoded).hexdigest(), "other": "preserve"}
    assert update_state(session_id, lambda state: {**state, saved_key: saved_retry})
    if intervening_read:
        assert _reason(payload, "Read", {}, capsys) is None
        assert read_state(session_id)[saved_key] == {"other": "preserve"}
        assert _reason(payload, tool, inputs, capsys)
    else:
        assert _reason(payload, tool, inputs, capsys) is None


@pytest.mark.parametrize("tool", sorted(user_facing_checks.USER_FACING_TEXT_TOOL_NAMES))
def test_own_english_explanation_remains_blocked_on_identical_retry(tool: str, capsys) -> None:

    inputs = _input(tool, "Now I will explain the implementation and ask you to approve the following plan.")
    payload = {"session_id": "recovery", "agent_id": "one"}
    for _ in range(2):
        assert _reason(payload, tool, inputs, capsys)


@pytest.mark.parametrize("tool", sorted(user_facing_checks.USER_FACING_TEXT_TOOL_NAMES))
def test_english_explanation_outside_material_remains_blocked(tool, capsys):

    text = '原文: "The material is preserved." Now I will explain the implementation in English.'
    payload = {"session_id": "mixed-material", "agent_id": "one"}
    for _ in range(2):
        assert _reason(payload, tool, _input(tool, text), capsys)


@pytest.mark.parametrize("tool", sorted(user_facing_checks.USER_FACING_TEXT_TOOL_NAMES))
def test_non_text_input_change_invalidates_recovery(tool, capsys):

    text = '原文: "Now the requested action has failed because the remote process no longer exists."'
    payload = {"session_id": "all-inputs", "agent_id": "one"}
    inputs = _input(tool, text)
    assert _reason(payload, tool, inputs, capsys)
    assert _reason(payload, tool, {**inputs, "changed_argument": True}, capsys)


def test_language_recovery_is_scoped_to_actor_tool_and_body(capsys) -> None:

    text = '原文: "Now the requested action has failed because the remote process no longer exists."'
    first = {"session_id": "boundary", "agent_id": "one"}
    second = {"session_id": "boundary", "agent_id": "two"}
    assert _reason(first, "ExitPlanMode", _input("ExitPlanMode", text), capsys)
    assert _reason(second, "ExitPlanMode", _input("ExitPlanMode", text), capsys)
    assert _reason(first, "AskUserQuestion", _input("AskUserQuestion", text), capsys)
    assert _reason(first, "ExitPlanMode", _input("ExitPlanMode", text + "原文。 "), capsys)


@pytest.mark.parametrize("tool", sorted(user_facing_checks.USER_FACING_TEXT_TOOL_NAMES))
def test_intervening_operation_invalidates_recovery(tool, capsys) -> None:

    text = '原文: "Now the remote process no longer exists and the requested action has failed."'
    payload = {"session_id": "intervening", "agent_id": "one"}
    assert _reason(payload, tool, _input(tool, text), capsys)
    assert _reason(payload, "Read", {}, capsys) is None
    assert _reason(payload, tool, _input(tool, text), capsys)


def test_generic_reissues_survive_other_operations_and_are_consumed(capsys) -> None:
    payload = {"session_id": "generic-many", "agent_id": "one"}
    first = {"subagent_type": "Explore", "prompt": "調査する。"}
    second = {"subagent_type": "Plan", "prompt": "計画する。"}
    assert _reason(payload, "Agent", first, capsys)
    assert _reason(payload, "Agent", second, capsys)
    assert _reason(payload, "Read", {}, capsys) is None
    assert _reason(payload, "Agent", first, capsys) is None
    assert _reason(payload, "Agent", second, capsys) is None
    assert _reason(payload, "Agent", first, capsys)
    assert _reason(payload, "Agent", second, capsys)


@pytest.mark.parametrize("difference", ["actor", "tool", "input", "session"])
def test_generic_retry_requires_same_session_actor_tool_and_full_input(difference, capsys) -> None:
    payload = {"session_id": "generic-scope", "agent_id": "one"}
    inputs = {"subagent_type": "Explore", "prompt": "調査する。", "run_in_background": True}
    assert _reason(payload, "Agent", inputs, capsys)
    changed_payload = {**payload}
    changed_input = {**inputs}
    tool = "Agent"
    if difference == "actor":
        changed_payload["agent_id"] = "two"
    elif difference == "session":
        changed_payload["session_id"] = "generic-other"
    elif difference == "tool":
        tool = "Task"
    else:
        changed_input["run_in_background"] = False
    assert _reason(changed_payload, tool, changed_input, capsys)
    assert _reason(payload, "Agent", inputs, capsys) is None


def test_generic_retry_retains_only_latest_32_inputs(capsys) -> None:
    payload = {"session_id": "generic-bounded"}
    for index in range(33):
        assert _reason(payload, "Agent", {"prompt": f"調査{index}"}, capsys)
    assert _reason(payload, "Agent", {"prompt": "調査32"}, capsys) is None
    assert _reason(payload, "Agent", {"prompt": "調査0"}, capsys)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("call_prefix", ["toolu", "repl"])
async def test_saved_report_uses_one_validated_snapshot(monkeypatch, tmp_path, capsys, call_prefix):
    session = "report-owner"
    reference = "rejected-send-to-user-abcd1234"
    root = managed_temp.resolve_session_temp(session)
    saved = root / reference / "message.md"
    saved.parent.mkdir()
    visible = "検査した長文を一度だけ表示する。\n" * 100
    saved.write_bytes(visible.encode("utf-8"))
    monkeypatch.setattr(mcp_tools, "_MANAGER", SimpleNamespace(launcher_session_id=lambda: session))
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", None)
    monkeypatch.setattr(mcp_tools, "_UI_DELEGATED", False)
    request = AsyncMock(return_value={"status": "confirmed"})
    monkeypatch.setattr(function_hook_requests, "request", request)

    def hook(arguments: dict, *, identifier: str = session) -> dict:
        payload = {
            "session_id": identifier,
            "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_to_user",
            "tool_use_id": f"{call_prefix}_report",
            "tool_input": arguments,
            "cwd": str(tmp_path),
        }
        assert dispatch.main(json.dumps(payload)) == 0
        return json.loads(capsys.readouterr().out)["hookSpecificOutput"]

    with pytest.raises(ToolError, match="事前検査"):
        await mcp_tools.mcp.call_tool("send_to_user", {"body_ref": reference})
    for invalid in ({}, {"body_ref": reference, "message": visible}):
        with pytest.raises(ToolError, match="一方だけ"):
            await mcp_tools.mcp.call_tool("send_to_user", invalid)
    with pytest.raises(ToolError, match="証明できない"):
        await mcp_tools.mcp.call_tool("send_to_user_preflight", {})
    assert hook({"body_ref": reference}, identifier="other")["permissionDecision"] == "deny"
    for misplaced in (str(saved), "../" + reference, "%2e%2e/" + reference):
        assert hook({"body_ref": misplaced})["permissionDecision"] == "deny"
    alias = root / "rejected-send-to-user-abcd1235" / "message.md"
    alias.parent.mkdir()
    os.link(saved, alias)
    assert hook({"body_ref": alias.parent.name})["permissionDecision"] == "deny"
    alias.unlink()
    request.assert_not_awaited()
    ready = hook({"body_ref": reference})
    assert ready["permissionDecision"] == "allow"
    saved.write_text("検査後に変えた別の本文。", encoding="utf-8")
    response = await mcp_tools.mcp.call_tool("send_to_user", ready["updatedInput"])
    request.assert_awaited_once_with(session, "send_to_user", {"message": visible})
    assert isinstance(response, CallToolResult)
    assert response.structured_content == {"status": "confirmed"}
