"""agents_server共有プロンプトと状態遷移の契約を検証する。"""

import logging

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agent_toolkit._agents_server import state, turn_state, wait_output_tracking


def test_turn_start_observation_survives_late_send_and_response_loss() -> None:
    session = state.SessionState("thread-1", "/work")
    session.record_turn_started("turn-1")
    session.record_turn_sent()
    session.record_turn_ambiguous()

    assert session.turn_start is turn_state.TurnStart.STARTED
    assert session.turn_id == "turn-1"
    assert not session.turn_start_ambiguous
    session.record_turn_completed()
    assert session.turn_completed


def test_turn_end_transitions_keep_start_observation_and_exclusive_projections() -> None:
    """失敗後の終了待ち・完了・実行中を排他的に表し、開始の識別子を保持する。"""
    session = state.SessionState("thread-1", "/work")
    session.record_turn_started("turn-1")

    session.record_failure_pending_completion()
    assert session.failure_pending_completion and not session.turn_completed

    session.record_turn_completed()
    assert session.turn_completed and not session.failure_pending_completion

    session.record_turn_running()
    assert not session.turn_completed and not session.failure_pending_completion
    assert session.turn_start is turn_state.TurnStart.STARTED
    assert session.turn_id == "turn-1"


def test_result_contract_recovery_consumption_survives_turn_and_resume_state() -> None:
    session = state.SessionState(
        "parent-1",
        "/tmp",
        result_contract_recovery_consumed=True,
        hold=turn_state.ResultContractWait({"status": "completed", "agent_message": "", "error": None}, "形式違反"),
    )

    state.initialize_turn(session)
    resumed = state.SessionResumeState.from_session(session)

    assert session.result_contract_recovery_consumed
    assert not session.result_contract_recovery_pending
    assert session.result_contract_recovery_reason is None
    assert resumed.result_contract_recovery_consumed


def test_pending_tool_uses_track_tools_outside_agents_server() -> None:
    session = state.SessionState("parent-1", "/tmp")

    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "Bash", "input": {"command": "secret --token=abc"}}]},
    )

    assert [entry["name"] for entry in session.active_tool_uses()] == ["Bash"]

    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"tool_use_id": "toolu_1", "content": "done"}]},
    )

    assert not session.active_tool_uses()


def test_active_tool_uses_include_input_detail_and_sort_by_start() -> None:
    session = state.SessionState("parent-1", "/tmp")
    session.pending_tool_uses.update(
        {
            "toolu_2": ("Read", "2026-09-15T00:00:02+00:00", "file_path=/tmp/a.py"),
            "toolu_1": ("Bash", "2026-09-15T00:00:01+00:00", "command=rg -n foo"),
            "toolu_3": ("TodoWrite", "2026-09-15T00:00:03+00:00", ""),
        }
    )

    assert session.active_tool_uses() == [
        {"name": "Bash", "started_at": "2026-09-15T00:00:01+00:00", "detail": "command=rg -n foo"},
        {"name": "Read", "started_at": "2026-09-15T00:00:02+00:00", "detail": "file_path=/tmp/a.py"},
        {"name": "TodoWrite", "started_at": "2026-09-15T00:00:03+00:00"},
    ]


def test_current_item_is_projected_as_active_tool_use() -> None:
    """Codex backendの進行中itemを、種別・開始時刻と入力の要約として返し、内部IDを保持だけに使う。"""
    session = state.SessionState("thread-1", "/tmp", engine="codex")

    session.record_current_item_start({"type": "commandExecution", "id": "item-1", "command": "rg -n foo"})
    entries = session.active_tool_uses()

    assert len(entries) == 1
    assert entries[0]["type"] == "commandExecution"
    assert "id" not in entries[0]
    assert session.current_item is not None and session.current_item["id"] == "item-1"

    assert entries[0]["detail"] == "command=rg -n foo"
    assert entries[0]["started_at"]
    assert session.last_action == "commandExecution: command=rg -n foo"

    session.record_current_item_start(None)

    assert not session.active_tool_uses()
    assert session.current_item is None and session.current_item_started_at is None

    session.record_current_item_start({"type": "commandExecution", "id": "item-2"})
    state.initialize_turn(session)
    assert not session.active_tool_uses()
    assert session.current_item is None and session.current_item_started_at is None


def test_last_action_prefers_tool_use_issued_after_text() -> None:
    session = state.SessionState("parent-1", "/tmp")

    session.set_progress("調査を続ける")
    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "Bash", "input": {}}]},
    )

    assert session.last_action == "Bash"


def test_last_action_changes_between_repeated_calls_of_the_same_tool() -> None:
    session = state.SessionState("parent-1", "/tmp")

    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "Bash", "input": {"command": "git status"}}]},
    )
    first = session.last_action
    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_2", "name": "Bash", "input": {"command": "git diff"}}]},
    )

    assert first == "Bash: command=git status"
    assert session.last_action == "Bash: command=git diff"


def test_action_detail_collapses_whitespace_and_truncates_at_limit() -> None:
    session = state.SessionState("parent-1", "/tmp")

    session.record_tool_use_start("toolu_1", "Write", {"path": "a.py", "content": "x\r\ny  z", "line": 12})

    assert session.last_action == "Write: path=a.py content=x y z line=12"

    session.record_tool_use_start("toolu_2", "Write", {"content": "y" * 300})
    detail = session.active_tool_uses()[-1]["detail"]

    assert detail == f"content={'y' * 192}…"


@pytest.mark.asyncio
async def test_terminal_transition_logs_safe_fields_once(caplog: pytest.LogCaptureFixture) -> None:
    session = state.SessionState("session-1", "/tmp")
    session.status = "completed"
    session.agent_message = "秘密の結果本文"
    session.record_turn_completed()

    with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.state"):
        session.touch()
        session.touch()

    assert caplog.text.count("session_transition event=terminal") == 1
    assert "session_id=session-1 writer=state status=completed turn_seq=0" in caplog.text
    assert "秘密の結果本文" not in caplog.text


_ALLOWED_CHANGED_AXES = {
    "send": {"start"},
    "start": {"start"},
    "lost": {"start"},
    "failure": {"end"},
    "complete": {"start", "end"},
    "item": {"item"},
    "clear-item": {"item"},
    "hold": {"hold"},
    "release": {"hold"},
    "reserve": {"effort"},
    "resume": {"effort"},
    "finish-resume": {"effort"},
    "overload": {"overload"},
    "reset-overload": {"overload"},
    "usage": {"usage"},
    "reset-usage": {"usage"},
    "next": {"start", "end", "hold", "item", "effort"},
}


_TURN_EDGES = {
    "start": {
        ("UNSENT", "SENT"),
        ("UNSENT", "STARTED"),
        ("SENT", "STARTED"),
        ("SENT", "AMBIGUOUS"),
        ("AMBIGUOUS", "STARTED"),
    },
    "end": {
        ("IN_PROGRESS", "FAILURE_PENDING"),
        ("IN_PROGRESS", "COMPLETED"),
        ("FAILURE_PENDING", "COMPLETED"),
    },
    "effort": {("none", "pending"), ("pending", "running"), ("pending", "none"), ("running", "none"), ("running", "pending")},
}


def _observed_axes(session: state.SessionState) -> dict[str, str | int | bool]:
    effort = "none"
    if session.effort_resuming:
        effort = "running"
    elif session.effort_resume_turn is not None:
        effort = "pending"
    return {
        "start": session.turn_start.name,
        "end": session.turn_end.name,
        "hold": session.hold is not None,
        "item": bool(session.active_tool_uses()),
        "effort": effort,
        "overload": session.overload_resume_count,
        "usage": session.usage_limit_wait_count,
    }


@given(st.lists(st.sampled_from(tuple(_ALLOWED_CHANGED_AXES)), min_size=1, max_size=35))
def test_generated_turn_operations_preserve_state_edges(operations: list[str]) -> None:
    session = state.SessionState("thread", "/project")
    actions = {
        "send": session.record_turn_sent,
        "start": session.record_turn_started,
        "lost": session.record_turn_ambiguous,
        "failure": session.record_failure_pending_completion,
        "item": lambda: session.record_current_item_start({"type": "commandExecution"}),
        "clear-item": lambda: session.record_current_item_start(None),
        "hold": lambda: session.hold_result(turn_state.BackgroundWait({"status": "completed"}, None)),
        "release": session.clear_hold,
        "reserve": lambda: session.reserve_effort_resume(session.turn_seq),
        "resume": session.begin_effort_resume,
        "finish-resume": session.finish_effort_resume,
        "overload": session.advance_overload_retry,
        "reset-overload": session.reset_overload_retry,
        "usage": session.advance_usage_limit_retry,
        "reset-usage": session.reset_usage_limit_retry,
        "next": lambda: state.begin_reply(session),
    }
    for operation in operations:
        before = _observed_axes(session)
        if operation == "complete":
            session.record_turn_completed()
            session.status = "completed"
        elif operation == "lost" and not session.turn_start_sent:
            pass
        elif operation != "failure" or not session.turn_completed:
            actions[operation]()
        after = _observed_axes(session)
        changed = {axis for axis in before if before[axis] != after[axis]}
        assert changed <= _ALLOWED_CHANGED_AXES[operation]
        if operation != "next":
            for axis in changed & _TURN_EDGES.keys():
                assert (before[axis], after[axis]) in _TURN_EDGES[axis]
        for axis in changed & {"overload", "usage"}:
            assert after[axis] in (0, int(before[axis]) + 1)
        if operation == "next":
            assert after["start"] == "UNSENT" and after["end"] == "IN_PROGRESS"
            assert after["effort"] != "pending" and not after["item"] and not after["hold"]
        elif operation in {"send", "lost"} and before["start"] == "STARTED":
            assert after["start"] == "STARTED"
        elif operation == "resume" and after["effort"] == "running":
            assert before["effort"] in {"pending", "running"} and session.turn_completed
        elif operation == "finish-resume" and before["effort"] == "running":
            assert after["effort"] == "none"
        assert (
            bool(session.active_tool_uses())
            == (session.current_item is not None)
            == (session.current_item_started_at is not None)
        )
        assert (session.overload_resume_count > 0) == (session.overload_first_at is not None)
        assert (session.usage_limit_wait_count > 0) == (session.usage_limit_first_at is not None)
        if (
            session.hold
            or session.turn_start_ambiguous
            or session.effort_resume_turn == session.turn_seq
            or session.effort_resuming
        ):
            assert not session.result_available
