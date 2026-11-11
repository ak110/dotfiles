"""claudeの基本動作の契約を検証する。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import json
import pathlib
import sys
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from agent_toolkit._agents_server import claude, claude_runtime, state
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import process_tree, session_registry, state_paths
from agent_toolkit._testing import late_descendant
from agent_toolkit._testing.agents_server_claude_support import _TransportClient
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    DelayedClaudeClient,
    ErrorAssistantMessage,
    FailingClaudeClient,
    FakeClaudeClient,
    InterruptAwareClaudeClient,
    MultipleBlockAssistantMessage,
    QueueClaudeClient,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    _assert_no_forbidden_keys,
    _start_claude_until_available,
    install_backend,
)


@pytest.mark.parametrize("session_id", [None, "saved-session"])
@pytest.mark.usefixtures("agents_server_isolation")
def test_explore_mcp_uses_plugin_definition_on_start_and_resume(tmp_path: pathlib.Path, session_id: str | None) -> None:
    """pyfltrの既存起動定義を使い、個別MCPツールの用途は探索プロンプトへ委ねる。"""
    options = claude._build_options(str(tmp_path), "model", "high", session_id, "explore")
    root = claude.plugin_roots.SERVER_PLUGIN_ROOT
    definition = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["pyfltr"]
    definition["args"] = [arg.replace("${CLAUDE_PLUGIN_ROOT}", str(root)) for arg in definition["args"]]
    assert options.mcp_servers == {"pyfltr": definition}
    assert "mcp__pyfltr__*" in options.allowed_tools
    assert options.disallowed_tools == ["Skill"]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_start_records_owner_and_cli_processes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """登録簿の記録は、所有プロセスとSDKの転送が起動したClaude CLIの識別を持つ。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    owner = session_registry.owner_process()
    assert owner is not None
    options = SimpleNamespace(system_prompt={"type": "preset", "preset": "claude_code"})
    with late_descendant.held_process(tmp_path, env=host_environ()) as process:
        child = session_registry.ProcessIdentity.of(process.pid)
        assert child is not None
        client = _TransportClient([[SystemMessage("claude-recorded"), ResultMessage("完了")]], pid=process.pid)
        async with claude.ClaudeServerManager(client_factory=lambda _options: client, publish_registry=True) as manager:
            monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: options)
            session = await manager.start("調査", str(tmp_path))
            record = session_registry.registry_directory() / f"{session.session_id}.json"
            processes = json.loads(record.read_text(encoding="utf-8"))["processes"]
        assert processes == [
            {"pid": owner.pid, "create_time": owner.create_time},
            {"pid": child.pid, "create_time": child.create_time},
        ]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_start_result_wait_and_reply(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Claudeのinit、結果の受信、終了後のreplyを1つの長命タスクで処理する。"""
    client = FakeClaudeClient(
        [
            [SystemMessage("claude-session"), AssistantMessage("途中経過"), ResultMessage("Claude結果")],
            [SystemMessage("claude-session"), AssistantMessage("reply中"), ResultMessage("reply結果")],
        ]
    )
    options = SimpleNamespace(system_prompt={"type": "preset", "preset": "claude_code"})
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: options)
        session = await manager.start("調査", str(tmp_path), "model", "high")
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert session.session_id == "claude-session"
        assert session.status == "completed"
        assert session.agent_message == "Claude結果"
        assert session.progress == "途中経過"
        reply = await manager.send_message(session, "続行")
        assert reply["delivery"] == "reply_started"
        assert reply["previous_result"]["agent_message"] == "Claude結果"
        _assert_no_forbidden_keys(reply)
        assert client.queries == ["調査", "続行"]
    assert client.connected is True
    assert client.disconnected is True


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_resources_remain_identical_when_init_is_resent_per_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """initの再送時も所有タスクのキューと状態を再生成しない。"""
    client = FakeClaudeClient(
        [
            [SystemMessage("claude-stable"), ResultMessage("初回結果")],
            [SystemMessage("claude-stable"), ResultMessage("1回目のreply結果")],
            [SystemMessage("claude-stable"), ResultMessage("2回目のreply結果")],
        ]
    )
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        session = await manager.start("調査", str(tmp_path))
        channel = manager._channels[session.session_id]
        for prompt, expected in (("続行1", "1回目のreply結果"), ("続行2", "2回目のreply結果")):
            for _ in range(200):
                if session.result_available:
                    break
                await asyncio.sleep(0.01)
            assert session.result_available is True
            reply = await asyncio.wait_for(manager.send_message(session, prompt), timeout=5)
            assert reply["delivery"] == "reply_started"
            for _ in range(200):
                if session.agent_message == expected:
                    break
                await asyncio.sleep(0.01)
            assert session.agent_message == expected
            assert manager._channels[session.session_id] is channel
            assert manager.sessions[session.session_id] is session


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_kill_uses_owner_task_interrupt_and_maps_terminal_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Claudeのkillは所有タスクからinterruptを呼び、中断理由を状態へ写像する。"""
    client = InterruptAwareClaudeClient()
    async with (
        server_manager.AgentsServerManager() as manager,
        claude.ClaudeServerManager(manager.sessions, manager._condition, client_factory=lambda _options: client) as backend,
    ):
        install_backend(manager, "claude", backend)
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())

        try:
            monkeypatch.setattr(
                _atk_config,
                "parse_unresolved_model_candidates",
                lambda _model_type, **_kwargs: [("claude", "model", "high")],
            )
            start_response = await manager.start("plan", "調査", str(tmp_path))
            response = await manager.kill(start_response["session_id"], timeout=1)
            assert response["status"] == "interrupted"
            assert response["kill_requested"] is True
            assert client.disconnected
            assert client.interrupts == 1
        finally:
            await manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_message_gap_does_not_cancel_stream(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """旧poll期限を過ぎてもstreamを維持し、解放後の結果を返す。"""
    client = DelayedClaudeClient([[SystemMessage("claude-delayed"), AssistantMessage("途中"), ResultMessage("完了")]])
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        starting = asyncio.create_task(manager.start("調査", str(tmp_path)))
        assert await asyncio.wait_for(client.requested.get(), timeout=5) == 1
        loop = asyncio.get_running_loop()
        after_poll_deadline = loop.time() + 0.1
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(loop, "time", lambda: after_poll_deadline)
            advanced = asyncio.Event()
            loop.call_later(0, advanced.set)
            await advanced.wait()
            assert not client.cancelled.is_set()
        client.release.put_nowait(None)
        session = await starting
        assert await asyncio.wait_for(client.requested.get(), timeout=5) == 2
        assert not session.result_available and session.progress == "途中"
        client.release.put_nowait(None)
        for _ in range(40):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert session.status == "completed"
        assert session.agent_message == "完了"
        assert session.progress == "途中"
    assert client.disconnected is True


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_start_returns_on_message_start(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """APIの応答開始を受信した時点で、完成したメッセージを待たずに`start`の可用性待機を終える。"""
    response, elapsed, observed = await _start_claude_until_available(
        monkeypatch, tmp_path, [SystemMessage("claude-streaming"), StreamEvent("message_start")], 5.0
    )

    assert response["session_id"] == "claude-streaming"
    assert elapsed < 2.0
    assert observed


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_start_ignores_non_start_stream_events(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """応答開始以外の部分出力とAPI失敗のメッセージでは、可用性待機を上限まで続ける。"""
    messages = [
        SystemMessage("claude-unstarted"),
        StreamEvent("content_block_delta"),
        ErrorAssistantMessage("API Error: 429 rate limit"),
    ]
    response, elapsed, observed = await _start_claude_until_available(monkeypatch, tmp_path, messages, 0.3)

    assert response["session_id"] == "claude-unstarted"
    assert elapsed >= 0.3
    assert not observed


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_concatenates_multiple_text_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """Claudeの複数TextBlockを改行区切りで連結して進捗へ保持する。"""
    assistant_message = AssistantMessage("一")
    assistant_message.content = MultipleBlockAssistantMessage("一", "二").content
    client = FakeClaudeClient([[SystemMessage("claude-blocks"), assistant_message, ResultMessage("完了")]])
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        session = await manager.start("調査", str(tmp_path))
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert session.progress == "一 二"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_task_exception_disconnects_and_retains_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """message taskの例外時に切断し、failedの結果を共有sessionへ保持する。"""
    client = FailingClaudeClient([])
    sessions: dict[str, state.SessionState] = {}
    async with claude.ClaudeServerManager(sessions, client_factory=lambda _options: client) as manager:
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        session = await manager.start("調査", str(tmp_path))
        for _ in range(20):
            if client.disconnected:
                break
            await asyncio.sleep(0.01)
        assert client.disconnected is True
        assert sessions[session.session_id] is session
        assert session.status == "failed"
        # 初期化の完了後の失敗は、原因の特定に要する診断を終了時の結果へ添える。
        assert session.error["message"] == "stream failed"
        assert set(session.error) == {"message", "engine", "model", "stderr", "childPid", "debugFile"}
        assert session.error["engine"] == "claude"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_failure_keeps_last_nonempty_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """空の後続本文と失敗結果は最後の非空assistant本文を失わない。"""
    result = ResultMessage("")
    result.is_error = True
    client = FakeClaudeClient([[SystemMessage("claude-failed"), AssistantMessage("途中経過"), AssistantMessage(""), result]])
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        session = await manager.start("調査", str(tmp_path))
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert session.status == "failed"
        assert session.agent_message == "途中経過"
        assert session.progress == "途中経過"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_empty_result_is_not_completed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """非空assistant本文を伴わないResultMessageは正常完了にしない。"""
    client = FakeClaudeClient([[SystemMessage("claude-empty"), AssistantMessage(""), ResultMessage("")]])
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        session = await manager.start("調査", str(tmp_path))
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert session.status == "failed"
        assert session.error == {"message": "Claude Agent SDK returned no assistant output"}


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_retention_expiry_disconnects_and_retains_result_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保持期限経過時にSDKを切断し、未回収結果を伴う再開状態を退避する。"""
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.01)
    client = FakeClaudeClient([[SystemMessage("claude-expired"), ResultMessage("完了")]])
    sessions: dict[str, state.SessionState] = {}
    async with server_manager.AgentsServerManager() as manager:
        manager.sessions = sessions
        async with claude.ClaudeServerManager(
            sessions,
            manager._condition,
            client_factory=lambda _options: client,
            expire_session=manager._expire_session,
        ) as backend:
            monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
            session = await backend.start("調査", str(tmp_path))
            for _ in range(20):
                if client.disconnected and session.session_id not in sessions:
                    break
                await asyncio.sleep(0.01)
            assert client.disconnected is True
            assert session.session_id not in sessions
            assert manager.expired_sessions == {
                session.session_id: state.SessionResumeState(
                    session_id=session.session_id,
                    cwd=str(tmp_path),
                    prompt="調査",
                    model=None,
                    effort=None,
                    engine="claude",
                    claude_connection="inherit",
                    claude_role="delegate",
                    label=session.label,
                    created_at=session.created_at,
                    started_at=session.started_at,
                    updated_at=session.updated_at,
                    turn_seq=session.turn_seq,
                    status="completed",
                    agent_message="完了",
                    final_responses=((1, "完了"),),
                    finalized_at=session.finalized_at,
                    result_delivered=False,
                    retention_deadline=session.retention_deadline,
                )
            }
            response = await manager.wait()
            assert response == {"session_id": session.session_id, "status": "completed", "agent_message": "完了"}
            assert await manager.wait() == {"status": "expired"}


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_claude_server_close_disconnects_and_retains_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """サーバー終了時にSDKを切断し、取得済みの結果recordは保持する。"""
    client = FakeClaudeClient([[SystemMessage("claude-close"), ResultMessage("完了")]])
    sessions: dict[str, state.SessionState] = {}
    async with claude.ClaudeServerManager(sessions, client_factory=lambda _options: client) as backend:
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        session = await backend.start("調査", str(tmp_path))
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        await backend.close()
        assert client.disconnected is True
        assert sessions[session.session_id].agent_message == "完了"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_close_reclaims_cli_when_sdk_disconnect_never_returns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """SDK切断が返らない場合も、切断前に確保した実CLIと子孫を回収する。"""
    script = tmp_path / "runtime.py"
    script.write_text(
        "import pathlib, subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', {late_descendant.held_source(tmp_path / 'nested.release')!r}])\n"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        "child.wait(timeout=120)\n",
        encoding="utf-8",
    )
    disconnect_started = asyncio.Event()

    class StalledClient(QueueClaudeClient):
        _transport: Any

        async def disconnect(self) -> None:
            disconnect_started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(claude_runtime, "_DISCONNECT_TIMEOUT", 0.05)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    with process_tree.process_scope([sys.executable, str(script), str(tmp_path / "child.pid")], env=host_environ()) as process:
        targets = [psutil.Process(process.pid)]
        client = StalledClient([[SystemMessage("claude-stalled-disconnect"), ResultMessage("完了")]])
        client._transport = SimpleNamespace(_process=process)
        try:

            async def ready() -> None:
                while not (tmp_path / "child.pid").exists():
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(ready(), 10)
            targets += process_tree.collect_descendants(process.pid)
            assert len(targets) == 2
            async with claude.ClaudeServerManager({}, client_factory=lambda _options: client) as backend:
                await backend.start("停止試験", str(tmp_path))
                await asyncio.wait_for(backend.close(), 10)
            assert disconnect_started.is_set()
            await asyncio.to_thread(process.wait, timeout=2)
            assert all(
                not target.is_running() or target.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD) for target in targets
            )
        finally:
            await asyncio.to_thread(process_tree.reclaim_processes, targets, timeout=0.5)
            await asyncio.to_thread(process.wait, timeout=2)
