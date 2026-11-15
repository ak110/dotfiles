"""通常の再開記録に含まれない保留・通知・生存資源の情報も世代間で保つ。"""

import dataclasses
import json

import pytest

from agent_toolkit._agents_server import service_state, state, turn_state
from agent_toolkit._common import claude_usage_limit


@pytest.mark.parametrize(
    "hold",
    [
        turn_state.BackgroundWait({"agent_message": "子を待つ"}, None),
        turn_state.UsageLimitWait({"status": "failed"}, 123.4),
        turn_state.ClaudeConnectionWait({"status": "failed"}),
        turn_state.CodexProviderWait({"status": "failed"}),
        turn_state.ResultContractWait({"agent_message": "原結果"}, "形式の回復"),
    ],
)
def test_transfer_retains_control_state_and_creates_new_locks(hold: turn_state.ResultHold) -> None:
    session = state.SessionState(
        "parent",
        "/project",
        engine="claude",
        hold=hold,
        excluded_candidates=frozenset({("codex", "model", "high")}),
        usage_limit=claude_usage_limit.UsageLimitState("rejected", "five_hour", 456),
        live_tasks={"background": state.LiveTask("Bash", "試験", "2026-11-15T00:00:00Z")},
        live_child_session_ids={"child"},
        pending_tool_uses={"tool": ("Bash", "time", "test")},
        final_responses=((1, "原文"),),
        turn_end=turn_state.TurnEnd.COMPLETED,
        retention_deadline=123.4,
        codex_subagent_activity_ids={("thread", "turn", "item", "completed")},
    )
    copied = service_state.restore(json.loads(json.dumps(service_state.snapshot(session))))
    for field in dataclasses.fields(session):
        if field.name in {"turn_control_lock", "_registration_lock"}:
            assert getattr(copied, field.name) is not getattr(session, field.name)
        else:
            assert getattr(copied, field.name) == getattr(session, field.name), field.name
    copied.live_child_session_ids.add("another")
    assert session.live_child_session_ids == {"child"}
    with pytest.raises(ValueError):
        service_state.restore({})
