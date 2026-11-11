"""`_agents_server/manager_resume.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import pathlib
from types import SimpleNamespace
from typing import Any

import pytest

from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import shared_roots, state, status_file
from agent_toolkit._testing.agents_server_support import (
    BlockingResultClaudeClient,
    BlockingResumeBackend,
    BlockingResumeClaudeClient,
    ConcurrentOwnerGoneBackend,
    FakeClaudeClient,
    ResultMessage,
    SystemMessage,
    _blocking_resume_claude_manager,
    _complete,
    install_backend,
)
from agent_toolkit._testing.helpers import delivery_payload

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_send_message_timeout_covers_resume(tmp_path: pathlib.Path) -> None:
    """継続要求のtimeoutは保存済みsessionの再開待ちにも適用する。"""
    async with server_manager.AgentsServerManager() as manager:
        backend = BlockingResumeBackend(manager.sessions, "claude")
        install_backend(manager, "claude", backend)
        session = state.SessionState("claude-expired", str(tmp_path), engine="claude")
        _complete(session)
        session.retention_deadline = asyncio.get_running_loop().time() - 1
        manager.sessions[session.session_id] = session

        manager._start_resume(state.SessionResumeState.from_session(session), "準備指示", None)  # pylint: disable=protected-access
        await asyncio.wait_for(backend.resume_started.wait(), timeout=1)
        with pytest.raises(TimeoutError, match="send_message timed out: claude-expired"):
            await manager.send_message(session.session_id, "追加指示", timeout=0.01)

        assert not backend.resume_calls

    assert not manager._pending_resumes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "session_id",
    ["3468feae-b2bf-4d67-ac55-3c40207e8b5b", "claude-expired"],
)
async def test_kill_cancels_pending_resume_without_followup_message(
    session_id: str,
    tmp_path: pathlib.Path,
) -> None:
    """再開入力のtimeout後は追加送信なしのkillで保留作業を回収する。"""
    async with server_manager.AgentsServerManager() as manager:
        backend = BlockingResumeBackend(manager.sessions, "claude")
        install_backend(manager, "claude", backend)
        session = state.SessionState(session_id, str(tmp_path), engine="claude")
        _complete(session)
        session.retention_deadline = asyncio.get_running_loop().time() - 1
        manager.sessions[session.session_id] = session

        manager._start_resume(state.SessionResumeState.from_session(session), "準備指示", None)  # pylint: disable=protected-access
        await asyncio.wait_for(backend.resume_started.wait(), timeout=1)
        with pytest.raises(TimeoutError, match=f"send_message timed out: {session_id}"):
            await manager.send_message(session.session_id, "追加指示", timeout=0.01)

        response = await manager.kill(session.session_id, timeout=1)

        assert response["status"] == "interrupted"
        assert response["kill_requested"] is False
        assert not manager._pending_resumes
        assert backend.resume_finished.is_set()
        assert backend.interrupt_calls == 0


@pytest.mark.asyncio
async def test_concurrent_owner_gone_send_messages_resume_once(tmp_path: pathlib.Path) -> None:
    """所有主体終了を同時検出した継続要求は再開を共有して両方を配送する。"""
    async with server_manager.AgentsServerManager() as manager:
        backend = ConcurrentOwnerGoneBackend(manager.sessions, "claude")
        install_backend(manager, "claude", backend)
        session = state.SessionState("claude-race", str(tmp_path), engine="claude")
        manager.sessions[session.session_id] = session
        backend.owner_gone_session = session

        first, second = await asyncio.gather(
            manager.send_message(session.session_id, "先行指示", timeout=1),
            manager.send_message(session.session_id, "後続指示", timeout=1),
        )

        assert backend.resume_calls == [session.session_id]
        assert {first["delivery"], second["delivery"]} == {"reply_started", "steered"}


@pytest.mark.asyncio
@pytest.mark.parametrize("root", ["manager-one", "manager-two"])
async def test_each_manager_delivers_its_root_to_claude_start_and_resume(
    root: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """`AGENT_TOOLKIT_OWNER_SESSION`が別のrootを指していても、SDKで新規に開始するsessionと再開するsessionへ各Managerのrootを渡す。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "other-environment-root")
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity(root, "root.json", None), state_root=tmp_path)
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = manager._backend("claude")
        options_received: list[Any] = []

        def client_factory(options: Any) -> FakeClaudeClient:
            options_received.append(options)
            return FakeClaudeClient([[SystemMessage("claude-root-test"), ResultMessage("完了")]])

        monkeypatch.setattr(backend, "_client_factory", client_factory)
        session = await backend.start("新規", str(tmp_path))
        await backend.resume(session.session_id, state.ResumePrompt("再開"), str(tmp_path))
        assert len(options_received) == 2
        assert [options.env["AGENT_TOOLKIT_OWNER_SESSION"] for options in options_received] == [root, root]
        assert options_received[0].resume is None
        assert options_received[1].resume == session.session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("query_started_before_timeout", [False, True])
async def test_claude_resume_timeout_drops_prompt_without_duplicate_resume(
    query_started_before_timeout: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """query開始の前後で期限切れになっても旧promptを配送せず、再開を重複しない。"""
    client = BlockingResumeClaudeClient()
    async with _blocking_resume_claude_manager(monkeypatch, client) as (manager, _):
        session_id = "claude-pending"
        manager.expired_sessions[session_id] = state.SessionResumeState(
            session_id=session_id,
            cwd=str(tmp_path),
            model=None,
            effort=None,
            engine="claude",
        )

        pending = None
        initial_ticket = 0
        release_connect = asyncio.Event()
        try:
            if query_started_before_timeout:
                resume_state = manager.expired_sessions[session_id]
                pending, initial_ticket = manager._start_resume(resume_state, "準備指示", None)  # pylint: disable=protected-access
                await asyncio.wait_for(client.query_started.wait(), timeout=1)
            else:
                original_connect = client.connect

                async def blocked_connect() -> None:
                    await release_connect.wait()
                    await original_connect()

                monkeypatch.setattr(client, "connect", blocked_connect)

            with pytest.raises(TimeoutError, match="send_message timed out: claude-pending"):
                await manager.send_message(session_id, "再開指示", timeout=0.01)

            if query_started_before_timeout:
                assert pending is not None
                pending.prompt.cancel(initial_ticket)
                await asyncio.wait_for(client.query_cancelled.wait(), timeout=1)
            else:
                assert not client.query_started.is_set()
                release_connect.set()
                client.release_query.set()
            assert not client.queries
            response = await manager.wait()
            assert set(response) == {"session_id", "status", "progress", "elapsed_seconds"}
            assert response["status"] == "running"
            assert response["progress"] == ""
            assert isinstance(response["elapsed_seconds"], int)
            assert response["elapsed_seconds"] >= 0
            assert session_id not in manager.expired_sessions

            response = await manager.send_message(session_id, "後続指示", timeout=1)

            assert response["delivery"] == "reply_started"
            assert client.connect_calls == 1
            assert [delivery_payload(value) for value in client.queries] == ["後続指示"]
        finally:
            client.release_query.set()
            client.stop_stream.set()
            await manager.close()


@pytest.mark.asyncio
async def test_claude_kill_cleans_owned_pending_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保留resumeのkillは応答前にClaude所有taskとclientを回収する。"""
    client = BlockingResumeClaudeClient()
    async with _blocking_resume_claude_manager(monkeypatch, client) as (manager, backend):
        session_id = "claude-pending"
        manager.expired_sessions[session_id] = state.SessionResumeState(
            session_id=session_id,
            cwd=str(tmp_path),
            model=None,
            effort=None,
            engine="claude",
        )

        manager._start_resume(manager.expired_sessions[session_id], "準備指示", None)  # pylint: disable=protected-access
        await asyncio.wait_for(client.query_started.wait(), timeout=1)
        with pytest.raises(TimeoutError, match="send_message timed out: claude-pending"):
            await manager.send_message(session_id, "再開指示", timeout=0.01)

        assert backend._tasks
        response = await manager.kill(session_id, timeout=1)

        assert response["status"] == "interrupted"
        assert response["kill_requested"] is False
        assert not manager._pending_resumes
        assert not backend._tasks
        assert client.disconnected is True


@pytest.mark.asyncio
async def test_claude_pending_resume_retains_previous_result_after_retention_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """再開待機中に保持期限を越えた直前結果を後続応答へ含める。"""
    client = BlockingResumeClaudeClient()
    async with _blocking_resume_claude_manager(monkeypatch, client) as (manager, _):
        session_id = "claude-pending"
        session = state.SessionState(session_id, str(tmp_path), engine="claude")
        session.status = "completed"
        session.record_turn_completed()
        session.agent_message = "期限付き結果"
        session.touch()
        session.retention_deadline = asyncio.get_running_loop().time() + 0.04
        original_deadline = session.retention_deadline
        manager.sessions[session_id] = session

        try:
            pending, initial_ticket = manager._start_resume(  # pylint: disable=protected-access
                state.SessionResumeState.from_session(session), "準備指示", session.previous_result()
            )
            await asyncio.wait_for(client.query_started.wait(), timeout=1)
            with pytest.raises(TimeoutError, match="send_message timed out: claude-pending"):
                await manager.send_message(session_id, "期限前指示", timeout=0.01)

            pending.prompt.cancel(initial_ticket)
            await asyncio.wait_for(client.query_cancelled.wait(), timeout=0.1)
            with monkeypatch.context() as clock_patch:
                clock_patch.setattr(asyncio.get_running_loop(), "time", lambda: original_deadline + 0.01)
                response = await manager.send_message(session_id, "期限後指示", timeout=1)
            assert response["previous_result"]["agent_message"] == "期限付き結果"
            assert client.connect_calls == 1
            assert [delivery_payload(value) for value in client.queries] == ["期限後指示"]
        finally:
            client.release_query.set()
            client.stop_stream.set()
            await manager.close()


@pytest.mark.asyncio
async def test_claude_resume_owns_saved_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Claude backendがresume後の同じsession IDを新しい所有タスクへ登録する。"""
    client = FakeClaudeClient([[SystemMessage("claude-saved"), ResultMessage("再開結果")]])
    captured: dict[str, str | None] = {}

    def build_options(_cwd: str, _model: str | None, _effort: str | None, session_id: str | None = None, **_kwargs: Any) -> Any:
        captured["session_id"] = session_id
        return SimpleNamespace()

    async with claude_backend.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        monkeypatch.setattr(claude_backend, "_build_options", build_options)
        prompt = state.ResumePrompt("続行")
        session = await manager.resume("claude-saved", prompt, str(tmp_path), "model", "high")
        for _ in range(20):
            if session.result_available:
                break
            await asyncio.sleep(0.01)
        assert captured == {"session_id": "claude-saved"}
        assert session.session_id == "claude-saved"
        assert session.agent_message == "再開結果"


@pytest.mark.asyncio
async def test_claude_resume_replaces_retained_terminal_state_while_new_turn_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """resumeは登録簿に残る前turnの終了状態を新しいturnへ引き継がない。"""
    session_id = "claude-retained"
    client = BlockingResultClaudeClient(session_id)
    async with claude_backend.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        monkeypatch.setattr(claude_backend, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        retained = state.SessionState(session_id, str(tmp_path), engine="claude")
        _complete(retained, message="前turnの結果")
        manager.sessions[session_id] = retained
        prompt = state.ResumePrompt("続行")
        resumed = await manager.resume(session_id, prompt, str(tmp_path))

        assert resumed is not retained
        assert manager.sessions[session_id] is resumed
        assert resumed.status == "running"
        assert resumed.agent_message == ""
