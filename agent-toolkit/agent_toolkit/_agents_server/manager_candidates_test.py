"""`_agents_server/manager.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import logging
import pathlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import responses, shared_layout, shared_roots, state, status_file
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._testing.agents_server_support import (
    DelayedUnavailableBackend,
    FakeBackend,
    UnavailableStartBackend,
    _install_backend,
    _manager_with_fake,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed", [False, True])
async def test_start_advances_candidate_when_engine_reports_unavailable(
    delayed: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """利用上限で終了した候補を除外し、別engineの次候補で起動する。

    起動応答の前に終了した場合と、応答の後に終了した場合の双方を対象とする。
    """
    candidates = [("codex", "first", "high"), ("claude", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with _manager_with_fake("claude") as (manager, claude):
        codex: FakeBackend = (
            DelayedUnavailableBackend(manager.sessions, "codex", manager._condition)
            if delayed
            else UnavailableStartBackend(manager.sessions, "codex")
        )
        _install_backend(manager, "codex", codex)

        response = await manager.start("plan", "調査", str(tmp_path))

        assert codex.start_calls == [("first", "high", "delegate")]
        assert claude.start_calls == [("second", "medium", "delegate")]
        assert response["engine"] == "claude"
        assert response["model"] == "second"
        assert response["status"] == "running"
        assert response["excluded_candidates"] == [
            {
                "engine": "codex",
                "model": "first",
                "effort": "high",
                "reason": "usageLimitExceeded",
                "session_id": "codex-session",
            }
        ]
        assert manager.sessions[response["session_id"]].excluded_candidates == frozenset({candidates[0]})


@pytest.mark.asyncio
async def test_agy_init_failure_advances_direct_candidate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """agyがinit前に標準エラー付きで終了しても、直接指定した次候補へ進む。"""
    async with _manager_with_fake("claude") as (manager, claude):
        agy = FakeBackend(manager.sessions, "agy")
        start_mock = AsyncMock(
            side_effect=RuntimeError("Antigravity CLI ended before the init event: stderr=model unavailable")
        )
        monkeypatch.setattr(agy, "start", start_mock)
        _install_backend(manager, "agy", agy)

        response = await manager.start("agy:gemini-3.8-flash/medium,claude:opus[1m]/medium", "調査", str(tmp_path))

        assert start_mock.await_count == 1
        assert claude.start_calls == [("opus[1m]", "medium", "delegate")]
        assert response["engine"] == "claude"
        assert response["excluded_candidates"] == [
            {
                "engine": "agy",
                "model": "gemini-3.8-flash",
                "effort": "medium",
                "reason": "Antigravity CLI ended before the init event: stderr=model unavailable",
            }
        ]
        assert set(manager.sessions) == {"claude-session"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        ({"message": "agy result failed"}, "agy result failed"),
        ({"message": "agy process failed", "stderr": "rate limit"}, "rate limit"),
    ],
)
async def test_agy_failed_turn_advances_config_candidate(
    error: dict[str, str],
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """agyのresult失敗とstderr付き失敗を、設定候補でも除外する。"""
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda _model_type, **_kwargs: [("agy", "gemini-3.8-flash", "medium"), ("claude", "opus[1m]", "medium")],
    )
    async with _manager_with_fake("claude") as (manager, claude):
        agy = UnavailableStartBackend(manager.sessions, "agy", error)
        _install_backend(manager, "agy", agy)

        response = await manager.start("execute", "調査", str(tmp_path))

        assert agy.release_calls == ["agy-session"]
        assert claude.start_calls == [("opus[1m]", "medium", "delegate")]
        assert response["excluded_candidates"] == [
            {"engine": "agy", "model": "gemini-3.8-flash", "effort": "medium", "reason": reason, "session_id": "agy-session"}
        ]
        assert set(manager.sessions) == {"claude-session"}


@pytest.mark.asyncio
async def test_agy_init_failure_reports_reason_when_candidates_exhausted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """最後のagy候補がinit前に失敗した場合も、根拠を委譲元へ返す。"""
    async with server_manager.AgentsServerManager() as manager:
        agy = FakeBackend(manager.sessions, "agy")
        monkeypatch.setattr(agy, "start", AsyncMock(side_effect=RuntimeError("stderr=model unavailable")))
        _install_backend(manager, "agy", agy)

        with pytest.raises(RuntimeError, match="stderr=model unavailable"):
            await manager.start("agy:gemini-3.8-flash/medium", "調査", str(tmp_path))


@pytest.mark.asyncio
async def test_agy_exhaustion_does_not_return_abandoned_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """失敗による終了の後にinit前の例外が続いた場合は、放棄済みのsessionを返さない。"""
    async with server_manager.AgentsServerManager() as manager:
        agy = UnavailableStartBackend(manager.sessions, "agy", {"message": "first failed"})
        original_start = agy.start

        async def start_or_raise(*args: Any, **kwargs: Any) -> state.SessionState:
            if args[2] == "second":
                raise RuntimeError("stderr=second failed")
            return await original_start(*args, **kwargs)

        monkeypatch.setattr(agy, "start", start_or_raise)
        _install_backend(manager, "agy", agy)

        with pytest.raises(RuntimeError, match="stderr=second failed") as exc_info:
            await manager.start("agy:first/medium,agy:second/medium", "調査", str(tmp_path))

        assert "first failed" in str(exc_info.value)
        assert agy.release_calls == ["agy-session"]
        assert not manager.sessions


@pytest.mark.asyncio
async def test_abandoned_candidate_is_released(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """候補切替の前に、放棄したsessionの保持情報・結果・backend資源を除く。"""
    candidates = [("codex", "first", "high"), ("claude", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        codex = UnavailableStartBackend(manager.sessions, "codex")
        claude = FakeBackend(manager.sessions, "claude")
        _install_backend(manager, "codex", codex)
        _install_backend(manager, "claude", claude)
        writer.activate()
        original_start = codex.start

        async def start_and_publish(*args: Any, **kwargs: Any) -> state.SessionState:
            session = await original_start(*args, **kwargs)
            writer.flush()
            return session

        monkeypatch.setattr(codex, "start", start_and_publish)
        abandoned_result = shared_layout.results_directory("root-session", tmp_path) / "codex-session.json"

        response = await manager.start("plan", "調査", str(tmp_path))

        assert response["session_id"] == "claude-session"
        assert response["excluded_candidates"][0]["session_id"] == "codex-session"
        assert set(manager.sessions) == {"claude-session"}
        listed = (await manager.list_sessions(include_terminated=True))["sessions"]
        assert [item["session_id"] for item in listed] == ["claude-session"]
        assert not abandoned_result.exists()
        assert codex.release_calls == ["codex-session"]
        await manager.close()


@pytest.mark.asyncio
async def test_start_returns_failed_session_when_every_candidate_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """全候補が利用上限で終了した場合だけ、最後の候補の失敗を返す。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with server_manager.AgentsServerManager() as manager:
        codex = UnavailableStartBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", codex)

        response = await manager.start("plan", "調査", str(tmp_path))

        assert codex.start_calls == [("first", "high", "delegate"), ("second", "high", "delegate")]
        assert response["status"] == "failed"
        assert response["model"] == "second"
        assert (await manager.wait())["error"] == codex.error


@pytest.mark.parametrize("api_error_status", [401, 403])
@pytest.mark.asyncio
async def test_authentication_failure_switches_to_next_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    api_error_status: int,
) -> None:
    """認証と認可の失敗は別候補で起動し、切替の内容を応答へ返す。"""
    candidates = [("claude", "first", "high"), ("codex", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with server_manager.AgentsServerManager() as manager:
        _install_backend(
            manager,
            "claude",
            UnavailableStartBackend(
                manager.sessions,
                "claude",
                {"message": "Failed to authenticate.", "apiErrorStatus": api_error_status},
            ),
        )
        _install_backend(manager, "codex", FakeBackend(manager.sessions, "codex"))

        response = await manager.start("plan", "調査", str(tmp_path))
        public = responses.public_start_response(response)

        assert response["engine"] == "codex"
        assert response["model"] == "second"
        assert public["excluded_candidates"] == [
            {
                "engine": "claude",
                "model": "first",
                "effort": "high",
                "reason": str(api_error_status),
                "session_id": "claude-session",
            }
        ]
        assert public["engine"] == "codex"
        assert public["model"] == "second"
        assert public["effort"] == "medium"


@pytest.mark.asyncio
async def test_engine_switch_is_written_to_the_diagnostic_log(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """候補の切替は、除外した候補と採用した候補を持つ行として診断ログへ残る。"""
    candidates = [("claude", "first", "high"), ("codex", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with server_manager.AgentsServerManager() as manager:
        _install_backend(
            manager,
            "claude",
            UnavailableStartBackend(manager.sessions, "claude", {"message": "auth", "apiErrorStatus": 401}),
        )
        _install_backend(manager, "codex", FakeBackend(manager.sessions, "codex"))

        with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.mcp"):
            await manager.start("plan", "調査", str(tmp_path))

        switch_records = [record.getMessage() for record in caplog.records if record.getMessage().startswith("engine_switch ")]
        assert len(switch_records) == 1
        assert '"engine": "claude"' in switch_records[0]
        assert '"reason": "401"' in switch_records[0]
        assert '"engine": "codex"' in switch_records[0]


@pytest.mark.asyncio
async def test_exclusion_record_is_read_by_a_newly_created_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """除外した候補は状態ディレクトリの記録から解決し、別のmanagerの起動でも先頭候補を試さない。"""
    candidates = [("claude", "first", "high"), ("codex", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with server_manager.AgentsServerManager() as failing:
        _install_backend(
            failing,
            "claude",
            UnavailableStartBackend(failing.sessions, "claude", {"message": "auth", "apiErrorStatus": 401}),
        )
        _install_backend(failing, "codex", FakeBackend(failing.sessions, "codex"))
        await failing.start("plan", "調査", str(tmp_path))

        async with server_manager.AgentsServerManager() as restarted:
            claude = UnavailableStartBackend(restarted.sessions, "claude", {"message": "auth", "apiErrorStatus": 401})
            _install_backend(restarted, "claude", claude)
            codex = FakeBackend(restarted.sessions, "codex")
            _install_backend(restarted, "codex", codex)

            response = await restarted.start("plan", "再起動後", str(tmp_path))

            assert not claude.start_calls
            assert response["engine"] == "codex"
            assert codex.start_calls == [("second", "medium", "delegate")]


@pytest.mark.asyncio
async def test_internal_server_error_keeps_the_first_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """サービス内部の失敗では候補を進めず、切替の項目も返さない。"""
    candidates = [("claude", "first", "high"), ("codex", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with server_manager.AgentsServerManager() as manager:
        claude = UnavailableStartBackend(
            manager.sessions,
            "claude",
            {"message": "internal", "apiErrorStatus": 500},
        )
        _install_backend(manager, "claude", claude)
        codex = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", codex)

        response = await manager.start("plan", "調査", str(tmp_path))

        assert response["status"] == "failed"
        assert response["engine"] == "claude"
        assert not codex.start_calls
        assert "excluded_candidates" not in responses.public_start_response(response)
