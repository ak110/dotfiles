"""共有PID宛ての制御要求を、別会話で消費しない境界を検証する。"""

# 保存完了と受付予約の内部境界を代用I/Oで検証する。
# pylint: disable=protected-access

import asyncio
import json
import pathlib
import time
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import service_context, service_io, service_turns
from agent_toolkit._agents_server.service_coordinator import ServiceCoordinator
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._common import effort_requests, runtime_environment, session_registry, session_release


@pytest.mark.asyncio
async def test_effort_request_is_consumed_by_its_conversation_only(monkeypatch, tmp_path):
    first = SessionState(session_id="first", cwd=str(tmp_path), effort="low", model_type="chores")
    second = SessionState(session_id="second", cwd=str(tmp_path), effort="low", model_type="chores")
    first.effort_levels = second.effort_levels = ("low", "medium")
    request = tmp_path / "request.effort-request"
    packet = {"session_id": "second", "actor": "parent-second", "direction": "up", "deadline": time.monotonic() + 30}
    pending = [(request, packet)]
    responses = []
    applied = []
    finished = asyncio.Event()

    def respond(path, value):
        assert path == request
        responses.append(value)
        pending.clear()
        finished.set()

    original_read = pathlib.Path.read_text
    original_exists = pathlib.Path.exists
    record = session_registry.registry_directory(tmp_path) / "second.json"
    monkeypatch.setattr(
        pathlib.Path,
        "read_text",
        lambda path, *args, **kwargs: (
            json.dumps({session_registry.LAUNCHER_KEY: "parent-second"})
            if path == record
            else original_read(path, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(pathlib.Path, "exists", lambda path: path == request or original_exists(path))
    monkeypatch.setattr(effort_requests, "pending", lambda **_kwargs: list(pending))
    monkeypatch.setattr(effort_requests, "respond", respond)
    monkeypatch.setattr(session_release, "process_requests", AsyncMock())

    async def apply(session, value):
        applied.append((session.session_id, value, runtime_environment.environment()["CALLER"]))

    def conversation(session, marker):
        manager = SimpleNamespace(
            sessions={session.session_id: session},
            _effort_catalog=AsyncMock(),
            _apply_effort=apply,
            _process_auto_resume=AsyncMock(return_value=False),
            save_control_state=AsyncMock(),
            _service_paused=set(),
        )
        caller = runtime_environment.CallerContext({"HOME": str(tmp_path), "CALLER": marker}, tmp_path)
        return service_context.Conversation(cast(service_context.AgentsServerManager, manager), caller)

    pool = service_context.ServiceManagers(state_root=tmp_path)
    pool.conversations.update(first=conversation(first, "first"), second=conversation(second, "second"))
    async with pool, asyncio.timeout(5):
        await finished.wait()
    assert applied == [("second", "medium", "second")]
    assert first.effort == "low" and second.effort == "medium"
    assert len(responses) == 1 and responses[0]["changed"] is True
    assert "error" not in responses[0]


@pytest.mark.asyncio
async def test_accepted_effort_is_drained_through_control_save(monkeypatch, tmp_path):
    gate = service_turns.TurnGate("old", {"generation": "old", "epoch": 1})
    saving = asyncio.Event()
    complete_save = asyncio.Event()
    applied = []
    pending = [(tmp_path / "same.effort-request", {"session_id": "child"})]

    async def process(*_args, **_kwargs):
        applied.append("child")
        pending.clear()

    async def save():
        saving.set()
        await complete_save.wait()

    manager = SimpleNamespace(
        sessions={"child": object()},
        _service_paused=set(),
        _effort_catalog=AsyncMock(),
        _apply_effort=AsyncMock(),
        save_control_state=save,
        _process_auto_resume=AsyncMock(return_value=False),
    )
    pool = service_context.ServiceManagers(state_root=tmp_path)
    pool.conversations["owner"] = service_context.Conversation(
        cast(service_context.AgentsServerManager, manager),
        runtime_environment.CallerContext({}, tmp_path),
        service_io.Runtime("old", {}, cast(service_io.ResourceBroker, None), gate=gate),
    )
    monkeypatch.setattr(effort_requests, "pending", lambda **_kwargs: list(pending))
    monkeypatch.setattr(service_context.effort_control, "process_requests", process)
    monkeypatch.setattr(session_release, "process_requests", AsyncMock())
    monitor = asyncio.create_task(pool._monitor())
    pause = None
    try:
        async with asyncio.timeout(3):
            await saving.wait()
            pause = asyncio.create_task(gate.pause_session("child"))
            await asyncio.sleep(0)
            assert not pause.done()
            complete_save.set()
            await pause
            assert applied == ["child"]
    finally:
        complete_save.set()
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
        if pause is not None:
            await pause


@pytest.mark.asyncio
async def test_generations_distinguish_current_queried_and_retired(tmp_path):
    """照会した世代・受付中の世代・退出済みの世代を区別して返す。"""
    from agent_toolkit._agents_server.service_projection import ProjectionHub  # pylint: disable=import-outside-toplevel

    directory = tmp_path / "services/agents-server"
    hub = ProjectionHub("old", directory)
    registry = hub.registry
    for name, version in (("gone", "0.9.0"), ("old", "1.0.0"), ("new", "1.0.1")):
        registry.reserve(name, tmp_path, version)
        registry.publish(name, endpoint=name, process=name, server_instance=f"instance-{name}")
    registry.retire("gone", "gone")
    managers = service_context.ServiceManagers(state_root=tmp_path, projections=hub)
    assert await managers.generations() == [
        {"generation": "old", "epoch": 2, "server_instance": "instance-old", "current": False, "queried": True},
        {"generation": "new", "epoch": 3, "server_instance": "instance-new", "current": True, "queried": False},
    ]


@pytest.mark.asyncio
async def test_monitor_ticks_do_not_enumerate_host_processes(monkeypatch, tmp_path):
    """常駐する管理サービスの周期処理は、待機中の会話があってもホスト全体のプロセスを列挙しない。

    周期処理の仕事量がホストのプロセス数に比例すると、常駐中のCPU使用が規模とともに増える。
    """
    import psutil  # pylint: disable=import-outside-toplevel

    from agent_toolkit._testing.agents_server_support import _manager_with_fake  # pylint: disable=import-outside-toplevel

    calls: list[str] = []

    def counting(name, original):
        def wrapper(*args, **kwargs):
            calls.append(name)
            return original(*args, **kwargs)

        return wrapper

    for name in ("process_iter", "pids"):
        monkeypatch.setattr(psutil, name, counting(name, getattr(psutil, name)))
    monkeypatch.setattr(psutil.Process, "children", counting("children", psutil.Process.children))
    async with _manager_with_fake("codex") as (manager, _backend):
        waiting = SessionState(session_id="waiting", cwd=str(tmp_path), status="running", engine="codex")
        manager.sessions["waiting"] = waiting
        caller = runtime_environment.CallerContext({"HOME": str(tmp_path)}, tmp_path)
        pool = service_context.ServiceManagers(state_root=tmp_path)
        pool.conversations["parent"] = service_context.Conversation(manager, caller)
        # 実サービスと同じく管理サービスの周期処理（世代移行の確認）を監視の毎巡へ登録する。
        # 周期処理が失敗すると自動再開まで進まないため、下の計数は周期処理の成功も含めて数える。
        target = {"generation": "G1", "epoch": 1, "process": "1:owner"}
        coordinator = ServiceCoordinator(target, tmp_path, pool, AsyncMock(), AsyncMock())
        monkeypatch.setattr(coordinator.registry, "current", lambda: target)
        assert getattr(pool.before_controls, "__self__", None) is coordinator
        ticks = []
        original_resume = manager._process_auto_resume

        async def counted(**kwargs):
            ticks.append(1)
            return await original_resume(**kwargs)

        monkeypatch.setattr(manager, "_process_auto_resume", counted)
        async with pool:
            async with asyncio.timeout(10):
                while len(ticks) < 3:
                    await asyncio.sleep(0.05)
        manager.sessions.pop("waiting", None)
    assert len(ticks) >= 3
    assert not calls
