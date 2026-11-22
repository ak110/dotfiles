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
@pytest.mark.asyncio
async def test_transfer_retains_control_state_and_creates_new_locks(hold: turn_state.ResultHold) -> None:
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
    copied = service_state.restore(json.loads(json.dumps(await service_state.snapshot(session))))
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


def test_resume_display_action_does_not_change_projection_schema() -> None:
    """同じ終了状態の旧版と新版で世代間投影のフィールド集合を変えない。"""
    codec = service_state._StateCodec()  # pylint: disable=protected-access
    session = state.SessionState("child", "/project", last_action="Read: 計画")
    stopped = state.SessionResumeState.from_session(session)
    payload = codec.encode(stopped)
    assert stopped.last_action == "Read: 計画"
    assert "last_action" not in payload["fields"]
    assert codec.decode(payload).last_action == ""


@pytest.mark.asyncio
async def test_active_item_transfer_preserves_the_wire_pair() -> None:
    """進行中のitemは、別世代が使う従来の2項目で渡して表示を保つ。"""
    session = state.SessionState("child", "/project")
    session.record_current_item_start({"type": "commandExecution", "command": "git status"})

    wire = json.loads(json.dumps(await service_state.snapshot(session)))
    assert "_active_item" not in wire
    assert "current_item" in wire and "current_item_started_at" in wire
    restored = service_state.restore(wire)
    assert restored.active_tool_uses() == session.active_tool_uses()
    assert restored.active_tool_uses()[0]["detail"] == "command=git status"

    session.record_current_item_start(None)
    wire["current_item_started_at"] = (await service_state.snapshot(session))["current_item_started_at"]
    assert not service_state.restore(wire).active_tool_uses()


@pytest.mark.asyncio
async def test_effort_and_retry_states_transfer_with_legacy_fields() -> None:
    """予約・再開中・再試行連鎖を、稼働中の別世代が読む形式で往復させる。"""
    session = state.SessionState("child", "/project", turn_seq=3, status="completed", turn_end=turn_state.TurnEnd.COMPLETED)
    session.reserve_effort_resume(3)
    session.advance_overload_retry()
    session.advance_usage_limit_retry()
    first_at = session.usage_limit_first_at
    session.advance_usage_limit_retry()

    wire = json.loads(json.dumps(await service_state.snapshot(session)))
    assert {
        "effort_resume_turn",
        "effort_resuming",
        "overload_resume_count",
        "overload_first_at",
        "usage_limit_wait_count",
        "usage_limit_first_at",
    } <= wire.keys()
    restored = service_state.restore(wire)
    assert restored.effort_resume_turn == 3 and not restored.effort_resuming
    assert restored.overload_resume_count == 1 and restored.overload_first_at == session.overload_first_at
    assert restored.usage_limit_wait_count == 2 and restored.usage_limit_first_at == first_at

    assert restored.begin_effort_resume()
    running = service_state.restore(await service_state.snapshot(restored))
    assert running.effort_resume_turn is None and running.effort_resuming
    assert not running.result_available
    running.finish_effort_resume()
    assert running.result_available
