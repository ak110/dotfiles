"""`_agents_server/mcp_transport.py`の振る舞いを検証する。"""

from __future__ import annotations

import contextlib

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import os
import pathlib
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import anyio
import claude_agent_sdk
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCRequest, JSONRPCResponse

import agent_toolkit.agents_server_mcp as entry_script
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import (
    logging_config,
    mcp_tools,
    mcp_transport,
    session_errors,
)
from agent_toolkit._common import process_tree, state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX, ActionableError

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@contextlib.asynccontextmanager
async def _stdio_client(tmp_path: pathlib.Path) -> AsyncGenerator[ClientSession]:
    """pluginと同じlocked起動を、専用の状態領域へ分離する。"""
    plugin_root = pathlib.Path(__file__).resolve().parents[2]
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"AGENT_TOOLKIT_OWNER_SESSION", "CLAUDE_CODE_SESSION_ID", "VIRTUAL_ENV"}
    }
    env.update(XDG_STATE_HOME=str(tmp_path), LOCALAPPDATA=str(tmp_path))
    # 分離HOMEではmiseのshimが親のtrust状態を読めないため、uv本体を選ぶ。
    search_path = os.pathsep.join(directory for directory in os.get_exec_path() if pathlib.Path(directory).name != "shims")
    command = shutil.which("uv", path=search_path)
    assert command is not None
    params = StdioServerParameters(
        command=command,
        args=[
            "run",
            "--project",
            str(plugin_root),
            "--locked",
            "--no-default-groups",
            str(plugin_root / "agent_toolkit" / "agents_server_mcp.py"),
        ],
        env=env,
    )
    with anyio.fail_after(20):
        async with stdio_client(params) as streams:
            read, write = streams
            async with ClientSession(read, write) as client:
                initialized = await client.initialize()
                assert initialized.protocol_version == "2025-11-25"
                yield client


@pytest.mark.asyncio
async def test_stdio_initializes_and_lists_tools(tmp_path: pathlib.Path) -> None:
    """公開stdioで登録schemaを取得でき、終了時の診断とManager.closeの完了がログへ残る。"""
    async with _stdio_client(tmp_path) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == {"start", "send_message", "kill", "stop", "list", "show"}
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
    assert "manager closeが完了しました" in log


@pytest.mark.asyncio
async def test_stdio_tool_results(tmp_path: pathlib.Path) -> None:
    """SDK経由の接続で、成功時は構造化された結果を返し、呼び出し側が修正できる入力の不備はエラーとして返す。"""
    async with _stdio_client(tmp_path) as client:
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
async def test_stdio_processing_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """ツール処理で起きた例外も、公開transportでは理由と次の操作を持つエラーとして返る。"""

    def fail_list(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("injected processing failure")

    monkeypatch.setattr(mcp_tools._MANAGER, "list_sessions", fail_list)
    client_write, server_read = anyio.create_memory_object_stream[SessionMessage | Exception](10)
    server_write, client_read = anyio.create_memory_object_stream[SessionMessage](10)

    @contextlib.asynccontextmanager
    async def transport() -> AsyncGenerator[Any]:
        async with server_read, server_write:
            yield server_read, server_write

    monkeypatch.setattr(mcp_transport, "stdio_server", transport)
    async with anyio.create_task_group() as group:
        group.start_soon(mcp_tools.mcp.run_stdio_async)
        async with client_read, client_write, ClientSession(client_read, client_write) as client:
            await client.initialize()
            result = await client.call_tool("list", {})
            assert result.is_error
            assert any(
                "injected processing failure" in part.text and NEXT_ACTION_PREFIX in part.text
                for part in result.content
                if part.type == "text"
            )


def test_main_persists_mcp_initialize_diagnostics(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """stdio起動でinitializeの受信、応答完了および失敗を永続ログへ記録する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    # SDK 2.3.0のJSON-RPC型から、受信と送信の公式構造を生成する。
    requests = [SessionMessage(JSONRPCRequest(jsonrpc="2.0", method="initialize", id=i)) for i in range(1, 5)]

    async def send(message: SessionMessage) -> None:
        if getattr(message.message, "id", None) == 3:
            raise OSError("write failed")

    @contextlib.asynccontextmanager
    async def transport() -> AsyncGenerator[Any]:
        yield SimpleNamespace(receive=AsyncMock(side_effect=requests)), SimpleNamespace(send=send)

    async def run(_server: Any, read: Any, write: Any, _options: Any) -> None:
        for i in range(1, 5):
            await read.receive()
            if i == 4:
                return
            response = (
                JSONRPCError(jsonrpc="2.0", id=i, error=ErrorData(code=-32602, message="invalid initialize"))
                if i == 2
                else JSONRPCResponse(jsonrpc="2.0", id=i, result={})
            )
            with contextlib.suppress(OSError):
                await write.send(SessionMessage(response))

    monkeypatch.setattr(mcp_transport, "stdio_server", transport)
    monkeypatch.setattr(mcp_transport.Server, "run", run)

    assert entry_script.main([]) == 0

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


_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]
_LAUNCH_TIMEOUT = 60.0


class _LaunchedServer:
    """起動ランチャーまたは変更前の起動形でagents_serverを起動し、JSON-RPCを1行ずつ送受信する。"""

    def __init__(
        self,
        tmp_path: pathlib.Path,
        host_environment: dict[str, str],
        *,
        direct: bool = False,
        arguments: tuple[str, ...] = (),
    ) -> None:
        # 起動用の依存環境と依存のキャッシュは実行ホストの値を使い、状態ディレクトリと一時領域だけを分離する。
        # 依存環境を分離すると、作業者ごとに数百MBのコピーを一時領域へ作るためである。
        # `UV_FROZEN`は起動形の`--locked`と重なるとuvが無視する旨の警告を標準エラーへ出力するため、CIのjob環境から引き継がない。
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
        search_path = os.pathsep.join(directory for directory in os.get_exec_path() if pathlib.Path(directory).name != "shims")
        env["PATH"] = search_path
        uv = shutil.which("uv", path=search_path)
        assert uv is not None
        target = str(_PLUGIN_ROOT / "agent_toolkit" / "agents_server_mcp.py")
        command = (
            [uv, "run", "--project", str(_PLUGIN_ROOT), "--locked", "--no-default-groups", target, *arguments]
            if direct
            else [sys.executable, str(_PLUGIN_ROOT / "scripts" / "launch.py"), "agent_toolkit/agents_server_mcp.py", *arguments]
        )
        self.state_dir = tmp_path / "agent-toolkit"
        self.temporary = tmp_path / "tmp"
        # テストを実行する端末のCtrl-Cが子へ届かないよう、独立したセッションで起動する。
        self._owner = contextlib.ExitStack()
        self._closed = False
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
        """子の停止、開始済みreaderのjoin、全パイプのcloseを全件実行する。"""
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

    def wait(self) -> int:
        """終了を待ち、読取threadを回収してから全パイプを閉じる。"""
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

    def await_event(self, name: str) -> None:
        """診断ファイルに指定のイベントが現れるまで、上限付きで待つ。"""
        deadline = time.monotonic() + _LAUNCH_TIMEOUT
        while not any(event["event"] == name for event in self.events()):
            assert time.monotonic() < deadline, f"診断に{name}が現れない"
            time.sleep(0.05)

    def server_pid(self) -> int:
        return next(event["pid"] for event in self.events() if event["event"] == "started")


def test_launcher_records_stdin_close_exit(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> None:
    """親が標準入力を閉じた正常終了を、サーバーとランチャーの双方が時刻付きで記録し、tools/listと終了コード0を保つ。"""
    with _LaunchedServer(tmp_path, host_environ()) as server:
        server.initialize()
        tools = server.request(2, "tools/list", {})["result"]["tools"]
        assert {tool["name"] for tool in tools} == {"start", "send_message", "kill", "stop", "list", "show"}
        assert server.process.stdin is not None
        server.process.stdin.close()

        assert server.wait() == 0
        events = server.events()
        names = [event["event"] for event in events]
        assert names[:2] == ["started", "bound"]
        assert {"stdin_closed", "exited", "process_exited"} <= set(names)
        assert next(event for event in events if event["event"] == "exited")["outcome"] == "normal"
        exited = next(event for event in events if event["event"] == "process_exited")
        assert exited["writer"] == "launcher" and exited["returncode"] == 0 and exited["signal"] is None
        assert all(event["at"] for event in events)
        assert not list(server.temporary.glob("agent-toolkit-agents-server-launch-*"))


def test_launcher_check_dependencies_exits_silently(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> None:
    """起動単位を持たない正常終了は、診断ファイルの所在を受け取れなかった旨を標準エラーへ出力しない。"""
    # ランチャーは子の標準エラーを中継する。依存環境を初めて作成するuvの進行表示を
    # 判定対象の標準エラーへ含めないよう、先に作成を済ませる。
    with _LaunchedServer(tmp_path / "warmup", host_environ(), arguments=("--check-dependencies",)) as warmup:
        assert warmup.wait() == 0, warmup.stderr
        with _LaunchedServer(tmp_path, host_environ(), arguments=("--check-dependencies",)) as server:
            assert server.wait() == 0
            assert server.stderr == b"", server.stderr
            assert not list((server.state_dir / logging_config.INSTANCE_DIRECTORY_NAME).glob("*.jsonl"))


@pytest.mark.skipif(os.name == "nt", reason="POSIXのシグナル転送と終了状態を検証する")
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
def test_launcher_preserves_signal_exit_status(
    tmp_path: pathlib.Path,
    signum: signal.Signals,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """ランチャーが受けたシグナルを記録し、変更前の起動形と同じ終了状態を呼出元へ返す。

    MCPホストと同じく、シグナルは起動したプロセスのpidへ送る（uvもpid宛てのSIGINT・SIGTERMを子へ転送する）。
    SIGINTを受けたサーバーは標準入力の読取が終わるまで停止しないため（変更前の起動形も同じ）、受信の記録を同期点として標準入力を閉じる。
    """

    def deliver(launched: _LaunchedServer) -> None:
        launched.process.send_signal(signum)
        if signum == signal.SIGINT:
            launched.await_event("signal_received")
            assert launched.process.stdin is not None
            launched.process.stdin.close()

    # 起動の待ち時間を重ねるため、対照とランチャーを同時に起動する。
    with (
        _LaunchedServer(tmp_path / "control", host_environ(), direct=True) as control,
        _LaunchedServer(tmp_path / "launcher", host_environ()) as server,
    ):
        for launched in (control, server):
            launched.initialize()
        for launched in (control, server):
            deliver(launched)

        assert server.wait() == control.wait()
        events = server.events()
        assert {"event": "signal_received", "signal": signum.name} in [
            {"event": event["event"], "signal": event.get("signal")} for event in events
        ]
        exited = next(event for event in events if event["event"] == "process_exited")
        assert [item["signal"] for item in exited["launcher_signals"]] == [signum.name]


@pytest.mark.skipif(os.name == "nt", reason="POSIXのSIGKILLを使う")
def test_launcher_records_server_kill(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> None:
    """サーバーだけが記録できずに終わった場合も、ランチャーが終了状態と標準エラー末尾を記録する。"""
    with _LaunchedServer(tmp_path, host_environ()) as server:
        server.initialize()
        os.kill(server.server_pid(), signal.SIGKILL)
        server.wait()

        names = [event["event"] for event in server.events()]
        assert "exited" not in names
        exited = next(event for event in server.events() if event["event"] == "process_exited")
        assert exited["returncode"] != 0
        assert "stderr_tail" in exited
        assert server.diagnostics_status() == "collected"


@pytest.mark.skipif(os.name == "nt", reason="POSIXのSIGKILLを使う")
def test_launcher_and_server_kill_is_incomplete(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> None:
    """ランチャーとサーバーがともに記録できずに終わった診断は`incomplete`として返し、終了状態を補わない。"""
    with _LaunchedServer(tmp_path, host_environ()) as server:
        server.initialize()
        server_pid = server.server_pid()
        # ランチャーを先に止め、サーバーの終了を観測する主体を残さない。サーバーが標準出力を保持するため、両方を止めてから待つ。
        server.process.kill()
        os.kill(server_pid, signal.SIGKILL)
        server.wait()
        deadline = time.monotonic() + _LAUNCH_TIMEOUT
        while time.monotonic() < deadline:
            try:
                os.kill(server_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)

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
        with original_scope([sys.executable, "-c", "import time; time.sleep(120)"], **kwargs) as process:
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
