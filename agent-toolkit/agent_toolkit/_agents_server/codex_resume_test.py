"""Codexのresumeを検証する。"""

from __future__ import annotations

import asyncio
import os
import pathlib
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import notice_inbox, state
from agent_toolkit._agents_server.notify import send_notification
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import inherited_venv, state_paths
from agent_toolkit._testing.agents_server_support import (
    BlockingResumeCodexClient,
    CompletingInterruptClient,
    FakeCodexClient,
    HoldingTurnStartClient,
    InterruptErrorClient,
    LostTurnStartClient,
    NaturallyCompletingTurnStartClient,
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

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


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
    async with server_manager.AgentsServerManager() as manager, server_manager.AgentsServerManager() as other:
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
            session.announced = True
            manager._status_writer.activate()
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
            assert not notice_inbox.take_notices(
                root,
                session.session_id,
                state_root=tmp_path,
                recipient_session_id=manager._status_writer.launcher_session_id(),
                owner_status_file=manager._status_writer.path.name,
            )
            if environment_owner is None:
                assert (
                    notice_inbox.take_notices(
                        other._status_writer.root_session_id,
                        session.session_id,
                        state_root=tmp_path,
                        recipient_session_id=other._status_writer.launcher_session_id(),
                        owner_status_file=other._status_writer.path.name,
                    )
                    == []
                )
        finally:
            await manager.close()
            await other.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_concurrent_kills_preserve_shared_request_when_turn_completes_before_lock_handoff(
    tmp_path: pathlib.Path,
) -> None:
    """ロック待ち中にturnが終了しても並行killは要求済み状態を共有する。"""
    async with (
        server_manager.AgentsServerManager() as manager,
        subject.AppServerManager(manager.sessions, manager._condition) as backend,
    ):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        manager.sessions[session.session_id] = session
        backend._clients[session.session_id] = cast(Any, CompletingInterruptClient(backend, session))
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
async def test_codex_turn_start_response_loss_remains_running_until_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """初回のturn/startの応答が失われた後もsessionを保持し、完了通知から結果を取得できるようにする。"""
    async with subject.AppServerManager() as manager:
        client = LostTurnStartClient()

        async def ensure_client(*_args: Any, **_kwargs: Any) -> LostTurnStartClient:
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
    """steer拒否後は同一turnの終了を待ってからreplyへ切り替える。"""
    async with subject.AppServerManager() as manager:
        client = SteerRaceClient()

        async def ensure_client(*_args: Any, **_kwargs: Any) -> SteerRaceClient:
            return client

        monkeypatch.setattr(manager, "_ensure_client", ensure_client)
        session = await manager.start("調査", str(tmp_path))
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
@pytest.mark.parametrize("notification_first", [False, True])
async def test_resume_waits_for_thread_close_after_unsubscribe(
    monkeypatch: pytest.MonkeyPatch, notification_first: bool
) -> None:
    """閉鎖中の再開拒否は対象threadの閉鎖通知で解消し、閉鎖通知が先に届いても取りこぼさない。"""
    async with subject.AppServerManager() as backend:
        client = FakeCodexClient()
        closed_notice = {"method": "thread/closed", "params": {"threadId": "thread-1"}}
        rejected = asyncio.Event()
        attempts = 0

        async def request(method: str, params: dict[str, Any]) -> dict[str, Any]:
            nonlocal attempts
            assert method == "thread/resume" and params == {
                "threadId": "thread-1",
                "baseInstructions": subject.SUBAGENT_BASE_INSTRUCTIONS,
            }
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
    async with subject.AppServerManager() as backend:
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
    async with subject.AppServerManager() as manager:
        client = InterruptErrorClient()
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        manager.sessions[session.session_id] = session
        manager._clients[session.session_id] = cast(Any, client)

        await manager._interrupt(session.session_id, session.turn_id, client)

        assert session.error == {"message": "turn/interrupt: interrupt rejected"}
        assert session.protocol_warnings == ["turn/interrupt failed: turn/interrupt: interrupt rejected"]


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_backend_send_supports_terminal_reply_without_result_flag(tmp_path: pathlib.Path) -> None:
    """Codexの終了後のreplyは結果回収フラグなしで開始できる。"""
    async with subject.AppServerManager() as manager:
        client = FakeCodexClient()
        session = state.SessionState("thread-codex", str(tmp_path), engine="codex")
        session.turn_id = "turn-old"
        _complete(session, message="Codex結果")
        manager.sessions[session.session_id] = session
        manager._clients[session.session_id] = cast(Any, client)
        response = await manager.send_message(session, "続行")
        assert response["delivery"] == "reply_started"
        assert response["status"] == "running"
        assert response["previous_result"]["agent_message"] == "Codex結果"
        _assert_no_forbidden_keys(response)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_shared_manager_integrates_codex_start_and_send_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """共有MCP層から実Codexバックエンドの開始と継続入力を通す。"""
    async with (
        server_manager.AgentsServerManager(status_writer=None) as manager,
        subject.AppServerManager(manager.sessions, manager._condition) as backend,
    ):
        client = FakeCodexClient()

        async def ensure_client(*_args: Any, **_kwargs: Any) -> FakeCodexClient:
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
    async with (
        server_manager.AgentsServerManager() as manager,
        subject.AppServerManager(manager.sessions, manager._condition) as backend,
    ):
        client = FakeCodexClient()

        async def ensure_client(*_args: Any, **_kwargs: Any) -> FakeCodexClient:
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
        assert next((method, params) for method, params in client.requests if method == "thread/resume") == (
            "thread/resume",
            {
                "threadId": "thread-saved",
                "baseInstructions": subject.SUBAGENT_BASE_INSTRUCTIONS,
                "cwd": str(tmp_path),
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
                "serviceTier": "default",
                "model": "gpt-test",
                "modelProvider": "openai",
                "config": {"bypass_hook_trust": True, "features": {"context_management": {"experimental_mode": False}}},
                "developerInstructions": subject._developer_instructions("delegate"),  # noqa: SLF001
            },
        )
        assert client.requests[-1][0] == "turn/start"


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_codex_resume_timeout_drops_prompt_without_duplicate_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Codex再開のtimeout後に旧promptを配送せず、後続入力で再開を重複させない。"""
    async with (
        server_manager.AgentsServerManager() as manager,
        subject.AppServerManager(manager.sessions, manager._condition) as backend,
    ):
        client = BlockingResumeCodexClient()

        async def ensure_client(*_args: Any, **_kwargs: Any) -> BlockingResumeCodexClient:
            return client

        monkeypatch.setattr(backend, "_ensure_client", ensure_client)
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
            assert [method for method, _params in client.requests] == ["config/read", "thread/read", "thread/resume"]
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
    async with _pending_codex_manager(monkeypatch, HoldingTurnStartClient) as (manager, backend, client):
        manager.expired_sessions[session_id] = state.SessionResumeState(
            session_id=session_id,
            cwd=str(tmp_path),
            model=None,
            effort=None,
            engine="codex",
        )

        await _start_blocked_codex_resume(manager, client, session_id)

        response = await manager.kill(session_id, timeout=1)

        assert response["status"] == "interrupted"
        assert response["kill_requested"] is True
        assert [method for method, _params in client.requests] == [
            "config/read",
            "thread/read",
            "thread/resume",
            "turn/start",
            "turn/interrupt",
        ]
        assert client.active_turn is False
        assert not manager._pending_resumes
        assert not backend._background_tasks


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
    async with _pending_codex_manager(monkeypatch, NaturallyCompletingTurnStartClient) as (manager, backend, client):
        manager.expired_sessions[session_id] = state.SessionResumeState(
            session_id=session_id,
            cwd=str(tmp_path),
            model=None,
            effort=None,
            engine="codex",
        )

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
            "config/read",
            "thread/read",
            "thread/resume",
            "turn/start",
        ]
        assert client.active_turn is False
        assert not manager._pending_resumes
        assert not backend._background_tasks
