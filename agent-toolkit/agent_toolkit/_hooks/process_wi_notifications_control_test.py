import json

import pytest

from agent_toolkit._atk.wi import process_loop_control, process_loop_log
from agent_toolkit._common import process_loop_session, session_state
from agent_toolkit._hooks import posttooluse, process_wi_notifications

_SESSION = "running-circuit"


@pytest.fixture(name="circuit")
def _circuit(monkeypatch):
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", _SESSION)


@pytest.mark.parametrize("observe_cancel", [False, True])
@pytest.mark.usefixtures("circuit")
def test_abort_notifies_once_and_again_after_cancel(capsys, observe_cancel):
    payload = json.dumps({"session_id": _SESSION, "tool_name": "Read", "tool_input": {}, "tool_response": "本文"})
    process_loop_control.cmd_process_loop_abort()
    capsys.readouterr()
    assert posttooluse.main(payload) == 0
    notice = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "中断要求" in notice and "completion_report: true" in notice and "atk agents-exit-session" in notice
    assert process_loop_log.abort_path().exists()
    process_loop_control.cmd_process_loop_abort()
    assert process_wi_notifications.build_control_notice(_SESSION) is None
    process_loop_control.cmd_process_loop_abort_cancel()
    if observe_cancel:
        assert process_wi_notifications.build_control_notice(_SESSION) is None
    process_loop_control.cmd_process_loop_abort()
    capsys.readouterr()
    assert posttooluse.main(payload) == 0
    assert "中断要求" in capsys.readouterr().out
    assert process_wi_notifications.build_control_notice(_SESSION) is None


@pytest.mark.usefixtures("circuit")
def test_instructions_are_delivered_to_running_circuit_not_next_launcher():
    process_loop_log.append_instruction("現在の変更を先に検証する")
    process_loop_log.append_instruction("公開結果も回収する")
    notice = process_wi_notifications.build_control_notice(_SESSION) or ""
    assert "現在の変更を先に検証する" in notice and "公開結果も回収する" in notice
    assert not process_loop_log.read_instructions()
    with process_loop_log.launch_instructions() as instructions:
        assert not instructions
    assert process_wi_notifications.build_control_notice(_SESSION) is None
    process_loop_log.append_instruction("次の指示")
    assert "次の指示" in (process_wi_notifications.build_control_notice(_SESSION) or "")


@pytest.mark.usefixtures("circuit")
def test_instructions_locked_by_launcher_are_not_delivered_twice():
    process_loop_log.append_instruction("起動時に届ける指示")
    with process_loop_log.launch_instructions() as instructions:
        assert instructions == "起動時に届ける指示"
        assert process_wi_notifications.build_control_notice(_SESSION) is None
        assert process_loop_log.read_instructions() == [instructions]
    assert process_wi_notifications.build_control_notice(_SESSION) is None


@pytest.mark.parametrize("excluded", ["once", "delegated", "owner", "unidentified", "other-session", "direct"])
@pytest.mark.usefixtures("circuit")
def test_control_does_not_reach_excluded_conversations(monkeypatch, excluded):
    if excluded == "once":
        monkeypatch.setenv(process_loop_session.ONCE_ENV, "1")
    elif excluded == "delegated":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    elif excluded == "owner":
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "parent")
    elif excluded == "unidentified":
        monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID")
    elif excluded == "other-session":
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", "other-session")
    else:
        monkeypatch.delenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION")
        assert session_state.update_state(_SESSION, session_state.set_process_wi_invoked)
    process_loop_control.cmd_process_loop_abort()
    process_loop_log.append_instruction("対象外には届けない指示")
    assert process_wi_notifications.build_control_notice(_SESSION) is None
    assert process_loop_log.abort_path().exists()
    assert process_loop_log.read_instructions() == ["対象外には届けない指示"]


@pytest.mark.usefixtures("circuit")
def test_native_subagent_does_not_consume_control(capsys):
    process_loop_log.append_instruction("メインだけへ届ける指示")
    payload = json.dumps({"session_id": _SESSION, "agent_id": "child", "tool_name": "Read", "tool_input": {}})
    assert posttooluse.main(payload) == 0
    assert "メインだけへ届ける指示" not in capsys.readouterr().out
    assert process_loop_log.read_instructions() == ["メインだけへ届ける指示"]
