"""process-loopの統括も公開PreToolUse hookで質問を拒否されないことを検証する。"""

import json

import pytest

from agent_toolkit._testing.pretooluse_support import _run

# 主体と起動環境の判定に必要なフィールドを持つ合成PreToolUse入力。
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


@pytest.mark.parametrize(
    ("session_id", "extra", "environment"),
    [
        ("loop-session", {}, _PROCESS_LOOP),
        ("other-session", {}, _PROCESS_LOOP),
        ("loop-session", {"agent_id": "subagent-1"}, _PROCESS_LOOP),
        ("loop-session", {}, {**_PROCESS_LOOP, "AGENT_TOOLKIT_DELEGATED_SESSION": "1"}),
        ("loop-session", {}, {}),
    ],
    ids=["process-loop-main", "nested-claude", "subagent", "delegated-session", "interactive"],
)
def test_question_is_not_denied_because_of_process_loop(
    session_id: str, extra: dict[str, str], environment: dict[str, str]
) -> None:
    """統括・入れ子・サブエージェント・委譲先・対話の質問を、process-loopという起動環境だけで拒否しない。"""
    result = _run(
        _payload(session_id, **extra),
        {"AGENT_TOOLKIT_OWNER_SESSION": "", "AGENT_TOOLKIT_DELEGATED_SESSION": "0", **environment},
    )
    assert result.returncode == 0
    if result.stdout.strip():
        specific = json.loads(result.stdout).get("hookSpecificOutput", {})
        # 確認の前に読む資料の未取得による拒否は、起動環境によらない別の判定である。
        reason = specific.get("permissionDecisionReason", "")
        assert specific.get("permissionDecision") != "deny" or "確認の前に全文を読むと定めた資料" in reason
