"""`_agents_server/manager.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import json
import logging
import os
import pathlib
import uuid
from typing import Any

import pytest

from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import mcp_tools, shared_roots, state, status_file
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import mcp_environment, session_registry, state_paths
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing.agents_server_support import (
    _LAUNCHER_ENVIRONMENT_NAMES,
    AssistantMessage,
    FakeBackend,
    FakeClaudeClient,
    ResultMessage,
    SystemMessage,
    _assert_no_forbidden_keys,
    _complete,
    _install_backend,
    _RegistryBackend,
    install_backend,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_parallel_claude_delegates_get_distinct_session_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """remote会話contextを与えたMCPの整備後、並行会話と回収結果が独立する。

    CLI代用のID決定は2026年10月18日の実MCP観測で確認したremote IDの継承を模す。
    """
    parent = str(uuid.uuid4())
    for name, value in {
        "CLAUDE_CODE_SESSION_ID": parent,
        "CLAUDE_CODE_REMOTE": "true",
        "CLAUDE_CODE_REMOTE_SESSION_ID": "cse_test_parallel",
        "CLAUDE_CODE_POST_FOR_SESSION_INGRESS_V2": "true",
    }.items():
        monkeypatch.setenv(name, value)
    async with server_manager.AgentsServerManager() as manager:
        mcp_tools._prepare_child_environment()
        assert manager._status_writer is not None
        manager._status_writer.activate()
        inherited: list[dict[str, str]] = []

        def client_factory(options: Any) -> FakeClaudeClient:
            environment = {**os.environ, **options.env}
            inherited.append(environment)
            identifier = parent if environment.get("CLAUDE_CODE_REMOTE_SESSION_ID") else str(uuid.uuid4())
            return FakeClaudeClient([[SystemMessage(identifier), AssistantMessage(identifier), ResultMessage(identifier)]])

        async with claude_backend.ClaudeServerManager(
            manager.sessions,
            client_factory=client_factory,
            publish_registry=True,
            root_session_id=parent,
        ) as backend:
            install_backend(manager, "claude", backend)
            try:
                started = await asyncio.gather(
                    *[manager.start("claude:sonnet/high", "識別子確認", str(tmp_path)) for _ in range(2)]
                )
                identifiers = {response["session_id"] for response in started}
                assert len(identifiers) == 2 and parent not in identifiers
                assert {response["root_session_id"] for response in started} == {parent}
                for environment in inherited:
                    forbidden = mcp_environment._NON_INHERITABLE_NAMES - {
                        "agent_toolkit_owner_session",
                        "agent_toolkit_delegated_session",
                        "agent_toolkit_delegated_launch_kind",
                    }
                    assert not forbidden.intersection(name.casefold() for name in environment)
                    assert environment["AGENT_TOOLKIT_OWNER_SESSION"] == parent
                    assert environment["AGENT_TOOLKIT_DELEGATED_SESSION"] == "1"
                    assert environment["AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND"] == "delegate"
                for identifier in identifiers:
                    session = manager.sessions[identifier]
                    await session.drain_registration()
                assert all([(await manager.show_session(identifier))["result_available"] for identifier in identifiers])
                assert manager._status_writer is not None
                await manager._status_writer.flush_async()
                for identifier in identifiers:
                    result = manager._status_writer.read_result(identifier)
                    assert result is not None and result["agent_message"] == identifier
            finally:
                await manager.close()


@pytest.mark.parametrize("existing", ["running", "completed", "root", "stopped", "expired", "registry"])
@pytest.mark.asyncio
async def test_start_rejects_duplicate_session_id(existing: str, tmp_path: pathlib.Path) -> None:
    """重複したbackend初期化は永続化前に失敗し、既存の登録と結果を保持する。"""
    async with server_manager.AgentsServerManager() as manager:
        assert manager._status_writer is not None
        manager._status_writer.activate()
        identifier = manager._status_writer.root_session_id if existing == "root" else str(uuid.uuid4())
        old = state.SessionState(identifier, str(tmp_path), engine="claude", publish_registry=True)
        if existing == "completed":
            _complete(old, message="保持すべき結果")
        else:
            old.touch()
        await old.drain_registration()
        if existing == "root":
            pass
        elif existing in {"running", "completed"}:
            manager.sessions[identifier] = old
        elif existing == "stopped":
            manager.stopped_sessions[identifier] = state.SessionResumeState.from_session(old)
        elif existing == "expired":
            manager.expired_sessions[identifier] = state.SessionResumeState.from_session(old)
        await manager._status_writer.flush_async()
        before = {path: path.read_bytes() for path in state_paths.state_dir().rglob("*.json")}
        async with claude_backend.ClaudeServerManager(
            manager.sessions,
            client_factory=lambda _options: FakeClaudeClient([[SystemMessage(identifier)]]),
            publish_registry=True,
            root_session_id=manager._status_writer.root_session_id,
        ) as backend:
            install_backend(manager, "claude", backend)
            try:
                with pytest.raises(ActionableError, match="duplicate session_id") as raised:
                    await manager.start("claude:sonnet/high", "重複確認", str(tmp_path))
                assert raised.value.next_action
                assert "existing=" in str(raised.value)
                if existing in {"running", "completed"}:
                    assert manager.sessions[identifier] is old
                assert {path: path.read_bytes() for path in before} == before
            finally:
                await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
async def test_start_projects_shared_state_without_internal_fields(
    engine: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """両engineの開始応答が同じ公開射影を持つ。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        _install_backend(manager, engine, FakeBackend(manager.sessions, engine))
        monkeypatch.setattr(
            _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [(engine, "model", "high")]
        )
        with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.mcp"):
            response = await manager.start("plan", "調査", str(tmp_path))
        assert response == {
            "session_id": f"{engine}-session",
            "engine": engine,
            "status": "running",
            "model_type": "plan",
            "model": "model",
            "effort": "high",
            "root_session_id": "root-session",
            "label": "調査",
        }
        _assert_no_forbidden_keys(response)
        assert f"session_transition event=start session_id={engine}-session writer=mcp-manager status=running" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment", "host_alias", "expected"),
    [
        ({"CLAUDE_CODE_SESSION_ID": "claude-main"}, None, "claude-main"),
        (
            {
                "AGENT_TOOLKIT_OWNER_SESSION": "owner-root",
                "AGENT_TOOLKIT_DELEGATED_SESSION": "1",
                "CLAUDE_CODE_SESSION_ID": "claude-delegate",
            },
            None,
            "claude-delegate",
        ),
        (
            {"AGENT_TOOLKIT_OWNER_SESSION": "owner-root", "AGENT_TOOLKIT_STATUS_HOST_SESSION": "writer-1"},
            ("owner-root", "writer-1", "codex-thread-9"),
            "codex-thread-9",
        ),
        ({"CODEX_THREAD_ID": "codex-thread-direct"}, None, "codex-thread-direct"),
    ],
    ids=["claude-main", "claude-delegate", "codex-delegate", "codex-direct"],
)
async def test_start_records_launcher_in_registry_and_keeps_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    environment: dict[str, str],
    host_alias: tuple[str, str, str] | None,
    expected: str,
) -> None:
    """`start`は作成時点の委譲元を登録簿へ記録し、状態の更新と解放済みへの置き換えの後も保持する。

    親のセッション記録に起動結果が残らない委譲先は、登録簿の委譲元が無いと`atk serve`の一覧で親を持たない。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    for name in _LAUNCHER_ENVIRONMENT_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    if host_alias is not None:
        root_session_id, writer, host = host_alias
        shared_roots.write_host_alias(root_session_id, writer, host, tmp_path)
    async with server_manager.AgentsServerManager() as manager:
        _install_backend(manager, "codex", _RegistryBackend(manager.sessions, "codex"))
        monkeypatch.setattr(
            _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [("codex", "model", "high")]
        )

        response = await manager.start("plan", "調査", str(tmp_path))
        session_id = str(response["session_id"])
        registry = session_registry.registry_directory(tmp_path) / f"{session_id}.json"

        assert json.loads(registry.read_text(encoding="utf-8"))["launcher_session_id"] == expected
        _complete(manager.sessions[session_id])
        assert (await manager.wait())["status"] == "completed"
        assert json.loads(registry.read_text(encoding="utf-8"))["status"] == "completed"
        assert json.loads(registry.read_text(encoding="utf-8"))["launcher_session_id"] == expected
        await manager.stop(session_id)
        assert json.loads(registry.read_text(encoding="utf-8"))["launcher_session_id"] == expected
        assert session_registry.resolve(session_id).state is session_registry.Resolution.RELEASED
        await manager.close()
