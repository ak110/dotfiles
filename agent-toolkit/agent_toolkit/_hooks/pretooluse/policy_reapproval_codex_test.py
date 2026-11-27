import io
import json
import pathlib
import sys

import pytest

from agent_toolkit import hook
from agent_toolkit._common import session_state

_OPTIONS = (
    {"label": "この方針で進める", "description": "送った方針のとおりに着手する"},
    {"label": "修正して進める", "description": "修正点を自由記述で伝える"},
)


@pytest.mark.parametrize("tool", ["request_user_input", "request_user_input_async"])
def test_codex_policy_reapproval_requires_matching_real_answer(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tool: str
) -> None:
    monkeypatch.setattr(session_state, "state_path", lambda sid: tmp_path / f"{sid}.json")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    asynchronous = tool == "request_user_input_async"
    base = {"session_id": "codex", "turn_id": "turn", "cwd": str(tmp_path)}

    def run(entry: str, payload: dict) -> str:
        capsys.readouterr()
        with monkeypatch.context() as stdin, io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())) as stream:
            stdin.setattr(sys, "stdin", stream)
            assert hook.main([entry]) == 0
        text = capsys.readouterr().out.strip()
        result = json.loads(text) if text else {}
        scoped = result.get("hookSpecificOutput", {})
        if result.get("decision") == "block":
            return result["reason"]
        return scoped.get("permissionDecisionReason", "") if scoped.get("permissionDecision") == "deny" else ""

    def ask(call: str, value: str, modifier: str = "") -> None:
        question = f"送った{modifier}方針で進めてよいですか？"
        labels = [option["label"] for option in _OPTIONS]
        item = (
            {"title": question, "options": labels}
            if asynchronous
            else {"header": "進め方", "question": question, "options": list(_OPTIONS), "id": "p"}
        )
        body = {"questions": [item]}
        common = {**base, "tool_name": tool, "tool_input": body, "tool_use_id": call}
        assert not run("pretooluse", {**common, "hook_event_name": "PreToolUse"})
        if asynchronous:
            run("posttooluse", {**common, "hook_event_name": "PostToolUse", "tool_response": {}})
            replies = [{"questionItemId": json.dumps([tool, call, 0]), "answer": value}]
            prompt = f"<send_user_message_question_reply>{json.dumps(replies)}</send_user_message_question_reply>"
            run("user_prompt_submit", {**base, "hook_event_name": "UserPromptSubmit", "prompt": prompt, "model": "m"})
        else:
            response = {"answers": {"p": {"answers": [value]}}}
            run("posttooluse", {**common, "hook_event_name": "PostToolUse", "tool_response": response})

    def blocked() -> str:
        body = {"command": "git commit -m test"}
        return run("pretooluse", {**base, "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": body})

    approved, revise = "この方針で進める", "修正して進める"
    ask("first", revise)
    assert "実回答" in blocked()
    ask("revised", approved)
    assert not blocked()
    ask("modified", revise, "対象が長い2件目の改訂版の")
    ask("other-scope", approved)
    assert blocked()
    ask("modified-match", approved, "対象が長い2件目の改訂版の")
    assert not blocked()
