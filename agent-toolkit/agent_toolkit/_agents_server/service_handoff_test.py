from __future__ import annotations

# pylint: disable=protected-access
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolRequestParams, CallToolResult

from agent_toolkit._agents_server import (
    mcp_tools,
    retained_results,
    service_rpc,
    service_transport,
    shared_layout,
    shared_roots,
    state,
    status_file,
)
from agent_toolkit._agents_server.manager import AgentsServerManager
from agent_toolkit._agents_server.service_context import Conversation
from agent_toolkit._agents_server.service_force import _pending_launch
from agent_toolkit._agents_server.service_publication_test import _coordinator
from agent_toolkit._common import delegated_session, runtime_environment, session_registry
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing.agents_server_support import _complete, _write_notice


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("completed_before_handoff", [False, True])
@pytest.mark.parametrize("cli_collects", [False, True])
async def test_formal_handoff_keeps_the_child_at_its_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, completed_before_handoff: bool, cli_collects: bool
) -> None:
    target = {
        "generation": "current",
        "server_instance": "same-instance",
        "process": service_transport.process_identity(os.getpid()),
    }
    service = _coordinator(tmp_path / "services" / "agents-server", target)
    monkeypatch.setattr(service, "_current", lambda: target)
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", service.managers)

    def conversation(identifier: str) -> Conversation:
        writer = status_file.StatusFileWriter(
            {}, shared_roots.StatusFileIdentity("shared-root", f"{identifier}.json", identifier), state_root=tmp_path
        )
        manager = AgentsServerManager(writer)
        caller = runtime_environment.CallerContext(
            {"CODEX_THREAD_ID": identifier, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}, tmp_path
        )
        item = Conversation(manager, caller)
        service.managers.conversations[identifier] = item
        return item

    source = conversation("predecessor")
    parent = conversation("parent")
    successor = conversation("successor")
    unrelated = conversation("unrelated")
    child_id = "18f5c370-d7bc-46dc-a057-310aebb09fa5"
    child = state.SessionState(child_id, str(tmp_path), engine="codex", turn_seq=1, announced=True)
    resolve = session_registry.resolve

    def registry_record(identifier: str, **kwargs: Any) -> session_registry.SessionResolution:
        if identifier == child_id:
            return session_registry.SessionResolution(
                session_registry.Resolution.RUNNING, server_instance=target["server_instance"]
            )
        return resolve(identifier, **kwargs)

    monkeypatch.setattr(session_registry, "resolve", registry_record)
    monkeypatch.setattr(session_registry, "owner_server_instance", lambda: target["server_instance"])
    predecessor = state.SessionState("predecessor", str(tmp_path))
    already_collected = state.SessionState("8ae24391-700c-4fa9-967d-7b1d938d28de", str(tmp_path))
    collected_during_launch = state.SessionState("7839888f-d43e-401a-9fa4-a4d4ac155e37", str(tmp_path))
    untracked = state.SessionState("untracked", str(tmp_path))
    source.manager.sessions[untracked.session_id] = untracked

    async def start_explore(self, prompt, cwd, *, label=None, model_type=None):
        request, _notice = delegated_session.split_own_contract_notice(prompt)
        session = {
            "前任": predecessor,
            "子": child,
            "回収済み": already_collected,
            "起動中に回収": collected_during_launch,
        }[request]
        assert self is (parent.manager if session is predecessor else source.manager)
        assert cwd == str(tmp_path) and label is None and model_type is None
        self.sessions[session.session_id] = session
        return {"session_id": session.session_id, "status": "running", "label": ""}

    async def start_handoff(self, session_id, *, model_type=None):
        assert self is parent.manager and session_id == "predecessor"
        identifier = collected_during_launch.session_id
        _complete(collected_during_launch, message="後任起動中に回収した結果")
        with runtime_environment.bind(source.caller):
            if cli_collects:
                writer = source.manager._status_writer
                assert writer is not None
                await writer.retain_result_async(collected_during_launch)
                claimed, error = retained_results.take_result(
                    "shared-root",
                    identifier,
                    "predecessor.json",
                    collector="atk-agents-wait",
                    state_root=tmp_path,
                    stash_path=tmp_path / "cli-run" / "results" / f"{identifier}.json",
                )
                assert error is None and claimed is not None
                assert claimed["agent_message"] == "後任起動中に回収した結果"
                assert not collected_during_launch.result_delivered
                assert await writer.result_state_async(identifier, session=collected_during_launch) == "consumed"
            else:
                result = await source.manager.wait(inherited_targets={identifier})
                assert result["agent_message"] == "後任起動中に回収した結果"
        source.manager.sessions.pop(identifier)
        cache = source.manager.expired_sessions if completed_before_handoff else source.manager.stopped_sessions
        cache[identifier] = state.SessionResumeState.from_session(collected_during_launch)
        assert cache[identifier].result_delivered is not cli_collects
        self.sessions["successor"] = state.SessionState("successor", str(tmp_path))
        return {"session_id": "successor", "status": "running", "label": "", "cwd": str(tmp_path), "model_type": model_type}

    delivered = []

    async def send_message(self, session_id, prompt, timeout=270):
        assert session_id == child.session_id
        assert timeout == 270
        delivered.append((self, prompt))
        return {"delivery": "steered", "label": ""}

    monkeypatch.setattr(AgentsServerManager, "start_explore", start_explore)
    monkeypatch.setattr(AgentsServerManager, "start_handoff", start_handoff)
    monkeypatch.setattr(AgentsServerManager, "send_message", send_message)

    async def call(item: Conversation, name: str, **arguments: Any) -> CallToolResult:
        with runtime_environment.bind(item.caller):
            result = await service.call_tool(None, CallToolRequestParams(name=name, arguments=arguments))
        assert isinstance(result, CallToolResult)
        return result

    try:
        assert not (await call(parent, "start", mode="explore", prompt="前任", cwd=str(tmp_path))).is_error
        assert not (await call(source, "start", mode="explore", prompt="子", cwd=str(tmp_path))).is_error
        assert not (await call(source, "start", mode="explore", prompt="回収済み", cwd=str(tmp_path))).is_error
        assert not (await call(source, "start", mode="explore", prompt="起動中に回収", cwd=str(tmp_path))).is_error
        _complete(already_collected, message="前任が回収した結果")
        with runtime_environment.bind(source.caller):
            assert (await source.manager.wait())["session_id"] == already_collected.session_id
        assert session_registry.resolve(child.session_id, state_root=tmp_path).resume_info is None
        _complete(predecessor)
        if completed_before_handoff:
            _complete(child, message="子の結果")
        writer = source.manager._status_writer
        assert writer is not None
        if completed_before_handoff:
            for error in (OSError, asyncio.CancelledError):
                with monkeypatch.context() as failure:
                    failure.setattr(writer, "result_state_async", AsyncMock(side_effect=error))
                    with pytest.raises(error):
                        await call(parent, "start", handoff_from="predecessor")
                assert "successor" not in parent.manager.sessions
                assert (await call(successor, "collect_handoff", timeout=0)).structured_content == {"status": "expired"}
        assert not (await call(parent, "start", handoff_from="predecessor")).is_error
        duplicate = await call(parent, "start", handoff_from="predecessor")
        assert duplicate.is_error and "successor" in str(duplicate.content)
        refused = await call(unrelated, "show", session_id=child_id)
        assert refused.is_error and "belongs to another writer" in str(refused.content)
        shown = await call(successor, "show", session_id=child_id)
        assert not shown.is_error, shown.content
        assert shown.structured_content is not None and shown.structured_content["cwd"] == str(tmp_path)
        assert (await call(successor, "show", session_id="untracked")).is_error
        refused_collected = await call(successor, "show", session_id=already_collected.session_id)
        assert refused_collected.is_error
        collected_id = collected_during_launch.session_id
        denied_show = await call(successor, "show", session_id=collected_id)
        assert denied_show.is_error
        denied_send = await call(successor, "send_message", session_id=collected_id, prompt="続行")
        assert denied_send.is_error
        if cli_collects:
            assert "未回収状態" in str(denied_show.content)
            assert "未回収状態" in str(denied_send.content)
        assert (await call(unrelated, "collect_handoff", timeout=0)).structured_content == {"status": "expired"}
        if not completed_before_handoff:
            await writer.activate_async()
            await writer.flush_async()
            notices = shared_layout.notices_directory("shared-root", tmp_path)
            _write_notice(notices, child_id, 1, "2026-11-24T00:00:00+00:00", "子からの通知")
            notice = (await call(successor, "collect_handoff", timeout=0)).structured_content
            assert notice is not None and notice["status"] == "running"
            assert notice["notices"] == [{"body": "子からの通知"}]
            running = (await call(successor, "collect_handoff", timeout=0)).structured_content
            assert running is not None and running["status"] == "running" and "notices" not in running
            assert not (await call(successor, "send_message", session_id=child_id, prompt="続行")).is_error
            assert delivered == [(source.manager, "続行")]
            _complete(child, message="子の結果")
        collected = await call(successor, "collect_handoff", timeout=0)
        assert not collected.is_error, collected.content
        assert collected.structured_content is not None and collected.structured_content["agent_message"] == "子の結果"
        expired = (await call(successor, "collect_handoff", timeout=0)).structured_content
        assert expired is not None and expired["status"] == "expired"
        assert child.result_delivered
        _complete(untracked, message="前任だけの結果")
        assert (await source.manager.wait())["session_id"] == "untracked"
    finally:
        for item in service.managers.conversations.values():
            item.manager.sessions.clear()
            await item.manager.close()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize(("foreign_owner", "release_failure"), [(False, False), (True, False), (False, True)])
async def test_public_force_kill_stops_delegation_tree_during_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign_owner: bool, release_failure: bool
) -> None:
    target = {
        "generation": "current",
        "server_instance": "same-instance",
        "process": service_transport.process_identity(os.getpid()),
    }
    service = _coordinator(tmp_path / "services" / "agents-server", target)
    monkeypatch.setattr(service, "_current", lambda: target)
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", service.managers)
    previous_target = {**target, "generation": "previous", "server_instance": "previous-instance"}
    previous = _coordinator(service.directory, previous_target)
    foreign = {"generation": "previous"}
    rpc_calls: list[str] = []

    async def owner(identifier):
        return foreign if foreign_owner and identifier == "third" else None

    async def request(target, operation, arguments):
        assert target == foreign and operation == "force_owned"
        rpc_calls.append(arguments["session_id"])
        await previous.force_owned(arguments["launcher"], arguments["session_id"], arguments["timeout"])

    monkeypatch.setattr(service, "_owner", owner)
    monkeypatch.setattr(service_rpc, "request", request)
    starting = asyncio.Event()
    stopping = asyncio.Event()
    known_stopping = asyncio.Event()
    allow_root_stop = asyncio.Event()
    allow_registration = asyncio.Event()
    original_call = mcp_tools.mcp._call_registered_tool

    async def tool_call(context, params):
        if params.name != "start":
            return await original_call(context, params)
        starting.set()
        await allow_registration.wait()
        manager = service.managers.conversations["second"].manager
        manager.sessions["late"] = state.SessionState("late", str(tmp_path), status="running", engine="codex")
        return CallToolResult(content=[], structured_content={"session_id": "late", "status": "running"})

    monkeypatch.setattr(mcp_tools.mcp, "_call_registered_tool", tool_call)
    stopped: list[str] = []
    sending = asyncio.Event()

    async def release(session):
        stopped.append(session.session_id)
        if session.session_id == "first":
            stopping.set()
            await allow_root_stop.wait()
        if {"second", "third"}.issubset(stopped):
            known_stopping.set()
        if session.session_id == "late" and release_failure:
            raise ActionableError("停止できない", next_action="停止を再試行する")

    async def send(self, session_id, *_args):
        session = self.sessions[session_id]
        if session.status == "interrupted":
            session.status = "running"
            return {"delivery": "reply_started", "label": ""}
        async with session.turn_control_lock:
            sending.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                session.status = "failed"
                raise

    monkeypatch.setattr(AgentsServerManager, "_send_message_response", send)
    for parent, child in (("caller", "first"), ("first", "second"), ("second", "third"), ("other", "unrelated")):
        manager = AgentsServerManager(None)
        manager.sessions[child] = state.SessionState(child, str(tmp_path), status="running", engine="codex")
        monkeypatch.setattr(manager, "_backend", lambda _engine: SimpleNamespace(release_session=release))
        caller = runtime_environment.CallerContext(
            {"CODEX_THREAD_ID": parent, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}, tmp_path
        )
        coordinator = previous if foreign_owner and child == "third" else service
        coordinator.managers.conversations[parent] = Conversation(manager, caller)
        session_registry.publish(
            child,
            state=session_registry.PublicationState.from_status("running", result_available=False),
            launcher_session_id=parent,
            owner_root_session_id=parent,
            engine="codex",
            cwd=str(tmp_path),
            turn_seq=1,
            state_root=tmp_path,
        )

    async def call(name: str, **arguments: Any) -> CallToolResult:
        with runtime_environment.bind(service.managers.conversations["caller"].caller):
            result = await service.call_tool(None, CallToolRequestParams(name=name, arguments=arguments))
        assert isinstance(result, CallToolResult)
        return result

    async def start_descendant() -> CallToolResult:
        with runtime_environment.bind(service.managers.conversations["second"].caller):
            result = await service.call_tool(None, CallToolRequestParams(name="start", arguments={}))
        assert isinstance(result, CallToolResult)
        return result

    if foreign_owner:
        service.managers.conversations["second"] = previous.managers.conversations["second"]
    late = asyncio.create_task(start_descendant())
    pending = asyncio.create_task(call("send_message", session_id="first", prompt="送信"))
    force = None
    try:
        async with asyncio.timeout(3):
            await sending.wait()
            await starting.wait()
            force = asyncio.create_task(call("kill", session_id="first", force=True))
            await stopping.wait()
            await known_stopping.wait()
            rejected = await start_descendant()
            assert rejected.is_error
            assert "強制停止中" in str(rejected.content)
            allow_root_stop.set()
            completed, _ = await asyncio.wait({force}, timeout=0.1)
            assert not completed, "下位の起動が未確定のままforceが返った"
            allow_registration.set()
            assert (await late).is_error
            result = await force
        if release_failure:
            assert result.is_error
            assert "回収の完了を確認できません" in str(result.content)
            stopped_late = service.managers.conversations["second"].manager.sessions["late"]
            stopped_late.status = "interrupted"
            stopped_late.record_turn_completed()
            session_registry.publish(
                "late",
                state=session_registry.PublicationState.from_status("interrupted", result_available=True),
                launcher_session_id="second",
                owner_root_session_id="second",
                engine="codex",
                cwd=str(tmp_path),
                turn_seq=1,
                state_root=tmp_path,
            )
            retried = await call("kill", session_id="first", force=True)
            assert not retried.is_error, retried.content
            pending_dir = session_registry.registry_directory(tmp_path) / ".force" / "pending-starts"
            assert not any(pending_dir.glob("*.failed"))
            return
        assert not result.is_error, result.content
        assert set(stopped) == {"first", "second", "third", "late"}
        assert rpc_calls == (["third"] if foreign_owner else [])
        assert pending.done() and (await pending).is_error
        assert service.managers.conversations["caller"].manager.sessions["first"].status == "interrupted"
        assert service.managers.conversations["other"].manager.sessions["unrelated"].status == "running"
        assert service.managers.conversations["second"].manager.sessions["late"].status == "interrupted"
    finally:
        tasks = [pending, late] if force is None else [pending, late, force]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    resumed = await call("send_message", session_id="first", prompt="再開")
    assert not resumed.is_error and resumed.structured_content == {"delivery": "reply_started", "label": ""}


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_force_children_finds_grandchild_registered_during_child_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = {
        "generation": "current",
        "server_instance": "same-instance",
        "process": service_transport.process_identity(os.getpid()),
    }
    service = _coordinator(tmp_path / "services" / "agents-server", target)
    monkeypatch.setattr(service, "_owner", AsyncMock(return_value=None))
    stopped: list[str] = []

    def publish(child: str, launcher: str, status: Literal["running", "interrupted"] = "running") -> None:
        session_registry.publish(
            child,
            engine="claude",
            cwd=str(tmp_path),
            state=session_registry.PublicationState.from_status(status, result_available=status != "running"),
            launcher_session_id=launcher,
            turn_seq=1,
            state_root=tmp_path,
        )

    async def stop(launcher: str, identifier: str, _timeout: float) -> None:
        stopped.append(identifier)
        if identifier == "child":
            publish("late-grandchild", "child")
        publish(identifier, launcher, "interrupted")

    monkeypatch.setattr(service, "force_owned", stop)
    publish("child", "parent")
    publish("unrelated", "other")
    await service._force_children("parent", 0)
    assert stopped == ["child", "late-grandchild"]
    assert session_registry.resolve("late-grandchild", state_root=tmp_path).state is session_registry.Resolution.TERMINAL
    assert session_registry.resolve("unrelated", state_root=tmp_path).state is session_registry.Resolution.RUNNING


@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.parametrize("change_turn", [False, True])
async def test_force_rechecks_descendant_after_queued_send_and_turn_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change_turn: bool
) -> None:
    target = {
        "generation": "review",
        "server_instance": "review-instance",
        "process": service_transport.process_identity(os.getpid()),
    }
    service = _coordinator(tmp_path / "services" / "agents-server", target)
    monkeypatch.setattr(service, "_current", lambda: target)
    monkeypatch.setattr(mcp_tools, "_SERVICE_MANAGERS", service.managers)
    admitted = asyncio.Event()
    released: list[str] = []

    async def release(session: state.SessionState) -> None:
        released.append(session.session_id)
        if session.session_id == "second":
            publish("second", "first", "interrupted", released.count("second"))

    def publish(child: str, launcher: str, status: Literal["running", "interrupted"], turn: int) -> None:
        session_registry.publish(
            child,
            state=session_registry.PublicationState.from_status(status, result_available=status == "interrupted"),
            engine="codex",
            cwd=str(tmp_path),
            launcher_session_id=launcher,
            owner_root_session_id=launcher,
            turn_seq=turn,
            state_root=tmp_path,
        )

    async def deliver(self: AgentsServerManager, identifier: str, *_args: Any) -> dict[str, str]:
        item = self.sessions[identifier]
        item.status = "running"
        item.record_turn_running()
        publish(identifier, "first", "running", 2)
        return {"delivery": "reply_started", "label": ""}

    monkeypatch.setattr(AgentsServerManager, "_send_message_response", deliver)
    for parent, child in (("caller", "first"), ("first", "second")):
        mgr = AgentsServerManager(None)
        mgr.sessions[child] = state.SessionState(child, str(tmp_path), status="running", engine="codex")
        monkeypatch.setattr(mgr, "_backend", lambda _: SimpleNamespace(release_session=release))
        service.managers.conversations[parent] = Conversation(
            mgr,
            runtime_environment.CallerContext(
                {"CODEX_THREAD_ID": parent, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}, tmp_path
            ),
        )
        publish(child, parent, "running", 1)
    child_mgr = service.managers.conversations["first"].manager
    real_require = service._require_unfenced

    async def require(identifier: str) -> dict[str, str]:
        result = await real_require(identifier)
        if identifier == "second":
            admitted.set()
        return result

    monkeypatch.setattr(service, "_require_unfenced", require)
    lock = service._locks.setdefault("second", asyncio.Lock())

    async def call(parent: str, name: str, **args: Any) -> CallToolResult:
        caller = service.managers.conversations[parent].caller
        with runtime_environment.bind(caller):
            result = await service.call_tool(None, CallToolRequestParams(name=name, arguments=args))
        assert isinstance(result, CallToolResult)
        return result

    queued: asyncio.Task[CallToolResult] | None = None
    force: asyncio.Task[CallToolResult] | None = None
    try:
        async with asyncio.timeout(4):
            async with _pending_launch(tmp_path, "second"):
                async with lock:
                    if not change_turn:
                        queued = asyncio.create_task(call("first", "send_message", session_id="second", prompt="次の指示"))
                        await admitted.wait()
                    force = asyncio.create_task(call("caller", "kill", session_id="first", force=True))
                    while child_mgr.sessions["second"].status != "interrupted" or "second" in child_mgr._force_stopping:
                        if force.done():
                            raise AssertionError(force.result())
                        await asyncio.sleep(0.01)
                    if change_turn:
                        await deliver(child_mgr, "second")
                assert not force.done()
                if queued is not None:
                    assert (await queued).is_error
            result = await force
            assert not result.is_error, result.content
            assert child_mgr.sessions["second"].status == "interrupted"
            assert released.count("second") == (2 if change_turn else 1)
            resolved = session_registry.resolve("second", state_root=tmp_path)
            assert resolved.state is session_registry.Resolution.TERMINAL
            assert resolved.recorded_turn_seq == (2 if change_turn else 1)
    finally:
        tasks = [t for t in (queued, force) if t is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
