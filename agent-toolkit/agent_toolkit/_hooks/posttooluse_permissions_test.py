"""PostToolUseの対象除外前に実ホスト応答を承認記録へ渡す。"""

import json

import pytest

from agent_toolkit._atk.wi import operation_permissions
from agent_toolkit._hooks import posttooluse


@pytest.mark.parametrize(
    ("event", "tool", "expected"),
    [
        ("PermissionDenied", "Read", ["denial"]),
        ("PostToolUseFailure", "Read", ["result"]),
        ("PostToolUse", "AskUserQuestion", ["answer", "result"]),
        ("PostToolUseFailure", "AskUserQuestion", ["result"]),
    ],
)
def test_original_response_observed_before_filter(monkeypatch, event, tool, expected):
    """除外された拒否・失敗も一度記録し、失敗をユーザー回答にはしない。"""
    calls = []
    payload = {"session_id": "permissions-session", "tool_name": tool, "tool_input": {}, "hook_event_name": event}
    monkeypatch.setattr(operation_permissions, "observe_denial", lambda value: calls.append(("denial", value)))
    monkeypatch.setattr(operation_permissions, "observe_answer", lambda value: calls.append(("answer", value)))
    monkeypatch.setattr(operation_permissions, "observe_result", lambda value: calls.append(("result", value)))
    posttooluse.main(json.dumps(payload))
    assert calls == [(kind, payload) for kind in expected]
