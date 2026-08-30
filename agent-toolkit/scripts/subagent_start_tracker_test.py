"""`SubagentStart`による`plan-impl-executor`追跡開始のテスト。"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

import _fork_runner
import pytest
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE

_SCRIPT = pathlib.Path(__file__).parent / "hook.py"


def _run(subcommand: str, payload: object, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({"TMPDIR": str(state_dir), "TEMP": str(state_dir), "TMP": str(state_dir)})
    return _fork_runner.run_script(
        _SCRIPT,
        argv=(subcommand,),
        input=json.dumps(payload, ensure_ascii=False),
        env=env,
    )


def _state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@pytest.mark.parametrize(
    "agent_type",
    [
        "plan-impl-executor",
        "agent-toolkit:plan-impl-executor",
        "feedbacks-planner",
        "agent-toolkit:feedbacks-planner",
    ],
)
def test_executor_names_are_registered(tmp_path: pathlib.Path, agent_type: str) -> None:
    payload = {
        "hook_event_name": "SubagentStart",
        "session_id": "sid",
        "agent_id": "agent-a",
        "agent_type": agent_type,
    }

    assert _run("subagent_start_tracker", payload, tmp_path).returncode == 0
    active = _state(tmp_path, "sid")["plan_impl_executor_active_subagent_sessions"]
    assert active["agent-a"]["subagent_type"] == agent_type


def test_duplicate_notification_is_idempotent_and_other_agents_are_preserved(tmp_path: pathlib.Path) -> None:
    first = {
        "hook_event_name": "SubagentStart",
        "session_id": "sid",
        "agent_id": "agent-a",
        "agent_type": "plan-impl-executor",
    }
    second = {**first, "agent_id": "agent-b"}

    _run("subagent_start_tracker", first, tmp_path)
    started_at = _state(tmp_path, "sid")["plan_impl_executor_active_subagent_sessions"]["agent-a"]["started_at"]
    _run("subagent_start_tracker", first, tmp_path)
    _run("subagent_start_tracker", second, tmp_path)

    active = _state(tmp_path, "sid")["plan_impl_executor_active_subagent_sessions"]
    assert set(active) == {"agent-a", "agent-b"}
    assert active["agent-a"]["started_at"] == started_at


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        {"hook_event_name": "Other", "session_id": "sid", "agent_id": "agent", "agent_type": "plan-impl-executor"},
        {"hook_event_name": "SubagentStart", "session_id": "", "agent_id": "agent", "agent_type": "plan-impl-executor"},
        {"hook_event_name": "SubagentStart", "session_id": "sid", "agent_id": "", "agent_type": "plan-impl-executor"},
        {"hook_event_name": "SubagentStart", "session_id": "sid", "agent_id": "agent", "agent_type": "other"},
    ],
)
def test_invalid_or_unrelated_payload_does_not_create_state(tmp_path: pathlib.Path, payload: object) -> None:
    result = _run("subagent_start_tracker", payload, tmp_path)
    assert result.returncode == 0
    assert not list(tmp_path.glob(SESSION_STATE_FILENAME_TEMPLATE.format(session_id="*")))


def test_first_nonempty_subagent_stop_passes_without_posttooluse(tmp_path: pathlib.Path) -> None:
    start = {
        "hook_event_name": "SubagentStart",
        "session_id": "sid",
        "agent_id": "agent-a",
        "agent_type": "plan-impl-executor",
    }
    _run("subagent_start_tracker", start, tmp_path)

    result = _run(
        "subagent_stop_advisor",
        {"session_id": "sid", "agent_id": "agent-a", "last_assistant_message": "完了報告"},
        tmp_path,
    )

    assert result.stdout == ""
