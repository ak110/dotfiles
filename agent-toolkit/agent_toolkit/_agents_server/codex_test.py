"""Codex backendのsession状態遷移を検証する。"""

import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import state as shared_state
from agent_toolkit._plan import locations as plan_file


class _ThreadStartClient:
    """thread/startの入力を保持して固定threadを返す検体。"""

    def __init__(self) -> None:
        self.params: dict[str, Any] | None = None

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "thread/start"
        self.params = params
        return {"thread": {"id": "inner-thread"}}


class _InspectableAppServerManager(subject.AppServerManager):
    """通知入力を検体へ公開する。"""

    async def handle_notification(self, message: dict[str, Any]) -> None:
        await self._handle_notification(message)


def _completed_turn(session: shared_state.SessionState) -> dict[str, Any]:
    return {
        "method": "turn/completed",
        "params": {
            "threadId": session.session_id,
            "turn": {
                "id": session.turn_id,
                "status": "completed",
                "error": None,
            },
        },
    }


@pytest.mark.asyncio
async def test_completed_turn_with_unobserved_child_is_published_immediately(tmp_path: pathlib.Path) -> None:
    """未観測の子sessionを記録し、Codexのturn終端結果を直ちに公開する。"""
    session = shared_state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.live_child_session_ids.add("child-1")
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_completed_turn(session))

    assert session.result_available is True
    assert session.status == "completed"
    assert session.awaiting_auto_resume is False
    assert session.error == {"unobservedSessions": ["child-1"]}
    assert session.live_child_session_ids == set()
    await manager.close()


@pytest.mark.asyncio
async def test_thread_start_overrides_agents_server_with_owner_and_writer_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """内側MCPサーバーへルートと書込主体を完全な定義で配送する。"""
    client = _ThreadStartClient()
    manager = subject.AppServerManager()
    monkeypatch.setattr(manager, "_ensure_client", lambda: _return(client))
    monkeypatch.setattr(plan_file, "resolve_owner_session_id", lambda: "root-session")
    monkeypatch.setattr(
        subject.status_file, "write_host_alias", lambda root, writer, thread: aliases.append((root, writer, thread))
    )
    aliases: list[tuple[str, str, str]] = []
    monkeypatch.setattr(manager, "_start_turn", lambda *_args: _return_none())

    await manager.start("実装する", str(tmp_path))

    assert client.params is not None
    server = client.params["config"]["mcp_servers"]["agents_server"]
    assert server["command"] == "uv"
    assert server["args"][-1].endswith("agent_toolkit/agents_server_mcp.py")
    assert set(server["env"]) == {"AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_STATUS_HOST_SESSION"}
    assert server["env"]["AGENT_TOOLKIT_OWNER_SESSION"] == "root-session"
    assert server["default_tools_approval_mode"] == "approve"
    assert "AGENT_TOOLKIT_DELEGATED_SESSION" not in server["env"]
    assert aliases == [("root-session", server["env"]["AGENT_TOOLKIT_STATUS_HOST_SESSION"], "inner-thread")]


async def _return(value: Any) -> Any:
    return value


async def _return_none() -> None:
    return None
