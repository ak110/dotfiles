"""実検証失敗の案内を、報告書式やスキル取得の認定と切り離して確かめる。  pyfltr: allow-comment"""

import pytest

from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks import bugfix_gate
from agent_toolkit._testing.pretooluse_support import codex_execution_payload


@pytest.mark.parametrize("actor", ["main", "lane"])
def test_verification_failure_is_notified_once_per_failure(tmp_path, actor):
    failure: dict = {
        "session_id": "verification-flow",
        "cwd": str(tmp_path),
        "agent_id": actor,
        "hook_event_name": "PostToolUse",
        "tool_use_id": "failed-1",
        "tool_name": "Bash",
        "tool_input": {"command": "uv run --frozen pytest sample_test.py"},
        "tool_response": {"exit_code": 1},
    }

    def warning(subject=actor):
        return bugfix_gate.verification_notice({**failure, "agent_id": subject}, "Read", {}, failure["session_id"])

    assert not warning()
    bugfix_gate.observe_verification(failure)
    assert not warning("other")
    notice = warning()
    assert "verification-bugfix" in notice
    assert "agent-basics:bugfix" in notice
    assert not warning()
    bugfix_gate.observe_verification(failure)
    assert not warning()
    bugfix_gate.observe_verification({**failure, "tool_use_id": "failed-2"})
    assert warning()


@pytest.mark.parametrize("command", ["rg needle missing.txt", "cat missing.txt"])
def test_nonverification_exit_is_not_a_verification_failure(command):
    bugfix_gate.observe_verification(
        {
            "session_id": "ordinary-operation",
            "tool_use_id": "failed-1",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"exit_code": 1},
        }
    )
    assert not bugfix_gate.verification_notice({}, "Read", {}, "ordinary-operation")


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("code,pending", [(0, False), (1, True), (2, False), (3, False), (4, False), (6, True)])
def test_ci_failure_is_distinguished_from_waiting_and_missing_runs(tmp_path, code, pending, host):
    """CI失敗1/6だけを保持し、待機の終了を不具合の失敗にしない。"""
    payload = {
        "session_id": "ci-failure",
        "tool_use_id": "ci-1",
        "hook_event_name": "PostToolUseFailure" if code else "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "atk run-script wait-ci -- --baseline baseline.json"},
        "tool_response": {"exit_code": code},
    }
    if host == "codex":
        payload.update(turn_id="ci-turn", tool_response="CIの終了状態を確認した")
        payload = codex_execution_payload(payload, tmp_path, exit_code=code)
    bugfix_gate.observe_verification(payload)
    current = read_state("ci-failure").get(bugfix_gate.FAILURE_STATE_KEY, {}).get("main", {})
    assert bool(current.get("pending")) is pending
    notice = bugfix_gate.verification_notice(payload, "Bash", {"command": "git commit -m fix"}, "ci-failure")
    assert bool(notice) is pending


@pytest.mark.parametrize("field", ["tool_use_id", "turn_id", "session_id"])
def test_ci_failure_uses_only_the_same_native_execution(tmp_path, field):
    """別の実行・ターン・主体の失敗や本文中の終了コードを、現在のCI失敗に流用しない。"""
    payload = codex_execution_payload(
        {
            "session_id": "ci-failure",
            "turn_id": "ci-turn",
            "tool_use_id": "ci-1",
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "atk run-script wait-ci -- --baseline baseline.json"},
            "tool_response": "exit_code: 1",
        },
        tmp_path,
        exit_code=1,
    )
    payload[field] = "different"
    bugfix_gate.observe_verification(payload)
    assert not read_state(payload["session_id"]).get(bugfix_gate.FAILURE_STATE_KEY)
