"""engine別のbackendの型（`_agents_server/backends.py`）を検証する。"""

import asyncio
import pathlib

import pytest

from agent_toolkit._agents_server import backends, engine_availability, state


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
        backend_type = backends.backend_class(engine)
        assert backend_type is not None
        assert isinstance(backend, backend_type)
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
    assert not backends.excludes_with_recorded_reason("claude", engine_availability.LEGACY_CLAUDE_RATE_LIMIT_REASON)
    assert backends.excludes_with_recorded_reason("codex", engine_availability.LEGACY_CLAUDE_RATE_LIMIT_REASON)
    assert backends.interrupt_requires_turn_id("codex")
    assert not backends.interrupt_requires_turn_id("claude")
    assert [engine for engine in sorted(created) if created[engine].START_FAILURE_EXCLUDES_CANDIDATE] == ["agy"]
    assert [engine for engine in sorted(created) if created[engine].ORPHAN_TAKEOVER] == ["codex"]


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
