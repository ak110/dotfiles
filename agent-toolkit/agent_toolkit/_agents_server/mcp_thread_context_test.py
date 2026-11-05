"""通常CodexのMCP要求が付けるthreadIdから、親と復旧対象を分離する。"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import uuid
from typing import Any, cast

import anyio
import pytest
from mcp.server.context import ServerRequestContext
from mcp.types import CallToolRequestParams, CallToolResult

from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, launch_requests, mcp_tools, shared_roots, state, wait_targets
from agent_toolkit._agents_server.manager import AgentsServerManager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_support import FakeBackend, _complete, install_backend

# SDKが登録する公開要求の境界とMCPの起動寿命を、モデルを呼ばずに接続する。
# pylint: disable=protected-access
pytestmark = pytest.mark.usefixtures("agents_server_isolation")


class _Backend(FakeBackend):
    resume_gate: asyncio.Event | None = None
    resume_entered: asyncio.Event | None = None

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        del self.sessions[session.session_id]
        session.session_id = str(uuid.uuid4())
        session.publish_registry = True
        self.sessions[session.session_id] = session
        return session

    async def resume(self, *args: Any, **kwargs: Any) -> state.SessionState:
        if self.resume_entered is not None:
            self.resume_entered.set()
        if self.resume_gate is not None:
            await self.resume_gate.wait()
        session = await super().resume(*args, **kwargs)
        _complete(session, message="元の子の成果")
        return session


@pytest.fixture(name="configured")
def _configured(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> list[tuple[AgentsServerManager, _Backend]]:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [("claude", "model", "high")]
    )

    async def ready(_cwd: str) -> None:
        """外部ホストの準備だけを代用する。"""

    monkeypatch.setattr(launch_requests, "check_plugin_commands", ready)
    created: list[tuple[AgentsServerManager, _Backend]] = []

    def manager(*args: Any, **kwargs: Any) -> AgentsServerManager:
        result = AgentsServerManager(*args, **kwargs)
        backend = _Backend(result.sessions, "claude")
        backend.publish_registry = True
        install_backend(result, "claude", backend)
        created.append((result, backend))
        return result

    monkeypatch.setattr(mcp_tools, "AgentsServerManager", manager)
    monkeypatch.setattr(mcp_tools, "_MANAGER", AgentsServerManager(launcher_environment={}))
    return created


async def _call(parent: Any, name: str, arguments: dict[str, Any]) -> CallToolResult:
    # threadIdはCodex CLI 0.166.0が実際のtools/callへ付けた_metaの項目である。
    params = CallToolRequestParams.model_validate({"name": name, "arguments": arguments, "_meta": {"threadId": parent}})
    response = await mcp_tools.mcp._call_registered_tool(cast(ServerRequestContext[None], None), params)
    assert isinstance(response, CallToolResult)
    return response


@pytest.mark.asyncio
async def test_native_thread_metadata_separates_parallel_parents(
    configured: list[tuple[AgentsServerManager, _Backend]], tmp_path: pathlib.Path
) -> None:
    """同じMCP接続の2会話が起動した子を、それぞれの親とルートへ保存する。"""
    parents = [str(uuid.uuid4()), str(uuid.uuid4())]
    async with mcp_tools._mcp_lifespan(mcp_tools.mcp):
        responses = await asyncio.gather(
            *(
                _call(
                    parent,
                    "start",
                    {"cwd": str(tmp_path), "mode": "explore", "prompt": "元の依頼", "model_type": "chores_fast"},
                )
                for parent in parents
            )
        )
        for parent, response in zip(parents, responses, strict=True):
            assert not response.is_error
            assert response.structured_content is not None
            identifier = response.structured_content["session_id"]
            assert session_registry.child_session_ids(parent, state_root=tmp_path) == (identifier,)
            listed = await _call(parent, "list", {})
            assert listed.structured_content is not None
            assert listed.structured_content["root_session_id"] == parent
            assert [item["session_id"] for item in listed.structured_content["sessions"]] == [identifier]
        assert len(configured) == 2


@pytest.mark.parametrize("hold_connection", [False, True])
@pytest.mark.asyncio
async def test_native_thread_metadata_recovers_same_child_and_cli_wait(
    configured: list[tuple[AgentsServerManager, _Backend]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    hold_connection: bool,
) -> None:
    """環境に親IDの無いMCPを閉じ、新しい接続の最初の公開要求から子を復旧してCLIへ配送する。"""
    parent = str(uuid.uuid4())
    async with mcp_tools._mcp_lifespan(mcp_tools.mcp):
        response = await _call(
            parent, "start", {"cwd": str(tmp_path), "mode": "explore", "prompt": "保存する依頼", "model_type": "chores_fast"}
        )
        assert response.structured_content is not None
        identifier = response.structured_content["session_id"]
    monkeypatch.setattr(mcp_tools, "_MANAGER", AgentsServerManager(launcher_environment={}))
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", parent)
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket, **_kwargs: 0.0)
    entered, gate = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(_Backend, "resume_entered", entered)
    monkeypatch.setattr(_Backend, "resume_gate", gate)
    async with mcp_tools._mcp_lifespan(mcp_tools.mcp):
        if hold_connection:
            async with asyncio.TaskGroup() as requests:
                started = requests.create_task(_call(parent, "list", {}))
                with anyio.fail_after(3):
                    await entered.wait()
                try:
                    assert wait_targets.read_wait_targets(parent, "root.json", tmp_path)[0] == {identifier}
                    assert session_registry.resolve(identifier, state_root=tmp_path).resume_pending
                    identity = shared_roots.resolve_wait_identity(dict(os.environ), None, tmp_path)
                    assert identity is not None and identity.root_session_id == parent
                    capsys.readouterr()
                    with pytest.raises(SystemExit) as pending:
                        await asyncio.to_thread(atk.main, ["agents", "wait"])
                    assert pending.value.code == 3
                finally:
                    gate.set()
                await started
        else:
            gate.set()
            await _call(parent, "list", {})
        _, backend = configured[-1]
        capsys.readouterr()
        with anyio.fail_after(3):
            with pytest.raises(SystemExit) as completed:
                await asyncio.to_thread(atk.main, ["agents", "wait"])
        assert completed.value.code == 0
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        result = next(row for row in rows if row.get("session_id") == identifier and row.get("status") == "completed")
        assert backend.resume_calls == [identifier]
        assert result["agent_message"] == "元の子の成果"


@pytest.mark.parametrize("parent", ["../outside", "", 1])
@pytest.mark.asyncio
async def test_native_thread_metadata_rejects_invalid_identity(
    configured: list[tuple[AgentsServerManager, _Backend]], parent: Any
) -> None:
    """パスとして使えない会話IDでは子も状態のルートも作成しない。"""
    async with mcp_tools._mcp_lifespan(mcp_tools.mcp):
        response = await _call(parent, "list", {})
        assert response.is_error
        assert not configured
