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


def test_codec_round_trips_registry_resume_state() -> None:
    """世代移行の`move_context`は登録簿由来の停止済みsessionも符号化できる。

    未登録だと旧世代の共有監視が毎巡TypeErrorで中断し、自動再開と状態保存が止まった（2026-11-17の巡回で観測）。
    """
    codec = service_state._StateCodec()  # pylint: disable=protected-access
    stopped = {
        "child": state.RegistryResumeState(
            "child",
            "/project",
            None,
            None,
            "codex",
            excluded_candidates=frozenset({("codex", "model", "high")}),
            result_root_session_id="root",
        )
    }
    copied = codec.decode(json.loads(json.dumps(codec.encode(stopped))))
    assert copied == stopped
    # 基底の再開状態へ縮退すると照会時の鮮度確認が外れるため、厳密な型を確かめる。
    assert type(copied["child"]) is state.RegistryResumeState  # pylint: disable=unidiomatic-typecheck
