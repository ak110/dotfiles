"""Codexのrpcを検証する。"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import codex_rpc, mcp_tools, session_errors, state
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._common import plugin_environment
from agent_toolkit._testing.codex_backend_support import _ChunkProcess, _DisconnectedStdin, _ignore_message, _SilentProcess

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


@pytest.mark.asyncio
async def test_json_rpc_write_failure_preserves_bounded_diagnostics() -> None:
    """接続断は到達段階、子PIDおよび直前stderrを例外とログへ残す。"""
    async with subject.JsonRpcProcess(_ignore_message, _ignore_message) as client:
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


@pytest.mark.asyncio
async def test_stdout_reader_frames_response_larger_than_stream_limit_and_following_notification() -> None:
    """transportのchunk長を超える1 recordと直後のrecordを、ともに既存の通知処理へ渡す。"""
    oversized = "x" * (subject.APP_SERVER_STREAM_LIMIT_BYTES + 1)
    response = json.dumps({"id": 7, "result": {"value": oversized}}, separators=(",", ":"))
    notification = json.dumps({"method": "test/notification", "params": {"value": "received"}}, separators=(",", ":"))
    notifications: list[dict[str, Any]] = []
    received = asyncio.Event()

    async def on_notification(message: dict[str, Any]) -> None:
        notifications.append(message)
        received.set()

    async with subject.JsonRpcProcess(on_notification, _ignore_message) as client:
        client.process = _ChunkProcess(f"{response}\n{notification}\n".encode())  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
        pending: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        client._pending[7] = pending
        reader = asyncio.create_task(client._read_stdout())

        result = await asyncio.wait_for(pending, timeout=2)
        await asyncio.wait_for(received.wait(), timeout=2)
        reader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reader

        assert result["result"]["value"] == oversized
        assert notifications == [{"method": "test/notification", "params": {"value": "received"}}]
        assert client.reader_failure is None


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.asyncio
async def test_client_start_aborts_when_initialize_never_answers(monkeypatch: pytest.MonkeyPatch, cancelled: bool) -> None:
    """initialize待ちの時間切れと取り消しの両方で、起動済み子プロセスを終了する。"""
    monkeypatch.setattr(state, "SESSION_INITIALIZATION_TIMEOUT", 0.05)
    process = _SilentProcess()

    async def _spawn(*args: Any, **kwargs: Any) -> _SilentProcess:
        del args, kwargs  # noqa
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    async with subject.JsonRpcProcess(_ignore_message, _ignore_message) as client:
        entered = asyncio.Event()
        original_request = client.request

        async def request(*args: Any, **kwargs: Any) -> dict[str, Any]:
            entered.set()
            return await original_request(*args, **kwargs)

        monkeypatch.setattr(client, "request", request)
        starting = asyncio.create_task(client.start())
        await asyncio.wait_for(entered.wait(), 2)
        if cancelled:
            starting.cancel()
        with pytest.raises(asyncio.CancelledError if cancelled else session_errors.SessionInitializationTimeoutError):
            await starting

        assert client.closed is True
        assert process.returncode == -15


@pytest.mark.asyncio
async def test_client_logs_process_start_initialize_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Codex子プロセスの起動、initialize応答および終了を親loggerへ記録する。"""
    process = _SilentProcess()
    messages: list[str] = []

    async def _spawn(*args: Any, **kwargs: Any) -> _SilentProcess:
        del args, kwargs  # noqa
        return process

    def record(message: str, *args: object) -> None:
        messages.append(message % args)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    monkeypatch.setattr(codex_rpc._LOG, "info", record)
    async with subject.JsonRpcProcess(_ignore_message, _ignore_message) as client:
        client.request = AsyncMock(return_value={"serverInfo": {"name": "codex"}})  # type: ignore[method-assign]
        client.notify = AsyncMock()  # type: ignore[method-assign]

        await client.start()
        await client.close()

        assert any("Codex App Serverを起動しました" in message for message in messages)
        assert any("initialize応答を受信しました: keys=['serverInfo']" in message for message in messages)
        assert any("Codex App Serverを終了しました: returncode=-15" in message for message in messages)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("user_project_environment", [False, True])
async def test_codex_json_rpc_process_passes_stable_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, user_project_environment: bool
) -> None:
    """App ServerへCLIとdataを渡し、plugin依存環境だけを除き、呼び出し元の設定と所有者を保つ。"""
    observed: dict[str, Any] = {}
    plugin_venv = tmp_path / "plugin venv"
    user_bin = tmp_path / "user-bin"
    runtime_data = tmp_path / "plugin data"
    original_path = os.pathsep.join((str(plugin_venv / "bin"), str(user_bin), ""))
    monkeypatch.setenv("PATH", original_path)
    monkeypatch.setenv("VIRTUAL_ENV", str(plugin_venv))
    monkeypatch.setenv(plugin_environment.RUNTIME_ENVIRONMENT_ENV, str(plugin_venv))
    project_environment = str(tmp_path / "user-project-env") if user_project_environment else str(plugin_venv)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", project_environment)
    monkeypatch.setenv(plugin_environment.RUNTIME_DATA_ENV, str(runtime_data))

    async def create_subprocess(*_args: Any, **kwargs: Any) -> Any:
        observed.update(kwargs)
        raise RuntimeError("capture complete")

    monkeypatch.setattr(subject.asyncio, "create_subprocess_exec", create_subprocess)
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    async with subject.JsonRpcProcess(
        lambda _message: asyncio.sleep(0), lambda _message: asyncio.sleep(0), root_session_id="owner-session"
    ) as client:
        with pytest.raises(RuntimeError, match="capture complete"):
            await client.start()
        environment = observed.pop("env")
        assert observed == {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "limit": subject.APP_SERVER_STREAM_LIMIT_BYTES,
            "cwd": subject.APP_SERVER_WORKING_DIRECTORY,
        }
        assert environment["AGENT_TOOLKIT_OWNER_SESSION"] == "owner-session"
        assert "AGENT_TOOLKIT_DELEGATED_SESSION" not in environment
        assert environment["PATH"].split(os.pathsep) == [str(plugin_roots.SERVER_PLUGIN_ROOT / "scripts"), str(user_bin), ""]
        assert environment[plugin_environment.RUNTIME_DATA_ENV] == str(runtime_data)
        assert "VIRTUAL_ENV" not in environment
        assert plugin_environment.RUNTIME_ENVIRONMENT_ENV not in environment
        if user_project_environment:
            assert environment["UV_PROJECT_ENVIRONMENT"] == project_environment
        else:
            assert "UV_PROJECT_ENVIRONMENT" not in environment
        assert os.environ["PATH"] == original_path
        assert observed["limit"] > 64 * 1024


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_app_server_inherits_prepared_conversation_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCPの共通整備後に起動するApp Serverへ起動元contextが届かず、明示ownerだけが届く。"""
    monkeypatch.setattr(subject.child_environment, "_INHERITED", {})
    for name in subject.mcp_environment._NON_INHERITABLE_NAMES:
        monkeypatch.setenv(name.upper(), "source-context")
    mcp_tools._prepare_child_environment()
    captured: dict[str, str] = {}

    async def spawn(*_args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs["env"])
        raise RuntimeError("環境観測完了")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    async with subject.JsonRpcProcess(_ignore_message, _ignore_message, root_session_id="selected-owner") as client:
        with pytest.raises(RuntimeError, match="環境観測完了"):
            await client.start()
        forbidden = subject.mcp_environment._NON_INHERITABLE_NAMES - {
            "agent_toolkit_owner_session",
            "agent_toolkit_delegated_plugin_root",
        }
        assert not forbidden.intersection(name.casefold() for name in captured)
        assert captured["AGENT_TOOLKIT_OWNER_SESSION"] == "selected-owner"
        assert captured["AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT"] == str(plugin_roots.SERVER_PLUGIN_ROOT)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_json_rpc_request_marks_sent_before_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON-RPC送信完了を応答待ちより先に呼出側へ通知する。"""
    async with subject.JsonRpcProcess(lambda _message: asyncio.sleep(0), lambda _message: asyncio.sleep(0)) as client:
        client.process = cast(Any, _SilentProcess())
        sent = asyncio.Event()

        async def send(_message: dict[str, Any]) -> None:
            return None

        monkeypatch.setattr(client, "_send", send)
        request = asyncio.create_task(client.request("turn/start", on_sent=sent.set))
        await asyncio.wait_for(sent.wait(), timeout=0.1)

        assert not request.done()
        assert len(client._pending) == 1

        request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        assert not client._pending
