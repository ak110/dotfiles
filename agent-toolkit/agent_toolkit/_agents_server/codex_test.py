"""Codex backendのsession状態遷移を検証する。"""

# テストではloggerを含む内部境界を直接検証する。
# pylint: disable=protected-access

import asyncio
import contextlib

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import psutil
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agent_toolkit import atk
from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import (
    notice_inbox,
    resume_waits,
    session_errors,
    shared_roots,
    state,
)
from agent_toolkit._agents_server import (
    plugin_root as plugin_roots,
)
from agent_toolkit._agents_server.notify import send_notification
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import inherited_venv, plugin_environment, session_registry, state_paths
from agent_toolkit._testing.agents_server_support import (
    BlockingResumeCodexClient,
    CompletingInterruptClient,
    FakeCodexClient,
    HoldingTurnStartClient,
    InterruptErrorClient,
    LostTurnStartClient,
    NaturallyCompletingTurnStartClient,
    ServerRequestClient,
    SteerRaceClient,
    _actionable_message,
    _assert_no_forbidden_keys,
    _complete,
    _pending_codex_manager,
    _start_blocked_codex_resume,
    _without_root,
    install_backend,
)
from agent_toolkit._testing.helpers import delivery_payload
from agent_toolkit._testing.isolation import CONFIG_DIRECTORY_ENVIRONMENT_NAMES, HOME_ENVIRONMENT_NAMES
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules


class _ThreadStartClient:
    """thread/startの入力を保持して固定threadを返すスタブ。"""

    def __init__(self) -> None:
        self.params: dict[str, Any] | None = None

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "thread/start"
        self.params = params
        return {"thread": {"id": "inner-thread"}}


class _TierClient:
    """実際のbackendが送る要求と送信通知を再現し、モデルの実行を伴わずtierを確かめる。"""

    closed = False
    reader_failure = None

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.parent_environment: dict[str, str] | None = None

    async def request(self, method: str, params: dict[str, Any], *, on_sent: Any = None) -> dict[str, Any]:
        if method == "config/read":
            return {"config": {"mcp_servers": {"agents_server": {}, "external": {"enabled": True}}}}
        self.requests.append((method, dict(params)))
        if on_sent is not None:
            on_sent()
        if method in {"thread/start", "thread/resume"}:
            return {"thread": {"id": params.get("threadId", "tier-thread")}}
        assert method == "turn/start"
        return {"turn": {"id": f"turn-{len(self.requests)}"}}


class _ProcessTierClient(_TierClient):
    """App Serverの子プロセスを持つ偽クライアント。"""

    def __init__(self, pid: int) -> None:
        super().__init__()
        self.process = SimpleNamespace(pid=pid)


class _SandboxProbeClient:
    """公開command/exec応答を模し、接続の回収を観測する。"""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.process = None
        self.closed = False
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.response: dict[str, Any] = {"exitCode": 0, "stderr": "", "stdout": ""}
        self.error: BaseException | None = None

    async def start(self) -> None:
        return None

    async def request(self, method: str, params: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        assert method == "command/exec"
        self.requests.append((method, params))
        if self.error is not None:
            raise self.error
        return self.response

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_start_and_resume_record_owner_and_app_server_processes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新規起動と再開の登録簿の記録は、所有プロセスとturnを実行するApp Serverの識別を持つ。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(subject._atk_config, "_config_dir", lambda: tmp_path / "config")
    owner = session_registry.owner_process()
    assert owner is not None
    expected = [{"pid": owner.pid, "create_time": owner.create_time}] * 2
    record = session_registry.registry_directory() / "tier-thread.json"
    client = _ProcessTierClient(owner.pid)
    manager = subject.AppServerManager({}, asyncio.Condition(), publish_registry=True)
    monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))

    await manager.start("開始", str(tmp_path))
    assert json.loads(record.read_text(encoding="utf-8"))["processes"] == expected

    record.unlink()
    restarted = subject.AppServerManager({}, asyncio.Condition(), publish_registry=True)
    monkeypatch.setattr(restarted, "_ensure_client", AsyncMock(return_value=client))
    await restarted.resume("tier-thread", state.ResumePrompt("再開"), str(tmp_path))
    assert json.loads(record.read_text(encoding="utf-8"))["processes"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [True, False])
async def test_config_changes_apply_to_new_reply_and_restored_turns(
    initial: bool, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開configの変更を3種類のRPCの要求へ明示し、実際に送った最新turnの速度を保持する。"""
    monkeypatch.setattr(subject._atk_config, "_config_dir", lambda: tmp_path / "config")
    monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODE", raising=False)

    def set_fast(enabled: bool) -> None:
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "set", "codex_fast_mode", str(enabled).lower()], home=tmp_path)
        assert not capsys.readouterr().err

    set_fast(initial)
    client = _TierClient()
    manager = subject.AppServerManager({}, asyncio.Condition(), publish_registry=True)
    monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))
    session = await manager.start("開始", str(tmp_path), "gpt-6-sol", "medium")
    tier = "priority" if initial else "default"
    assert [method for method, _params in client.requests] == ["thread/start", "turn/start"]
    assert all(params["serviceTier"] == tier for _method, params in client.requests)
    assert session.fast_mode is initial
    session.status = "completed"
    session.turn_completed = True
    session.touch()
    set_fast(not initial)
    client.requests.clear()
    reply = await manager.send_message(session, "続行")
    assert reply["delivery"] == "reply_started"
    assert all(params["serviceTier"] == ("default" if initial else "priority") for _method, params in client.requests)
    assert session.fast_mode is (not initial)

    set_fast(initial)
    restored_client = _TierClient()
    restarted = subject.AppServerManager({}, asyncio.Condition())
    monkeypatch.setattr(restarted, "_ensure_client", AsyncMock(return_value=restored_client))
    restored = await restarted.resume(
        session.session_id,
        state.ResumePrompt("再開"),
        str(tmp_path),
        session.model,
        session.effort,
        turn_seq=session.turn_seq,
        fast_mode=session.fast_mode,
    )
    assert [method for method, _params in restored_client.requests] == ["thread/resume", "turn/start"]
    assert all(params["serviceTier"] == tier for _method, params in restored_client.requests)
    assert restored.fast_mode is initial


class _SilentClient:
    """要求を受理したまま応答を返さないApp Serverクライアントのスタブ。"""

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        del method, params  # noqa
        await asyncio.Event().wait()
        return {}  # pragma: no cover - 待機が解けないことを表す到達不能の分岐


class _HangingStream:
    """行を返さないまま待機し続けるストリームのスタブ。"""

    async def read(self, limit: int) -> bytes:
        assert limit > 0
        await asyncio.Event().wait()
        return b""  # pragma: no cover - 待機が解けないことを表す到達不能の分岐

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


class _ChunkStream:
    """有限byte列を指定長以下のchunkで返し、返却後は接続を保つ。"""

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.offset = 0

    async def read(self, limit: int) -> bytes:
        assert limit > 0
        if self.offset < len(self.content):
            end = min(self.offset + limit, len(self.content))
            chunk = self.content[self.offset : end]
            self.offset = end
            return chunk
        await asyncio.Event().wait()
        return b""  # pragma: no cover - 接続を保つための到達不能の分岐


class _ChunkProcess:
    """stdoutだけをchunk readerへ渡す子プロセスのスタブ。"""

    def __init__(self, content: bytes) -> None:
        self.stdout = _ChunkStream(content)
        self.returncode: int | None = None


@pytest.mark.asyncio
async def test_json_rpc_write_failure_preserves_bounded_diagnostics() -> None:
    """接続断は到達段階、子PIDおよび直前stderrを例外とログへ残す。"""
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

    client = subject.JsonRpcProcess(on_notification, _ignore_message)
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


async def _ignore_message(message: dict[str, Any]) -> None:
    del message  # noqa


class _InspectableAppServerManager(subject.AppServerManager):
    """通知入力をテストへ公開する。"""

    async def handle_notification(self, message: dict[str, Any]) -> None:
        await self._handle_notification(message)


def _completed_turn(session: state.SessionState) -> dict[str, Any]:
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


def _item_started(session: state.SessionState, item_type: str) -> dict[str, Any]:
    return {
        "method": "item/started",
        "params": {
            "threadId": session.session_id,
            "turnId": session.turn_id,
            "item": {"id": f"item-{item_type}", "type": item_type},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("output_type", ["reasoning", "agentMessage"])
async def test_model_output_is_observed_only_after_non_input_item(tmp_path: pathlib.Path, output_type: str) -> None:
    """入力の記録である`userMessage`ではモデル出力を観測済みにせず、モデル由来のitemで観測済みにする。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_item_started(session, "userMessage"))
    after_input = session.model_output_observed
    await manager.handle_notification(_item_started(session, output_type))

    assert not after_input
    assert session.model_output_observed


@pytest.mark.asyncio
async def test_completed_turn_with_live_child_holds_result_for_auto_resume(tmp_path: pathlib.Path) -> None:
    """未観測の子sessionが残るturnは、待機表明を完了報告として公開せず、自動再開まで結果を保留する。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.live_child_session_ids.add("child-1")
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_completed_turn(session))

    assert session.result_available is False
    assert session.awaiting_auto_resume is True
    assert session.status == "running"
    assert session.pending_result == {"status": "completed", "agent_message": "", "error": None}
    assert session.error is None
    assert session.live_child_session_ids == {"child-1"}
    await manager.close()


@pytest.mark.asyncio
async def test_completed_turn_after_consumed_auto_resume_publishes_unobserved_child(tmp_path: pathlib.Path) -> None:
    """そのturnで自動再開を消費済みなら保留せず、残る子sessionを未観測として記録して直ちに公開する。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.live_child_session_ids.add("child-1")
    session.auto_resume_consumed = True
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_completed_turn(session))

    assert session.result_available is True
    assert session.status == "completed"
    assert session.awaiting_auto_resume is False
    assert session.error == {"unobservedSessions": ["child-1"]}
    assert session.live_child_session_ids == set()
    await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("turn_status", ["completed", "failed", "interrupted"])
async def test_send_message_child_reactivation_holds_parent_until_child_terminates(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, turn_status: str
) -> None:
    """再開した子が残る親の完了・失敗・中断をshowで保留し、子の終端後の再開結果をwaitへ公開する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    manager = server_manager.AgentsServerManager(None)
    backend = subject.AppServerManager(manager.sessions, manager._condition)
    install_backend(manager, "codex", backend)
    client = FakeCodexClient()
    backend.client = cast(Any, client)
    monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
    session = await backend.start("親の作業", str(tmp_path))
    session.terminal_child_session_ids.add("child")
    session.auto_resume_consumed = True
    session_registry.publish("child", terminal=False, cwd=str(tmp_path))
    item = {
        "type": "mcpToolCall",
        "id": "reply-child",
        "server": "agents_server",
        "tool": "send_message",
        "arguments": {"session_id": "child", "prompt": "残作業を続ける"},
        "result": {"structuredContent": {"session_id": "child", "delivery": "reply_started", "status": "running"}},
    }
    try:
        await backend._handle_notification(
            {"method": "item/completed", "params": {"threadId": session.session_id, "item": item}}
        )
        completion = _completed_turn(session)
        completion["params"]["turn"].update(
            {
                "status": turn_status,
                "error": {"codexErrorInfo": "usageLimitExceeded"} if turn_status == "failed" else None,
                # turn/completedが同じtool結果を再掲するApp Serverの形式も通す。
                "items": [item],
            }
        )
        await backend._handle_notification(completion)
        shown = manager.show_session(session.session_id)
        assert shown["status"] == "running" and shown["result_held"]
        assert shown["live_child_sessions"] == [{"session_id": "child", "cwd": str(tmp_path)}]
        assert not session.result_available
        session_registry.publish("child", terminal=True, status="completed", cwd=str(tmp_path))
        pending = await manager.wait()
        assert pending["status"] in {"starting", "running"}
        assert session.auto_resume_consumed
        await backend._handle_notification(_completed_turn(session))
        result = await manager.wait()
        assert result["status"] == "completed"
        assert not session.awaiting_auto_resume
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "delivery",
    (
        "stdout",
        "output-file",
        "shell-stdout",
        "shell-output-file",
        "absolute-stdout",
        "absolute-shell",
        "shell-unrelated",
        "failure",
        "unrelated",
    ),
)
async def test_cli_wait_updates_observed_child_sessions(tmp_path: pathlib.Path, delivery: str) -> None:
    """CLI待機が回収した子だけを観測済みにし、失敗時は未観測として残す。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.live_child_session_ids.add("child-1")
    manager = _InspectableAppServerManager({session.session_id: session})
    result = json.dumps({"session_id": "child-1", "status": "completed"}) + "\n"
    if delivery in {"output-file", "shell-output-file"}:
        output_path = tmp_path / "wait-results.jsonl"
        output_path.write_text(result, encoding="utf-8")
        output = f"保存先: {output_path}\n"
    else:
        output = result
    command = "atk agents wait"
    if delivery in {"output-file", "shell-output-file"}:
        command = "timeout 300 atk agents wait"
    if delivery == "unrelated":
        command = "printf 'unrelated command'"
    if delivery == "shell-stdout":
        command = '/bin/bash -lc "atk agents wait"'
    if delivery == "shell-output-file":
        command = '/bin/sh -c "timeout 300 atk agents wait"'
    if delivery == "shell-unrelated":
        command = '/bin/bash -lc "printf unrelated"'
    if delivery in {"absolute-stdout", "absolute-shell"}:
        command = shlex.quote(str(plugin_roots.SERVER_PLUGIN_ROOT / "scripts" / "atk")) + " agents wait"
        if delivery == "absolute-shell":
            command = "/bin/sh -c " + shlex.quote(command)
    item = {
        "type": "commandExecution",
        "id": "wait-command",
        "command": command,
        "aggregatedOutput": output,
        "exitCode": 1 if delivery == "failure" else 0,
    }

    await manager.handle_notification(
        {"method": "item/completed", "params": {"threadId": session.session_id, "turnId": session.turn_id, "item": item}}
    )
    await manager.handle_notification(_completed_turn(session))

    # 回収できなかった子は追跡に残り、自動再開まで結果を保留する。回収済みなら保留せず直ちに公開する。
    unobserved = delivery in {"failure", "unrelated", "shell-unrelated"}
    assert session.awaiting_auto_resume is unobserved
    assert session.result_available is not unobserved
    assert session.live_child_session_ids == ({"child-1"} if unobserved else set())
    assert session.error is None
    await manager.close()


@pytest.mark.asyncio
async def test_thread_start_overrides_agents_server_with_owner_and_writer_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """内側MCPサーバーへルートと書込主体を完全な定義で配送する。"""
    client = _ThreadStartClient()
    runtime_data = tmp_path / "plugin data"
    monkeypatch.setenv(plugin_environment.RUNTIME_DATA_ENV, str(runtime_data))
    manager = subject.AppServerManager(root_session_id="root-session")
    monkeypatch.setattr(manager, "_ensure_client", lambda: _return(client))
    monkeypatch.setattr(shared_roots, "write_host_alias", lambda root, writer, thread: aliases.append((root, writer, thread)))
    aliases: list[tuple[str, str, str]] = []
    monkeypatch.setattr(manager, "_start_turn", lambda *_args: _return_none())

    await manager.start("実装する", str(tmp_path))

    assert client.params is not None
    server = client.params["config"]["mcp_servers"]["agents_server"]
    assert server["command"] == "uv"
    assert server["args"][-1].endswith("agent_toolkit/agents_server_mcp.py")
    assert set(server["env"]) == {
        "AGENT_TOOLKIT_OWNER_SESSION",
        "AGENT_TOOLKIT_STATUS_HOST_SESSION",
        plugin_environment.RUNTIME_DATA_ENV,
    }
    assert server["env"][plugin_environment.RUNTIME_DATA_ENV] == str(runtime_data)
    assert server["env"]["AGENT_TOOLKIT_OWNER_SESSION"] == "root-session"
    assert server["default_tools_approval_mode"] == "approve"
    parent_environment = dict(os.environ)
    inherited_venv.strip_inherited_venv(parent_environment)
    assert server["env_vars"] == list(subject.mcp_environment.runtime_environment_variable_names(parent_environment))
    assert "AGENT_TOOLKIT_DELEGATED_SESSION" not in server["env"]
    assert aliases == [("root-session", server["env"]["AGENT_TOOLKIT_STATUS_HOST_SESSION"], "inner-thread")]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "resume", "reply"])
@pytest.mark.parametrize("configured", [False, True])
async def test_mcp_mise_namespace_names_and_explicit_owner_are_separate(
    operation: str, configured: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """開始・追送・保存会話の再開で親の任意名を配送し、起動contextは明示値を優先する。"""
    runtime_data = tmp_path / "plugin data"
    monkeypatch.setenv(plugin_environment.RUNTIME_DATA_ENV, str(runtime_data))
    names = ("MISE_STATE_DIR", "MISE_CACHE_DIR", "MISE_DATA_DIR", "MISE_CONFIG_DIR")
    values = {name: str(tmp_path / name.lower()) for name in names}
    for name, value in values.items():
        if configured:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MISE_UNPUBLISHED_DIR", "unpublished-location")
    monkeypatch.setenv("DIAGNOSTIC_TOKEN", "synthetic-credential")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "caller-environment")
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", "parent-plugin-data")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "ambient-root")
    monkeypatch.setenv("AGENT_TOOLKIT_STATUS_HOST_SESSION", "ambient-host")
    client = _TierClient()
    manager = subject.AppServerManager(root_session_id="chosen-root")
    monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))
    aliases: list[tuple[str, str, str]] = []
    monkeypatch.setattr(shared_roots, "write_host_alias", lambda root, writer, thread: aliases.append((root, writer, thread)))
    try:
        if operation in {"start", "reply"}:
            session = await manager.start("開始", str(tmp_path))
            if operation == "reply":
                _complete(session)
                monkeypatch.delenv("MISE_UNPUBLISHED_DIR")
                monkeypatch.setenv("NEW_REPLY_API_KEY", "reply-secret")
                client.requests.clear()
                await manager.send_message(session, "続行")
        else:
            session = await manager.resume("saved-thread", state.ResumePrompt("再開"), str(tmp_path))
        method_name = "start" if operation == "start" else "resume"
        params = next(params for method, params in client.requests if method == f"thread/{method_name}")
        server = params["config"]["mcp_servers"]["agents_server"]
        assert set(names) & set(server["env_vars"]) == (set(names) if configured else set())
        assert len(server["env_vars"]) == len(set(server["env_vars"]))
        assert set(server["env"]) == {
            "AGENT_TOOLKIT_OWNER_SESSION",
            "AGENT_TOOLKIT_STATUS_HOST_SESSION",
            plugin_environment.RUNTIME_DATA_ENV,
        }
        assert server["env"][plugin_environment.RUNTIME_DATA_ENV] == str(runtime_data)
        assert "UV_PROJECT_ENVIRONMENT" in server["env_vars"]
        assert "DIAGNOSTIC_TOKEN" in server["env_vars"]
        assert ("MISE_UNPUBLISHED_DIR" in server["env_vars"]) is (operation != "reply")
        assert ("NEW_REPLY_API_KEY" in server["env_vars"]) is (operation == "reply")
        assert server["env"]["AGENT_TOOLKIT_OWNER_SESSION"] == "chosen-root"
        writer = server["env"]["AGENT_TOOLKIT_STATUS_HOST_SESSION"]
        assert writer and writer != "ambient-host"
        assert aliases == [("chosen-root", writer, session.session_id)] * (2 if operation == "reply" else 1)
        assert not {
            "CLAUDE_PLUGIN_DATA",
            "PLUGIN_DATA",
            "AGENT_TOOLKIT_OWNER_SESSION",
            "AGENT_TOOLKIT_STATUS_HOST_SESSION",
            "AGENT_TOOLKIT_DELEGATED_SESSION",
        } & set(server["env_vars"])
        encoded = json.dumps(server)
        assert not any(
            value in encoded
            for value in (
                *values.values(),
                "unpublished-location",
                "synthetic-credential",
                "reply-secret",
                "caller-environment",
                "parent-plugin-data",
            )
        )
        assert "ambient-root" not in encoded and "ambient-host" not in encoded
    finally:
        await manager.close()


@pytest.mark.usefixtures("_restore_session_listeners")
@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["start", "resume", "reply"])
async def test_inner_mcp_uses_actual_app_server_parent_environment(
    operation: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """内側MCPはApp Serverへ渡した環境の名前を使い、stdio子で値・空値・未設定を保持する。"""
    supplied = {
        "UNREGISTERED_VENDOR_API_KEY": "synthetic-api-value",
        "LC_ALL": "C",
        "DBUS_SESSION_BUS_ADDRESS": "synthetic-keyring-address",
        "EMPTY_CUSTOM_SETTING": "",
        "UV_PROJECT_ENVIRONMENT": "caller-env",
        "CLAUDE_CODE_SESSION_ID": "parent-context",
        "AGENT_TOOLKIT_OWNER_SESSION": "parent-owner",
    }
    client = _TierClient()
    # App Serverの生成後に外側の環境が変わっても、実際の配送元は起動時のsnapshotである。
    client.parent_environment = {**host_environ(), **supplied}
    client.parent_environment.pop("MISSING_CUSTOM_SETTING", None)
    monkeypatch.setenv("NOT_IN_APP_SERVER_ENV", "not-deliverable")
    manager = subject.AppServerManager(root_session_id="chosen-owner")
    manager.client = cast(Any, client)
    monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))
    monkeypatch.setattr(shared_roots, "write_host_alias", lambda *_args: None)
    if operation == "resume":
        session = await manager.resume("saved", state.ResumePrompt("再開"), str(tmp_path))
    else:
        session = await manager.start("開始", str(tmp_path))
        if operation == "reply":
            _complete(session)
            client.requests.clear()
            await manager.send_message(session, "続行")
    params = next(params for method, params in client.requests if method.startswith("thread/"))
    server = params["config"]["mcp_servers"]["agents_server"]
    assert "NOT_IN_APP_SERVER_ENV" not in server["env_vars"]
    assert "CLAUDE_CODE_SESSION_ID" not in server["env_vars"]
    # Codexのenv_varsとenvの解決順をstdio子の環境へ射影する。実CodexのMCP起動は別の受入観測で検収する。
    child_environment = {name: client.parent_environment[name] for name in server["env_vars"]}
    child_environment.update(server["env"])
    keys = [*supplied, "MISSING_CUSTOM_SETTING"]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import json, os, sys; print(json.dumps({key: os.environ.get(key) for key in sys.argv[1:]}))",
        *keys,
        env=child_environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
        assert process.returncode == 0 and not stderr
        received = json.loads(stdout)
        assert received == {
            **supplied,
            "CLAUDE_CODE_SESSION_ID": None,
            "AGENT_TOOLKIT_OWNER_SESSION": "chosen-owner",
            "MISSING_CUSTOM_SETTING": None,
        }
        encoded = json.dumps(server)
        assert all(value not in encoded for value in supplied.values() if value and len(value) > 1)
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
        manager.client = None
        await manager.close()


@pytest.mark.asyncio
async def test_client_start_aborts_when_initialize_never_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """initializeの応答が返らない接続を上限で打ち切り、子プロセスを終了する。"""
    monkeypatch.setattr(state, "SESSION_INITIALIZATION_TIMEOUT", 0.05)
    process = _SilentProcess()

    async def _spawn(*args: Any, **kwargs: Any) -> _SilentProcess:
        del args, kwargs  # noqa
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    client = subject.JsonRpcProcess(_ignore_message, _ignore_message)

    with pytest.raises(session_errors.SessionInitializationTimeoutError):
        await client.start()

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
    monkeypatch.setattr(state, "SESSION_INITIALIZATION_TIMEOUT", 0.05)
    manager = subject.AppServerManager()
    monkeypatch.setattr(manager, "_ensure_client", lambda: _return(_SilentClient()))

    with pytest.raises(session_errors.SessionInitializationTimeoutError):
        await manager.start("実装する", str(tmp_path))

    assert not manager.sessions
    await manager.close()


def _make_plugin_root(base: pathlib.Path, version: str, *, versioned: bool, root_is_version: bool = False) -> pathlib.Path:
    """`plugin.json`を持つ配布物rootを、版別ディレクトリの有無を変えて作成する。"""
    root = base / version / "agent-toolkit" if versioned else base / "checkout" / "agent-toolkit"
    if root_is_version:
        root = base / version
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(f'{{"version": "{version}"}}\n', encoding="utf-8")
    (root / "agent_toolkit").mkdir()
    (root / "agent_toolkit" / "agents_server_mcp.py").write_text("# entry\n", encoding="utf-8")
    (root / "scripts").mkdir()
    for name in ("launch.py", "atk", "atk.cmd"):
        (root / "scripts" / name).write_text("# entry\n", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "pyvenv.cfg").write_text("home = /usr\n", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _isolate_stable_plugin_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """複製先の記録とmanaged-tempの作成先をテストごとに分離する。"""
    monkeypatch.setattr(plugin_roots, "_stable_plugin_roots", {})
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setenv("TMPDIR", str(tmp_path / "managed-temp"))
    (tmp_path / "managed-temp").mkdir()


@pytest.mark.parametrize("root_is_version", [True, False])
def test_versioned_plugin_root_is_copied_to_a_stable_location(tmp_path: pathlib.Path, root_is_version: bool) -> None:
    """版別ディレクトリ配下の配布物rootは、複製元を失っても解決できる実体へ写す。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True, root_is_version=root_is_version)

    resolved = plugin_roots.resolve_stable_plugin_root(source)

    assert resolved != source
    shutil.rmtree(source)
    assert (resolved / "agent_toolkit" / "agents_server_mcp.py").is_file()
    assert not (resolved / ".venv").exists()


def test_versioned_plugin_root_is_copied_once_per_source(tmp_path: pathlib.Path) -> None:
    """同じ複製元への解決を繰り返しても複製先は変わらない。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True)

    first = plugin_roots.resolve_stable_plugin_root(source)
    second = plugin_roots.resolve_stable_plugin_root(source)

    assert first == second
    assert first != source
    assert [entry.name for entry in first.parent.parent.iterdir() if entry.is_dir()] == [first.parent.name]


def test_plain_plugin_root_is_used_as_is(tmp_path: pathlib.Path) -> None:
    """版別ディレクトリに当たらない配布物rootは複製せずそのまま使う。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=False)

    assert plugin_roots.resolve_stable_plugin_root(source) == source


def test_copy_failure_falls_back_to_the_resolved_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """複製に失敗しても例外を伝播させず、解決したrootで委譲の起動を成立させる。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True)

    def _fail(*_args: Any, **_kwargs: Any) -> pathlib.Path:
        raise plugin_roots._managed_temp.ManagedTempError("一時領域を作成できない")

    monkeypatch.setattr(plugin_roots._managed_temp, "create_managed_temp", _fail)

    assert plugin_roots.resolve_stable_plugin_root(source) == source


def _lease_plugin_source(base: pathlib.Path, version: str = "1.0.0") -> pathlib.Path:
    root = _make_plugin_root(base, version, versioned=True, root_is_version=True)
    (root / "payload.txt").write_text("起動した版", encoding="utf-8")
    return root


def _real_mcp_environment(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> dict[str, str]:
    """公開ランチャー用のホーム・状態を隔離し、ホストのPythonと取得済みパッケージを使う。"""
    environment = host_environ()
    for name in HOME_ENVIRONMENT_NAMES:
        environment[name] = str(tmp_path / "home")
    for name in CONFIG_DIRECTORY_ENVIRONMENT_NAMES:
        environment[name] = str(tmp_path / name.lower())
    state_base = tmp_path / "runtime-state"
    environment.update(XDG_STATE_HOME=str(state_base), LOCALAPPDATA=str(state_base))
    environment[plugin_environment.RUNTIME_DATA_ENV] = str(tmp_path / "runtime-data")
    environment["PATH"] = os.pathsep.join(
        directory for directory in os.get_exec_path(environment) if pathlib.Path(directory).name != "shims"
    )
    # 隔離HOMEでもテストを動かしているPythonを公開ランチャーから解決し、環境ごとの再取得を避ける。
    python_directory = pathlib.Path(sys.base_prefix) / "bin" if os.name == "posix" else pathlib.Path(sys.base_prefix)
    environment["PATH"] = str(python_directory) + os.pathsep + environment["PATH"]
    environment.update(UV_NO_MANAGED_PYTHON="1", UV_PYTHON_DOWNLOADS="never", UV_LINK_MODE="copy")
    for name in ("PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        environment.pop(name, None)
    return environment


def _wait_mcp_processes(owned: dict[int, psutil.Process], *, timeout: float = 3) -> None:
    """所有プロセスの終端を待ち、PID待機が失敗した場合は同じidentityの生存を再確認する。"""
    try:
        _, alive = psutil.wait_procs(list(owned.values()), timeout=timeout)
    except OSError as error:
        # pidfd_openが終了済みPIDでEINVALを返した実観測への対処。
        # 生存と取得不能は失敗を保ち、終了を確認した場合だけ待機失敗から進める。
        alive = []
        for process in owned.values():
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    alive.append(process)
            except psutil.NoSuchProcess:
                continue
        if alive:
            raise
        print(f"MCP終了の再確認: wait_error={error}; 全所有identityの生存なし")
    assert not alive, [process.pid for process in alive]


@pytest.mark.asyncio
@pytest.mark.usefixtures("share_package_caches")
async def test_stable_plugin_root_shared_across_processes(
    tmp_path: pathlib.Path,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """外側2台と保持先起動の実MCPが配布物を共有し、元版の更新・削除後も応答して終了後に回収できる。"""
    plugin = pathlib.Path(__file__).resolve().parents[2]
    manifest = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    source = tmp_path / "distribution" / version
    shutil.copytree(plugin, source, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".git", "node_modules"))
    version_file = pathlib.Path("skills/managed-temp/SKILL.md")
    original = (source / version_file).read_bytes()
    environment = _real_mcp_environment(tmp_path, host_environ)
    state_base = tmp_path / "runtime-state"
    uv = shutil.which("uv", path=environment["PATH"])
    assert uv is not None
    lease_directory = state_base / "agent-toolkit/plugin-root-leases"
    clients: list[ClientSession] = []
    owned: dict[int, psutil.Process] = {}
    logs: list[pathlib.Path] = []
    observed: list[dict[str, Any]] = []
    area: pathlib.Path | None = None
    expected_tools = {"start", "send_message", "kill", "stop", "list", "show"}
    try:
        with contextlib.ExitStack() as files, anyio.fail_after(45):
            async with contextlib.AsyncExitStack() as stack:
                for index in range(3):
                    root = source if index < 2 else area / version if area is not None else source
                    command = plugin_environment.launch_command(root, pathlib.Path("agent_toolkit/agents_server_mcp.py"))
                    log = tmp_path / f"mcp-{index}.stderr"
                    logs.append(log)
                    error_output = files.enter_context(log.open("w", encoding="utf-8"))
                    streams = await stack.enter_async_context(
                        stdio_client(StdioServerParameters(command=uv, args=command[1:], env=environment), errlog=error_output)
                    )
                    client = await stack.enter_async_context(ClientSession(*streams))
                    initialized = await client.initialize()
                    assert initialized.server_info.name == "agents_server"
                    tools = {tool.name for tool in (await client.list_tools()).tools}
                    assert tools == expected_tools
                    clients.append(client)
                    leases = [json.loads(path.read_text(encoding="utf-8")) for path in lease_directory.glob("*.json")]
                    assert len(leases) == index + 1
                    areas = {pathlib.Path(record["path"]) for record in leases}
                    assert len(areas) == 1
                    area = areas.pop()
                    held = area / version
                    marker = json.loads((area / ".agent-toolkit-plugin-root.json").read_text(encoding="utf-8"))
                    assert marker == {"source": str(source), "version": version, "root_name": version}
                    assert list(area.parent.glob("agents-server-plugin-root-*")) == [area]
                    assert (held / version_file).read_bytes() == original
                    for record in leases:
                        process = psutil.Process(record["pid"])
                        assert process.create_time() == record["created_at"]
                        ancestors = process.parents()
                        parent_index = next(i for i, parent in enumerate(ancestors) if parent.pid == os.getpid())
                        for member in (process, *ancestors[:parent_index]):
                            owned[member.pid] = member
                    observed.append({"server": index, "tools": sorted(tools), "leases": leases, "marker": marker})
                    if index == 1:
                        (source / version_file).write_text("更新された配布元", encoding="utf-8")
                        assert (held / version_file).read_bytes() == original
                        for client in clients:
                            assert {tool.name for tool in (await client.list_tools()).tools} == expected_tools
                        shutil.rmtree(source)
                        assert not source.exists()
                        for target in (area, held):
                            protected = subprocess.run(
                                [
                                    sys.executable,
                                    str(plugin / "agent_toolkit/_managed_temp.py"),
                                    "cleanup",
                                    "--path",
                                    str(target),
                                    "--force-remove",
                                ],
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                env=environment,
                                timeout=15,
                                check=False,
                            )
                            assert protected.returncode == 2, protected.stderr
                for client in clients:
                    assert {tool.name for tool in (await client.list_tools()).tools} == expected_tools
                    listed = await client.call_tool("list", {})
                    assert not listed.is_error
                    assert listed.structured_content is not None and listed.structured_content["sessions"] == []
                assert area is not None
                assert (area / version / version_file).read_bytes() == original
    finally:
        _wait_mcp_processes(owned)
        for log in logs:
            print(f"S02 MCP診断 {log.name}:\n" + log.read_text(encoding="utf-8"))
    assert area is not None
    cleaned = subprocess.run(
        [sys.executable, str(plugin / "agent_toolkit/_managed_temp.py"), "cleanup", "--path", str(area)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=15,
        check=False,
    )
    assert cleaned.returncode == 0, cleaned.stderr
    assert not area.exists()
    print("S02: " + json.dumps({"observed": observed, "terminated_processes": sorted(owned), "root_removed": str(area)}))


# 90cad03ac601a24107d34758748f1d022ed5342cのplugin_root.pyにある、版別rootの
# 正常系の保持生成を固定するfixture。旧版と同じprefix/copytree/除外/cacheを使い、
# leaseと共有markerを生成しない。版判定と失敗時fallbackはこの試験の対象外。
# MCP本体・公開launcher・managed-temp・診断ログは現行のpluginコピーを使う。
_LEGACY_PLUGIN_ROOT_SOURCE = '''"""旧形式の正常系保持生成を実MCPへ提供する試験fixture。"""
from pathlib import Path
import shutil
from agent_toolkit._atk import managed_temp

STABLE_PLUGIN_ROOT_PREFIX = "agents-server-plugin-root"
STABLE_PLUGIN_ROOT_EXCLUDED = (".venv", "__pycache__", ".git")
_stable_plugin_roots = {}

def resolve_stable_plugin_root(plugin_root=None):
    root = Path(__file__).resolve().parents[2] if plugin_root is None else plugin_root
    if root not in _stable_plugin_roots:
        area = managed_temp.create_managed_temp(STABLE_PLUGIN_ROOT_PREFIX)
        destination = Path(area) / root.name
        shutil.copytree(
            root, destination,
            ignore=shutil.ignore_patterns(*STABLE_PLUGIN_ROOT_EXCLUDED), symlinks=True,
        )
        _stable_plugin_roots[root] = destination
    return _stable_plugin_roots[root]

SERVER_PLUGIN_ROOT = resolve_stable_plugin_root()
'''


@pytest.mark.asyncio
@pytest.mark.usefixtures("share_package_caches")
async def test_legacy_plugin_root_real_mcp_lifecycle(
    tmp_path: pathlib.Path,
    host_environ: Callable[[], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧形式の生成fixtureを使う実MCPをOS越しに保護し、全終了後の同じ公開cleanupで回収する。"""
    plugin = pathlib.Path(__file__).resolve().parents[2]
    version = json.loads((plugin / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))["version"]
    source = tmp_path / "legacy-distribution" / version
    shutil.copytree(plugin, source, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".git", "node_modules"))
    (source / "agent_toolkit/_agents_server/plugin_root.py").write_text(_LEGACY_PLUGIN_ROOT_SOURCE, encoding="utf-8")
    environment = _real_mcp_environment(tmp_path, host_environ)
    uv = shutil.which("uv", path=environment["PATH"])
    assert uv is not None
    command = plugin_environment.launch_command(source, pathlib.Path("agent_toolkit/agents_server_mcp.py"))
    log = tmp_path / "legacy-mcp.stderr"
    owned: dict[int, psutil.Process] = {}
    observation: dict[str, Any] = {}
    area: pathlib.Path | None = None

    def cleanup(target: pathlib.Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(plugin / "agent_toolkit/_managed_temp.py"), "cleanup", "--path", str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            timeout=15,
            check=False,
        )

    try:
        with log.open("w", encoding="utf-8") as errors, anyio.fail_after(45):
            async with contextlib.AsyncExitStack() as stack:
                streams = await stack.enter_async_context(
                    stdio_client(StdioServerParameters(command=uv, args=command[1:], env=environment), errlog=errors)
                )
                client = await stack.enter_async_context(ClientSession(*streams))
                initialized = await client.initialize()
                assert initialized.server_info.name == "agents_server"
                tools = {tool.name for tool in (await client.list_tools()).tools}
                assert "start" in tools
                entrypoint = source / "agent_toolkit/agents_server_mcp.py"
                servers = []
                for process in psutil.Process().children(recursive=True):
                    arguments = process.cmdline()
                    if len(arguments) > 1 and pathlib.Path(arguments[1]) == entrypoint:
                        servers.append(process)
                        ancestors = process.parents()
                        parent_index = next(i for i, parent in enumerate(ancestors) if parent.pid == os.getpid())
                        for member in (process, *ancestors[:parent_index]):
                            owned[member.pid] = member
                assert len(servers) == 1
                server = servers[0]
                cache_base = "LOCALAPPDATA" if sys.platform == "win32" else "XDG_CACHE_HOME"
                temp_root = pathlib.Path(environment[cache_base]) / "agent-toolkit/managed-temp"
                areas = list(temp_root.glob("agents-server-plugin-root-*"))
                assert len(areas) == 1
                area = areas[0]
                assert (area / version / "agent_toolkit/agents_server_mcp.py").is_file()
                assert not (area / ".agent-toolkit-plugin-root.json").exists()
                leases = tmp_path / "runtime-state/agent-toolkit/plugin-root-leases"
                assert not list(leases.glob("*.json"))
                arguments = server.cmdline()
                assert pathlib.Path(arguments[1]).parts[-2:] == ("agent_toolkit", "agents_server_mcp.py")
                assert not pathlib.Path(arguments[1]).is_relative_to(area)
                assert server.username() == psutil.Process().username()
                try:
                    opened = [entry._asdict() for entry in server.open_files()]
                    open_files_error = None
                except psutil.Error as error:
                    opened = []
                    open_files_error = type(error).__name__
                # 実MCPを1台に限定したOS観測でも共通判定へ届くことを確認する。
                # インベントリーの対象だけを限定し、PID/argv/username/open_filesは実OSから得る。
                with monkeypatch.context() as probe:
                    probe.setattr(psutil, "process_iter", lambda: [server])
                    probe.setattr(state_paths, "state_dir", lambda: leases.parent)
                    with plugin_roots.plugin_root_leases.plugin_root_lock(area):
                        assert plugin_roots.plugin_root_leases.plugin_root_in_use(area)
                protected = cleanup(area)
                assert protected.returncode == 2, protected.stderr
                assert area.exists()
                assert "使用中または使用状態を確認できない配布物を保持した" in protected.stderr
                assert {tool.name for tool in (await client.list_tools()).tools} == tools
                observation = {
                    "fixture_origin": "90cad03:plugin_root.pyの版別root正常系保持生成",
                    "runtime": "現行MCPと現行launcher。旧版全体の再実行には当たらない",
                    "platform": sys.platform,
                    "pid": server.pid,
                    "created_at": server.create_time(),
                    "cmdline": arguments,
                    "open_files": opened,
                    "open_files_error": open_files_error,
                    "single_real_process_probe": True,
                    "source": str(source),
                    "area": str(area),
                    "live_cleanup": {"exit_code": protected.returncode, "stdout": protected.stdout, "stderr": protected.stderr},
                }
    finally:
        _wait_mcp_processes(owned, timeout=5)
        print("旧形式MCP診断:\n" + log.read_text(encoding="utf-8"))
    assert area is not None
    cleaned = cleanup(area)
    assert cleaned.returncode == 0, cleaned.stderr
    assert not area.exists()
    observation.update(
        terminated_processes=sorted(owned),
        stopped_cleanup={"exit_code": cleaned.returncode, "stdout": cleaned.stdout, "stderr": cleaned.stderr},
        root_removed=True,
    )
    print("旧形式MCP観測: " + json.dumps(observation, ensure_ascii=False))


@pytest.mark.parametrize("legacy_server_present", [False, True])
def test_copy_failure_is_not_reused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, legacy_server_present: bool
) -> None:
    """同namespaceの旧MCPが生存する場合も、失敗物を回収して次の解決で完成した実体を使用する。"""
    source = _lease_plugin_source(tmp_path / "broken")
    if legacy_server_present:
        process = psutil.Process()
        log = state_paths.state_dir() / "agents-server.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.touch()
        monkeypatch.setattr(process, "cmdline", lambda: ["python", "/old/agent_toolkit/agents_server_mcp.py"])
        monkeypatch.setattr(process, "open_files", lambda: [SimpleNamespace(path=str(log))])
        monkeypatch.setattr(psutil, "process_iter", lambda: [process])

    def fail_copy(_src: pathlib.Path, dst: pathlib.Path, **_kwargs: object) -> None:
        dst.mkdir()
        (dst / "partial").touch()
        raise OSError("copy interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copytree", fail_copy)
        assert plugin_roots.resolve_stable_plugin_root(source) == source
    assert not managed_temp.list_managed_temp()
    resolved = plugin_roots.resolve_stable_plugin_root(source)
    assert resolved != source
    assert (resolved / "payload.txt").read_text(encoding="utf-8") == "起動した版"


def test_distinct_sources_and_versions_have_separate_roots(tmp_path: pathlib.Path) -> None:
    """配布元または版が異なる実体を、それぞれ独立に保持する。"""
    roots = [
        plugin_roots.resolve_stable_plugin_root(_lease_plugin_source(tmp_path / "first", "1.0.0")),
        plugin_roots.resolve_stable_plugin_root(_lease_plugin_source(tmp_path / "second", "1.0.0")),
        plugin_roots.resolve_stable_plugin_root(_lease_plugin_source(tmp_path / "first", "2.0.0")),
    ]
    assert len(set(roots)) == 3
    assert all((root / "payload.txt").read_text(encoding="utf-8") == "起動した版" for root in roots)


def test_agents_server_config_uses_the_stable_plugin_root() -> None:
    """内側MCPサーバーの起動コマンドは、安定した配布物rootの実体だけを指す。"""
    config = subject.AppServerManager()._agents_server_config("owner", "writer")

    args = config["mcp_servers"]["agents_server"]["args"]
    expected_root = plugin_roots.resolve_stable_plugin_root()
    assert args == [
        "run",
        "--no-config",
        "--no-project",
        "--python",
        "3.15",
        "python",
        str(expected_root / "scripts" / "launch.py"),
        "agent_toolkit/agents_server_mcp.py",
    ]
    assert pathlib.Path(args[-2]).is_file()
    assert (expected_root / args[-1]).is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "shell", "write"])
@pytest.mark.parametrize("windows", [False, True])
async def test_start_and_resume_pass_the_same_stable_plugin_root_to_all_launch_kinds(
    launch_kind: state.LaunchKind, windows: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """開始と再開の全役割へ、実在する同じplugin rootと、plugin rootの再解決を禁じる指示を渡す。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True, root_is_version=True)
    (source / "skills" / "sample").mkdir(parents=True)
    (source / "skills" / "sample" / "SKILL.md").write_text("# 手順\n", encoding="utf-8")
    plugin_root = plugin_roots.resolve_stable_plugin_root(source)
    shutil.rmtree(source)
    monkeypatch.setattr(plugin_roots, "SERVER_PLUGIN_ROOT", plugin_root)
    cli_instructions = plugin_environment.cli_instructions
    monkeypatch.setattr(plugin_environment, "cli_instructions", lambda root: cli_instructions(root, windows=windows))
    client = _TierClient()
    manager = subject.AppServerManager(root_session_id="owner")
    monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))
    monkeypatch.setattr(subject, "JsonRpcProcess", _SandboxProbeClient)

    await manager.start("開始", str(tmp_path), launch_kind=launch_kind)
    start_params = next(params for method, params in client.requests if method == "thread/start")
    client.requests.clear()
    await manager.resume("tier-thread", state.ResumePrompt("再開"), str(tmp_path), launch_kind=launch_kind)
    resume_params = next(params for method, params in client.requests if method == "thread/resume")

    for params in (start_params, resume_params):
        instructions = params["developerInstructions"]
        assert str(plugin_root) in instructions
        command = next(line.removeprefix("起動部分: ") for line in instructions.splitlines() if line.startswith("起動部分: "))
        entry = plugin_root / "scripts" / ("atk.cmd" if windows else "atk")
        assert entry.is_file()
        if windows:
            assert command == "& '" + str(entry).replace("'", "''") + "'"
        else:
            assert shlex.split(command) == [str(entry)]
        assert (subject.python_runtime_instructions() in instructions) is (launch_kind != "delegate")
        if launch_kind == "explore":
            assert params["config"]["mcp_servers"]["agents_server"] == {"enabled": False}
        else:
            args = params["config"]["mcp_servers"]["agents_server"]["args"]
            assert pathlib.Path(args[-2]) == plugin_root / "scripts" / "launch.py"
            assert pathlib.Path(args[-2]).is_file()
            assert (plugin_root / args[-1]).is_file()
        assert (plugin_root / "skills" / "sample" / "SKILL.md").is_file()
        assert "別hostのcache版数から別のplugin rootを組み立てない" in instructions
    assert start_params["developerInstructions"] == resume_params["developerInstructions"]


async def _return(value: Any) -> Any:
    return value


async def _return_none() -> None:
    return None


def _overloaded_turn(session: state.SessionState, error_info: str = "serverOverloaded") -> dict[str, Any]:
    message = _completed_turn(session)
    message["params"]["turn"]["status"] = "failed"
    message["params"]["turn"]["error"] = {
        "message": "Selected model is at capacity. Please try a different model.",
        "codexErrorInfo": error_info,
    }
    return message


@pytest.mark.asyncio
async def test_overloaded_turn_after_availability_check_holds_result_and_reports_wait(tmp_path: pathlib.Path) -> None:
    """可用性確認を過ぎた後の過負荷は失敗を公開せず保留し、待機中の状態を`api_error`で示す。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", turn_seq=1)
    session.availability_checked = True
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_overloaded_turn(session))

    assert session.result_available is False
    assert session.awaiting_auto_resume is True
    assert session.status == "running"
    assert session.pending_result is not None
    assert session.pending_result["status"] == "failed"
    assert session.overload_resume_count == 1
    assert session.overload_resume_at is not None
    assert session.api_error is not None
    assert session.api_error["type"] == "serverOverloaded"
    assert session.api_error["http_status"] is None
    assert session.api_error["count"] == 1
    await manager.close()


@pytest.mark.asyncio
async def test_overloaded_reply_turn_is_held_even_before_flag(tmp_path: pathlib.Path) -> None:
    """委譲元の`send_message`で始めたturn（2番目以降）の過負荷も自動継続の対象とする。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-2", turn_seq=2)
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_overloaded_turn(session))

    assert session.awaiting_auto_resume is True
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("availability_checked", "error_info", "resume_count"),
    [
        (False, "serverOverloaded", 0),
        (True, "usageLimitExceeded", 0),
        (True, "rateLimitExceeded", 0),
        (True, "serverOverloaded", len(resume_waits.OVERLOAD_RESUME_DELAYS_SECONDS)),
    ],
)
async def test_turn_outside_overload_resume_is_published_as_failed(
    tmp_path: pathlib.Path, availability_checked: bool, error_info: str, resume_count: int
) -> None:
    """起動確認前の過負荷、他の可用性失敗、上限到達後の過負荷は現行どおり失敗として公開する。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", turn_seq=1)
    session.availability_checked = availability_checked
    session.overload_resume_count = resume_count
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_overloaded_turn(session, error_info))

    assert session.result_available is True
    assert session.status == "failed"
    assert session.error["codexErrorInfo"] == error_info
    assert session.awaiting_auto_resume is False
    assert session.overload_resume_count == 0
    await manager.close()


@pytest.mark.asyncio
async def test_non_overload_completion_ends_overload_chain(tmp_path: pathlib.Path) -> None:
    """継続したturnが過負荷以外で終われば結果を公開し、連鎖の回数を戻す。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-2", turn_seq=2)
    session.overload_resume_count = 2
    session.overload_first_at = "2026-10-04T00:00:00+00:00"
    manager = _InspectableAppServerManager({session.session_id: session})

    await manager.handle_notification(_completed_turn(session))

    assert session.result_available is True
    assert session.status == "completed"
    assert session.overload_resume_count == 0
    assert session.overload_first_at is None
    await manager.close()


@pytest.mark.asyncio
async def test_kill_during_overload_wait_publishes_last_failure_without_new_turn(tmp_path: pathlib.Path) -> None:
    """待機中の`kill`は新しいturnを始めず、保留した最後の失敗でsessionを終端する。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", turn_seq=1)
    session.availability_checked = True
    manager = _InspectableAppServerManager({session.session_id: session})
    await manager.handle_notification(_overloaded_turn(session))

    await manager.interrupt(session)

    assert session.result_available is True
    assert session.status == "failed"
    assert session.error["codexErrorInfo"] == "serverOverloaded"
    assert session.overload_resume_count == 0
    assert session.overload_resume_at is None
    assert session.turn_seq == 1
    await manager.close()


@pytest.mark.asyncio
async def test_send_message_during_overload_wait_delivers_instruction_as_reply(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """待機中の委譲元の`send_message`は保留を確定し、その指示を同じsessionのreplyとして配送する。"""
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", turn_seq=1)
    session.availability_checked = True
    manager = _InspectableAppServerManager({session.session_id: session})
    await manager.handle_notification(_overloaded_turn(session))
    delivered: list[str] = []

    async def start_reply(_session: state.SessionState, prompt: str) -> tuple[str, dict[str, Any], None]:
        delivered.append(prompt)
        return "reply_started", {}, None

    monkeypatch.setattr(manager, "_start_reply_locked", start_reply)

    result = await manager.send_message(session, "委譲元の指示")

    assert delivered == ["委譲元の指示"]
    assert result["delivery"] == "reply_started"
    assert result["previous_result"]["status"] == "failed"
    assert session.awaiting_auto_resume is False
    assert session.overload_resume_count == 0
    assert session.overload_resume_at is None
    await manager.close()


def test_developer_instructions_include_user_rules_except_embedded(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`~/.claude/rules/`配下の規範ファイルを委譲先の指示へ連結し、全体指示ファイルが埋め込むものだけを除く。

    連結しないとCodexの委譲先はClaudeの委譲先が受け取るホスト固有の制約を知らずに操作し、
    埋め込み済みの本文を重ねると指示が重複する。起動時に読んだ値を使い回すと、ユーザーの編集が再開へ反映されない。
    """
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(plugin_roots, "SERVER_PLUGIN_ROOT", tmp_path / "plugin")
    rules = home / ".claude" / "rules"
    (rules / "agent-toolkit").mkdir(parents=True)
    plain = rules / "env.local.md"
    plain.write_text("# env.local.md\n\nGitHubへ書き込まない。\n", encoding="utf-8")
    nested = rules / "team" / "review.md"
    nested.parent.mkdir()
    nested.write_text("# review.md\n\nレビューは日本語で書く。\n", encoding="utf-8")
    embedded = rules / "agent-toolkit" / "01-agent.md"
    embedded.write_text(
        '<atk-auto source="agent-toolkit" kind="rules" path="agent-toolkit/rules/01-agent.md">\n'
        "埋め込み済みの本文\n</atk-auto>\n",
        encoding="utf-8",
    )
    unmatched = rules / "myprojects.md"
    unmatched.write_text(
        '<atk-auto source="dotfiles" kind="rules" path=".chezmoi-source/dot_claude/rules/myprojects.md.tmpl">\n'
        "Claude Code向けの一覧\n</atk-auto>\n",
        encoding="utf-8",
    )
    codex_home.mkdir()
    (codex_home / "AGENTS.md").write_text(
        "# 全体指示\n\n"
        '<atk-auto source="agent-toolkit" kind="rules" path="agent-toolkit/rules/01-agent.md">\n本文\n</atk-auto>\n',
        encoding="utf-8",
    )

    for launch_kind in ("delegate", "explore", "write", "shell"):
        instructions = subject._developer_instructions(launch_kind)
        assert "GitHubへ書き込まない。" in instructions
        assert "レビューは日本語で書く。" in instructions
        assert "Claude Code向けの一覧" in instructions
        assert "埋め込み済みの本文" not in instructions
        for path in (plain, nested, unmatched):
            assert f'kind="user-rules" path="{path}"' in instructions

    plain.write_text("# env.local.md\n\n書き換えた制約。\n", encoding="utf-8")
    rewritten = subject._developer_instructions("delegate")
    assert "書き換えた制約。" in rewritten
    assert "GitHubへ書き込まない。" not in rewritten


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("environment_owner", [None, "environment-root"])
async def test_manager_root_connects_codex_start_resume_and_notification(
    environment_owner: str | None, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """環境ownerの有無にかかわらず、Managerのrootで起動・再開・通知回収を接続する。"""
    for name in (
        "AGENT_TOOLKIT_OWNER_SESSION",
        "CLAUDE_CODE_SESSION_ID",
        "CODEX_THREAD_ID",
        "AGENT_TOOLKIT_STATUS_HOST_SESSION",
    ):
        monkeypatch.delenv(name, raising=False)
    if environment_owner is not None:
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", environment_owner)
        monkeypatch.setenv("CODEX_THREAD_ID", "host-thread")
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    manager = server_manager.AgentsServerManager()
    other = server_manager.AgentsServerManager()
    assert manager._status_writer is not None
    assert other._status_writer is not None
    root = manager._status_writer.root_session_id
    if environment_owner is None:
        assert root != other._status_writer.root_session_id
    else:
        assert root == environment_owner
    client = FakeCodexClient()
    backend = manager._backend("codex")
    monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
    try:
        session = await backend.start("調査", str(tmp_path))
        await backend.resume("resumed-thread", state.ResumePrompt("続行"), str(tmp_path))
        configs = [params["config"] for method, params in client.requests if method in {"thread/start", "thread/resume"}]
        assert len(configs) == 2
        parent_environment = dict(os.environ)
        inherited_venv.strip_inherited_venv(parent_environment)
        for config in configs:
            assert config["mcp_servers"]["agents_server"]["env"]["AGENT_TOOLKIT_OWNER_SESSION"] == root
            assert config["mcp_servers"]["agents_server"]["env_vars"] == list(
                subject.mcp_environment.runtime_environment_variable_names(parent_environment)
            )
        environment = {**configs[0]["mcp_servers"]["agents_server"]["env"], "CODEX_THREAD_ID": session.session_id}
        assert send_notification("途中の観測", environment=environment, state_root=tmp_path) == 0
        response = await manager.wait()
        assert response["session_id"] == session.session_id
        assert response["status"] == "starting"
        assert "途中の観測" in response["notices"][0]["body"]
        assert f"delegate:{session.session_id}" in response["notices"][0]["body"]
        assert notice_inbox.take_notices(root, session.session_id, state_root=tmp_path) == []
        if environment_owner is None:
            assert (
                notice_inbox.take_notices(other._status_writer.root_session_id, session.session_id, state_root=tmp_path) == []
            )
    finally:
        await manager.close()
        await other.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_concurrent_kills_preserve_shared_request_when_turn_completes_before_lock_handoff(
    tmp_path: pathlib.Path,
) -> None:
    """ロック待ち中にturnが終端しても並行killは要求済み状態を共有する。"""
    manager = server_manager.AgentsServerManager()
    backend = subject.AppServerManager(manager.sessions, manager._condition)
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session
    backend.client = cast(Any, CompletingInterruptClient(backend, session))
    install_backend(manager, "codex", backend)

    async with session.turn_control_lock:
        first_task = asyncio.create_task(manager.kill(session.session_id, timeout=1))
        await asyncio.sleep(0)
        second_task = asyncio.create_task(manager.kill(session.session_id, timeout=1))
        await asyncio.sleep(0)

    first, second = await asyncio.gather(first_task, second_task)

    assert first["kill_requested"] is True
    assert second["kill_requested"] is True
    assert session.status == "interrupted"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_start_uses_noninteractive_policy_and_shared_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Codex開始時の承認・sandbox固定値と公開射影を検証する。"""
    manager = subject.AppServerManager()
    client = FakeCodexClient()

    async def ensure_client() -> FakeCodexClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    session = await manager.start("調査", str(tmp_path), "gpt-test", "high")
    assert session.status == "starting"
    thread_start = client.requests[0][1]
    turn_start = client.requests[1][1]
    assert thread_start["approvalPolicy"] == "never"
    assert thread_start["sandbox"] == "danger-full-access"
    assert turn_start["sandboxPolicy"] == {"type": "dangerFullAccess"}
    assert turn_start["model"] == "gpt-test"
    assert turn_start["effort"] == "high"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_explore_limits_start_resume_and_followup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """探索の開始・再開・追送は読取専用と外部tool無効を保ち、通常起動と分ける。"""
    normal_manager = subject.AppServerManager()
    normal_client = FakeCodexClient()

    async def ensure_normal_client() -> FakeCodexClient:
        return normal_client

    monkeypatch.setattr(normal_manager, "_ensure_client", ensure_normal_client)
    await normal_manager.start("調査", str(tmp_path), "model", "high")

    explore_manager = subject.AppServerManager()
    explore_client = _TierClient()

    async def ensure_explore_client() -> _TierClient:
        return explore_client

    monkeypatch.setattr(explore_manager, "_ensure_client", ensure_explore_client)
    monkeypatch.setattr(subject, "JsonRpcProcess", _SandboxProbeClient)
    session = await explore_manager.start("調査", str(tmp_path), "model", "high", launch_kind="explore")
    await explore_manager._start_turn(session, "追送", cast(subject.JsonRpcProcess, explore_client))
    await explore_manager._resume_thread(session, explore_client)

    normal_thread = normal_client.requests[0][1]
    explore_thread = explore_client.requests[0][1]
    assert normal_thread["config"] == {"bypass_hook_trust": True}
    assert normal_thread["developerInstructions"] == subject._developer_instructions("delegate")  # noqa: SLF001
    assert explore_thread["config"] == {
        "bypass_hook_trust": True,
        "project_doc_max_bytes": 0,
        "mcp_servers": {"agents_server": {"enabled": False}, "external": {"enabled": False}},
        "features": {"apps": False, "plugins": False, "multi_agent": False},
    }
    assert explore_thread["developerInstructions"] == subject._developer_instructions("explore")  # noqa: SLF001
    for method, params in explore_client.requests:
        assert params["approvalPolicy"] == "never"
        if method in {"thread/start", "thread/resume"}:
            assert params["sandbox"] == "read-only"
            assert params["config"] == explore_thread["config"]
        else:
            assert params["sandboxPolicy"] == {"type": "readOnly"}
    assert normal_client.requests[1][1]["sandboxPolicy"] == {"type": "dangerFullAccess"}


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_shell_start_shares_explore_thread_conditions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Codexのシェル実行起動は探索と同じthread条件で開始し、指示だけを実行専用へ替える。"""
    manager = subject.AppServerManager()
    client = FakeCodexClient()

    async def ensure_client() -> FakeCodexClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("make test", str(tmp_path), "model", "high", launch_kind="shell")

    thread_params = client.requests[0][1]
    assert thread_params["config"] == {"bypass_hook_trust": True, "project_doc_max_bytes": 0}
    assert thread_params["developerInstructions"] == subject._developer_instructions("shell")  # noqa: SLF001


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_resume_passes_delegate_instructions(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Codexの再開は`mode`に応じた委譲先宣言をthread/resumeへ渡す。"""
    client = _TierClient()
    manager = subject.AppServerManager()
    monkeypatch.setattr(manager, "_ensure_client", AsyncMock(return_value=client))
    normal_session = state.SessionState("thread-normal", str(tmp_path), engine="codex")
    explore_session = state.SessionState("thread-explore", str(tmp_path), engine="codex", launch_kind="explore")
    shell_session = state.SessionState("thread-shell", str(tmp_path), engine="codex", launch_kind="shell")

    for session in (normal_session, explore_session, shell_session):
        await manager.resume(session.session_id, state.ResumePrompt("続行"), session.cwd, launch_kind=session.launch_kind)

    normal_resume, explore_resume, shell_resume = [params for method, params in client.requests if method == "thread/resume"]
    assert normal_resume["developerInstructions"] == subject._developer_instructions("delegate")  # noqa: SLF001
    assert normal_resume["config"] == {"bypass_hook_trust": True}
    assert explore_resume["developerInstructions"] == subject._developer_instructions("explore")  # noqa: SLF001
    assert explore_resume["config"]["project_doc_max_bytes"] == 0
    assert explore_resume["config"]["features"] == {"apps": False, "plugins": False, "multi_agent": False}
    assert shell_resume["developerInstructions"] == subject._developer_instructions("shell")  # noqa: SLF001
    assert shell_resume["config"] == {"bypass_hook_trust": True, "project_doc_max_bytes": 0}


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_turn_start_response_loss_remains_running_until_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """初回のturn/startの応答が失われた後もsessionを保持し、完了通知から結果を取得できるようにする。"""
    manager = subject.AppServerManager()
    client = LostTurnStartClient()

    async def ensure_client() -> LostTurnStartClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    session = await manager.start("調査", str(tmp_path))
    assert session.status == "running"
    assert session.turn_start_ambiguous is True
    await manager._handle_notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": session.session_id,
                "turn": {"id": "turn-lost", "status": "completed", "error": None},
            },
        }
    )
    assert session.result_available is True


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_steer_rejection_waits_for_terminal_race_then_replies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """steer拒否後は同一turnの終端を待ってからreplyへ切り替える。"""
    manager = subject.AppServerManager()
    client = SteerRaceClient()

    async def ensure_client() -> SteerRaceClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    session = await manager.start("調査", str(tmp_path))
    manager.client = cast(Any, client)
    send_task = asyncio.create_task(manager.send_message(session, "続行"))
    await client.steer_called.wait()
    assert send_task.done() is False
    await manager._handle_notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": session.session_id,
                "turn": {"id": session.turn_id, "status": "completed", "error": None},
            },
        }
    )
    response = await send_task
    assert response["delivery"] == "reply_started"
    assert [method for method, _ in client.requests].count("thread/resume") == 1


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_server_request_marks_target_failed_and_replies(tmp_path: pathlib.Path) -> None:
    """非対話server requestへerror応答し、対象sessionをfailedへ遷移する。"""
    manager = subject.AppServerManager()
    client = ServerRequestClient()
    manager.client = cast(Any, client)
    session = state.SessionState("thread-1", str(tmp_path), engine="codex")
    manager.sessions[session.session_id] = session

    await manager._handle_server_request(
        {"id": 7, "method": "item/tool/requestUserInput", "params": {"threadId": session.session_id}}
    )

    assert session.status == "failed"
    assert session.result_available is True
    assert client.sent[-1]["id"] == 7
    assert client.sent[-1]["error"]["code"] == -32601


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_reader_failure_marks_all_active_sessions_failed(tmp_path: pathlib.Path) -> None:
    """reader異常は同じ接続上の全active sessionをfailedへ遷移する。"""
    manager = subject.AppServerManager()
    for session_id in ("thread-1", "thread-2"):
        manager.sessions[session_id] = state.SessionState(session_id, str(tmp_path), engine="codex")

    await manager._handle_client_failure(RuntimeError("invalid JSON line"))

    assert {session.status for session in manager.sessions.values()} == {"failed"}
    assert all(session.result_available for session in manager.sessions.values())
    assert all(session.retention_deadline is not None for session in manager.sessions.values())


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reader", "unknown-request"])
async def test_codex_connection_failure_preserves_other_engines(tmp_path: pathlib.Path, failure: str) -> None:
    """共有一覧に他engineと保留中Claudeがあっても、Codex接続の失敗はCodexだけを変更する。"""
    manager = subject.AppServerManager()
    active = state.SessionState("codex-active", str(tmp_path), engine="codex")
    completed = state.SessionState("codex-done", str(tmp_path), engine="codex", status="completed", turn_completed=True)
    others = [
        state.SessionState("claude-active", str(tmp_path), engine="claude", turn_id="claude-turn"),
        state.SessionState(
            "claude-held",
            str(tmp_path),
            engine="claude",
            awaiting_auto_resume=True,
            pending_result={"status": "completed", "agent_message": "待機中"},
        ),
        state.SessionState("agy-active", str(tmp_path), engine="agy", turn_id="agy-turn"),
    ]
    for session in (active, completed, *others):
        manager.sessions[session.session_id] = session
    before = [
        (item.status, item.error, item.awaiting_auto_resume, item.result_available, item.pending_result, item.updated_at)
        for item in others
    ]
    if failure == "reader":
        await manager._handle_client_failure(RuntimeError("invalid JSON line"))
    else:
        await manager._fail_for_request({}, "item/tool/requestUserInput")
    assert active.status == "failed" and active.result_available
    assert completed.status == "completed"
    assert [
        (item.status, item.error, item.awaiting_auto_resume, item.result_available, item.pending_result, item.updated_at)
        for item in others
    ] == before
    for item in others:
        assert manager._find_session({"threadId": item.session_id}) is None
        if item.turn_id:
            assert manager._find_session({"turnId": item.turn_id}) is None
    assert manager._find_session({"threadId": active.session_id}) is active


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("release_result", ["unsubscribed", "notLoaded", "notSubscribed", "closed"])
async def test_stop_releases_codex_descendants_and_preserves_resume(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, release_result: str
) -> None:
    """stopは親・子・孫の購読を解放し、既解放と接続閉鎖を許容し、同じ会話の再開を保つ。"""
    manager = server_manager.AgentsServerManager(None)
    backend = subject.AppServerManager(manager.sessions, manager._condition)
    install_backend(manager, "codex", backend)
    client = FakeCodexClient()
    backend.client = cast(Any, client)
    monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
    original_request = client.request

    async def request(method: str, params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        response = await original_request(method, params, **kwargs)
        if method == "thread/unsubscribe":
            assert params is not None
            if release_result in {"unsubscribed", "notSubscribed"}:
                await backend._handle_notification({"method": "thread/closed", "params": {"threadId": params["threadId"]}})
            return {"status": release_result}
        return response

    monkeypatch.setattr(client, "request", request)
    try:
        session = await backend.start("開始", str(tmp_path))
        await backend._handle_notification(
            {
                "method": "turn/started",
                "params": {"threadId": session.session_id, "turn": {"id": session.turn_id}},
            }
        )
        for owner, child, kind in ((session.session_id, "child", "started"), ("child", "grandchild", "interacted")):
            await backend._handle_notification(
                {
                    "method": "item/started",
                    "params": {
                        "threadId": owner,
                        "item": {
                            "id": f"spawn-{child}",
                            "type": "subAgentActivity",
                            "kind": kind,
                            "agentThreadId": child,
                        },
                    },
                }
            )
        await backend._handle_notification(
            {
                "method": "thread/started",
                "params": {
                    "thread": {
                        "id": "great-grandchild",
                        "parentThreadId": "grandchild",
                    }
                },
            }
        )
        assert session.codex_subagent_thread_ids == {"child", "grandchild", "great-grandchild"}
        assert session.status == "running"
        _complete(session)
        client.requests.clear()
        if release_result == "closed":
            client.closed = True
        stopped = await manager.stop(session.session_id)
        assert stopped == {}
        unsubscribed = [params["threadId"] for method, params in client.requests if method == "thread/unsubscribe"]
        if release_result == "closed":
            assert not unsubscribed
        else:
            assert set(unsubscribed) == {session.session_id, "child", "grandchild", "great-grandchild"}
            assert client.closed is False
        assert not session.codex_subagent_thread_ids
        client.closed = False
        client.requests.clear()
        response = await manager.send_message(session.session_id, "前の会話を続ける")
        assert response["delivery"] == "reply_started"
        assert [method for method, _params in client.requests] == ["thread/resume", "turn/start"]
    finally:
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("notification_first", [False, True])
async def test_release_waits_for_each_thread_closure_and_keeps_shared_client(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, notification_first: bool
) -> None:
    """公開stopは親子の閉鎖を待ち、別会話を共有接続で継続できる。"""
    manager = server_manager.AgentsServerManager(None)
    backend = subject.AppServerManager(manager.sessions, manager._condition)
    install_backend(manager, "codex", backend)
    client = FakeCodexClient()
    backend.client = cast(Any, client)
    monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
    requested: asyncio.Queue[str] = asyncio.Queue()
    original = client.request

    async def request(method: str, params: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        if method != "thread/unsubscribe":
            return await original(method, params, **kwargs)
        assert params is not None
        thread_id = params["threadId"]
        if notification_first:
            await backend._handle_notification({"method": "thread/closed", "params": {"threadId": thread_id}})
        requested.put_nowait(thread_id)
        return {"status": "unsubscribed"}

    monkeypatch.setattr(client, "request", request)
    session = await backend.start("開始", str(tmp_path))
    _complete(session)
    session.codex_subagent_thread_ids.add("child")
    other = await backend.resume("other-thread", state.ResumePrompt("独立会話"), str(tmp_path))
    stop = asyncio.create_task(manager.stop(session.session_id))
    try:
        for expected in ("child", session.session_id):
            assert await asyncio.wait_for(requested.get(), 1) == expected
            if not notification_first:
                assert not stop.done()
                assert session.session_id not in manager.stopped_sessions
                await backend._handle_notification({"method": "thread/closed", "params": {"threadId": "unrelated"}})
                assert not stop.done()
                await backend._handle_notification({"method": "thread/closed", "params": {"threadId": expected}})
        assert await asyncio.wait_for(stop, 1) == {}
        assert not client.closed
        assert (await manager.send_message(other.session_id, "別会話を続ける"))["delivery"] == "steered"
        assert (await manager.send_message(session.session_id, "保存会話を続ける"))["delivery"] == "reply_started"
        assert not backend._resume_close_events
    finally:
        stop.cancel()
        await asyncio.gather(stop, return_exceptions=True)
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "request", "connection", "reader", "invalid-status"])
async def test_failed_thread_closure_keeps_stop_unreleased(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """閉鎖を確認できないstopは失敗を返し、再試行の対象と追跡済み子を保持する。"""
    manager = server_manager.AgentsServerManager(None)
    backend = subject.AppServerManager(manager.sessions, manager._condition)
    install_backend(manager, "codex", backend)
    client = FakeCodexClient()
    backend.client = cast(Any, client)
    entered = asyncio.Event()

    async def request(_method: str, _params: dict[str, Any]) -> dict[str, Any]:
        entered.set()
        if failure == "request":
            raise subject.AppServerError("unsubscribe rejected")
        if failure == "invalid-status":
            return {}
        return {"status": "unsubscribed"}

    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(subject, "DEFAULT_WAIT_TIMEOUT", 0.05)
    session = state.SessionState("thread", str(tmp_path), engine="codex", status="completed", turn_completed=True)
    session.codex_subagent_thread_ids.add("child")
    manager.sessions[session.session_id] = session
    stop = asyncio.create_task(manager.stop(session.session_id))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if failure in {"connection", "reader"}:
            if failure == "connection":
                client.closed = True
            else:
                client.reader_failure = RuntimeError("reader failed")
            await backend._handle_client_failure(RuntimeError("connection lost"))
        with pytest.raises(TimeoutError if failure == "timeout" else subject.AppServerError):
            await stop
        assert manager.sessions[session.session_id] is session
        assert session.session_id not in manager.stopped_sessions
        assert session.codex_subagent_thread_ids == {"child"}
        assert not backend._resume_close_events
    finally:
        stop.cancel()
        await asyncio.gather(stop, return_exceptions=True)
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("notification_first", [False, True])
async def test_resume_waits_for_thread_close_after_unsubscribe(
    monkeypatch: pytest.MonkeyPatch, notification_first: bool
) -> None:
    """閉鎖中の再開拒否は対象threadの閉鎖通知で解消し、閉鎖通知が先に届いても取りこぼさない。"""
    backend = subject.AppServerManager()
    client = FakeCodexClient()
    closed_notice = {"method": "thread/closed", "params": {"threadId": "thread-1"}}
    rejected = asyncio.Event()
    attempts = 0

    async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal attempts
        assert method == "thread/resume" and params == {"threadId": "thread-1"}
        attempts += 1
        if attempts == 1:
            if notification_first:
                await backend._handle_notification(closed_notice)
            rejected.set()
            raise subject.JsonRpcResponseError(
                method, -32600, "thread thread-1 is closing; retry thread/resume after the thread is closed"
            )
        return {"thread": {"id": "thread-1"}}

    monkeypatch.setattr(client, "request", request)
    task = asyncio.create_task(backend._request_thread_resume(client, {"threadId": "thread-1"}))
    try:
        await asyncio.wait_for(rejected.wait(), timeout=1)
        if not notification_first:
            await backend._handle_notification({"method": "thread/closed", "params": {"threadId": "other"}})
            assert not task.done() and attempts == 1
            await backend._handle_notification(closed_notice)
        assert await asyncio.wait_for(task, timeout=1) == {"thread": {"id": "thread-1"}}
        assert attempts == 2
        assert not backend._resume_close_events
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_resume_close_wait_preserves_initialization_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """閉鎖通知が届かなければ初期化上限で終了し、再送と待機登録を残さない。"""
    backend = subject.AppServerManager()
    client = FakeCodexClient()
    request = AsyncMock(
        side_effect=subject.JsonRpcResponseError(
            "thread/resume", -32600, "thread thread-1 is closing; retry thread/resume after the thread is closed"
        )
    )
    monkeypatch.setattr(client, "request", request)
    monkeypatch.setattr(subject.shared_state, "SESSION_INITIALIZATION_TIMEOUT", 0.01)
    with pytest.raises(TimeoutError):
        await backend._request_thread_resume(client, {"threadId": "thread-1"})
    assert request.await_count == 1
    assert not backend._resume_close_events


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_interrupt_response_error_is_recorded_on_target_turn(tmp_path: pathlib.Path) -> None:
    """turn/interruptの応答エラーを対象turnの状態へ記録する。"""
    manager = subject.AppServerManager()
    manager.client = cast(Any, InterruptErrorClient())
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    await manager._interrupt(session.session_id, session.turn_id)

    assert session.error == {"message": "turn/interrupt: interrupt rejected"}
    assert session.protocol_warnings == ["turn/interrupt failed: turn/interrupt: interrupt rejected"]


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
    client = subject.JsonRpcProcess(
        lambda _message: asyncio.sleep(0), lambda _message: asyncio.sleep(0), root_session_id="owner-session"
    )
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
async def test_codex_json_rpc_request_marks_sent_before_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON-RPC送信完了を応答待ちより先に呼出側へ通知する。"""
    client = subject.JsonRpcProcess(lambda _message: asyncio.sleep(0), lambda _message: asyncio.sleep(0))
    client.process = cast(Any, SimpleNamespace(stdin=object()))
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


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_backend_send_supports_terminal_reply_without_result_flag(tmp_path: pathlib.Path) -> None:
    """Codexの終端後のreplyは結果回収フラグなしで開始できる。"""
    manager = subject.AppServerManager()
    client = FakeCodexClient()
    manager.client = cast(Any, client)
    session = state.SessionState("thread-codex", str(tmp_path), engine="codex")
    session.turn_id = "turn-old"
    _complete(session, message="Codex結果")
    manager.sessions[session.session_id] = session
    response = await manager.send_message(session, "続行")
    assert response["delivery"] == "reply_started"
    assert response["status"] == "running"
    assert response["previous_result"]["agent_message"] == "Codex結果"
    _assert_no_forbidden_keys(response)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_progress_notification_is_shared(tmp_path: pathlib.Path) -> None:
    """Codexのdelta通知を共有SessionStateのprogressへ反映する。"""
    manager = subject.AppServerManager()
    session = state.SessionState("thread-codex", str(tmp_path), engine="codex")
    session.turn_id = "turn-1"
    manager.sessions[session.session_id] = session
    await manager._handle_notification(
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-codex", "turnId": "turn-1", "itemId": "item-1", "delta": "進捗\n"},
        }
    )
    assert session.progress == "進捗 "

    await manager._handle_notification(
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-codex", "turnId": "turn-1", "itemId": "item-1", "delta": "途中"},
        }
    )
    assert session.progress == "進捗 途中"

    await manager._handle_notification(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-codex",
                "turnId": "turn-1",
                "item": {"id": "item-1", "type": "agentMessage", "text": "完成した全文"},
            },
        }
    )
    assert session.progress == "完成した全文"

    await manager._handle_notification(
        {
            "method": "item/plan/delta",
            "params": {"threadId": "thread-codex", "turnId": "turn-1", "itemId": "plan-1", "delta": "計画"},
        }
    )
    assert session.progress == "完成した全文"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_shared_manager_integrates_codex_start_and_send_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """共有MCP層から実Codexバックエンドの開始と継続入力を通す。"""
    manager = server_manager.AgentsServerManager(status_writer=None)
    backend = subject.AppServerManager(manager.sessions, manager._condition)
    client = FakeCodexClient()

    async def ensure_client() -> FakeCodexClient:
        return client

    monkeypatch.setattr(backend, "_ensure_client", ensure_client)
    install_backend(manager, "codex", backend)
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [("codex", "gpt-test", "high")]
    )
    response = await manager.start("plan", "調査", str(tmp_path))
    assert response == {
        "session_id": "thread-codex",
        "engine": "codex",
        "status": "running",
        "model_type": "plan",
        "model": "gpt-test",
        "effort": "high",
        "fast_mode": False,
        "label": "調査",
    }
    backend.client = cast(Any, client)
    steered = await manager.send_message("thread-codex", "追加指示")
    assert steered == {"delivery": "steered", "label": "調査"}
    assert manager.sessions["thread-codex"].turn_seq == 1
    assert client.requests[-1][0] == "turn/steer"
    killed = await manager.kill("thread-codex", timeout=0)
    assert killed["kill_requested"] is True
    assert client.requests[-1][0] == "turn/interrupt"
    with pytest.raises(ValueError, match="being interrupted") as raised:
        await manager.send_message("thread-codex", "競合入力")
    assert "atk agents wait" in _actionable_message(raised.value)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_shared_manager_send_message_resumes_expired_codex_thread(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """共有MCP層のstartが保存済みCodex threadを再開する。"""
    manager = server_manager.AgentsServerManager()
    backend = subject.AppServerManager(manager.sessions, manager._condition)
    client = FakeCodexClient()

    async def ensure_client() -> FakeCodexClient:
        return client

    monkeypatch.setattr(backend, "_ensure_client", ensure_client)
    install_backend(manager, "codex", backend)

    manager.expired_sessions["thread-saved"] = state.SessionResumeState(
        session_id="thread-saved",
        cwd=str(tmp_path),
        model="gpt-test",
        effort="high",
        engine="codex",
    )
    response = await manager.send_message("thread-saved", "続行")

    assert _without_root(response) == {"delivery": "reply_started", "label": ""}
    assert client.requests[0] == (
        "thread/resume",
        {
            "threadId": "thread-saved",
            "cwd": str(tmp_path),
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
            "serviceTier": "default",
            "model": "gpt-test",
            "config": {"bypass_hook_trust": True},
            "developerInstructions": subject._developer_instructions("delegate"),  # noqa: SLF001
        },
    )
    assert client.requests[1][0] == "turn/start"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_resume_timeout_drops_prompt_without_duplicate_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Codex再開のtimeout後に旧promptを配送せず、後続入力で再開を重複させない。"""
    manager = server_manager.AgentsServerManager()
    backend = subject.AppServerManager(manager.sessions, manager._condition)
    client = BlockingResumeCodexClient()

    async def ensure_client() -> BlockingResumeCodexClient:
        return client

    monkeypatch.setattr(backend, "_ensure_client", ensure_client)
    backend.client = cast(Any, client)
    install_backend(manager, "codex", backend)
    session_id = "thread-pending"
    manager.expired_sessions[session_id] = state.SessionResumeState(
        session_id=session_id,
        cwd=str(tmp_path),
        model=None,
        effort=None,
        engine="codex",
    )

    try:
        with pytest.raises(TimeoutError, match="send_message timed out: thread-pending"):
            await manager.send_message(session_id, "再開指示", timeout=0.01)

        response = await manager.wait()
        assert set(response) == {"session_id", "status", "progress", "elapsed_seconds"}
        assert response["status"] == "running"
        assert response["progress"] == ""
        assert isinstance(response["elapsed_seconds"], int)
        assert response["elapsed_seconds"] >= 0
        assert session_id not in manager.expired_sessions

        client.release_resume.set()
        await asyncio.wait_for(client.resume_finished.wait(), timeout=0.1)
        assert [method for method, _params in client.requests] == ["thread/resume"]
        response = await manager.send_message(session_id, "後続指示", timeout=1)

        assert response["delivery"] == "reply_started"
        assert [method for method, _params in client.requests].count("thread/resume") == 1
        turn_starts = [params for method, params in client.requests if method == "turn/start"]
        delivered = [item["text"] for params in turn_starts for item in params["input"]]
        assert [delivery_payload(value) for value in delivered] == ["後続指示"]
    finally:
        client.release_resume.set()
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_id",
    ["3468feae-b2bf-4d67-ac55-3c40207e8b5b", "thread-pending"],
)
async def test_codex_kill_interrupts_turn_with_pending_start_response(
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """再開turnの応答待ちでもkillだけでApp Server側作業を終える。"""
    manager, backend, client = _pending_codex_manager(monkeypatch, HoldingTurnStartClient)
    manager.expired_sessions[session_id] = state.SessionResumeState(
        session_id=session_id,
        cwd=str(tmp_path),
        model=None,
        effort=None,
        engine="codex",
    )

    try:
        await _start_blocked_codex_resume(manager, client, session_id)

        response = await manager.kill(session_id, timeout=1)

        assert response["status"] == "interrupted"
        assert response["kill_requested"] is True
        assert [method for method, _params in client.requests] == [
            "thread/resume",
            "turn/start",
            "turn/interrupt",
        ]
        assert client.active_turn is False
        assert not manager._pending_resumes
        assert not backend._background_tasks
    finally:
        await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_id",
    ["3468feae-b2bf-4d67-ac55-3c40207e8b5b", "thread-pending"],
)
async def test_codex_kill_reports_no_request_when_pending_turn_completes_naturally(
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保留中のresumeを取り消す間に自然に終了したturnを、中断要求済みと報告しない。"""
    manager, backend, client = _pending_codex_manager(monkeypatch, NaturallyCompletingTurnStartClient)
    manager.expired_sessions[session_id] = state.SessionResumeState(
        session_id=session_id,
        cwd=str(tmp_path),
        model=None,
        effort=None,
        engine="codex",
    )

    try:
        await _start_blocked_codex_resume(manager, client, session_id)
        pending = manager._pending_resumes[session_id]

        kill_task = asyncio.create_task(manager.kill(session_id, timeout=1))
        while pending.task.cancelling() == 0:
            await asyncio.sleep(0)
        await client.complete_turn(session_id)
        response = await kill_task

        assert response["status"] == "completed"
        assert response["kill_requested"] is False
        assert [method for method, _params in client.requests] == [
            "thread/resume",
            "turn/start",
        ]
        assert client.active_turn is False
        assert not manager._pending_resumes
        assert not backend._background_tasks
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["success", "namespace", "other", "rpc", "timeout", "invalid", "wrong-code"])
async def test_explore_sandbox_probe_distinguishes_failure_kinds(
    failure: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開の起動では確定した名前空間の作成拒否だけを候補不成立として扱い、probeの接続をthread開始より先に回収する。"""
    probe = _SandboxProbeClient()
    if failure in {"namespace", "wrong-code"}:
        probe.response = {
            "exitCode": 1 if failure == "namespace" else 124,
            "stderr": "bwrap: No permissions to create a new namespace",
            "stdout": "",
        }
    elif failure == "other":
        probe.response = {"exitCode": 1, "stderr": "permission denied", "stdout": ""}
    elif failure == "invalid":
        probe.response = {"exitCode": True, "stderr": "", "stdout": ""}
    elif failure == "rpc":
        probe.error = subject.JsonRpcResponseError("command/exec", -32600, "request rejected")
    elif failure == "timeout":
        probe.error = TimeoutError("probe deadline")
    monkeypatch.setattr(subject, "JsonRpcProcess", lambda *_args, **_kwargs: probe)
    manager = subject.AppServerManager()
    client = _TierClient()

    async def ensure_client() -> _TierClient:
        assert probe.closed
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    if failure == "success":
        session = await manager.start("調査", str(tmp_path), launch_kind="explore")
        assert session.session_id == "tier-thread"
        assert [method for method, _params in client.requests] == ["thread/start", "turn/start"]
    else:
        error_type = (
            subject.StartCandidateUnavailable
            if failure == "namespace"
            else (TimeoutError if failure == "timeout" else subject.AppServerError)
        )
        with pytest.raises(error_type) as raised:
            await manager.start("調査", str(tmp_path), launch_kind="explore")
        if failure == "namespace":
            assert isinstance(raised.value, subject.StartCandidateUnavailable)
            assert raised.value.reason == "exploreSandboxNamespaceDenied"
        else:
            assert not isinstance(raised.value, subject.StartCandidateUnavailable)
        assert not manager.sessions and not client.requests
    assert probe.closed
    params = probe.requests[0][1]
    assert params["cwd"] == str(tmp_path)
    assert params["sandboxPolicy"] == {"type": "readOnly"}
    assert params["disableOutputCap"] is True
    assert params["timeoutMs"] > 0
    await manager.close()


@pytest.mark.asyncio
async def test_context_usage_notification_is_session_scoped(tmp_path: pathlib.Path) -> None:
    """0.162.1の通知が持つ`last`だけを同じthread・turnへ配送し、累計と活動を混ぜない。"""
    # codex-cli 0.162.1 generate-json-schemaのThreadTokenUsageUpdatedNotification。
    session = state.SessionState("usage-thread", str(tmp_path), engine="codex", turn_id="usage-turn")
    other = state.SessionState("other-thread", str(tmp_path), engine="codex", turn_id="other-turn")
    manager = _InspectableAppServerManager({session.session_id: session, other.session_id: other})
    initial_activity = session.updated_at

    async def notify(thread: str, turn: str, tokens: Any, window: Any = 1000000) -> None:
        await manager.handle_notification(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": thread,
                    "turnId": turn,
                    "tokenUsage": {
                        "last": {"totalTokens": tokens},
                        "total": {"totalTokens": 999999},
                        "modelContextWindow": window,
                    },
                },
            }
        )

    await notify(session.session_id, session.turn_id, 53500)
    assert session.context_tokens == 53500 and session.context_window == 1000000
    await notify("missing-thread", session.turn_id, 1)
    await notify(other.session_id, session.turn_id, 2)
    await notify(session.session_id, "old-turn", 3)
    assert session.context_tokens == 53500 and other.context_tokens is None
    await notify(session.session_id, session.turn_id, 1000)
    assert session.context_tokens == 1000
    await notify(session.session_id, session.turn_id, 0, None)
    assert session.context_tokens == 0 and session.context_window is None
    assert session.updated_at == initial_activity
    await notify(session.session_id, session.turn_id, True)
    assert session.context_tokens is None
    await manager.close()
