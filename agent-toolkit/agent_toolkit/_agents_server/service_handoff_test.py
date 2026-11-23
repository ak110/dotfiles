from __future__ import annotations

# pylint: disable=protected-access
import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolRequestParams, CallToolResult

from agent_toolkit._agents_server import mcp_tools, shared_layout, shared_roots, state, status_file
from agent_toolkit._agents_server.manager import AgentsServerManager
from agent_toolkit._agents_server.service_context import Conversation
from agent_toolkit._agents_server.service_publication_test import _coordinator
from agent_toolkit._common import runtime_environment, session_registry
from agent_toolkit._testing.agents_server_support import _complete, _write_notice


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("completed_before_handoff", [False, True])
async def test_formal_handoff_keeps_the_child_at_its_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, completed_before_handoff: bool
) -> None:
    target = {"generation": "current", "server_instance": "same-instance"}
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
        session = {
            "前任": predecessor,
            "子": child,
            "回収済み": already_collected,
            "起動中に回収": collected_during_launch,
        }[prompt]
        assert self is (parent.manager if session is predecessor else source.manager)
        assert cwd == str(tmp_path) and label is None and model_type is None
        self.sessions[session.session_id] = session
        return {"session_id": session.session_id, "status": "running", "label": ""}

    async def start_handoff(self, session_id, *, model_type=None):
        assert self is parent.manager and session_id == "predecessor"
        identifier = collected_during_launch.session_id
        _complete(collected_during_launch, message="後任起動中に回収した結果")
        with runtime_environment.bind(source.caller):
            result = await source.manager.wait(inherited_targets={identifier})
        assert result["agent_message"] == "後任起動中に回収した結果"
        source.manager.sessions.pop(identifier)
        cache = source.manager.expired_sessions if completed_before_handoff else source.manager.stopped_sessions
        cache[identifier] = state.SessionResumeState.from_session(collected_during_launch)
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
        assert (await call(successor, "show", session_id=collected_during_launch.session_id)).is_error
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
