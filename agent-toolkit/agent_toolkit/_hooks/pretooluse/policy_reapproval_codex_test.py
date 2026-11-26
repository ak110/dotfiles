import datetime
import io
import json
import pathlib
import sys
import time

import pytest

from agent_toolkit import hook
from agent_toolkit._common import session_state

_OPTIONS = (
    {"label": "この方針で進める", "description": "送った方針のとおりに着手する"},
    {"label": "修正して進める", "description": "修正点を自由記述で伝える"},
)


@pytest.mark.parametrize("tool", ["request_user_input", "request_user_input_async"])
def test_codex_policy_reapproval_accepts_revision_in_assistant_text(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tool: str
) -> None:
    monkeypatch.setattr(session_state, "state_path", lambda sid: tmp_path / f"{sid}.json")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    asynchronous = tool == "request_user_input_async"
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(json.dumps({"type": "session_meta", "payload": {}}) + "\n", encoding="utf-8")
    base = {"session_id": "codex", "turn_id": "turn", "cwd": str(tmp_path), "transcript_path": str(rollout)}

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

    def write(text: str, *, role: str = "assistant", kind: str = "output_text") -> None:
        stamp = datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        item = {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}
        with rollout.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"timestamp": stamp, "type": "response_item", "payload": item}, ensure_ascii=False) + "\n")

    def ask(call: str, value: str, modifier: str = "") -> None:
        question = f"送った{modifier}方針で進めてよいですか？"
        labels = [option["label"] for option in _OPTIONS]
        item = (
            {"title": f"方針の承認：{question}", "options": labels}
            if asynchronous
            else {"header": "方針の承認", "question": question, "options": list(_OPTIONS), "id": "p"}
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

    def wait_next_rollout_millisecond() -> None:
        time.sleep(0.002)

    approved, revise = "この方針で進める", "修正して進める"
    write("方針（案）\n目標: 修正前に書いた本文")
    ask("first", revise)
    reason = blocked()
    assert "応答の本文に書いて" in reason and "/mcp" not in reason
    ask("before-revision", approved)
    assert blocked()
    write("進捗: 改訂版を作成中")
    write("方針（案）\n目標: ユーザーの発話", role="user", kind="input_text")
    write("方針（案） 2件目の\n目標: 別の方針")
    ask("progress-only", approved)
    assert blocked()
    write("方針（案）\n目標: 既存の一行だけを確定する")
    wait_next_rollout_millisecond()
    ask("revised", revise)
    ask("stale", approved)
    assert blocked()
    write("方針（案）\n目標: 再度の修正を反映した")
    ask("revised-again", approved)
    assert not blocked()
    ask("modified", revise, "2件目の")
    write("方針（案）\n目標: 修飾なし")
    ask("modified-mismatch", approved, "2件目の")
    assert blocked()
    write("方針（案） 2件目の\n目標: 修飾あり")
    ask("modified-match", approved, "2件目の")
    assert not blocked()
