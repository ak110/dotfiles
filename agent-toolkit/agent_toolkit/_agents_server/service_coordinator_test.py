"""移譲の成否不明時の二重実行と、背景資源を巻き込む接続回収を防ぐ。"""

# 外部I/Oを代用し、所有CASと受付予約の内部境界を検証する。
# pylint: disable=protected-access

import asyncio
import pathlib
import types
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agent_toolkit._agents_server import service_io, service_rpc, state, turn_state
from agent_toolkit._agents_server.service_context import Conversation, ServiceManagers
from agent_toolkit._agents_server.service_coordinator import ServiceCoordinator
from agent_toolkit._agents_server.service_resources import ResourceBroker
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.runtime_environment import CallerContext


def _coordinator(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, *, current: bool = True) -> tuple:
    target: dict[str, Any] = {"generation": "G3" if current else "G1", "epoch": 3 if current else 1, "process": "1:owner"}
    managers = ServiceManagers()
    session = state.SessionState("session", str(tmp_path), engine="codex", turn_end=turn_state.TurnEnd.COMPLETED)
    client = types.SimpleNamespace(
        resource=types.SimpleNamespace(
            origin={"process": "0:origin"},
            runtime=types.SimpleNamespace(owner="G1"),
            operation=AsyncMock(return_value=True),
        ),
        pause=Mock(),
        detach=AsyncMock(return_value={"identifier": "resource", "origin": {"process": "0:origin"}}),
    )
    backend = Mock(
        _clients={session.session_id: client},
        _sdk_clients={},
        release_session=AsyncMock(),
        export_service_state=AsyncMock(return_value={}),
        _stop_auto_reset=AsyncMock(),
        adopt_session=AsyncMock(),
    )
    manager: Any = Mock(sessions={session.session_id: session}, _backends={"codex": backend}, save_control_state=AsyncMock())
    conversation = Conversation(manager, CallerContext({}, tmp_path))
    managers.conversations["parent"] = conversation
    coordinator = ServiceCoordinator(target, tmp_path, managers, Mock(), Mock())
    accepted = target if current else {"generation": "G3", "epoch": 3, "process": "3:new"}
    monkeypatch.setattr(coordinator.registry, "current", lambda: accepted)
    runtime = coordinator.bind_runtime(service_io.Runtime(target["generation"], target, ResourceBroker(target["generation"])))
    conversation.runtime = runtime
    return coordinator, session, backend


@pytest.mark.parametrize(
    "pending", ["live_tasks", "live_child_session_ids", "codex_live_subagent_thread_ids", "cli_turn_state"]
)
@pytest.mark.asyncio
async def test_next_input_preserves_pending_resources_and_rehomes_only_after_release(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    pending: str,
) -> None:
    coordinator, session, backend = _coordinator(tmp_path, monkeypatch)
    value = (
        {"live": state.LiveTask("Bash", "背景処理", "2026-11-15T00:00:00Z")}
        if pending == "live_tasks"
        else "running"
        if pending == "cli_turn_state"
        else {"live"}
    )
    setattr(session, pending, value)
    child = Conversation(Mock(), CallerContext({}, tmp_path))
    coordinator.managers.conversations[session.session_id] = child
    with service_io.bind(coordinator._runtime):
        async with service_io.next_input(session):
            assert not service_io.rehoming(session.session_id)
        backend.release_session.assert_not_awaited()
        setattr(session, pending, {} if pending == "live_tasks" else "idle" if pending == "cli_turn_state" else set())
        async with service_io.next_input(session):
            assert service_io.rehoming(session.session_id)
            assert child.active == 1
        assert not service_io.rehoming(session.session_id)
    backend.release_session.assert_awaited_once_with(session)
    assert child.active == 0


@pytest.mark.asyncio
async def test_lost_commit_response_and_target_loss_never_restarts_old_decoder(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, session, backend = _coordinator(tmp_path, monkeypatch, current=False)
    calls = []

    async def request(_target: dict, operation: str, _arguments: dict, **_kwargs: Any) -> dict:
        calls.append(operation)
        if operation == "prepare_move":
            return {"prepared": True}
        raise OSError("移譲先との応答が失われた")

    monkeypatch.setattr(service_rpc, "request", request)
    with pytest.raises(ActionableError, match="移譲結果を確定できません"):
        await coordinator.move(session.session_id)
    assert calls == ["prepare_move", "accept", "move_status"]
    backend.adopt_session.assert_not_awaited()
    backend.release_session.assert_not_awaited()
    assert session.session_id in coordinator.managers.conversations["parent"].manager.sessions
    with pytest.raises(ActionableError, match="所有を確定できない"):
        await coordinator.move(session.session_id)
    assert calls == ["prepare_move", "accept", "move_status"]


def test_background_turn_start_invalidates_previous_end_boundary(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, session, _backend = _coordinator(tmp_path, monkeypatch)
    coordinator.boundary(session, None)
    session.turn_end = None
    coordinator.started(session)
    assert not coordinator._ended_turn(session)


@pytest.mark.asyncio
async def test_abort_waits_for_inflight_commit_and_reports_the_committed_owner(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator, session, _backend = _coordinator(tmp_path, monkeypatch)
    identifier = session.session_id
    lock = coordinator._locks.setdefault(identifier, asyncio.Lock())
    async with lock:
        abort = asyncio.create_task(coordinator.abort_move(identifier, "transfer"))
        await asyncio.sleep(0)
        assert not abort.done()
        coordinator._received[identifier] = "transfer", {"generation": "origin"}
    assert await abort == {"owned": True}
