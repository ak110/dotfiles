# pylint: disable=protected-access
"""posttooluse_sessionsの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import agents_server_observations as _agents_server_observations
from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.posttooluse_support import (
    _run,
    _state_path,
    _TestAgentsServerSessionStateSupport,
)


class TestAgentsServerSessionState(_TestAgentsServerSessionStateSupport):
    """agents_serverのツール応答とsessionごとのcwd状態記録を検証する。"""

    @pytest.mark.parametrize(
        ("tool_name", "tool_input", "response"),
        (
            (
                "start",
                {"cwd": "/repo"},
                {"session_id": "remote", "status": "running", "root_session_id": "root-session"},
            ),
            (
                "start_explore",
                {"cwd": "/repo"},
                {"session_id": "remote", "status": "running", "root_session_id": "root-session"},
            ),
            (
                "start_shell",
                {"cwd": "/repo"},
                {"session_id": "remote", "status": "running", "root_session_id": "root-session"},
            ),
            ("wait", {}, {"session_id": "remote", "status": "completed", "agent_message": "完了"}),
            ("send_message", {"session_id": "remote"}, {"delivery": "reply_started"}),
            ("kill", {"session_id": "remote"}, {"status": "interrupted", "kill_requested": True}),
            ("stop", {"session_id": "remote"}, {}),
            ("list", {}, {"sessions": [], "root_session_id": "root-session"}),
        ),
    )
    def test_reduced_success_responses_have_no_missing_fields(
        self,
        tool_name: str,
        tool_input: dict[str, object],
        response: dict[str, object],
    ) -> None:
        """8ツールが項目を減らした成功応答を返しても、欠落として報告しない。"""
        qualified_name = f"mcp__plugin_agent-toolkit_agents_server__{tool_name}"
        payload = {"tool_input": tool_input}
        assert not _agents_server_observations._agents_server_missing_response_fields(  # pylint: disable=protected-access
            "local-session", payload, response, qualified_name
        )

    @pytest.mark.parametrize(
        ("tool_name", "tool_input", "response", "next_action_names"),
        (
            ("list", {}, {"sessions": [], "omitted": 0}, ["`show`"]),
            ("start", {"cwd": "/repo"}, {"session_id": "remote", "status": "running"}, ["`atk agents wait`", "`kill`"]),
        ),
    )
    def test_missing_response_fields_warn_with_next_action(
        self,
        tmp_path: pathlib.Path,
        tool_name: str,
        tool_input: dict[str, object],
        response: dict[str, object],
        next_action_names: list[str],
    ) -> None:
        """必須項目を欠く応答は、影響と実在する操作を次の操作とする警告として返す。"""
        result = _run(
            {
                "session_id": f"missing-fields-{tool_name}",
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{tool_name}",
                "tool_input": tool_input,
                "tool_response": {"structuredContent": response},
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert 'kind="warn"' in context
        assert "root_session_id" in context
        next_action = context.split("\n次の操作: ", maxsplit=1)[1]
        assert all(name in next_action for name in next_action_names)

    def test_reduced_terminal_start_status_is_recorded(self, tmp_path: pathlib.Path) -> None:
        """全候補不可用のstartが返す終了statusをそのまま記録する。"""
        sid = "terminal-start"
        remote_session_id = "remote-terminal-start"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"cwd": str(tmp_path), "prompt": "委譲する"},
                "tool_response": {
                    "structuredContent": {
                        "session_id": remote_session_id,
                        "status": "failed",
                        "engine": "codex",
                        "model": "gpt-test",
                        "effort": "high",
                        "model_type": "execute",
                    }
                },
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["status"] == "failed"

    @pytest.mark.parametrize("tool_name", ("start", "start_explore", "start_write", "send_message", "kill"))
    def test_json_response_records_session_state(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        """JSON文字列形状の成功応答を状態記録へ反映する。"""
        sid = f"json-response-{tool_name}"
        remote_session_id = "thread-json"
        start_tools = ("start", "start_explore", "start_write")
        status = "running" if tool_name in (*start_tools, "send_message") else "interrupted"
        tool_input = {"cwd": str(tmp_path)} if tool_name in start_tools else {"session_id": remote_session_id}
        if tool_name == "send_message":
            tool_input["prompt"] = "続行"
        response: dict[str, object] = {}
        if tool_name in start_tools:
            response.update({"session_id": remote_session_id, "status": status})
        elif tool_name == "kill":
            response["status"] = status
        elif tool_name == "send_message":
            response["delivery"] = "reply_started"
        if tool_name == "kill":
            response["kill_requested"] = True
        if tool_name not in start_tools:
            _state_path(sid).write_text(
                json.dumps(
                    {
                        "agents_server_cwd_by_session": {remote_session_id: str(tmp_path)},
                        "agents_server_sessions": {
                            remote_session_id: {
                                "session_id": remote_session_id,
                                "status": "running",
                                "pending_observation": True,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
        result = _run(
            {
                "session_id": sid,
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{tool_name}",
                "tool_input": tool_input,
                "tool_response": json.dumps(response),
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state["agents_server_sessions"][remote_session_id]["status"] == status
        if tool_name == "kill":
            assert state["agents_server_sessions"][remote_session_id]["kill_requested"] is True
        assert "cwd" not in state["agents_server_sessions"][remote_session_id]
        assert state["agents_server_cwd_by_session"][remote_session_id] == str(tmp_path)

    @pytest.mark.parametrize("tool_name", ("start", "start_explore"))
    def test_start_stores_input_cwd_under_response_session_id(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        """開始ツールの入力cwdを応答session_idへ保存し、session記録へ複製しない。"""
        sid = f"{tool_name}-cwd"
        result = _run(
            {
                "session_id": sid,
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{tool_name}",
                "tool_input": {"prompt": "調査", "cwd": str(tmp_path)},
                "tool_response": {
                    "structuredContent": {
                        "session_id": "thread-start",
                        "turn_id": "turn-start",
                        "status": "running",
                    }
                },
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state["agents_server_cwd_by_session"] == {"thread-start": str(tmp_path)}
        assert "cwd" not in state["agents_server_sessions"]["thread-start"]
        assert state["agents_server_sessions"]["thread-start"]["owner_agent_id"] == "main"

    def test_show_records_live_child_session_cwds(self, tmp_path: pathlib.Path) -> None:
        """`show`の応答が返す子sessionの識別子と`cwd`の対を、続行判定が読むキーへ記録する。"""
        sid = "show-child-cwd"
        record = self._publish_owned_child(sid, tmp_path)
        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__show",
                "tool_input": {"session_id": "thread-parent"},
                "tool_response": {
                    "structuredContent": {
                        "session_id": "thread-parent",
                        "status": "running",
                        "live_child_sessions": [{"session_id": "thread-child", "cwd": str(tmp_path)}],
                        "live_child_session_ids_without_cwd": ["thread-unknown"],
                    }
                },
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state["agents_server_cwd_by_session"] == {"thread-child": str(tmp_path)}
        assert state["agents_server_sessions"] == {"thread-child": record}
