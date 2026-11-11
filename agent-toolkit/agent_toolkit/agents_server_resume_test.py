# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""Claude backendの子session追跡と自動再開を検証する。"""

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access

import asyncio
import pathlib

import pytest

from agent_toolkit._agents_server import resume_waits, state
from agent_toolkit._common import session_registry
from agent_toolkit._testing.agents_server_resume_support import (
    _RESUME_WAIT_TIMEOUT,
    ControlledClaudeClient,
    ResultMessage,
    _auto_resume_after_child_termination,
    _await_state,
    _emit_child_start,
    _manager,
    _start,
    _wait_with_timeout,
)
from agent_toolkit._testing.agents_server_resume_support import _isolate_session_registry as _isolate_session_registry


@pytest.mark.asyncio
async def test_wait_defers_result_until_child_session_terminates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """孫sessionが終了するまで初回結果を返さず、確認後の結果を返す。"""
    client = ControlledClaudeClient("claude-child-wait")
    async with _manager(client, monkeypatch) as (manager, _backend):
        child_session_id = "child-wait"
        session = await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(child_session_id, state=session_registry.PublicationState.PENDING_RUNNING)
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("孫session待機中"))

        result = await _auto_resume_after_child_termination(manager, client, session, child_session_id)

        assert result["status"] == "completed"
        assert result["agent_message"] == "孫session確認後の結果"
        assert child_session_id in client.queries[1]


@pytest.mark.parametrize("registry_state", ["missing", "released", "unreadable"])
@pytest.mark.asyncio
async def test_wait_keeps_child_until_registration_recovers(
    registry_state: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """観測不能な孫sessionの識別子を保ち、終了を確認した後の再開結果を返す。"""
    client = ControlledClaudeClient(f"claude-child-{registry_state}")
    async with _manager(client, monkeypatch) as (manager, _backend):
        child_session_id = f"child-{registry_state}"
        session = await _start(manager, tmp_path, monkeypatch)
        if registry_state == "unreadable":
            registry_path = session_registry.registry_directory() / f"{child_session_id}.json"
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text("{", encoding="utf-8")
        elif registry_state == "released":
            session_registry.release(child_session_id, reason="retention_expired")
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("孫sessionの終了待機"))
        await _await_state(lambda: session.awaiting_auto_resume)
        await manager._advance_child_session_wait(session)
        assert session.live_child_session_ids == {child_session_id}
        assert not session.result_available
        assert len(client.queries) == 1

        result = await _auto_resume_after_child_termination(manager, client, session, child_session_id)

        assert result["status"] == "completed"
        assert result["agent_message"] == "孫session確認後の結果"
        assert "error" not in result
        assert child_session_id in client.queries[1]


@pytest.mark.parametrize("unobserved_state", ["missing", "unreadable"])
@pytest.mark.asyncio
async def test_wait_preserves_unobserved_child_sessions_until_all_terminate(
    unobserved_state: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """終了済みと観測不能の孫が混在しても、全ての終了を確認してから1回継続する。"""
    client = ControlledClaudeClient(f"claude-child-mixed-{unobserved_state}")
    async with _manager(client, monkeypatch) as (manager, _backend):
        terminal_session_id = f"child-terminal-{unobserved_state}"
        unobserved_session_id = f"child-{unobserved_state}"
        session = await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(terminal_session_id, state=session_registry.PublicationState.RESULT_COMPLETED)
        if unobserved_state == "unreadable":
            registry_path = session_registry.registry_directory() / f"{unobserved_session_id}.json"
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text("{", encoding="utf-8")
        _emit_child_start(client, terminal_session_id)
        _emit_child_start(client, unobserved_session_id)
        client.emit(ResultMessage("孫session混在時の初回結果"))

        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        await _await_state(lambda: session.awaiting_auto_resume)
        await manager._advance_child_session_wait(session)
        assert not wait_task.done() and len(client.queries) == 1
        assert session.live_child_session_ids == {unobserved_session_id}
        session_registry.publish(unobserved_session_id, state=session_registry.PublicationState.RESULT_COMPLETED)
        await _await_state(lambda: len(client.queries) == 2)
        client.emit(ResultMessage("孫session混在時の再開結果"))
        result = await wait_task

        assert result["status"] == "completed"
        assert result["agent_message"] == "孫session混在時の再開結果"
        assert "error" not in result
        assert terminal_session_id in client.queries[1]
        assert unobserved_session_id in client.queries[1]


@pytest.mark.asyncio
async def test_wait_tracks_new_unobserved_children_across_auto_resumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """再開turnで起動した新しい孫も、観測不能な状態になっても実際の終了まで追跡する。"""
    client = ControlledClaudeClient("claude-child-repeated-mixed")
    async with _manager(client, monkeypatch) as (manager, _backend):
        first_terminal_session_id = "child-terminal-first"
        missing_session_id = "child-missing-first"
        second_terminal_session_id = "child-terminal-second"
        unreadable_session_id = "child-unreadable-second"
        session = await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(first_terminal_session_id, state=session_registry.PublicationState.RESULT_COMPLETED)
        _emit_child_start(client, first_terminal_session_id)
        _emit_child_start(client, missing_session_id)
        client.emit(ResultMessage("1回目の孫session混在結果"))

        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        await _await_state(lambda: session.awaiting_auto_resume)
        await manager._advance_child_session_wait(session)
        assert len(client.queries) == 1 and not wait_task.done()
        session_registry.publish(missing_session_id, state=session_registry.PublicationState.RESULT_COMPLETED)
        await _await_state(lambda: len(client.queries) == 2)

        session_registry.publish(second_terminal_session_id, state=session_registry.PublicationState.RESULT_COMPLETED)
        registry_path = session_registry.registry_directory() / f"{unreadable_session_id}.json"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text("{", encoding="utf-8")
        _emit_child_start(client, second_terminal_session_id)
        _emit_child_start(client, unreadable_session_id)
        client.emit(ResultMessage("2回目の孫session混在結果"))
        await _await_state(lambda: session.awaiting_auto_resume)
        await manager._advance_child_session_wait(session)
        assert len(client.queries) == 2 and not wait_task.done()
        session_registry.publish(unreadable_session_id, state=session_registry.PublicationState.RESULT_COMPLETED)
        await _await_state(lambda: len(client.queries) == 3)

        client.emit(ResultMessage("連続自動再開後の結果"))
        result = await wait_task

        assert result["status"] == "completed"
        assert result["agent_message"] == "連続自動再開後の結果"
        assert "error" not in result
        assert first_terminal_session_id in client.queries[1]
        assert missing_session_id in client.queries[1]
        assert second_terminal_session_id in client.queries[2]
        assert unreadable_session_id in client.queries[2]


@pytest.mark.asyncio
async def test_wait_response_keys_are_unchanged_with_child_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """孫session追跡用の内部状態をwait応答へ追加しない。"""
    client = ControlledClaudeClient("claude-child-keys")
    async with _manager(client, monkeypatch) as (manager, _backend):
        child_session_id = "child-keys"
        session = await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(child_session_id, state=session_registry.PublicationState.PENDING_RUNNING)
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("孫session待機中"))

        result = await _auto_resume_after_child_termination(manager, client, session, child_session_id)

        assert set(result) == {
            "session_id",
            "agent_message",
            "status",
            "engine",
            "model",
            "effort",
            "model_type",
            "launch_identity",
            "observed_identity",
        }


@pytest.mark.asyncio
async def test_live_claude_child_survives_notification_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """従来の通知期限を過ぎても生存中の子を待ち、子の終了後の最終結果を返す。"""
    monkeypatch.setattr(resume_waits, "AUTO_RESUME_DEADLINE_SECONDS", 0.03)
    client = ControlledClaudeClient("claude-child-deadline")
    async with _manager(client, monkeypatch) as (manager, _backend):
        child_session_id = "child-deadline"
        session = await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(child_session_id, state=session_registry.PublicationState.PENDING_RUNNING)
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("孫session待機中"))

        await _await_state(lambda: session.awaiting_auto_resume)
        session.set_background_deadline(0.0)
        assert (await _wait_with_timeout(manager, 0))["status"] == "running"
        result = await _auto_resume_after_child_termination(manager, client, session, child_session_id)
        assert result["agent_message"] == "孫session確認後の結果"
        assert "error" not in result


@pytest.mark.asyncio
async def test_unobserved_child_sessions_merge_into_existing_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """未観測の子を待機中に明示終了すると、既存の失敗内容へ未観測sessionを併合する。"""
    monkeypatch.setattr(resume_waits, "AUTO_RESUME_DEADLINE_SECONDS", 0.03)
    client = ControlledClaudeClient("claude-child-error")
    async with _manager(client, monkeypatch) as (manager, _backend):
        child_session_id = "child-error"
        session = await _start(manager, tmp_path, monkeypatch)
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("失敗結果", is_error=True, errors=["既存エラー"]))

        await _await_state(lambda: session.awaiting_auto_resume)
        result = await manager.kill(session.session_id, timeout=1)

        assert result["error"] == {
            "message": "既存エラー",
            "unobservedSessions": [child_session_id],
            "heldResultFinalized": True,
        }


@pytest.mark.asyncio
async def test_unobserved_child_sessions_merge_with_existing_identifiers(tmp_path: pathlib.Path) -> None:
    """既存の未観測session識別子を保ったまま新しい識別子を併合する。"""
    session = state.SessionState("existing-unobserved", str(tmp_path))
    session.error = {
        "message": "既存エラー",
        "unobservedSessions": ["child-existing"],
    }

    resume_waits.record_unobserved_sessions(session, {"child-existing", "child-new"})

    assert session.error == {
        "message": "既存エラー",
        "unobservedSessions": ["child-existing", "child-new"],
    }
