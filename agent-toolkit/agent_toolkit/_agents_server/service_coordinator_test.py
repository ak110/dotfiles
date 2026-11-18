"""移譲の成否不明時の二重実行と、背景資源を巻き込む接続回収を防ぐ。"""

# 外部I/Oを代用し、所有CASと受付予約の内部境界を検証する。
# pylint: disable=protected-access

import asyncio
import contextlib
import dataclasses
import json
import pathlib
import types
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolRequestParams

from agent_toolkit._agents_server import (
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
async def test_client_stopped_at_boundary_resumes_when_acceptance_returns_before_move(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """終了境界で止めたclientは、移譲を待つ間に受付先が自世代へ戻れば取り込み直し、次の応答を受け取れる状態へ戻す。

    公開を確定した新世代が終了すると、生存する旧世代へ受付が戻る。止めたままでは、その会話のdecodeが再開しない。
    """
    coordinator, session, backend = _coordinator(tmp_path, monkeypatch, current=False)
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


@pytest.mark.parametrize("exit_at", ["after_prepare", "during_prepare", None])
@pytest.mark.asyncio
async def test_prepare_left_by_exited_generation_is_released_on_next_tick(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_at: str | None,
) -> None:
    """準備させた新世代が受付を公開せず終了したら、旧世代は次の巡回で静止を解き、状態保存と新しいturnの受付を続ける。

    この場合は受付公開の確定が届かず、静止したままだと状態保存・新しいturn・自動再開が止まり続ける。
    新世代が生存している間は、準備中の引継ぎを失わないよう静止を保つ。
    """
    directory = tmp_path / "services" / "generations"
    target = {"generation": "G1", "epoch": 1, "process": "1:owner"}
    coordinator = ServiceCoordinator(
        target, directory, ServiceManagers(), ProjectionHub("G1", directory), RegistryForwarder("G1", directory)
    )
    for registry in (coordinator.registry, coordinator.projections.registry, coordinator.registrations.registry):
        monkeypatch.setattr(registry, "current", lambda: target)
    exited = [False]
    monkeypatch.setattr(service_transport, "process_alive", lambda process: not exited[0] or process == target["process"])
    freeze_projections = coordinator.projections.freeze

    async def exit_while_preparing(token: str) -> None:
        # 準備の途中で新世代が終了し、その間の巡回が静止を解いた後に残りの静止が掛かる。
        exited[0] = True
        await coordinator.tick()
        await freeze_projections(token)

    if exit_at == "during_prepare":
        monkeypatch.setattr(coordinator.projections, "freeze", exit_while_preparing)
    await coordinator.prepare("token", {"generation": "G2", "epoch": 2, "process": "2:new"})
    exited[0] = exit_at is not None
    allow_resume = await coordinator.tick()
    save = {
        "identity": dataclasses.asdict(shared_roots.StatusFileIdentity("root", "root.json", None)),
        "source": "G1",
        "operation": "seed",
        "arguments": {"groups": {}, "published": []},
    }
    if exit_at is None:
        assert not allow_resume
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.2):
                await coordinator.projections.apply(save)
        return
    async with asyncio.timeout(2):
        await coordinator.projections.apply(save)
        async with coordinator.gate.admit(new_turn=True):
            pass
    assert allow_resume
