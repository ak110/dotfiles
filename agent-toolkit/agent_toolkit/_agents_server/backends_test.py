"""engine別のbackendの操作結果（`_agents_server/backends.py`）を検証する。"""

import asyncio
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import backends, engine_availability, manager, state
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_support import FakeBackend, install_backend

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def _failed(engine: str, error: dict[str, object]) -> state.SessionState:
    return state.SessionState(session_id="s", cwd="/", engine=engine, status="failed", error=error)


@pytest.mark.asyncio
async def test_backends_satisfy_protocol(tmp_path: pathlib.Path) -> None:
    """全engineのbackendを生成関数から`Backend`として得て、engine差の判定をbackendの中で行う。"""
    sessions: dict[str, state.SessionState] = {}
    condition = asyncio.Condition()
    created: dict[str, backends.Backend] = {
        engine: backends.create_backend(
            engine,
            sessions,
            condition,
            expire_session=lambda _session_id: None,
            root_session_id=None,
            log_directory=tmp_path,
        )
        for engine in sorted(backends.SUPPORTED_ENGINES)
    }

    for engine, backend in created.items():
        assert backend.unavailable_reason(state.SessionState(session_id="s", cwd="/", engine=engine)) is None
        await backend.close()

    assert created["claude"].unavailable_reason(_failed("claude", {"apiErrorStatus": 429})) == "429:rate_limit"
    assert created["codex"].unavailable_reason(_failed("codex", {"apiErrorStatus": 429})) == "429"
    rejected = '{"error": {"param": "model"}}'
    assert (
        created["codex"].unavailable_reason(_failed("codex", {"message": rejected}))
        == engine_availability.ENGINE_MODEL_REJECTED_REASON
    )
    assert created["claude"].unavailable_reason(_failed("claude", {"message": rejected})) is None
    assert created["agy"].unavailable_reason(_failed("agy", {"stderr": "boom"})) == "boom"
    assert (
        created["opencode"].unavailable_reason(_failed("opencode", {"stderr": "authentication failed"}))
        == "authentication failed"
    )
    assert not backends.excludes_with_recorded_reason("claude", engine_availability.LEGACY_CLAUDE_RATE_LIMIT_REASON)
    assert backends.excludes_with_recorded_reason("codex", engine_availability.LEGACY_CLAUDE_RATE_LIMIT_REASON)
    assert backends.interrupt_requires_turn_id("codex")
    assert not backends.interrupt_requires_turn_id("claude")


def test_unsupported_engine_has_no_backend() -> None:
    """backendを持たないengine名は生成を拒否し、記録済みの除外理由を常に根拠とする。"""
    assert backends.backend_class("other") is None
    assert backends.excludes_with_recorded_reason("other", "429")
    with pytest.raises(ValueError, match="unsupported engine: other"):
        backends.create_backend(
            "other",
            {},
            asyncio.Condition(),
            expire_session=lambda _session_id: None,
            root_session_id=None,
            log_directory=None,
        )


@pytest.mark.parametrize("engine", ["claude", "codex", "agy", "opencode"])
@pytest.mark.asyncio
async def test_start_exception_only_advances_cli_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, engine: str
) -> None:
    """実backendの開始境界が失敗した場合、CLI型だけが次候補へ進む。"""
    async with manager.AgentsServerManager(None) as owner:
        backend = owner._backend(engine)  # noqa: SLF001  # pylint: disable=protected-access

        async def failed_start(*_args: Any, **_kwargs: Any) -> state.SessionState:
            raise RuntimeError("開始境界の失敗")

        monkeypatch.setattr(backend, "start", failed_start)
        fallback_engine = "codex" if engine == "claude" else "claude"
        fallback = FakeBackend(owner.sessions, fallback_engine)
        install_backend(owner, fallback_engine, fallback)
        model = "local/first" if engine == "opencode" else "first"
        first = f"opencode:{model}" if engine == "opencode" else f"{engine}:{model}/medium"
        candidates = f"{first},{fallback_engine}:second/medium"
        if engine in {"agy", "opencode"}:
            response = await owner.start(candidates, "調査", str(tmp_path))
            assert response["engine"] == fallback_engine
            assert response["excluded_candidates"] == [
                {"engine": engine, "model": model, "effort": "medium", "reason": "開始境界の失敗"}
            ]
        else:
            with pytest.raises(RuntimeError, match="開始境界の失敗"):
                await owner.start(candidates, "調査", str(tmp_path))
            assert not fallback.start_calls


@pytest.mark.parametrize("engine", ["claude", "codex", "agy", "opencode"])
@pytest.mark.asyncio
async def test_orphan_takeover_only_publishes_confirmed_codex_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, engine: str
) -> None:
    """所有者不在の旧記録は、対応backendが元turnの終了を確認した場合だけ終了へ遷移する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    identifier = "a0278936-c56f-4a22-9f34-2a5683019a57"
    session_registry.publish(
        identifier,
        state=session_registry.PublicationState.PENDING_RUNNING,
        engine=engine,
        cwd=str(tmp_path),
        turn_seq=3,
        turn_id="turn-1",
    )
    async with manager.AgentsServerManager(None) as owner:
        backend = owner._backend(engine)  # noqa: SLF001  # pylint: disable=protected-access
        read_turns = AsyncMock(return_value=[("turn-1", "completed")])
        monkeypatch.setattr(backend, "read_thread_turns", read_turns, raising=False)
        await owner.take_over_orphaned_session(identifier)
        resolution = session_registry.resolve(identifier)
        if engine == "codex":
            assert resolution.state is session_registry.Resolution.TERMINAL
            assert (await owner.show_session(identifier))["status"] == "completed"
            read_turns.assert_awaited_once_with(identifier)
        else:
            assert resolution.state is session_registry.Resolution.RUNNING
            read_turns.assert_not_awaited()
