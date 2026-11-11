# pylint: disable=protected-access
"""posttooluse_backgroundの契約テスト。"""

import pathlib

import pytest

from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.posttooluse_support import (
    _run,
    _TestAgentsServerSessionStateSupport,
)


class TestAgentsServerSessionState(_TestAgentsServerSessionStateSupport):
    """agents_serverのツール応答とsessionごとのcwd状態記録を検証する。"""

    def test_background_kill_notice_clears_pending_observation(self, tmp_path: pathlib.Path) -> None:
        """観測操作がバックグラウンドタスクへ移った通知でも、観測を試みた事実として未観測作業を解消する。"""
        operation = "kill"
        sid = "background-kill"
        remote_session_id = "remote-background-kill"
        self._start_pending_session(tmp_path, sid, remote_session_id)
        result = _run(
            {
                "session_id": sid,
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{operation}",
                "tool_input": {"session_id": remote_session_id},
                "tool_response": self._background_notice_response(operation),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["pending_observation"] is False
        # 移行通知は公開状態を伴わないため、`start`の応答で確定した値をそのまま保つ。
        assert record["status"] == "running"
        assert record["turn_id"] == "turn-1"

    @pytest.mark.parametrize("operation", ["start", "start_explore"])
    def test_background_notice_of_start_records_nothing(self, tmp_path: pathlib.Path, operation: str) -> None:
        """開始操作の移行通知は採番後のsession識別子を含まないため、記録を作成しない。"""
        sid = f"background-start-{operation}"
        result = _run(
            {
                "session_id": sid,
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{operation}",
                "tool_input": {"cwd": str(tmp_path), "prompt": "委譲する"},
                "tool_response": self._background_notice_response(operation),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert not _read_state(tmp_path, sid).get("agents_server_sessions", {})

    def test_background_notice_of_send_message_keeps_state(self, tmp_path: pathlib.Path) -> None:
        """配送成否を確定できない`send_message`の移行通知では、未観測作業の有無を変えない。"""
        sid = "background-send-message"
        remote_session_id = "remote-background-send-message"
        self._start_pending_session(tmp_path, sid, remote_session_id)
        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
                "tool_input": {"session_id": remote_session_id, "prompt": "続行する"},
                "tool_response": self._background_notice_response("send_message"),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["pending_observation"] is True

    def test_background_notice_does_not_create_unknown_session(self, tmp_path: pathlib.Path) -> None:
        """移行通知の対象sessionに記録が無い場合は、新規の記録を作成しない。"""
        sid = "background-unknown-session"
        known_session_id = "remote-known"
        self._start_pending_session(tmp_path, sid, known_session_id)
        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__kill",
                "tool_input": {"session_id": "remote-unknown"},
                "tool_response": self._background_notice_response("kill"),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        sessions = _read_state(tmp_path, sid)["agents_server_sessions"]
        assert "remote-unknown" not in sessions
        assert sessions[known_session_id]["pending_observation"] is True
