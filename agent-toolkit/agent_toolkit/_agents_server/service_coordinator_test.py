"""移譲の成否不明時の二重実行と、背景資源を巻き込む接続回収を防ぐ。"""

# 外部I/Oを代用し、所有CASと受付予約の内部境界を検証する。
# pylint: disable=protected-access

import asyncio
import contextlib
import dataclasses
import json
import os
import pathlib
import types
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolRequestParams

from agent_toolkit._agents_server import (
    codex,
    service_context,
    service_coordinator,
    service_io,
    service_rpc,
    service_state,
    service_transport,
    shared_roots,
    state,
    turn_state,
)
from agent_toolkit._agents_server.service_context import Conversation, ServiceManagers
from agent_toolkit._agents_server.service_coordinator import ServiceCoordinator
from agent_toolkit._agents_server.service_projection import ProjectionHub
from agent_toolkit._agents_server.service_registry import RegistryForwarder
from agent_toolkit._agents_server.service_resources import ResourceBroker
from agent_toolkit._common import runtime_environment
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


def _transfers(monkeypatch: pytest.MonkeyPatch, published: list[int] | None = None) -> list[tuple[str, list[int]]]:
    """移譲先は予約と受入に成功する。各要求の時点で完了していた登録公開も記録する。"""
    sent: list[tuple[str, list[int]]] = []

    async def request(_target: dict, operation: str, _arguments: dict, **_kwargs: Any) -> dict:
        sent.append((operation, list(published or [])))
        return {"prepared": True} if operation == "prepare_move" else {"owned": True}

    monkeypatch.setattr(service_rpc, "request", request)
    return sent


@pytest.mark.asyncio
async def test_move_waits_registration_started_while_saving_control_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """移譲前の状態保存の応答を待つ間に始まった登録公開を、写す前に完了させる。

    2026-11-17、この公開の完了前に写して「session登録の保存完了前には移譲できません」となり、継続指示の実行が失敗した。
    """
    coordinator, session, _backend = _coordinator(tmp_path, monkeypatch, current=False)
    published: list[int] = []

    async def publish() -> None:
        await asyncio.sleep(0)
        published.append(session.turn_seq)

    async def save_control_state() -> None:
        # 保存の応答を待つ間に届いた通知が、同じsessionで新しい登録公開を始める。
        if not published:
            session._registration_task = asyncio.create_task(publish())

    coordinator.managers.conversations["parent"].manager.save_control_state = save_control_state
    sent = _transfers(monkeypatch, published)
    assert await coordinator.move(session.session_id) == {"moved": True}
    assert sent == [("prepare_move", [0]), ("accept", [0])]


@pytest.mark.parametrize("failure", ["control_save", "registration"])
@pytest.mark.asyncio
async def test_failed_save_before_transfer_returns_detached_resources_to_source(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """移譲前の状態保存か登録公開が失敗したら所有を移さず、切り離した実行資源と制御の受付を旧側へ戻して次の移譲を可能にする。"""
    coordinator, session, backend = _coordinator(tmp_path, monkeypatch, current=False)
    runtime = coordinator._runtime
    assert runtime is not None
    descriptor = {
        "identifier": runtime.broker.register({"close": AsyncMock()}),
        "origin": {"generation": runtime.generation, "process": runtime.target["process"]},
    }
    backend._clients[session.session_id].detach.return_value = descriptor
    manager = coordinator.managers.conversations["parent"].manager

    async def fail_registration() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("保存先へ書けない")

    async def start_failing_registration() -> None:
        session._registration_task = asyncio.create_task(fail_registration())

    manager.save_control_state.side_effect = (
        OSError("保存先へ書けない") if failure == "control_save" else start_failing_registration
    )
    sent = _transfers(monkeypatch)
    with pytest.raises((OSError, RuntimeError), match="保存先へ書けない"):
        await coordinator.move(session.session_id)
    assert not sent
    backend.adopt_session.assert_awaited_once_with(session, descriptor, {})
    assert manager.sessions == {session.session_id: session}
    async with coordinator.gate.admit(new_turn=False, session_id=session.session_id):
        pass
    manager.save_control_state.side_effect = None
    assert await coordinator.move(session.session_id) == {"moved": True}
    assert [operation for operation, _published in sent] == ["prepare_move", "accept"]


@pytest.mark.asyncio
@pytest.mark.parametrize("returned", [False, True])
async def test_inflight_codex_request_survives_failed_move(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, returned: bool
) -> None:
    """配送中で移せない間も同じclientが応答と後着通知を受け取り、旧所有者は照会に応じる。"""
    coordinator, session, _backend = _coordinator(tmp_path, monkeypatch, current=False)
    identifier = session.session_id
    runtime = coordinator._runtime
    assert runtime is not None
    started = asyncio.Event()
    finish = asyncio.Event()
    received: asyncio.Queue[dict] = asyncio.Queue()

    async def request(**_kwargs: Any) -> dict:
        started.set()
        await finish.wait()
        return {"result": {"done": True}}

    resource_id = runtime.broker.register({"request": request, "close": AsyncMock()})
    descriptor = {"identifier": resource_id, "sequence": 0, "origin": runtime.target, "pid": None, "environment": {}}
    conversation = coordinator.managers.conversations["parent"]
    backend = conversation.manager._backends["codex"] = codex.AppServerManager(conversation.manager.sessions)
    session.hold_result(turn_state.BackgroundWait({"status": "completed"}, None))
    session.live_child_session_ids.add("child")
    monkeypatch.setattr(backend, "_handle_notification", received.put)
    with service_io.bind(runtime):
        await backend.adopt_session(session, descriptor, {})
    client = coordinator._client(backend, identifier)
    assert isinstance(client, service_io.CodexResource)
    compactor = backend._wait_compactors[identifier]
    checkpoint = compactor.checkpoint()
    assert checkpoint["deadline"] is not None
    conversation.manager.take_over_orphaned_session = AsyncMock()
    conversation.manager.show_session = AsyncMock(return_value={"session_id": identifier})
    current = coordinator.registry.current()
    sent = _transfers(monkeypatch)
    inflight = asyncio.create_task(client.request("thread/read"))
    try:
        async with asyncio.timeout(5):
            await started.wait()
            coordinator.boundary(session, client)
            if returned:
                monkeypatch.setattr(coordinator.registry, "current", lambda: coordinator.target)
            await runtime.broker.emit(resource_id, {"kind": "notification", "value": {"n": 1}})
            with pytest.raises(RuntimeError, match="配送中"):
                await coordinator.move(identifier)
            params = CallToolRequestParams(name="show", arguments={"session_id": identifier})
            shown = await coordinator.owned_call("parent", conversation.caller, params)
            assert "result" in shown
            conversation.manager.show_session.assert_awaited_once_with(identifier, verbose=False)
            assert await received.get() == {"n": 1}
            assert coordinator._client(backend, identifier) is client
            assert compactor.checkpoint() == checkpoint
            assert not sent
            finish.set()
            assert await inflight == {"done": True}
            monkeypatch.setattr(coordinator.registry, "current", lambda: current)
            assert await coordinator.move(identifier) == {"moved": True}
            assert [operation for operation, _ in sent] == ["prepare_move", "accept"]
            assert not coordinator.owns("parent", identifier)
    finally:
        finish.set()
        await inflight
        await client.detach()
        await compactor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("detached", [False, True])
async def test_rollback_does_not_reopen_or_decode_for_a_different_resource_owner(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, detached: bool
) -> None:
    coordinator, session, backend = _coordinator(tmp_path, monkeypatch, current=False)
    runtime = coordinator._runtime
    assert runtime is not None
    identifier = session.session_id
    resource_id = runtime.broker.register({"close": AsyncMock()})
    descriptor = {"identifier": resource_id, "sequence": 0, "origin": runtime.target, "pid": None, "environment": {}}
    client = service_io.CodexResource.attach(runtime, descriptor, AsyncMock(), AsyncMock(), AsyncMock())
    backend._clients[identifier] = client
    conversation = coordinator.managers.conversations["parent"]

    async def fail_with_new_owner(*_args: Any) -> None:
        assert await runtime.broker.transfer(resource_id, runtime.owner, "other-instance")
        raise RuntimeError("準備失敗")

    failure = conversation.manager.save_control_state if detached else backend._stop_auto_reset
    failure.side_effect = fail_with_new_owner
    try:
        with pytest.raises((PermissionError, RuntimeError), match="所有"):
            await coordinator.move(identifier)
        params = CallToolRequestParams(name="show", arguments={"session_id": identifier})
        assert await coordinator.owned_call("parent", conversation.caller, params) == {"move_required": True}
        assert client.transferred
        backend.adopt_session.assert_not_awaited()
        assert await runtime.broker.transfer(resource_id, "other-instance", "other-instance")
    finally:
        await client.detach()


@pytest.mark.asyncio
@pytest.mark.parametrize("detached", [False, True])
@pytest.mark.parametrize("owner_changes", [False, True])
async def test_returned_source_rechecks_only_unconfirmed_ownership(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, detached: bool, owner_changes: bool
) -> None:
    coordinator, session, _ = _coordinator(tmp_path, monkeypatch, current=False)
    runtime = coordinator._runtime
    assert runtime is not None
    identifier = session.session_id
    conversation = coordinator.managers.conversations["parent"]
    manager = conversation.manager
    manager.show_session = AsyncMock(return_value={"session_id": identifier})
    manager.take_over_orphaned_session = AsyncMock()
    manager._service_paused = set()

    @contextlib.asynccontextmanager
    async def scope(_params: CallToolRequestParams):
        yield manager

    monkeypatch.setattr(coordinator.managers, "scope", scope)
    backend = manager._backends["codex"] = codex.AppServerManager(manager.sessions)
    received: asyncio.Queue[dict] = asyncio.Queue()
    monkeypatch.setattr(backend, "_handle_notification", received.put)
    resource_id = runtime.broker.register({"close": AsyncMock()})
    descriptor = {"identifier": resource_id, "sequence": 0, "origin": runtime.target, "pid": None, "environment": {}}
    with service_io.bind(runtime):
        await backend.adopt_session(session, descriptor, {})
    client = coordinator._client(backend, identifier)
    if not detached:
        monkeypatch.setattr(client, "detach", AsyncMock(side_effect=RuntimeError("配送中")))

    transfer = runtime.broker.transfer
    operation = "transfer" if detached else "acknowledge"
    confirm = getattr(runtime.broker, operation)
    attempts = 0
    second = asyncio.Event()
    release = asyncio.Event()

    async def lose_responses(**kwargs: Any) -> Any:
        nonlocal attempts
        if kwargs.get("sequence", 0) != 0:
            return await confirm(**kwargs)
        attempts += 1
        response = await confirm(**kwargs)
        if attempts == 2:
            second.set()
            await release.wait()
        if attempts <= 2:
            raise EOFError("所有確認の応答が失われた")
        return response

    monkeypatch.setattr(runtime.broker, operation, lose_responses)
    params = CallToolRequestParams(name="show", arguments={"session_id": identifier})
    try:
        async with asyncio.timeout(5):
            coordinator.boundary(session, client)
            monkeypatch.setattr(coordinator.registry, "current", lambda: coordinator.target)
            # 旧側に配送済みの制御要求が終わる前でも、生I/Oの所有者を確認できる。
            async with coordinator.gate.admit(new_turn=False, session_id=identifier):
                with pytest.raises(EOFError):
                    await coordinator.move(identifier)
            await runtime.broker.emit(resource_id, {"kind": "notification", "value": {"n": 1}})
            assert await coordinator.tick()
            await second.wait()
            assert await coordinator.tick()
            assert attempts == 2
            assert await coordinator.owned_call("parent", conversation.caller, params) == {"move_required": True}
            release.set()
            with runtime_environment.bind(conversation.caller):
                assert (await coordinator.call_tool(None, params)).is_error
            assert received.empty() and attempts == 2 and identifier in manager._service_paused
            if owner_changes:
                assert await transfer(resource_id, runtime.owner, "other-owner")
            assert await coordinator.tick()
            with runtime_environment.bind(conversation.caller):
                assert (await coordinator.call_tool(None, params)).is_error == owner_changes
            if owner_changes:
                assert await coordinator.owned_call("parent", conversation.caller, params) == {"move_required": True}
                assert received.empty() and client.transferred
                assert len(await runtime.broker.events(resource_id, "other-owner", 0)) == 1
            else:
                assert await received.get() == {"n": 1}
                assert identifier not in manager._service_paused
            assert await coordinator.tick()
            await asyncio.sleep(0)
            assert attempts == 3 and received.empty()
    finally:
        release.set()
        await service_io.CodexResource.detach(coordinator._client(backend, identifier))


def _stoppable_client(coordinator: ServiceCoordinator, session: state.SessionState, backend: Any) -> dict:
    """終了境界で止めると切り離し済みとして観測できるclientにし、切り離したときの実行資源の記述を返す。"""
    runtime = coordinator._runtime
    assert runtime is not None
    descriptor = {
        "identifier": runtime.broker.register({"close": AsyncMock()}),
        "origin": {"generation": runtime.generation, "process": runtime.target["process"]},
    }
    client = backend._clients[session.session_id]
    client.detach.return_value = descriptor
    client.transferred = False
    client.pause.side_effect = lambda: setattr(client, "transferred", True)
    backend._wait_compaction_turns = {}
    return descriptor


@pytest.mark.asyncio
async def test_client_stopped_at_boundary_resumes_when_acceptance_returns_before_move(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """終了境界で止めたclientは、移譲を待つ間に受付先が自世代へ戻れば取り込み直し、次の応答を受け取れる状態へ戻す。

    公開を確定した新世代が終了すると、生存する旧世代へ受付が戻る。止めたままでは、その会話のdecodeが再開しない。
    """
    coordinator, session, backend = _coordinator(tmp_path, monkeypatch, current=False)
    descriptor = _stoppable_client(coordinator, session, backend)
    client = backend._clients[session.session_id]
    current = [{"generation": "G3", "epoch": 3, "process": "3:new"}]
    monkeypatch.setattr(coordinator.registry, "current", lambda: current[0])
    # 同じsessionの別のtool呼出しがsessionの直列化を保持している間に、turnが終わる。
    async with coordinator._locks.setdefault(session.session_id, asyncio.Lock()):
        coordinator.boundary(session, client)
        assert client.transferred
        # 受付先の新世代が終了し、受付が旧世代へ戻る。
        current[0] = coordinator.target
    async with asyncio.timeout(5):
        assert await coordinator.move(session.session_id) == {"moved": False}
    backend.adopt_session.assert_awaited_once_with(session, descriptor, {})


@pytest.mark.asyncio
@pytest.mark.parametrize("returned", [False, True])
async def test_background_move_after_codex_client_was_discarded(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, returned: bool
) -> None:
    coordinator, session, _ = _coordinator(tmp_path, monkeypatch, current=False)
    runtime = coordinator._runtime
    assert runtime is not None
    manager = coordinator.managers.conversations["parent"].manager
    backend = manager._backends["codex"] = codex.AppServerManager(manager.sessions)
    identifier = session.session_id
    resource_id = runtime.broker.register({"shutdown": AsyncMock(return_value=[]), "close": AsyncMock()})
    descriptor = {"identifier": resource_id, "sequence": 0, "origin": runtime.target, "pid": None, "environment": {}}
    with service_io.bind(runtime):
        await backend.adopt_session(session, descriptor, {})
    client = coordinator._client(backend, identifier)
    async with coordinator._locks.setdefault(identifier, asyncio.Lock()):
        coordinator.boundary(session, client)
        await runtime.broker.emit(resource_id, {"kind": "notification", "value": {}})
        await backend.release_session(session)
        assert runtime.broker.backlog(resource_id) is None
        if returned:
            monkeypatch.setattr(coordinator.registry, "current", lambda: coordinator.target)
    request = AsyncMock(side_effect=[{"prepared": True}, {"owned": True}])
    monkeypatch.setattr(service_rpc, "request", request)
    assert await coordinator._moving[identifier] == {"moved": not returned}
    assert (identifier in manager.sessions) == returned
    if returned:
        request.assert_not_awaited()
    else:
        assert request.await_args_list[0].args[2]["resource"] is None


@pytest.mark.parametrize("failing_stage", [None, "projections.thaw", "registrations.thaw", "gate.thaw", "registry.restore"])
@pytest.mark.asyncio
async def test_client_stopped_during_update_resumes_after_publisher_keeps_acceptance(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_stage: str | None,
) -> None:
    """準備中に終了境界で止めたclientは、公開元が受付を公開せずに確定か終了をしたら、静止の解除の段が失敗しても取り込み直す。

    準備中に届いた移譲の要求は受付先が変わっていないため何もせずに終わる。その後の準備の終了でも取り込み直しを予約し直す。
    """
    coordinator, session, backend = _coordinator(tmp_path, monkeypatch)
    coordinator.projections = types.SimpleNamespace(freeze=AsyncMock(), thaw=AsyncMock(), inventory=dict)
    coordinator.registrations = types.SimpleNamespace(freeze=AsyncMock(), thaw=Mock())
    descriptor = _stoppable_client(coordinator, session, backend)
    client = backend._clients[session.session_id]
    if failing_stage == "projections.thaw":
        coordinator.projections.thaw.side_effect = OSError(failing_stage)
    elif failing_stage == "registrations.thaw":
        coordinator.registrations.thaw.side_effect = OSError(failing_stage)
    elif failing_stage == "gate.thaw":
        monkeypatch.setattr(coordinator.gate, "thaw", AsyncMock(side_effect=OSError(failing_stage)))
    process = service_transport.process_identity(os.getpid())
    if failing_stage == "registry.restore":
        monkeypatch.setattr(service_coordinator, "_PUBLISHER_PROBE_SECONDS", 0.01)
        monkeypatch.setattr(coordinator.registry, "restore", Mock(side_effect=OSError(failing_stage)))
    publisher = {"generation": "G4", "epoch": 4, "process": process}
    await coordinator.prepare("token", publisher)
    assert client.transferred
    assert await coordinator.move(session.session_id) == {"moved": False}
    backend.adopt_session.assert_not_awaited()
    async with asyncio.timeout(5):
        if failing_stage == "registry.restore":
            # 準備中の移譲を確かめた後に公開元を終了させ、復旧workerとの実行順に依存させない。
            monkeypatch.setattr(service_transport, "process_alive", lambda _process: False)
        else:
            await coordinator.commit_update("token", coordinator.target)
        while not backend.adopt_session.await_count:
            await asyncio.sleep(0.01)
    backend.adopt_session.assert_awaited_once_with(session, descriptor, {})


@pytest.mark.asyncio
async def test_prepare_move_failure_after_client_stopped_adopts_session_back(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """準備中に止めたclientで、移譲先へ届かない予約の要求が例外を出したら、取り込み直して制御の受付を残さない。

    公開元が既に終了して接続先が無い状態を、実在しないsocketのendpointと実物のservice_rpc.requestで作る。
    """
    coordinator, session, backend = _coordinator(tmp_path, monkeypatch)
    coordinator.projections = types.SimpleNamespace(freeze=AsyncMock(), thaw=AsyncMock(), inventory=dict)
    coordinator.registrations = types.SimpleNamespace(freeze=AsyncMock(), thaw=Mock())
    descriptor = _stoppable_client(coordinator, session, backend)
    client = backend._clients[session.session_id]
    monkeypatch.setattr(coordinator.registry, "restore", Mock(side_effect=OSError("registry.restore")))
    dead_process = "2147483647:dead"
    await coordinator.prepare("token", {"generation": "G4", "epoch": 4, "process": dead_process})
    assert client.transferred
    dead_source = {
        "generation": "G4",
        "epoch": 4,
        "process": dead_process,
        "endpoint": r"\\.\pipe\no-such-pipe" if os.name == "nt" else "/nonexistent/no-such-socket",
    }
    monkeypatch.setattr(coordinator.registry, "current", lambda: dead_source)
    requests: list[tuple[str, str]] = []
    real_request = service_rpc.request

    async def recording_request(target: dict, operation: str, arguments: dict, **kwargs: Any) -> Any:
        requests.append((target["generation"], operation))
        return await real_request(target, operation, arguments, **kwargs)

    monkeypatch.setattr(service_rpc, "request", recording_request)
    async with asyncio.timeout(5):
        with pytest.raises(FileNotFoundError):
            await coordinator.move(session.session_id)
    assert requests == [("G4", "prepare_move")]
    backend.adopt_session.assert_awaited_once_with(session, descriptor, {})
    assert session.session_id not in coordinator.gate._paused
    assert session.session_id not in coordinator._uncertain


@pytest.mark.asyncio
@pytest.mark.parametrize("moving", [False, True], ids=["owned-by-old-generation", "ownership-unconfirmed"])
async def test_show_keeps_owner_and_queried_generation_distinct(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, moving: bool
) -> None:
    coordinator, session, _backend = _coordinator(tmp_path, monkeypatch)
    manager = coordinator.managers.conversations["parent"].manager
    manager.sessions.clear()
    coordinator.target["server_instance"] = "queried-instance"

    @contextlib.asynccontextmanager
    async def scope(_params: CallToolRequestParams, **_kwargs: Any):
        yield manager

    monkeypatch.setattr(coordinator.managers, "scope", scope)
    owner = {"generation": "G1", "epoch": 1, "process": "1:old", "server_instance": "old-owner"}
    monkeypatch.setattr(coordinator, "_owner", AsyncMock(return_value=owner if not moving else None))
    monkeypatch.setattr(coordinator, "_owns", AsyncMock(return_value=True))
    if moving:
        coordinator._uncertain[session.session_id] = "移譲の結果を確認できない"
    # 未更新の旧世代の正常応答にも、受付側で両識別子を補える。
    original = {"session_id": session.session_id, "status": "running"}
    request = AsyncMock(
        return_value={
            "result": {
                "content": [{"type": "text", "text": json.dumps(original)}],
                "structuredContent": original,
            }
        }
    )
    monkeypatch.setattr(service_rpc, "request", request)
    params = CallToolRequestParams(name="show", arguments={"session_id": session.session_id})
    with runtime_environment.bind(CallerContext({}, tmp_path)):
        result = await coordinator.call_tool(None, params)
    if moving:
        assert result.is_error
        assert "所有を確認できず" in result.content[0].text
        assert result.structured_content is None
        request.assert_not_awaited()
    else:
        expected = {**original, "owner_server_instance": "old-owner", "queried_server_instance": "queried-instance"}
        assert not result.is_error
        assert result.structured_content == expected
        assert json.loads(result.content[0].text) == expected


@pytest.mark.asyncio
async def test_busy_session_control_reports_before_delivery(tmp_path, monkeypatch):
    coordinator, session, _backend = _coordinator(tmp_path, monkeypatch)
    manager = coordinator.managers.conversations["parent"].manager

    @contextlib.asynccontextmanager
    async def scope(_params: CallToolRequestParams, **_kwargs: Any):
        yield manager

    monkeypatch.setattr(coordinator.managers, "scope", scope)
    lock = coordinator._locks.setdefault(session.session_id, asyncio.Lock())
    async with lock:
        with runtime_environment.bind(CallerContext({}, tmp_path)):
            async with asyncio.timeout(2):
                result = await coordinator.call_tool(
                    None, CallToolRequestParams(name="kill", arguments={"session_id": session.session_id, "timeout": 0.25})
                )
    assert result.is_error
    assert "停止要求は未配送" in result.content[0].text


@pytest.mark.asyncio
async def test_failed_move_on_owner_reports_undelivered_input_and_next_action(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """所有世代での移譲が失敗した追送は、汎用の内部エラーではなく未配送と次の操作をツールの結果で返す。"""
    coordinator, session, _backend = _coordinator(tmp_path, monkeypatch)
    manager = coordinator.managers.conversations["parent"].manager
    manager.sessions.clear()

    @contextlib.asynccontextmanager
    async def scope(_params: CallToolRequestParams, **_kwargs: Any):
        yield manager

    monkeypatch.setattr(coordinator.managers, "scope", scope)
    monkeypatch.setattr(coordinator, "_owner", AsyncMock(return_value={"generation": "G1", "epoch": 1, "process": "1:old"}))
    monkeypatch.setattr(coordinator, "_owns", AsyncMock(return_value=True))

    async def request(_target: dict, operation: str, _arguments: dict, **_kwargs: Any) -> dict:
        if operation == "owned_call":
            return {"move_required": True}
        raise RuntimeError("OSError: 状態の保存先へ書けない")

    monkeypatch.setattr(service_rpc, "request", request)
    params = CallToolRequestParams(name="send_message", arguments={"session_id": session.session_id, "prompt": "続ける"})
    with runtime_environment.bind(CallerContext({}, tmp_path)):
        result = await coordinator.call_tool(None, params)
    assert result.is_error
    text = result.content[0].text
    assert "未配送" in text and "状態の保存先へ書けない" in text and "次の操作" in text


@pytest.mark.asyncio
async def test_registry_derived_stopped_sessions_move_with_their_context(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """登録簿から復元した停止済み・期限切れのsessionも、通常の停止済みsessionと同様に型と結果・ターンを保って新世代の会話へ移る。

    2026-11-18、旧世代の共有監視がこの状態を符号化できず毎巡失敗し、会話を新世代へ移せなかった。
    """
    stopped = state.RegistryResumeState(
        "child",
        str(tmp_path),
        None,
        None,
        "claude",
        turn_seq=2,
        status="completed",
        agent_message="未回収の結果",
        finalized_at="2026-11-18T00:00:00+00:00",
        result_root_session_id="root",
        result_owner_status_file="owner.json",
    )
    expired = state.RegistryResumeState(
        "gone", str(tmp_path / "removed"), None, None, "codex", turn_seq=1, status="interrupted"
    )
    local = state.SessionResumeState("local", str(tmp_path), None, None, "codex", turn_seq=4, status="completed")
    old, _session, _backend = _coordinator(tmp_path, monkeypatch, current=False)
    old.projections._codec = service_state._StateCodec()
    old.managers.conversations.clear()
    old.managers.conversations["context"] = Conversation(
        Mock(sessions={}, expired_sessions={"gone": expired}, stopped_sessions={"child": stopped, "local": local}),
        CallerContext({}, tmp_path),
    )
    packets: list[dict] = []

    async def request(_target: dict, operation: str, arguments: dict, **_kwargs: Any) -> None:
        assert operation == "move_context"
        packets.append(json.loads(json.dumps(arguments)))

    monkeypatch.setattr(service_rpc, "request", request)
    assert not await old.tick()
    assert "context" not in old.managers.conversations
    new, _session, _backend = _coordinator(tmp_path, monkeypatch)
    new.projections._codec = service_state._StateCodec()
    received = Mock(expired_sessions={}, stopped_sessions={}, save_control_state=AsyncMock())

    @contextlib.asynccontextmanager
    async def scope(_params: CallToolRequestParams, **_kwargs: Any):
        yield received

    monkeypatch.setattr(new.managers, "scope", scope)
    assert len(packets) == 1
    await new.accept_context(packets[0])
    assert received.stopped_sessions == {"child": stopped, "local": local}
    assert received.expired_sessions == {"gone": expired}
    # 基底の再開状態へ縮退すると、結果の原本の所在と照会時の鮮度確認が失われる。
    copied = {**received.stopped_sessions, **received.expired_sessions}
    assert {identifier: type(value) for identifier, value in copied.items()} == {
        "child": state.RegistryResumeState,
        "gone": state.RegistryResumeState,
        "local": state.SessionResumeState,
    }
    received.save_control_state.assert_awaited_once()


def _seed(source: str) -> dict:
    """状態保存の代わりに、射影の保存の受付だけを通す操作。"""
    return {
        "identity": dataclasses.asdict(shared_roots.StatusFileIdentity("root", "root.json", None)),
        "source": source,
        "operation": "seed",
        "arguments": {"groups": {}, "published": []},
    }


def _generation_one(
    directory: pathlib.Path, monkeypatch: pytest.MonkeyPatch, managers: ServiceManagers
) -> tuple[ServiceCoordinator, dict, list[bool]]:
    """受付先のままの旧世代G1と、準備を求める新世代の生存の切替を作る。"""
    target = {"generation": "G1", "epoch": 1, "process": "1:owner"}
    coordinator = ServiceCoordinator(
        target, directory, managers, ProjectionHub("G1", directory), RegistryForwarder("G1", directory)
    )
    for registry in (coordinator.registry, coordinator.projections.registry, coordinator.registrations.registry):
        monkeypatch.setattr(registry, "current", lambda: target)
    exited = [False]
    monkeypatch.setattr(service_transport, "process_alive", lambda process: not exited[0] or process == target["process"])
    return coordinator, target, exited


@pytest.mark.parametrize("exit_at", ["after_prepare", "during_prepare", None])
@pytest.mark.asyncio
async def test_prepare_left_by_exited_generation_is_released(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_at: str | None,
) -> None:
    """準備させた新世代が受付を公開せず終了したら、旧世代は静止を解き、状態保存と新しいturnの受付を続ける。

    この場合は受付公開の確定が届かず、静止したままだと状態保存・新しいturn・自動再開が止まり続ける。
    新世代が生存している間は、準備中の引継ぎを失わないよう、生存の確認を繰り返しても静止を保つ。
    """
    monkeypatch.setattr(service_coordinator, "_PUBLISHER_PROBE_SECONDS", 0.01)
    coordinator, _target, exited = _generation_one(tmp_path / "services" / "generations", monkeypatch, ServiceManagers())
    freeze_projections = coordinator.projections.freeze

    async def exit_while_preparing(token: str) -> None:
        # 準備の途中で新世代が終了し、その終了の観測が残りの静止より先に進む。
        exited[0] = True
        await asyncio.sleep(0.1)
        await freeze_projections(token)

    if exit_at == "during_prepare":
        monkeypatch.setattr(coordinator.projections, "freeze", exit_while_preparing)
    await coordinator.prepare("token", {"generation": "G2", "epoch": 2, "process": "2:new"})
    exited[0] = exit_at is not None
    if exit_at is None:
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.2):
                await coordinator.projections.apply(_seed("G1"))
        assert not await coordinator.tick()
        return
    async with asyncio.timeout(2):
        await coordinator.projections.apply(_seed("G1"))
        async with coordinator.gate.admit(new_turn=True):
            pass
    assert await coordinator.tick()


@pytest.mark.parametrize("waiting_save", ["release_request", "heartbeat"])
@pytest.mark.asyncio
async def test_update_left_by_exited_generation_is_released_while_monitor_waits_on_save(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    waiting_save: str,
) -> None:
    """共有監視の1巡が静止中の状態保存で止まっていても、準備させた新世代の終了を受けて静止を解き、その保存を完了させる。

    解放要求の処理やheartbeatの保存は、静止中は解除を待つ。解除を同じ1巡の処理に置くと、そこへ到達しない。
    """
    session = state.SessionState("session", str(tmp_path), engine="codex", turn_end=turn_state.TurnEnd.COMPLETED)
    saved: list[str] = []
    managers = ServiceManagers(state_root=tmp_path)
    coordinator, _target, exited = _generation_one(tmp_path / "services" / "generations", monkeypatch, managers)

    async def save(reason: str) -> None:
        await coordinator.projections.apply(_seed("G1"))
        saved.append(reason)

    async def stop(identifier: str, *, retain_result: bool) -> dict:
        del retain_result
        await save("release_request")
        return {"session_id": identifier}

    manager: Any = Mock(
        sessions={session.session_id: session},
        expired_sessions={},
        _backends={},
        _service_paused=set(),
        save_control_state=lambda: save("heartbeat"),
        stop=stop,
        _process_auto_resume=AsyncMock(return_value=False),
    )
    managers.conversations["parent"] = Conversation(manager, CallerContext({}, tmp_path))
    requested = [(tmp_path / "fixture.release-request", {"session_id": session.session_id})]
    if waiting_save == "heartbeat":
        requested.clear()

    async def process_release_requests(sessions: dict, stop_session: Any, *, state_root: pathlib.Path, requests: list) -> None:
        assert state_root == tmp_path and requests == requested
        path, payload = requests[0]
        identifier = payload["session_id"]
        assert identifier in sessions
        requested.remove((path, payload))
        await stop_session(identifier, retain_result=True)

    monkeypatch.setattr(service_context.session_release, "pending", lambda _state_root: list(requested))
    monkeypatch.setattr(service_context.session_release, "process_requests", process_release_requests)
    await coordinator.prepare("token", {"generation": "G2", "epoch": 2, "process": "2:new"})
    async with managers:
        await asyncio.sleep(0.3)
        assert not saved
        exited[0] = True
        async with asyncio.timeout(5):
            while waiting_save not in saved:
                await asyncio.sleep(0.01)
