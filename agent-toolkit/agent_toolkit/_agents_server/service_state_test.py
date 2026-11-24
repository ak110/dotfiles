import dataclasses
import json

import pytest

from agent_toolkit._agents_server import service_state, state, turn_state
from agent_toolkit._common import claude_usage_limit, session_registry


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


@pytest.mark.asyncio
async def test_restore_accepts_payload_from_generation_without_new_optional_fields() -> None:
    payload = json.loads(json.dumps(await service_state.snapshot(state.SessionState("parent", "/project"))))
    optional = next(
        field.name
        for field in dataclasses.fields(state.SessionState)
        if field.name in payload
        and (field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING)
    )
    del payload[optional]
    assert service_state.restore(payload).session_id == "parent"
    with pytest.raises(ValueError):
        service_state.restore({**payload, "unknown_field": {"kind": "value", "value": None}})
    without_required = dict(payload)
    del without_required["cwd"]
    with pytest.raises(ValueError):
        service_state.restore(without_required)


def test_codec_round_trips_registry_resume_state() -> None:
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

    assert type(copied["child"]) is state.RegistryResumeState  # pylint: disable=unidiomatic-typecheck


def test_resume_display_action_does_not_change_projection_schema() -> None:
    codec = service_state._StateCodec()  # pylint: disable=protected-access
    session = state.SessionState("child", "/project", last_action="Read: 計画")
    stopped = state.SessionResumeState.from_session(session)
    payload = codec.encode(stopped)
    assert stopped.last_action == "Read: 計画"
    assert "last_action" not in payload["fields"]
    assert codec.decode(payload).last_action == ""


@pytest.mark.asyncio
async def test_active_item_transfer_preserves_the_wire_pair() -> None:
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


def test_restore_reads_previous_public_release_snapshot() -> None:

    raw = """{
  "session_id": {"kind":"value","value":"old-session"},
  "cwd": {"kind":"value","value":"/project"},
  "model": {"kind":"value","value":null},
  "effort": {"kind":"value","value":null},
  "engine": {"kind":"value","value":"codex"},
  "fast_mode": {"kind":"value","value":null},
  "codex_model_provider": {"kind":"value","value":null},
  "codex_subscription_provider": {"kind":"value","value":null},
  "codex_state_provider": {"kind":"value","value":null},
  "codex_attempted_provider_ids": {"kind":"set","items":[]},
  "codex_auto_reset_pending": {"kind":"value","value":false},
  "claude_api_env_file": {"kind":"value","value":null},
  "claude_connection": {"kind":"value","value":null},
  "claude_role": {"kind":"value","value":null},
  "claude_connection_diagnostics": {"kind":"tuple","items":[]},
  "claude_mcp_diagnostics": {"kind":"tuple","items":[]},
  "claude_connection_failures": {"kind":"dict","items":{}},
  "claude_subscription_retry_at": {"kind":"value","value":null},
  "model_type": {"kind":"value","value":null},
  "launch_kind": {"kind":"value","value":"delegate"},
  "label": {"kind":"value","value":""},
  "prompt": {"kind":"value","value":""},
  "followup_prompts": {"kind":"tuple","items":[]},
  "final_responses": {"kind":"tuple","items":[]},
  "terminal_error": {"kind":"value","value":null},
  "effort_history": {"kind":"tuple","items":[]},
  "effort_levels": {"kind":"tuple","items":[]},
  "effort_resume_turn": {"kind":"value","value":null},
  "effort_resuming": {"kind":"value","value":false},
  "effort_request_results": {"kind":"dict","items":{}},
  "claude_applied_effort": {"kind":"value","value":null},
  "announced": {"kind":"value","value":false},
  "created_at": {"kind":"value","value":"2026-11-22T08:43:15.939816+00:00"},
  "started_at": {"kind":"value","value":"2026-11-22T08:43:15.939827+00:00"},
  "excluded_candidates": {"kind":"frozenset","items":[]},
  "turn_seq": {"kind":"value","value":0},
  "turn_id": {"kind":"value","value":""},
  "status": {"kind":"value","value":"running"},
  "plan": {"kind":"list","items":[]},
  "current_item": {"kind":"value","value":null},
  "current_item_started_at": {"kind":"value","value":null},
  "commentary": {"kind":"value","value":""},
  "diff_changed": {"kind":"value","value":false},
  "error": {"kind":"value","value":null},
  "agent_message": {"kind":"value","value":""},
  "result_delivered": {"kind":"value","value":false},
  "protocol_warnings": {"kind":"list","items":[]},
  "turn_start": {"kind":"enum","type":"turn_start","name":"UNSENT"},
  "interrupt_requested": {"kind":"value","value":false},
  "turn_end": {"kind":"enum","type":"turn_end","name":"IN_PROGRESS"},
  "model_output_observed": {"kind":"value","value":false},
  "live_tasks": {"kind":"dict","items":{}},
  "live_child_session_ids": {"kind":"set","items":[]},
  "terminal_child_session_ids": {"kind":"set","items":[]},
  "agents_wait_background_outputs": {"kind":"set","items":[]},
  "child_tool_uses": {"kind":"dict","items":{}},
  "pending_tool_uses": {"kind":"dict","items":{}},
  "last_action": {"kind":"value","value":""},
  "hold": {"kind":"value","value":null},
  "cli_turn_state": {"kind":"value","value":null},
  "cli_turn_state_fallback_logged": {"kind":"value","value":false},
  "result_contract_recovery_consumed": {"kind":"value","value":false},
  "availability_checked": {"kind":"value","value":false},
  "overload_resume_count": {"kind":"value","value":0},
  "overload_first_at": {"kind":"value","value":null},
  "usage_limit": {"kind":"value","value":null},
  "usage_limit_wait_count": {"kind":"value","value":0},
  "usage_limit_first_at": {"kind":"value","value":null},
  "finalized_at": {"kind":"value","value":null},
  "updated_at": {"kind":"value","value":"2026-11-22T08:43:15.939835+00:00"},
  "output_updated_at": {"kind":"value","value":null},
  "api_error": {"kind":"value","value":null},
  "context_tokens": {"kind":"value","value":null},
  "context_window": {"kind":"value","value":null},
  "retention_deadline": {"kind":"value","value":null},
  "_progress_text": {"kind":"value","value":""},
  "progress_items": {"kind":"dict","items":{}},
  "compaction_started_at_ms": {"kind":"dict","items":{}},
  "codex_subagent_thread_ids": {"kind":"set","items":[]},
  "codex_live_subagent_thread_ids": {"kind":"set","items":[]},
  "codex_terminal_subagent_thread_ids": {"kind":"set","items":[]},
  "codex_subagent_activity_ids": {"kind":"set","items":[]},
  "codex_self_message_observed": {"kind":"value","value":false},
  "codex_self_message_repair_consumed": {"kind":"value","value":false},
  "publish_registry": {"kind":"value","value":false},
  "launcher_session_id": {"kind":"value","value":null},
  "executor_process": {"kind":"value","value":null},
  "owner_root_session_id": {"kind":"value","value":null},
  "_published_registry_launcher": {"kind":"value","value":null},
  "_published_registry_state": {"kind":"value","value":null},
  "_published_registry_turn_seq": {"kind":"value","value":null},
  "_published_registry_fast_mode": {"kind":"value","value":null},
  "_published_registry_effort": {"kind":"value","value":null},
  "_published_registry_result_contract_recovery_consumed": {"kind":"value","value":false},
  "_published_registry_launch_info": {"kind":"value","value":null},
  "_published_registry_executor": {"kind":"value","value":null},
  "_published_registry_owner_root": {"kind":"value","value":null},
  "_terminal_notified": {"kind":"value","value":false},
  "_lifecycle_running": {"kind":"value","value":false}
}"""
    restored = service_state.restore(json.loads(raw))
    assert (restored.session_id, restored.cwd) == ("old-session", "/project")
    assert restored.startup_profile is None
    assert restored.reply_start_pending is False


def test_registry_resolution_guard_round_trip() -> None:
    expected = session_registry.SessionResolution(
        session_registry.Resolution.RUNNING,
        session_registry.ResumeInfo("codex", "/project", None, None, None, "delegate", 2, "running"),
        recorded_turn_seq=2,
    )
    codec = service_state._StateCodec()  # pylint: disable=protected-access
    assert codec.decode(json.loads(json.dumps(codec.encode(expected)))) == expected
