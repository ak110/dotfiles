"""Codex backendのsession状態遷移を検証する。"""

import asyncio
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


class _SilentClient:
    """要求を受理したまま応答を返さないApp Serverクライアントの検体。"""

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del method, params  # noqa
        await asyncio.Event().wait()
        return {}  # pragma: no cover - 待機が解けないことを表す到達不能の分岐


class _HangingStream:
    """行を返さないまま待機し続けるストリームの検体。"""

    async def readline(self) -> bytes:
        await asyncio.Event().wait()
        return b""  # pragma: no cover - 待機が解けないことを表す到達不能の分岐


class _AcceptingStdin:
    """書き込みを受理するだけのstdinの検体。"""

    def write(self, data: bytes) -> None:
        del data  # noqa

    async def drain(self) -> None:
        return None


class _SilentProcess:
    """起動後にJSON-RPC応答を返さない子プロセスの検体。"""

    def __init__(self) -> None:
        self.stdin = _AcceptingStdin()
        self.stdout = _HangingStream()
        self.stderr = _HangingStream()
        self.returncode: int | None = None

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


async def _ignore_message(message: dict[str, Any]) -> None:
    del message  # noqa


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


@pytest.mark.asyncio
async def test_client_start_aborts_when_initialize_never_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """initializeの応答が返らない接続を上限で打ち切り、子プロセスを終了する。"""
    monkeypatch.setattr(shared_state, "SESSION_INITIALIZATION_TIMEOUT", 0.05)
    process = _SilentProcess()

    async def _spawn(*args: Any, **kwargs: Any) -> _SilentProcess:
        del args, kwargs  # noqa
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    client = subject.JsonRpcProcess(_ignore_message, _ignore_message)

    with pytest.raises(shared_state.SessionInitializationTimeoutError):
        await client.start()

    assert client.closed is True
    assert process.returncode == -15


@pytest.mark.asyncio
async def test_start_aborts_when_thread_start_never_returns(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """thread/startの応答が返らない起動を上限で打ち切り、session状態を登録せずに例外で返す。"""
    monkeypatch.setattr(shared_state, "SESSION_INITIALIZATION_TIMEOUT", 0.05)
    manager = subject.AppServerManager()
    monkeypatch.setattr(manager, "_ensure_client", lambda: _return(_SilentClient()))

    with pytest.raises(shared_state.SessionInitializationTimeoutError):
        await manager.start("実装する", str(tmp_path))

    assert not manager.sessions
    await manager.close()


async def _return(value: Any) -> Any:
    return value


async def _return_none() -> None:
    return None
