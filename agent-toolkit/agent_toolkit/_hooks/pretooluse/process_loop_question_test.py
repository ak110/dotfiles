"""process-loopのメイン会話の`AskUserQuestion`だけを、公開PreToolUse hookが拒否することを検証する。"""

import json

import pytest

from agent_toolkit._testing.pretooluse_support import _run, denial_reason

# Claude Code 2.1.319の`AskUserQuestion`のPreToolUse入力から、判定に使う項目だけを残した。
_QUESTION = {"questions": [{"question": "どちらにしますか？", "header": "方針", "options": [{"label": "A"}, {"label": "B"}]}]}
_PROCESS_LOOP = {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1", "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": "loop-session"}


def _payload(session_id: str, **extra: str) -> dict[str, object]:
    return {
        "session_id": session_id,
        "hook_event_name": "PreToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": _QUESTION,
        "cwd": "/tmp",
        **extra,
    }


def test_process_loop_main_question_is_denied_with_uwi_guidance() -> None:
    """process-loopが起動したメイン会話の質問は表示前に拒否し、UWIへの記録と元項目の保留を案内する。"""
    reason = denial_reason(_run(_payload("loop-session"), _PROCESS_LOOP))
    assert "回答期限が働かない" in reason
    assert "事前承認型UWI" in reason and "未確定判断の保留と暫定判断" in reason


@pytest.mark.parametrize(
    ("session_id", "extra", "environment"),
    [
        ("other-session", {}, _PROCESS_LOOP),
        ("loop-session", {"agent_id": "subagent-1"}, _PROCESS_LOOP),
        ("loop-session", {}, {**_PROCESS_LOOP, "AGENT_TOOLKIT_DELEGATED_SESSION": "1"}),
        ("loop-session", {}, {}),
    ],
    ids=["nested-claude", "subagent", "delegated-session", "interactive"],
)
def test_question_outside_process_loop_main_is_not_denied(
    session_id: str, extra: dict[str, str], environment: dict[str, str]
) -> None:
    """入れ子の会話、サブエージェント、委譲先、通常の対話セッションの質問は拒否しない。"""
    result = _run(_payload(session_id, **extra), environment)
    assert result.returncode == 0
    if result.stdout.strip():
        specific = json.loads(result.stdout).get("hookSpecificOutput", {})
        assert specific.get("permissionDecision") != "deny"
