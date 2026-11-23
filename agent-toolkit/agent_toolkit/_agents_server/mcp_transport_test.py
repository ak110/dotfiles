"""`_agents_server/mcp_transport.py`の振る舞いを検証する。"""

from __future__ import annotations

import contextlib

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import os
import pathlib
import queue
import signal
import subprocess
import sys
import threading
from collections.abc import AsyncGenerator, Callable, Generator
from types import SimpleNamespace
from typing import Any

import anyio
import claude_agent_sdk
import psutil
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.message import SessionMessage
from mcp.types import CallToolRequestParams, CallToolResult, ErrorData, JSONRPCError, JSONRPCRequest, JSONRPCResponse

from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import (
    logging_config,
    mcp_tools,
    mcp_transport,
    service_transport,
    session_errors,
)
from agent_toolkit._common import process_identity, process_tree, runtime_environment, state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX, ActionableError
from agent_toolkit._testing import late_descendant
from agent_toolkit._testing.codex_plugin_support import _stop_mcp_services
from agent_toolkit._testing.plugin_environment_support import prepare_plugin_environment

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@contextlib.asynccontextmanager
async def _stdio_client(tmp_path: pathlib.Path, host_environment: dict[str, str]) -> AsyncGenerator[ClientSession]:
    plugin_root = pathlib.Path(__file__).resolve().parents[2]
    env = {
        key: value
        for key, value in host_environment.items()
        if key not in {"AGENT_TOOLKIT_OWNER_SESSION", "CLAUDE_CODE_SESSION_ID", "VIRTUAL_ENV"}
    }
    env.update(XDG_STATE_HOME=str(tmp_path), LOCALAPPDATA=str(tmp_path))

    search_path = os.pathsep.join(
        directory for directory in os.get_exec_path(host_environment) if pathlib.Path(directory).name != "shims"
    )
    env["PATH"] = search_path
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(plugin_root / "agent_toolkit" / "agents_server_mcp.py")],
        env=env,
    )
    service: psutil.Process | None = None
    supervisor: psutil.Process | None = None
    identity: process_identity.ProcessIdentity | None = None
    registry_dir = service_transport.namespace(runtime_environment.CallerContext(env, tmp_path))
    try:
        with anyio.fail_after(20):
            async with stdio_client(params) as streams:
                read, write = streams
                async with ClientSession(read, write) as client:
                    initialized = await client.initialize()
                    assert initialized.protocol_version == "2025-11-25"
                    path = registry_dir / "generations.json"
                    registry = json.loads(path.read_text(encoding="utf-8"))
                    target = registry["generations"][registry["current"]]
                    service = psutil.Process(int(target["process"].split(":", 1)[0]))
                    supervisor = service.parent()
                    assert supervisor is not None and str(path.parent) in supervisor.cmdline()
                    assert str(path.parent) in service.cmdline()
                    identity = process_identity.ProcessIdentity.capture(service)
                    yield client
    except BaseException:
        # CIの一時領域は終了後に消えるため、起動失敗の一次診断を試験の保存出力へ含める。
        for diagnostic in registry_dir.glob("*.stderr.log"):
            print(f"{diagnostic}:\n{diagnostic.read_text(encoding='utf-8', errors='replace')}", file=sys.stderr)
        raise
    finally:
        if service is not None and supervisor is not None and identity is not None:
            assert identity.matches(service)
            service.terminate()
            remaining = process_tree.wait_until_released([supervisor], timeout=20)
            if remaining:
                _stop_mcp_services(tmp_path)
                pytest.fail("service停止後もsupervisorが退出しない")
        else:
            _stop_mcp_services(tmp_path)


_EXPECTED_TOOLS = {
    "start",
    "collect_handoff",
    "send_message",
    "kill",
    "stop",
    "list",
    "show",
    "permissions",
    "send_to_user",
    "compact_conversation",
    "apply_runtime_changes",
}


@pytest.mark.asyncio
@pytest.mark.cpu_limit(
    2.5, reason="準備済みruntimeで実MCPのschemaを取得し、サービス・共有リスナーの回収まで確認する（Linux CI CPU合計1.084秒）。"
)
async def test_stdio_initializes_and_lists_tools(tmp_path: pathlib.Path, launcher_environment: dict[str, str]) -> None:
    async with _stdio_client(tmp_path, launcher_environment) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == _EXPECTED_TOOLS
        for tool in tools.values():
            assert tool.description
            assert tool.input_schema["type"] == "object"
            assert all(field.get("description") for field in tool.input_schema["properties"].values())
        schema = tools["send_message"].input_schema
        assert schema["required"] == ["session_id"]
        assert {"prompt", "instruction", "extra_params"} <= schema["properties"].keys()
    log = (tmp_path / "agent-toolkit" / "agents-server.log").read_text(encoding="utf-8")
    assert "MCP initializeを受信しました" in log
    assert "MCP initialize応答が完了しました" in log
    if os.name != "nt":
        assert "manager closeが完了しました" in log
    else:
        [path] = (tmp_path / "agent-toolkit" / logging_config.INSTANCE_DIRECTORY_NAME).glob("*.jsonl")
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert any(event["event"] == "process_exited" for event in events)
        reference = logging_config.diagnostics_reference(
            logging_config.InstanceReference(path.stem, 1), state_root=tmp_path / "agent-toolkit"
        )
        assert reference["status"] == "collected"


@pytest.mark.asyncio
@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "準備済みruntimeで実MCPの成功・失敗応答を取得し、サービス・共有リスナーの回収まで確認する。"
        "schema試験と同じ起動・回収境界（Linux CI CPU合計1.084秒）。"
    ),
)
async def test_stdio_tool_results(tmp_path: pathlib.Path, launcher_environment: dict[str, str]) -> None:
    async with _stdio_client(tmp_path, launcher_environment) as client:
        result = await client.call_tool("list", {})
        assert not result.is_error
        assert result.structured_content is not None
        assert result.structured_content["sessions"] == []
        for name, arguments in [("show", {"session_id": "missing"}), ("start", {})]:
            result = await client.call_tool(name, arguments)
            assert result.is_error
            body = "\n".join(part.text for part in result.content if part.type == "text")
            assert NEXT_ACTION_PREFIX in body


@pytest.mark.asyncio
async def test_registered_tool_processing_exception(monkeypatch: pytest.MonkeyPatch) -> None:

    def fail_list(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("injected processing failure")

    monkeypatch.setattr(mcp_tools._MANAGER, "list_sessions", fail_list)
    result = await mcp_tools.mcp._call_registered_tool(None, CallToolRequestParams(name="list", arguments={}))
    assert isinstance(result, CallToolResult) and result.is_error
    assert any(
        "injected processing failure" in part.text and NEXT_ACTION_PREFIX in part.text
        for part in result.content
        if part.type == "text"
    )


class _FailingSend:
    """id=3の応答だけ送信に失敗する送信先。"""

    async def send(self, item: SessionMessage) -> None:
        if getattr(item.message, "id", None) == 3:
            raise OSError("write failed")

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_initialization_streams_persist_initialize_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """共有管理サービスの接続と同じ組み立てで、initializeの受信、応答完了および失敗を永続ログへ記録する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    logging_config.configure_logging()
    # SDK 2.3.0のJSON-RPC型から、受信と送信の公式構造を生成する。
    sender, receiver = anyio.create_memory_object_stream[SessionMessage | Exception](4)
    for i in range(1, 5):
        sender.send_nowait(SessionMessage(JSONRPCRequest(jsonrpc="2.0", method="initialize", id=i)))
    tracker = mcp_transport._InitializationLogTracker()
    read = mcp_transport._InitializationLoggingReceiveStream(receiver, tracker)
    write = mcp_transport._InitializationLoggingSendStream(_FailingSend(), tracker)
    try:
        async with sender, receiver:
            for i in range(1, 5):
                await read.receive()
                if i == 4:
                    break
                response = (
                    JSONRPCError(jsonrpc="2.0", id=i, error=ErrorData(code=-32602, message="invalid initialize"))
                    if i == 2
                    else JSONRPCResponse(jsonrpc="2.0", id=i, result={})
                )
                with contextlib.suppress(OSError):
                    await write.send(SessionMessage(response))
    finally:
        tracker.transport_closed()
        logging_config.close_logging()

    content = (tmp_path / "agents-server.log").read_text(encoding="utf-8")
    assert "MCP initializeを受信しました: request_id=1" in content
    assert "MCP initialize応答が完了しました: request_id=1" in content
    assert "MCP initialize応答が失敗しました: request_id=2" in content
    assert "exception_type=ErrorData exception=invalid initialize" in content
    assert "MCP initialize応答の送信に失敗しました: request_id=3" in content
    assert "MCP initializeが未完了のままtransportが終了しました: request_id=4" in content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "operation"),
    [
        (ActionableError("理由の本文", next_action="`list`で保持状態を確かめる"), "`list`で保持状態を確かめる"),
        (session_errors.SessionInitializationTimeoutError("initialize timed out"), "atk agents logs"),
        (codex_backend.AppServerError("failed to start codex app-server"), "導入と認証"),
        (RuntimeError("unexpected failure"), "`show`"),
        (ValueError("unexpected value"), "受理形式"),
        (claude_agent_sdk.CLINotFoundError("Claude Code not found"), "導入と認証"),
    ],
    ids=["actionable", "initialization-timeout", "backend-start", "other", "plain-value-error", "claude-sdk-cli-missing"],
)
async def test_tool_error_body_carries_next_action(
    error: Exception,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCPツールのエラー本文は理由の後に`次の操作: `の行を持ち、失敗の種類に応じた操作を示す。

    MCPServerは例外の`str()`をエラー本文へ使うため、共通の例外型の次の操作も想定外の例外の案内も、
    登録の共通層が加えない限り委譲元へ届かない。
    """

    def raise_error(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise error

    async def keep_registry(_session_id: str) -> None:
        return None

    monkeypatch.setattr(
        mcp_tools, "_MANAGER", SimpleNamespace(take_over_orphaned_session=keep_registry, show_session=raise_error)
    )

    with pytest.raises(ToolError) as raised:
        await mcp_tools.mcp.call_tool("show", {"session_id": "3468feae-b2bf-4d67-ac55-3c40207e8b5b"})

    body = str(raised.value)
    reason, next_action = body.split(f"\n{NEXT_ACTION_PREFIX}", 1)
    assert reason.endswith(str(error))
    assert operation in next_action


def test_candidate_unavailable_next_action_follows_its_cause() -> None:
    """候補の不成立が原因に応じた次の操作を持つ場合はその操作を返し、利用上限で認証の確認へ誘導しない。"""
    waiting = "解除予定まで待つか、`model_type`へ別のengineの候補を指定して起動し直す"
    limited = codex_backend.StartCandidateUnavailable("利用上限で通常利用できない", next_action=waiting)
    assert mcp_transport._unexpected_error_next_action(limited) == waiting  # pylint: disable=protected-access
    plain = codex_backend.StartCandidateUnavailable("candidateDependencyUnavailable")
    assert "導入と認証" in mcp_transport._unexpected_error_next_action(plain)  # pylint: disable=protected-access


_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]
_LAUNCH_TIMEOUT = 60.0


@pytest.fixture(scope="session", name="launcher_runtime")
def _launcher_runtime(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    return tmp_path_factory.mktemp("launcher-runtime")


@pytest.fixture(name="launcher_environment")
def _launcher_environment(launcher_runtime: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> dict[str, str]:
    environment = host_environ()
    environment["AGENT_TOOLKIT_RUNTIME_DATA"] = str(launcher_runtime)
    environment.pop("UV_FROZEN", None)
    prepare_plugin_environment(_PLUGIN_ROOT, environment)
    return environment


class _LaunchedServer:
    def __init__(
        self,
        tmp_path: pathlib.Path,
        host_environment: dict[str, str],
        *,
        direct: bool = False,
        arguments: tuple[str, ...] = (),
    ) -> None:
        # 準備済み依存環境は呼出側の一時runtimeを使い、依存取得のcacheは実行ホストと共有する。
        # `UV_FROZEN`は起動ランチャーが実行するlock固定の依存同期と重なるため、CIのjob環境から引き継がない。
        env = {
            key: value
            for key, value in host_environment.items()
            if key not in {"AGENT_TOOLKIT_OWNER_SESSION", "CLAUDE_CODE_SESSION_ID", "VIRTUAL_ENV", "CLAUDECODE", "UV_FROZEN"}
        }
        env.update(
            XDG_STATE_HOME=str(tmp_path),
            LOCALAPPDATA=str(tmp_path),
            TMPDIR=str(tmp_path / "tmp"),
        )
        (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)
        search_path = os.pathsep.join(
            directory for directory in os.get_exec_path(host_environment) if pathlib.Path(directory).name != "shims"
        )
        env["PATH"] = search_path
        target = str(_PLUGIN_ROOT / "agent_toolkit" / "agents_server_mcp.py")
        command = (
            [sys.executable, target, *arguments]
            if direct
            else [sys.executable, str(_PLUGIN_ROOT / "scripts" / "launch.py"), "agent_toolkit/agents_server_mcp.py", *arguments]
        )
        self.state_dir = tmp_path / "agent-toolkit"
        self._state_base = tmp_path
        self.temporary = tmp_path / "tmp"

        self._owner = contextlib.ExitStack()
        self._closed = False
        self._services: list[tuple[psutil.Process, process_identity.ProcessIdentity, list[str]]] = []
        self._lines: queue.Queue[bytes] = queue.Queue()
        self.stderr = b""
        self._readers: list[threading.Thread] = []
        self.process = self._owner.enter_context(
            process_tree.process_scope(
                command,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=os.name != "nt",
                close_streams=False,
            )
        )
        try:
            for target in (self._read_stdout, self._drain_stderr):
                reader = threading.Thread(target=target, daemon=True)
                self._readers.append(reader)
                reader.start()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> _LaunchedServer:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.ExitStack() as cleanup:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None:
                    cleanup.callback(stream.close)
            for reader in self._readers:
                if reader.ident is not None:
                    cleanup.callback(self._join_reader, reader)
            self._owner.close()
            if not self._services:
                _stop_mcp_services(self._state_base)

            for process, identity, arguments in self._services:
                try:
                    if not process.is_running() or process.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                        continue
                    if not identity.matches(process):
                        assert not process.is_running() or process.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
                        continue
                    command = process.cmdline()
                    if not command:
                        assert not process_tree.wait_until_released([process], timeout=_LAUNCH_TIMEOUT)
                        continue
                    assert command == arguments
                    result = process_tree.stop_tree(process)
                    assert not result.residual and not result.unresolved, result.descriptions()
                except psutil.NoSuchProcess, psutil.ZombieProcess:
                    continue

    @staticmethod
    def _join_reader(reader: threading.Thread) -> None:
        reader.join(timeout=_LAUNCH_TIMEOUT)
        assert not reader.is_alive(), "サーバーの読取threadが終了しませんでした"

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._lines.put(line)

    def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        self.stderr = self.process.stderr.read()

    def request(self, request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            payload = json.loads(self._lines.get(timeout=_LAUNCH_TIMEOUT))
            if payload.get("id") == request_id:
                return payload

    def send(self, payload: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload).encode() + b"\n")
        self.process.stdin.flush()

    def initialize(self) -> None:
        """initializeの応答を同期点とし、シグナルの記録handlerの設置後に操作を始める。"""
        response = self.request(
            1,
            "initialize",
            {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        )
        assert "result" in response
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        listed = self.request(10, "tools/call", {"name": "list", "arguments": {}})
        assert "result" in listed and not listed["result"].get("isError", False)
        [path] = list(self.state_dir.rglob("generations.json"))
        registry = json.loads(path.read_text(encoding="utf-8"))
        target = registry["generations"][registry["current"]]
        process = psutil.Process(int(target["process"].split(":", 1)[0]))
        parent = process.parent()
        assert parent is not None
        for member in (process, parent):
            arguments = member.cmdline()
            assert str(path.parent) in arguments
            self._services.append((member, process_identity.ProcessIdentity.capture(member), arguments))

    def service_process(self) -> psutil.Process:
        process, identity, arguments = self._services[0]
        assert identity.matches(process) and process.cmdline() == arguments
        return process

    def supervisor_process(self) -> psutil.Process:
        process, identity, arguments = self._services[1]
        assert identity.matches(process) and process.cmdline() == arguments
        return process

    def wait(self) -> int:
        returncode = self.process.wait(timeout=_LAUNCH_TIMEOUT)
        self.close()
        return returncode

    def events(self) -> list[dict[str, Any]]:
        [path] = (self.state_dir / logging_config.INSTANCE_DIRECTORY_NAME).glob("*.jsonl")
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def diagnostics_status(self) -> str:
        [path] = (self.state_dir / logging_config.INSTANCE_DIRECTORY_NAME).glob("*.jsonl")
        reference = logging_config.diagnostics_reference(
            logging_config.InstanceReference(path.stem, 1), state_root=self.state_dir
        )
        return reference["status"]

    def server_pid(self) -> int:
        return next(event["pid"] for event in self.events() if event["event"] == "started")


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実MCPのinitialize/tools-listとstdin終了を通し、両主体の終了記録と一時資源回収を検証する（Linux "
        "CI CPU合計最大0.862秒）。"
    ),
)
def test_launcher_records_stdin_close_exit(tmp_path: pathlib.Path, launcher_environment: dict[str, str]) -> None:
    with _LaunchedServer(tmp_path, launcher_environment) as server:
        server.initialize()
        tools = server.request(2, "tools/list", {})["result"]["tools"]
        assert {tool["name"] for tool in tools} == _EXPECTED_TOOLS
        assert server.process.stdin is not None
        server.process.stdin.close()
        assert server.process.wait(timeout=_LAUNCH_TIMEOUT) == 0
        assert server.service_process().is_running()
        # POSIXはgraceful停止、Windowsは強制停止の結果をsupervisorに観測させる。
        supervisor = server.supervisor_process()
        server.service_process().terminate()
        assert not process_tree.wait_until_released([supervisor], timeout=_LAUNCH_TIMEOUT)
        assert server.wait() == 0
        events = server.events()
        names = [event["event"] for event in events]
        assert names[0] == "started" and "bound" in names
        exited = next(event for event in events if event["event"] == "process_exited")
        assert exited["writer"] == "launcher"
        if os.name != "nt":
            assert next(event for event in events if event["event"] == "exited")["outcome"] == "normal"
            assert exited["returncode"] == 0 and exited["signal"] is None
            log = (server.state_dir / "agents-server.log").read_text(encoding="utf-8")
            assert "manager closeが完了しました" in log
        else:
            assert "exited" not in names
            assert exited["returncode"] != 0 and "stderr_tail" in exited
            assert server.diagnostics_status() == "collected"
        assert all(event["at"] for event in events)
        assert not list(server.temporary.glob("agent-toolkit-agents-server-launch-*"))


@pytest.mark.cpu_limit(
    2.0,
    reason=("実launcherの依存確認をwarmupと確認の2起動で比較し、両起動の資源回収まで検証する（Linux CI CPU合計最大0.852秒）。"),
)
def test_launcher_check_dependencies_exits_silently(tmp_path: pathlib.Path, launcher_environment: dict[str, str]) -> None:

    with _LaunchedServer(tmp_path / "warmup", launcher_environment, arguments=("--check-dependencies",)) as warmup:
        assert warmup.wait() == 0, warmup.stderr
        with _LaunchedServer(tmp_path, launcher_environment, arguments=("--check-dependencies",)) as server:
            assert server.wait() == 0
            assert server.stderr == b"", server.stderr
            assert not list((server.state_dir / logging_config.INSTANCE_DIRECTORY_NAME).glob("*.jsonl"))


@pytest.mark.skipif(os.name == "nt", reason="POSIXのシグナル転送と終了状態を検証する")
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
@pytest.mark.cpu_limit(
    3.0,
    reason=(
        "実旧入口とlauncherへSIGTERM/SIGINTを送り、公開終了状態を同じ入力で比較する必要がある（Linux CI CPU合計最大1.353秒）。"
    ),
)
def test_launcher_preserves_signal_exit_status(
    tmp_path: pathlib.Path,
    signum: signal.Signals,
    launcher_environment: dict[str, str],
) -> None:

    def deliver(launched: _LaunchedServer) -> None:
        launched.process.send_signal(signum)

    # 起動の待ち時間を重ねるため、対照とランチャーを同時に起動する。
    with (
        _LaunchedServer(tmp_path / "control", launcher_environment, direct=True) as control,
        _LaunchedServer(tmp_path / "launcher", launcher_environment) as server,
    ):
        for launched in (control, server):
            launched.initialize()
        for launched in (control, server):
            deliver(launched)

        expected = server.process.wait(timeout=_LAUNCH_TIMEOUT)
        assert expected == control.process.wait(timeout=_LAUNCH_TIMEOUT)
        assert expected == 128 + signum
        assert server.service_process().is_running() and control.service_process().is_running()
        assert server.wait() == control.wait()
        events = server.events()
        exited = next(event for event in events if event["event"] == "process_exited")
        assert exited["returncode"] == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIXのSIGKILLを使う")
@pytest.mark.cpu_limit(
    3.0,
    reason=("実launcher/MCPのserverをSIGKILLし、終了ログとreader回収を検証する（Linux CI CPU合計最大1.339秒）。"),
)
def test_launcher_records_server_kill(tmp_path: pathlib.Path, launcher_environment: dict[str, str]) -> None:
    with _LaunchedServer(tmp_path, launcher_environment) as server:
        server.initialize()
        server.service_process().kill()
        assert server.process.stdin is not None
        server.process.stdin.close()
        server.wait()

        names = [event["event"] for event in server.events()]
        assert "exited" not in names
        exited = next(event for event in server.events() if event["event"] == "process_exited")
        assert exited["returncode"] != 0
        assert "stderr_tail" in exited
        assert server.diagnostics_status() == "collected"


@pytest.mark.skipif(os.name == "nt", reason="POSIXのSIGKILLを使う")
@pytest.mark.cpu_limit(
    2.25,
    reason=("実launcherとMCPを起動しSIGKILL後の終了記録と子孫回収を確認する境界（Linux CI CPU合計最大0.926秒）。"),
)
def test_launcher_and_server_kill_is_incomplete(tmp_path: pathlib.Path, launcher_environment: dict[str, str]) -> None:
    with _LaunchedServer(tmp_path, launcher_environment) as server:
        server.initialize()
        server_process = server.service_process()

        supervisor = server.supervisor_process()
        supervisor.kill()
        assert not process_tree.wait_until_released([supervisor], timeout=5)
        server_process.kill()
        assert server.process.stdin is not None
        server.process.stdin.close()
        server.wait()

        assert not process_tree.wait_until_released([server_process], timeout=_LAUNCH_TIMEOUT)

        names = {event["event"] for event in server.events()}
        assert not names & logging_config.TERMINATION_EVENTS
        assert server.diagnostics_status() == "incomplete"


@pytest.mark.parametrize("failure", ["start-reader", "body", "wait"])
def test_launched_server_reclaims_resources_on_failure(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """reader開始途中、本文、待機の失敗でも獲得済み子・thread・全pipeを回収する。"""
    original_scope = process_tree.process_scope
    original_start = threading.Thread.start
    processes: list[subprocess.Popen] = []
    readers: list[threading.Thread] = []

    @contextlib.contextmanager
    def sleeping_process(_command: list[str], **kwargs: Any) -> Generator[subprocess.Popen]:
        with original_scope(
            [sys.executable, "-c", late_descendant.held_source(tmp_path / "transport.release")], **kwargs
        ) as process:
            processes.append(process)
            yield process

    def start(reader: threading.Thread) -> None:
        readers.append(reader)
        if failure == "start-reader" and len(readers) == 2:
            raise RuntimeError("reader開始失敗")
        original_start(reader)

    monkeypatch.setattr(process_tree, "process_scope", sleeping_process)
    monkeypatch.setattr(threading.Thread, "start", start)
    expected = subprocess.TimeoutExpired if failure == "wait" else RuntimeError
    with pytest.raises(expected), _LaunchedServer(tmp_path, host_environ()) as server:
        if failure == "wait":
            original_wait = server.process.wait

            def wait(timeout: float | None = None) -> int:
                if timeout == _LAUNCH_TIMEOUT:
                    raise subprocess.TimeoutExpired(server.process.args, _LAUNCH_TIMEOUT)
                return original_wait(timeout=timeout)

            monkeypatch.setattr(server.process, "wait", wait)
            server.wait()
        raise RuntimeError("本文失敗")

    assert len(processes) == 1 and processes[0].poll() is not None
    assert all(not reader.is_alive() for reader in readers)
    assert all(
        stream is not None and stream.closed for stream in (processes[0].stdin, processes[0].stdout, processes[0].stderr)
    )
