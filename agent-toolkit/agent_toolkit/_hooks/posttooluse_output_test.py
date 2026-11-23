# pylint: disable=protected-access
"""posttooluse_outputの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import posttooluse
from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.hook_output_contract import validate_hook_output
from agent_toolkit._testing.posttooluse_support import (
    _assert_tool_specific_posttooluse_registration,
    _registered_posttooluse_names,
    _run,
    _shell_payload,
)


def test_hooks_json_routes_tool_specific_processing_to_posttooluse() -> None:
    """ツール別処理を持つ現行Claude入力群が登録から到達する。"""
    _assert_tool_specific_posttooluse_registration(_registered_posttooluse_names())


def test_posttooluse_registration_rejects_missing_webfetch() -> None:
    with pytest.raises(AssertionError):
        _assert_tool_specific_posttooluse_registration(_registered_posttooluse_names() - {"WebFetch", "*"})


@pytest.mark.parametrize("tool_name", ["Bash", "PowerShell"])
def test_persisted_shell_output_excerpt_is_replaced_by_unread_notice(tmp_path: pathlib.Path, tool_name: str) -> None:
    excerpt = "元の出力の先頭" * 100
    saved = str(tmp_path / "tool-results" / "b1.txt")
    payload = _shell_payload(
        tool_name, {"stdout": excerpt, "stderr": "警告", "persistedOutputPath": saved, "persistedOutputSize": 50000}
    )

    result = _run(payload, state_dir=tmp_path)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert validate_hook_output("PostToolUse", json.loads(result.stdout)) == []
    updated = output["updatedToolOutput"]
    assert excerpt not in updated["stdout"]
    assert saved in updated["stdout"] and "50000" in updated["stdout"]
    assert "全量取得" in updated["stdout"]
    assert "allow_large" not in updated["stdout"] and "file_path" not in updated["stdout"]
    assert {key: value for key, value in updated.items() if key != "stdout"} == {
        key: value for key, value in payload["tool_response"].items() if key != "stdout"
    }


@pytest.mark.parametrize(
    ("tool_name", "response", "event"),
    [
        pytest.param("Bash", {"stdout": "短い出力"}, "PostToolUse", id="not-persisted"),
        pytest.param("Bash", {"stdout": "x", "persistedOutputPath": ""}, "PostToolUse", id="empty-path"),
        pytest.param("Read", {"stdout": "x", "persistedOutputPath": "/tmp/x.txt"}, "PostToolUse", id="other-tool"),
        pytest.param("Bash", {"stdout": "x", "persistedOutputPath": "/tmp/x.txt"}, "PostToolUseFailure", id="failure"),
    ],
)
def test_shell_output_without_persisted_path_is_left_unchanged(
    tmp_path: pathlib.Path, tool_name: str, response: dict, event: str
) -> None:
    """退避していない出力、他のツールおよび失敗の結果では、ツール結果を置き換えない。"""
    payload = {**_shell_payload(tool_name, response), "hook_event_name": event}

    result = _run(payload, state_dir=tmp_path)

    assert result.returncode == 0, result.stderr
    assert "updatedToolOutput" not in result.stdout


@pytest.mark.parametrize("event", ["PostToolUse", "PostToolUseFailure", "PermissionDenied"])
@pytest.mark.parametrize("id_field", ["task_id", "shell_id"])
def test_registered_task_stop_consumes_only_successful_target(tmp_path: pathlib.Path, event: str, id_field: str) -> None:
    """登録の選択から停止対象の消費までを検証し、失敗・拒否と別対象を保持する。"""
    session_id = "task-stop-consume"
    assert session_state.update_state(
        session_id,
        lambda _previous: {"stall_detection_completed_at_by_task": {"task-1": 1.0, "task-2": 2.0}},
    )
    registered = _registered_posttooluse_names(event)
    if "*" in registered or "TaskStop" in registered:
        result = _run(
            {"hook_event_name": event, "session_id": session_id, "tool_name": "TaskStop", "tool_input": {id_field: "task-1"}},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
    expected = {"task-2": 2.0} if event == "PostToolUse" else {"task-1": 1.0, "task-2": 2.0}
    assert _read_state(tmp_path, session_id)["stall_detection_completed_at_by_task"] == expected


@pytest.mark.parametrize("tool_name", ["Agent", "Task"])
def test_agent_async_launch_records_agent_id(tmp_path: pathlib.Path, tool_name: str) -> None:
    session_id = f"agent-async-{tool_name.lower()}"
    result = _run(
        {
            "session_id": session_id,
            "tool_name": tool_name,
            "tool_input": {"subagent_type": "general-purpose", "prompt": "調査する", "run_in_background": True},
            "tool_response": {"isAsync": True, "status": "async_launched", "agentId": "a8d4542d5607ca48d"},
        },
        state_dir=tmp_path,
    )
    assert result.returncode == 0
    assert _read_state(tmp_path, session_id).get("background_task_ids") == ["a8d4542d5607ca48d"]


def test_agent_foreground_completion_records_nothing(tmp_path: pathlib.Path) -> None:
    session_id = "agent-foreground"
    result = _run(
        {
            "session_id": session_id,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "general-purpose", "prompt": "調査する"},
            "tool_response": {"status": "completed", "agentId": "a8d4542d5607ca48d", "content": []},
        },
        state_dir=tmp_path,
    )
    assert result.returncode == 0
    assert "background_task_ids" not in _read_state(tmp_path, session_id)


@pytest.mark.parametrize("agent_id", [None, ""], ids=["missing", "empty"])
def test_agent_launch_without_agent_id_records_nothing(tmp_path: pathlib.Path, agent_id: str | None) -> None:
    session_id = f"agent-no-id-{agent_id is None}"
    response: dict[str, object] = {"isAsync": True, "status": "async_launched"}
    if agent_id is not None:
        response["agentId"] = agent_id
    result = _run(
        {
            "session_id": session_id,
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "general-purpose", "prompt": "調査する"},
            "tool_response": response,
        },
        state_dir=tmp_path,
    )
    assert result.returncode == 0
    assert "background_task_ids" not in _read_state(tmp_path, session_id)


def test_record_failure_preserves_tool_result_recovery(monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise OSError("結果の記録に失敗")

    monkeypatch.setattr(posttooluse.termination_evidence, "observe_tool", fail)
    assert (
        posttooluse.main(
            json.dumps(
                {"session_id": "failure-evidence", "tool_name": "Read", "tool_input": {}, "tool_response": "取得した本文"}
            )
        )
        == 0
    )
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "結果の記録に失敗" in context
    assert "ツール結果" in context and "委譲元またはユーザー" in context
