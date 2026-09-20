"""AskUserQuestion回答後の参照先確認通知を検証する。"""

import json

from agent_toolkit._hooks import posttooluse
from agent_toolkit._hooks.reference_notice import REFERENCE_NOTICE_BODY


def test_ask_user_question_adds_reference_notice(capsys) -> None:
    """Claude CodeのAskUserQuestion成功後に共通通知を一度返す。"""
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "ask-user-reference",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": []},
        "tool_response": {"answers": {}},
    }
    assert posttooluse.main(json.dumps(payload)) == 0
    output = json.loads(capsys.readouterr().out)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert context.count(REFERENCE_NOTICE_BODY) == 1


def test_other_tool_does_not_add_reference_notice(capsys) -> None:
    """AskUserQuestion以外のPostToolUseには共通通知を追加しない。"""
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "other-tool-reference",
        "tool_name": "Read",
        "tool_input": {"file_path": "README.md"},
        "tool_response": "body",
    }
    assert posttooluse.main(json.dumps(payload)) == 0
    assert capsys.readouterr().out == ""


def test_codex_ask_user_question_does_not_add_reference_notice(capsys) -> None:
    """Codex payloadはClaude Code固有のAskUserQuestion通知から除外する。"""
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "codex-ask-user-reference",
        "turn_id": "turn-1",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": []},
        "tool_response": {"answers": {}},
    }
    assert posttooluse.main(json.dumps(payload)) == 0
    assert capsys.readouterr().out == ""
