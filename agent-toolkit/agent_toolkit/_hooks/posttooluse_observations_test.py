# pylint: disable=protected-access
"""posttooluse_observationsの契約テスト。"""

import asyncio
import json
import pathlib

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.posttooluse_support import (
    _run,
    _state_path,
    _TestAgentsServerSessionStateSupport,
)


class TestAgentsServerSessionState(_TestAgentsServerSessionStateSupport):
    """agents_serverのツール応答とsessionごとのcwd状態記録を検証する。"""

    @pytest.mark.parametrize(
        "phrase",
        [
            "moved to the background as task {task_id} and keeps running",
            "Command timed out and is now running in the background. ID: {task_id}",
            "Command did not complete within its 15s timeout and was moved to the background (ID: {task_id}).",
        ],
    )
    def test_agents_server_response_quoting_notice_is_not_backgrounded(self, tmp_path: pathlib.Path, phrase: str) -> None:
        """委譲先の返却本文が移行通知の文言を引用しても、背景へ移ったとみなさず通常の応答と同じ記録を行う。"""
        sid = f"show-quoting-notice-{phrase.split()[0].lower()}-{len(phrase)}"
        record = self._publish_owned_child(sid, tmp_path)
        quoted = f"委譲先の返却: background_tasks_test.py:1: {phrase.format(task_id='quoted-task-1')}"
        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__show",
                "tool_input": {"session_id": "thread-parent"},
                "tool_response": {
                    "content": [{"type": "text", "text": quoted}],
                    "structuredContent": {
                        "session_id": "thread-parent",
                        "status": "running",
                        "last_message": quoted,
                        "live_child_sessions": [{"session_id": "thread-child", "cwd": str(tmp_path)}],
                    },
                },
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        state = _read_state(tmp_path, sid)
        assert state["agents_server_cwd_by_session"] == {"thread-child": str(tmp_path)}
        assert "quoted-task-1" not in (state.get("background_task_ids") or [])
        assert state["agents_server_sessions"] == {"thread-child": record}

    @pytest.mark.parametrize("tool_name", ("send_message", "kill", "stop"))
    def test_continuation_uses_cwd_map_without_mutating_it(self, tmp_path: pathlib.Path, tool_name: str) -> None:
        """継続・観測・中断ツールはcwd mapを参照し、session記録へcwdを保存しない。

        破棄ツールはsession記録を除去し、cwd mapは変更しない。
        """
        sid = f"continuation-cwd-{tool_name}"
        remote_session_id = "thread-continuation"
        state: dict[str, object] = {"agents_server_cwd_by_session": {remote_session_id: str(tmp_path)}}
        _state_path(sid).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tool_input: dict[str, object] = {"session_id": remote_session_id}
        if tool_name == "send_message":
            tool_input["prompt"] = "続行"
        response: dict[str, object] = {}
        if tool_name == "send_message":
            response["delivery"] = "reply_started"
        if tool_name == "kill":
            response.update({"status": "interrupted", "kill_requested": True})
        if tool_name in {"send_message", "stop"}:
            state["agents_server_sessions"] = {
                remote_session_id: {
                    "session_id": remote_session_id,
                    "status": "running",
                    "pending_observation": True,
                }
            }
            _state_path(sid).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        result = _run(
            {
                "session_id": sid,
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{tool_name}",
                "tool_input": tool_input,
                "tool_response": {"structuredContent": response},
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        current = _read_state(tmp_path, sid)
        assert current["agents_server_cwd_by_session"] == {remote_session_id: str(tmp_path)}
        if tool_name == "stop":
            assert remote_session_id not in current.get("agents_server_sessions", {})
        else:
            assert "cwd" not in current["agents_server_sessions"][remote_session_id]

    def test_stop_removes_session_record(self, tmp_path: pathlib.Path) -> None:
        """stop成功応答で対象sessionのエントリーを状態キーから除去する。"""
        sid = "pending-stop"
        remote_session_id = "remote-stop"
        state = {
            "agents_server_cwd_by_session": {remote_session_id: str(tmp_path)},
            "agents_server_sessions": {
                remote_session_id: {
                    "session_id": remote_session_id,
                    "status": "completed",
                    "pending_observation": True,
                }
            },
        }
        _state_path(sid).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        result = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__stop",
                "tool_input": {"session_id": remote_session_id},
                "tool_response": {"structuredContent": {}},
            },
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        current = _read_state(tmp_path, sid)
        assert remote_session_id not in current["agents_server_sessions"]
        assert current["agents_server_cwd_by_session"] == {remote_session_id: str(tmp_path)}

    def test_pending_observation_transitions(self, tmp_path: pathlib.Path) -> None:
        """公開操作の応答だけから未観測作業の発生と解消を記録する。"""

        def run_operation(
            sid: str,
            remote_session_id: str,
            operation: str,
            *,
            status: str,
            delivery: str | None = None,
        ) -> bool:
            tool_input: dict[str, object] = {"session_id": remote_session_id}
            if operation in {"start", "start_explore", "start_write"}:
                tool_input = {"cwd": str(tmp_path), "prompt": "委譲する"}
            elif operation == "send_message":
                tool_input["prompt"] = "続行する"
            response: dict[str, object]
            if operation in {"start", "start_explore", "start_write"}:
                response = {"session_id": remote_session_id, "status": status}
            elif operation == "kill":
                response = {"status": status}
            elif operation == "send_message":
                response = {"delivery": delivery}
            else:
                response = {}
            if operation == "kill":
                response["kill_requested"] = True
            result = _run(
                {
                    "session_id": sid,
                    "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{operation}",
                    "tool_input": tool_input,
                    "tool_response": {"structuredContent": response},
                },
                state_dir=tmp_path,
            )
            assert result.returncode == 0
            return _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]["pending_observation"]

        def run_wait(sid: str, remote_session_id: str, *, status: str = "running") -> None:
            result = _run(
                {
                    "session_id": sid,
                    "tool_name": "Bash",
                    "tool_input": {"command": "atk agents wait"},
                    "tool_response": json.dumps({"session_id": remote_session_id, "status": status}),
                },
                state_dir=tmp_path,
            )
            assert result.returncode == 0

        for operation in ("start", "start_explore", "start_write"):
            sid = f"pending-{operation}"
            assert run_operation(sid, f"remote-{operation}", operation, status="running") is True

        for status in ("running", "completed"):
            sid = f"pending-wait-{status}"
            remote_session_id = f"remote-wait-{status}"
            assert run_operation(sid, remote_session_id, "start", status="running") is True
            run_wait(sid, remote_session_id, status=status)
            assert _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]["pending_observation"] is False

        for delivery in ("reply_started", "reply_ambiguous"):
            sid = f"pending-send-{delivery}"
            remote_session_id = f"remote-send-{delivery}"
            assert run_operation(sid, remote_session_id, "start", status="running") is True
            run_wait(sid, remote_session_id)
            assert (
                run_operation(
                    sid,
                    remote_session_id,
                    "send_message",
                    status="running",
                    delivery=delivery,
                )
                is True
            )

        sid = "pending-send-steered"
        remote_session_id = "remote-send-steered"
        assert run_operation(sid, remote_session_id, "start", status="running") is True
        run_wait(sid, remote_session_id)
        assert (
            run_operation(
                sid,
                remote_session_id,
                "send_message",
                status="running",
                delivery="steered",
            )
            is False
        )

        sid = "pending-send-reply-failed"
        remote_session_id = "remote-send-reply-failed"
        assert run_operation(sid, remote_session_id, "start", status="running") is True
        run_wait(sid, remote_session_id, status="completed")
        assert (
            run_operation(
                sid,
                remote_session_id,
                "send_message",
                status="completed",
                delivery="reply_failed",
            )
            is False
        )

        sid = "pending-send-reply-failed-preserves-true"
        remote_session_id = "remote-send-reply-failed-preserves-true"
        assert run_operation(sid, remote_session_id, "start", status="running") is True
        assert (
            run_operation(
                sid,
                remote_session_id,
                "send_message",
                status="completed",
                delivery="reply_failed",
            )
            is True
        )

        sid = "pending-kill"
        remote_session_id = "remote-kill"
        assert run_operation(sid, remote_session_id, "start", status="running") is True
        assert run_operation(sid, remote_session_id, "kill", status="interrupted") is False

    @pytest.mark.asyncio
    async def test_expired_kill_clears_pending_observation(self, tmp_path: pathlib.Path) -> None:
        """期限切れsessionへのkill成功応答で未観測状態を解消する。"""
        sid = "pending-expired-kill"
        remote_session_id = "remote-expired-kill"
        started = _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"cwd": str(tmp_path), "prompt": "委譲する"},
                "tool_response": {"structuredContent": {"session_id": remote_session_id, "status": "running"}},
            },
            state_dir=tmp_path,
        )
        assert started.returncode == 0

        async with server_manager.AgentsServerManager() as manager:
            session = SessionState(remote_session_id, str(tmp_path), engine="codex")
            session.status = "completed"
            session.record_turn_completed()
            session.touch()
            session.retention_deadline = asyncio.get_running_loop().time() - 1
            manager.sessions[remote_session_id] = session
            response = await manager.kill(remote_session_id, timeout=0)
            killed = _run(
                {
                    "session_id": sid,
                    "tool_name": "mcp__plugin_agent-toolkit_agents_server__kill",
                    "tool_input": {"session_id": remote_session_id},
                    "tool_response": {"structuredContent": response},
                },
                state_dir=tmp_path,
            )
            assert killed.returncode == 0

            record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
            assert record["status"] == "expired"
            assert record["kill_requested"] is False
            assert record["pending_observation"] is False

    def test_start_shell_records_pending_observation(self, tmp_path: pathlib.Path) -> None:
        """シェル実行委譲も観測を試みていない作業として記録する。"""
        sid = "pending-shell"
        remote_session_id = "remote-shell"
        _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start_shell",
                "tool_input": {"cwd": str(tmp_path), "command": "make test", "summary_policy": "終了状態だけ"},
                "tool_response": {"structuredContent": {"session_id": remote_session_id, "status": "running"}},
            },
            state_dir=tmp_path,
        )
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["pending_observation"] is True
        assert record["owner_agent_id"] == "main"

    def test_pending_work_records_the_agent_that_triggered_it(self, tmp_path: pathlib.Path) -> None:
        """startと配送成立send_messageは、各作業を発生させた主体を観測責任者として記録する。"""
        sid = "pending-owner"
        remote_session_id = "remote-owner"
        _run(
            {
                "session_id": sid,
                "agent_id": "child-1",
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"cwd": str(tmp_path), "prompt": "委譲する"},
                "tool_response": {"structuredContent": {"session_id": remote_session_id, "status": "running"}},
            },
            state_dir=tmp_path,
        )
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["pending_observation"] is True
        assert record["owner_agent_id"] == "child-1"

        _run(
            {
                "session_id": sid,
                "tool_name": "Bash",
                "tool_input": {"command": "atk agents wait"},
                "tool_response": json.dumps({"session_id": remote_session_id, "status": "completed"}),
            },
            state_dir=tmp_path,
        )
        _run(
            {
                "session_id": sid,
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
                "tool_input": {"session_id": remote_session_id, "prompt": "続行する"},
                "tool_response": {
                    "structuredContent": {
                        "session_id": remote_session_id,
                        "status": "running",
                        "delivery": "reply_started",
                    }
                },
            },
            state_dir=tmp_path,
        )
        record = _read_state(tmp_path, sid)["agents_server_sessions"][remote_session_id]
        assert record["pending_observation"] is True
        assert record["owner_agent_id"] == "main"
