"""`_agents_server/manager_registry.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import json
import logging
import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import agents_wait, shared_layout, shared_roots, state, status_file, wait_targets
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_support import (
    BlockingResumeBackend,
    FakeBackend,
    _actionable_message,
    _complete,
    _install_backend,
    _manager_with_fake,
    _without_body_path,
    install_backend,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine", "model_type"), (("codex", None), ("claude", "execute_fast")))
async def test_stop_discards_terminal_session(
    engine: str,
    model_type: str | None,
    tmp_path: pathlib.Path,
) -> None:
    """終了済みsessionを一覧と通常保持先から除き、backend資源を解放する。"""
    async with _manager_with_fake(engine) as (manager, backend):
        session = state.SessionState("terminal", str(tmp_path), engine=engine, model_type=model_type, turn_seq=3)
        _complete(session)
        manager.sessions[session.session_id] = session

        response = await manager.stop(session.session_id)

        assert response == {}
        listed = await manager.list_sessions()
        assert listed["sessions"] == []
        assert "omitted" not in listed
        assert "terminal" not in manager.sessions
        assert "terminal" not in manager.expired_sessions
        assert manager.stopped_sessions["terminal"].session_id == "terminal"
        assert backend.release_calls == ["terminal"]
        assert await manager.wait() == {"status": "expired"}


@pytest.mark.asyncio
async def test_stop_removes_status_file_projection_and_retained_result(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """破棄したsessionをstatusLineの射影と終了時の結果ファイルから除く。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        writer.activate()
        session = state.SessionState("visible", str(tmp_path), engine="codex", announced=True)
        _complete(session)
        manager.sessions[session.session_id] = session
        writer.flush()
        result_path = shared_layout.results_directory("root-session", tmp_path) / "visible.json"
        assert json.loads(writer.path.read_text(encoding="utf-8"))["sessions"][0]["session_id"] == "visible"
        assert result_path.exists()

        with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.status-file"):
            await manager.stop(session.session_id)
        writer.flush()

        assert json.loads(writer.path.read_text(encoding="utf-8"))["sessions"] == []
        assert not result_path.exists()
        assert "result_deleted session_id=visible writer=root.json collector=stop" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("operation", "collector"), (("kill", "kill"), ("send_message", "send-message")))
async def test_result_deletion_logs_actual_mcp_actor(
    operation: str,
    collector: str,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """killとsend_messageによる結果削除をMCP待機へ誤分類しない。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        writer.activate()
        session = state.SessionState("terminal", str(tmp_path), engine="codex")
        _complete(session)
        manager.sessions[session.session_id] = session
        writer.flush()

        with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.status-file"):
            if operation == "kill":
                await manager.kill(session.session_id, timeout=0)
            else:
                await manager.send_message(session.session_id, "続行")

        assert f"result_deleted session_id=terminal writer=root.json collector={collector}" in caplog.text
        assert "collector=mcp-wait" not in caplog.text
        await manager.close()


@pytest.mark.asyncio
async def test_stop_releases_wait_target_before_waiting_for_new_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """結果を破棄するstopは旧待機対象を解放し、後続sessionの結果待機を妨げない。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        writer.activate()
        old_session = state.SessionState("old-session", str(tmp_path), engine="codex", announced=True)
        manager.sessions[old_session.session_id] = old_session
        writer.flush()
        monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0)

        assert (
            agents_wait.wait_for_result(
                environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
                state_root=tmp_path,
            )
            == 3
        )
        running_response = json.loads(capsys.readouterr().out)
        assert running_response["session_id"] == "old-session"
        assert running_response["status"] == "running"
        _complete(old_session)
        writer.flush()

        await manager.stop(old_session.session_id)
        writer.flush()

        retained, error = wait_targets.read_wait_targets("root-session", "root.json", tmp_path)
        assert retained == set()
        assert error is None
        new_session = state.SessionState("new-session", str(tmp_path), engine="codex", announced=True)
        _complete(new_session, message="新しい結果")
        manager.sessions[new_session.session_id] = new_session
        writer.flush()

        assert (
            agents_wait.wait_for_result(
                environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
                state_root=tmp_path,
            )
            == 0
        )
        assert _without_body_path(json.loads(capsys.readouterr().out)) == {
            "session_id": "new-session",
            "status": "completed",
            "agent_message": "新しい結果",
            "engine": "codex",
            "model": None,
            "effort": None,
            "model_type": None,
        }
        await manager.close()


@pytest.mark.asyncio
async def test_stop_republishes_result_after_retain_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """結果公開の失敗後は解放を重ねず同じ本文だけを再公開する。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", backend)
        session = state.SessionState("retain-retry", str(tmp_path), engine="codex", turn_seq=4)
        _complete(session, message="保持する結果")
        manager.sessions[session.session_id] = session
        original_retain = writer.retain_result_async
        retain_calls = 0

        async def fail_once(resume_state: state.SessionResumeState) -> None:
            nonlocal retain_calls
            retain_calls += 1
            if retain_calls == 1:
                raise OSError("retain failed once")
            await original_retain(resume_state)

        monkeypatch.setattr(writer, "retain_result_async", fail_once)

        with pytest.raises(
            RuntimeError,
            match="backend resources released; state synchronization incomplete.*retain failed once",
        ):
            await manager.stop(session.session_id, retain_result=True)
        await manager.stop(session.session_id, retain_result=True)

        result_path = shared_layout.results_directory("root-session", tmp_path) / f"{session.session_id}.json"
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        assert payload["agent_message"] == "保持する結果"
        assert payload["turn_seq"] == 4
        assert retain_calls == 2
        assert backend.release_calls == [session.session_id]
        await manager.close()


@pytest.mark.asyncio
async def test_stop_does_not_release_twice_after_state_sync_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """状態同期の再発行では成功済みのbackend解放を繰り返さない。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", backend)
        session = state.SessionState("schedule-retry", str(tmp_path), engine="codex")
        _complete(session, message="保持する結果")
        manager.sessions[session.session_id] = session
        original_schedule = writer.schedule
        schedule_calls = 0

        def fail_once() -> None:
            nonlocal schedule_calls
            schedule_calls += 1
            if schedule_calls == 1:
                raise OSError("schedule failed once")
            original_schedule()

        monkeypatch.setattr(writer, "schedule", fail_once)

        with pytest.raises(
            RuntimeError,
            match="backend resources released; state synchronization incomplete.*schedule failed once",
        ) as raised:
            await manager.stop(session.session_id, retain_result=True)
        next_action = _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
        assert "`stop`" in next_action
        assert "`list`" in next_action
        await manager.stop(session.session_id, retain_result=True)

        assert schedule_calls == 2
        assert backend.release_calls == [session.session_id]
        assert writer.result_state(session.session_id) == "published"
        await manager.close()


@pytest.mark.asyncio
async def test_stop_discards_expired_session(tmp_path: pathlib.Path) -> None:
    """期限切れ保持先のsessionも破棄し、一覧非表示の再開状態へ移す。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("expired-stop", str(tmp_path), engine="codex")
        _complete(session)
        manager.expired_sessions[session.session_id] = state.SessionResumeState.from_session(session)

        response = await manager.stop(session.session_id)

        assert response == {}
        assert session.session_id not in manager.expired_sessions
        assert session.session_id in manager.stopped_sessions
        listed = await manager.list_sessions()
        assert listed["sessions"] == []
        assert "omitted" not in listed
        # expiredへ移した時点で資源は解放済みなので、stopは再開状態の移動だけを行う。
        assert not backend.release_calls


@pytest.mark.parametrize("with_writer", [False, True])
@pytest.mark.asyncio
async def test_stop_reports_registry_failure_after_releasing_resources(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, with_writer: bool
) -> None:
    """登録簿の保存失敗でも解放済みを明示し、再発行は状態の同期だけを行う。"""
    writer = (
        status_file.StatusFileWriter(
            {}, shared_roots.StatusFileIdentity("root-session", "root.json", None), state_root=tmp_path
        )
        if with_writer
        else None
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        session = state.SessionState("registry-retry", str(tmp_path), engine="codex")
        _complete(session)
        manager.sessions[session.session_id] = session
        release = session_registry.release
        calls = 0

        def fail_once(*args: Any, **kwargs: Any) -> bool:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("registry failed once")
            return release(*args, **kwargs)

        monkeypatch.setattr(session_registry, "release", fail_once)
        with pytest.raises(RuntimeError, match="backend resources released; state synchronization incomplete") as caught:
            await manager.stop(session.session_id)
        assert "`stop`" in _actionable_message(caught.value)
        assert isinstance(caught.value.__cause__, OSError)
        await manager.stop(session.session_id)
        assert calls == 2
        assert backend.release_calls == [session.session_id]
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("registry_content", "code", "operation"),
    [
        (None, "turn_unobserved", "新しいstartでやり直さない"),
        ("{", "unreadable", "atk agents wait"),
    ],
    ids=["running-elsewhere", "unreadable"],
)
async def test_unrecoverable_registry_record_reports_next_action(
    registry_content: str | None,
    code: str,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """再開できない登録簿の状態は、`error.recovery`の値に加えて、取るべき操作を次の操作の行で示す。

    `error.recovery`の値だけを返すと、委譲元は別の主体が実行中のsessionを新しいstartでやり直し得る。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_id = "0ba2f3f4-3e6c-4a1a-9a35-9f5e30b9f9b1"
    session_registry.publish(
        session_id, state=session_registry.PublicationState.PENDING_RUNNING, engine="codex", cwd=str(tmp_path)
    )
    if registry_content is not None:
        (session_registry.registry_directory() / f"{session_id}.json").write_text(registry_content, encoding="utf-8")
    async with _manager_with_fake("codex") as (manager, backend):
        with pytest.raises(ValueError, match=f"error.recovery={code}") as raised:
            await manager.send_message(session_id, "続行")

        assert operation in _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
        assert not backend.resume_calls
        await manager.close()


@pytest.mark.asyncio
async def test_stop_rejects_running_session(tmp_path: pathlib.Path) -> None:
    """実行中turnは中断も破棄もせず、killの先行を要求する。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("running", str(tmp_path), engine="codex")
        manager.sessions[session.session_id] = session

        with pytest.raises(ValueError, match="session is running: running") as exc_info:
            await manager.stop(session.session_id)
        assert "`kill`" in _actionable_message(exc_info.value)

        assert manager.sessions[session.session_id] is session
        assert backend.interrupt_calls == 0
        assert not backend.release_calls


@pytest.mark.asyncio
async def test_stop_rejects_pending_resume(tmp_path: pathlib.Path) -> None:
    """進行中の再開を実行中として拒否する。"""
    async with _manager_with_fake("codex") as (manager, _):
        resume_state = state.SessionResumeState.from_session(state.SessionState("pending", str(tmp_path)))
        manager.expired_sessions[resume_state.session_id] = resume_state
        blocker = BlockingResumeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", blocker)
        send_task = asyncio.create_task(manager.send_message(resume_state.session_id, "再開", timeout=1))
        await blocker.resume_started.wait()

        with pytest.raises(ValueError, match="session is running: pending") as exc_info:
            await manager.stop(resume_state.session_id)
        assert "`kill`" in _actionable_message(exc_info.value)

        assert not blocker.release_calls
        blocker.release_resume.set()
        await send_task
        await manager.close()


@pytest.mark.asyncio
async def test_stop_rejects_unknown_session() -> None:
    """保持していないsession IDは既存の未解決診断を返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        session_id = "3468feae-b2bf-4d67-ac55-3c40207e8b5b"

        with pytest.raises(ValueError, match=f"unknown session: {session_id}"):
            await manager.stop(session_id)
