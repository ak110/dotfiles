"""Codex backendのsession状態遷移を検証する。"""

# テストではloggerを含む内部境界を直接検証する。
# pylint: disable=protected-access

import asyncio
import pathlib
import shutil
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import state as shared_state
from agent_toolkit._plan import locations as plan_file


class _ThreadStartClient:
    """thread/startの入力を保持して固定threadを返すスタブ。"""

    def __init__(self) -> None:
        self.params: dict[str, Any] | None = None

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "thread/start"
        self.params = params
        return {"thread": {"id": "inner-thread"}}


class _SilentClient:
    """要求を受理したまま応答を返さないApp Serverクライアントのスタブ。"""

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del method, params  # noqa
        await asyncio.Event().wait()
        return {}  # pragma: no cover - 待機が解けないことを表す到達不能の分岐


class _HangingStream:
    """行を返さないまま待機し続けるストリームのスタブ。"""

    async def readline(self) -> bytes:
        await asyncio.Event().wait()
        return b""  # pragma: no cover - 待機が解けないことを表す到達不能の分岐


class _AcceptingStdin:
    """書き込みを受理するだけのstdinのスタブ。"""

    def write(self, data: bytes) -> None:
        del data  # noqa

    async def drain(self) -> None:
        return None


class _DisconnectedStdin:
    """書込時に接続断を返すstdinのスタブ。"""

    def write(self, data: bytes) -> None:
        del data  # noqa
        raise BrokenPipeError("peer disconnected")

    async def drain(self) -> None:
        return None


class _SilentProcess:
    """起動後にJSON-RPC応答を返さない子プロセスのスタブ。"""

    def __init__(self) -> None:
        self.stdin = _AcceptingStdin()
        self.stdout = _HangingStream()
        self.stderr = _HangingStream()
        self.returncode: int | None = None
        self.pid = 123

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


@pytest.mark.asyncio
async def test_json_rpc_write_failure_preserves_bounded_diagnostics() -> None:
    """接続断は到達段階、子PID及び直前stderrを例外とログへ残す。"""
    client = subject.JsonRpcProcess(_ignore_message, _ignore_message)
    process = _SilentProcess()
    process.__dict__["stdin"] = _DisconnectedStdin()
    # 実プロセスの代わりにテスト用の代替オブジェクトを割り当てるため、静的な型判定の対象から外す。
    client.process = process  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
    client._initialization_stage = "initialized_sent"
    client._stderr_text = "network unavailable"

    with pytest.raises(subject.AppServerError) as exc_info:
        await client.send({"method": "test"})

    message = str(exc_info.value)
    assert "peer disconnected" in message
    assert "initialized_sent" in message
    assert "child_pid" in message
    assert "network unavailable" in message


async def _ignore_message(message: dict[str, Any]) -> None:
    del message  # noqa


class _InspectableAppServerManager(subject.AppServerManager):
    """通知入力をテストへ公開する。"""

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
async def test_client_logs_process_start_initialize_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex子プロセスの起動、initialize応答及び終了を親loggerへ記録する。"""
    process = _SilentProcess()
    messages: list[str] = []

    async def _spawn(*args: Any, **kwargs: Any) -> _SilentProcess:
        del args, kwargs  # noqa
        return process

    def record(message: str, *args: object) -> None:
        messages.append(message % args)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(subject._LOG, "info", record)
    client = subject.JsonRpcProcess(_ignore_message, _ignore_message)
    client.request = AsyncMock(return_value={"serverInfo": {"name": "codex"}})  # type: ignore[method-assign]
    client.notify = AsyncMock()  # type: ignore[method-assign]

    await client.start()
    await client.close()

    assert any("Codex App Serverを起動しました" in message for message in messages)
    assert any("initialize応答を受信しました: keys=['serverInfo']" in message for message in messages)
    assert any("Codex App Serverを終了しました: returncode=-15" in message for message in messages)


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


def _make_plugin_root(base: pathlib.Path, version: str, *, versioned: bool) -> pathlib.Path:
    """`plugin.json`を持つ配布物rootを、版別ディレクトリの有無を変えて作成する。"""
    root = base / version / "agent-toolkit" if versioned else base / "checkout" / "agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(f'{{"version": "{version}"}}\n', encoding="utf-8")
    (root / "agent_toolkit").mkdir()
    (root / "agent_toolkit" / "agents_server_mcp.py").write_text("# entry\n", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _isolate_stable_plugin_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """複製先の記録と管理対象一時領域の作成先をテストごとに分離する。"""
    monkeypatch.setattr(subject, "_stable_plugin_roots", {})
    monkeypatch.setattr(subject._managed_temp, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "managed-temp"))
    (tmp_path / "managed-temp").mkdir()


def test_versioned_plugin_root_is_copied_to_a_stable_location(tmp_path: pathlib.Path) -> None:
    """版別ディレクトリ配下の配布物rootは、複製元を失っても解決できる実体へ写す。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True)

    resolved = subject.resolve_stable_plugin_root(source)

    assert resolved != source
    shutil.rmtree(source)
    assert (resolved / "agent_toolkit" / "agents_server_mcp.py").is_file()
    assert not (resolved / ".venv").exists()


def test_versioned_plugin_root_is_copied_once_per_source(tmp_path: pathlib.Path) -> None:
    """同じ複製元への解決を繰り返しても複製先は変わらない。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True)

    first = subject.resolve_stable_plugin_root(source)
    second = subject.resolve_stable_plugin_root(source)

    assert first == second
    assert first != source
    assert [entry.name for entry in first.parent.parent.iterdir() if entry.is_dir()] == [first.parent.name]


def test_plain_plugin_root_is_used_as_is(tmp_path: pathlib.Path) -> None:
    """版別ディレクトリに当たらない配布物rootは複製せずそのまま使う。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=False)

    assert subject.resolve_stable_plugin_root(source) == source


def test_copy_failure_falls_back_to_the_resolved_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """複製に失敗しても例外を伝播させず、解決したrootで委譲の起動を成立させる。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True)

    def _fail(*_args: Any, **_kwargs: Any) -> pathlib.Path:
        raise subject._managed_temp.ManagedTempError("一時領域を作成できない")

    monkeypatch.setattr(subject._managed_temp, "create_managed_temp", _fail)

    assert subject.resolve_stable_plugin_root(source) == source


def test_agents_server_config_uses_the_stable_plugin_root() -> None:
    """内側MCPサーバーの起動コマンドは、安定した配布物rootの実体だけを指す。"""
    config = subject.AppServerManager._agents_server_config("owner", "writer")

    args = config["mcp_servers"]["agents_server"]["args"]
    expected_root = subject.resolve_stable_plugin_root()
    assert args[1] == "--project"
    assert args[2] == str(expected_root)
    assert args[-1] == str(expected_root / "agent_toolkit" / "agents_server_mcp.py")
    assert pathlib.Path(args[-1]).is_file()


async def _return(value: Any) -> Any:
    return value


async def _return_none() -> None:
    return None
