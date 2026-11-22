# pylint: disable=protected-access
"""posttooluse_process_wiの契約テスト。"""

import json
import os
import pathlib

import pytest

from agent_toolkit._common import session_state, skill_modes
from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.posttooluse_support import _run, _state_path


class TestAgentLaunchDoesNotEnterAutonomousMode:
    @pytest.mark.parametrize(
        ("skill", "arguments"),
        [
            ("agent-toolkit:atk-mode", "process-wi"),
            ("agent-toolkit:atk-mode", "process-wi all-repos"),
            ("atk-mode", "process-wi single-lane 追加"),
            (skill_modes.PROCESS_WI, None),
        ],
    )
    def test_skill_launch_keeps_flag_unset(self, tmp_path: pathlib.Path, skill: str, arguments: str | None) -> None:
        sid = f"agent-launch-{skill.replace(':', '-')}"
        tool_input = {"skill": skill} if arguments is None else {"skill": skill, "args": arguments}
        result = _run({"session_id": sid, "tool_name": "Skill", "tool_input": tool_input}, state_dir=tmp_path)
        assert result.returncode == 0, result.stderr
        assert not _read_state(tmp_path, sid).get("process_wi_skill_invoked")


class TestExitSessionResetsProcessAwisFlag:
    @staticmethod
    def _invoke(session_id: str, state_dir: pathlib.Path, command: str = "atk agents-exit-session") -> None:
        result = _run(
            {
                "session_id": session_id,
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_response": {"stdout": '{"exit_session_invoked":true,"status":"unsupported"}'},
            },
            state_dir=state_dir,
        )
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr, result.stderr

    def test_reset_when_exit_session_invoked(self, tmp_path: pathlib.Path) -> None:
        sid = "exit-cli"

        _state_path(sid).write_text(
            json.dumps(
                {
                    "process_wi_skill_invoked": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._invoke(sid, tmp_path)
        state = _read_state(tmp_path, sid)
        assert state.get("process_wi_skill_invoked") is False

    def test_no_rewrite_when_exit_and_reset_state_is_already_complete(self, tmp_path: pathlib.Path) -> None:
        sid = "exit-no-rewrite"
        path = session_state.state_path(sid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "process_wi_skill_invoked": False,
                    "marker": "keep",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        observed = _run({"session_id": sid, "tool_name": "Read", "tool_input": {}}, state_dir=tmp_path)
        assert observed.returncode == 0, observed.stderr
        assert "Traceback" not in observed.stderr, observed.stderr

        os.utime(path, ns=(0, 0))
        mtime_before = path.stat().st_mtime_ns
        self._invoke(sid, tmp_path)
        assert _read_state(tmp_path, sid)["marker"] == "keep"
        assert path.stat().st_mtime_ns == mtime_before

    @pytest.mark.parametrize(
        ("command", "recorded"),
        [
            ("atk agents-exit-session 2>&1", True),
            ("atk agents-exit-session 2>/dev/null", True),
            ("atk agents-exit-session --x", False),
        ],
    )
    def test_redirection_does_not_count_as_argument(self, tmp_path: pathlib.Path, command: str, recorded: bool) -> None:
        """リダイレクトだけを伴う起動は単独の起動として記録し、引数を伴う起動は記録しない。"""
        sid = "exit-redirect"
        _state_path(sid).write_text(json.dumps({"process_wi_skill_invoked": True}), encoding="utf-8")
        self._invoke(sid, tmp_path, command)
        assert (_read_state(tmp_path, sid).get("process_wi_skill_invoked") is False) is recorded


class TestProcessAwisInvokedNonIdempotent:
    def test_reset_then_agent_launch_keeps_flag_false(self, tmp_path: pathlib.Path) -> None:
        sid = "reinvoke"
        _state_path(sid).write_text(json.dumps({"process_wi_skill_invoked": True}, ensure_ascii=False), encoding="utf-8")
        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "atk agents-exit-session"},
                "tool_response": {"stdout": '{"exit_session_invoked":true,"status":"unsupported"}'},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("process_wi_skill_invoked") is False
        _run(
            {
                "session_id": sid,
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:atk-mode", "args": "process-wi"},
            },
            state_dir=tmp_path,
        )
        assert _read_state(tmp_path, sid).get("process_wi_skill_invoked") is False
