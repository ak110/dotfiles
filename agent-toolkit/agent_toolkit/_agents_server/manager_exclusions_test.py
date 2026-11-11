"""`_agents_server/manager.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import datetime
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import unavailable_candidates
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._testing.agents_server_support import (
    DelayedUnavailableBackend,
    FakeBackend,
    UnavailableStartBackend,
    _carry_over_late_unavailability,
    _codex_model_rejected_error,
    _install_backend,
    _manager_with_fake,
    _start_late_unavailability,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_late_engine_unavailability_carries_over_to_next_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """開始確認の上限後に失敗した候補を、同じ起動条件の次回から除外する。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "medium")]
    async with _carry_over_late_unavailability(monkeypatch, tmp_path, candidates) as (manager, available):
        response = await manager.start("plan", "再試行", str(tmp_path))

        assert response["model"] == "second"
        assert response["effort"] == "medium"
        assert available.start_calls == [("second", "medium", "delegate")]


@pytest.mark.parametrize(
    ("providers", "expected_model", "expected_effort"), [("", "second", "medium"), ("subscription,payg", "first", "high")]
)
@pytest.mark.asyncio
async def test_carried_over_exclusion_survives_a_successful_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    providers: str,
    expected_model: str,
    expected_effort: str,
) -> None:
    """API設定ありの旧利用上限は再確認し、未設定の候補除外は成功後も保持する。"""
    if providers:
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_WORKER_CONNECTIONS", providers)
    candidates = [("codex", "first", "high"), ("codex", "second", "medium")]
    async with _carry_over_late_unavailability(monkeypatch, tmp_path, candidates) as (manager, available):
        recovered = await manager.start("plan", "再試行", str(tmp_path))
        following = await manager.start("plan", "通常起動", str(tmp_path))

        for response in (recovered, following):
            assert response["model"] == expected_model
            if providers:
                assert "excluded_candidates" not in response
            else:
                assert response["excluded_candidates"] == [
                    {"engine": "codex", "model": "first", "effort": "high", "reason": "usageLimitExceeded"}
                ]
        assert available.start_calls == [(expected_model, expected_effort, "delegate")] * 2


@pytest.mark.asyncio
async def test_carried_over_candidate_is_dropped_when_no_candidate_remains(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """前回から持ち越した除外だけで候補が尽きる場合は、除外を破棄して全候補を使う。"""
    candidates = [("codex", "only", "high")]
    async with _carry_over_late_unavailability(monkeypatch, tmp_path, candidates) as (manager, available):
        response = await manager.start("plan", "再試行", str(tmp_path))

        assert response["model"] == "only"
        assert available.start_calls == [("only", "high", "delegate")]
        assert "excluded_candidates" not in response
        assert not unavailable_candidates.load_unavailable_candidates(
            "plan", "delegate", now=datetime.datetime.now(datetime.UTC)
        )


@pytest.mark.asyncio
async def test_shell_launch_carries_over_candidate_within_shell_launch_kind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """shell起動で記録した除外の持ち越しは、同じ起動区分だけへ適用する。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    async with server_manager.AgentsServerManager() as manager:
        failure_release = asyncio.Event()
        delayed = DelayedUnavailableBackend(manager.sessions, "codex", manager._condition, failure_release=failure_release)
        _install_backend(manager, "codex", delayed)

        await manager.start_shell("make test", str(tmp_path), "終了状態だけ")
        failure_release.set()
        await asyncio.gather(*delayed.pending)
        await manager.wait()
        available = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", available)

        explored = await manager.start_explore("探索", str(tmp_path))
        shell = await manager.start_shell("make test", str(tmp_path), "終了状態だけ")

        assert explored["model"] == "first"
        assert shell["model"] == "second"
        assert available.start_calls == [("first", "high", "explore"), ("second", "medium", "shell")]


@pytest.mark.parametrize(
    ("providers", "expected_model", "expected_effort"), [("", "second", "medium"), ("subscription,payg", "first", "high")]
)
@pytest.mark.asyncio
async def test_stop_without_wait_carries_over_unavailable_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    providers: str,
    expected_model: str,
    expected_effort: str,
) -> None:
    """stop後の旧利用上限はAPI設定ありなら再確認し、未設定なら候補除外を保持する。"""
    if providers:
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_WORKER_CONNECTIONS", providers)
    candidates = [("codex", "first", "high"), ("codex", "second", "medium")]
    async with _start_late_unavailability(monkeypatch, tmp_path, candidates) as (manager, failed):
        assert await manager.stop(failed["session_id"]) == {}
        available = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", available)

        response = await manager.start("plan", "再試行", str(tmp_path))

        assert response["model"] == expected_model
        assert available.start_calls == [(expected_model, expected_effort, "delegate")]
        if providers:
            assert "excluded_candidates" not in response
        else:
            assert response["excluded_candidates"][0]["reason"] == "usageLimitExceeded"


@pytest.mark.asyncio
async def test_expired_kill_keeps_carried_over_unavailable_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """期限切れsessionへのkillだけを経た可用性失敗も、次回起動から除外する。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "medium")]
    async with _start_late_unavailability(monkeypatch, tmp_path, candidates) as (manager, failed):
        manager.sessions[failed["session_id"]].retention_deadline = asyncio.get_running_loop().time() - 1
        assert (await manager.kill(failed["session_id"], timeout=0))["status"] == "expired"
        available = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", available)

        response = await manager.start("plan", "再試行", str(tmp_path))

        assert response["model"] == "second"
        assert available.start_calls == [("second", "medium", "delegate")]


@pytest.mark.asyncio
async def test_start_keeps_failure_that_does_not_depend_on_the_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """engineの可用性以外で終了した候補では次候補へ進まない。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with server_manager.AgentsServerManager() as manager:
        codex = UnavailableStartBackend(
            manager.sessions,
            "codex",
            error={"message": "invalid request", "codexErrorInfo": "badRequest"},
        )
        _install_backend(manager, "codex", codex)

        response = await manager.start("plan", "調査", str(tmp_path))

        assert codex.start_calls == [("first", "high", "delegate")]
        assert response["status"] == "failed"
        assert response["model"] == "first"


@pytest.mark.asyncio
async def test_start_advances_candidate_when_codex_rejects_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """接続先がモデルIDを拒否して起動時の待機内に終了したCodex候補を除外し、次候補で起動する。"""
    candidates = [("codex", "gpt-6.1-sol", "medium"), ("claude", "opus[1m]", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with _manager_with_fake("claude") as (manager, claude):
        codex = UnavailableStartBackend(manager.sessions, "codex", error=_codex_model_rejected_error())
        _install_backend(manager, "codex", codex)

        response = await manager.start("execute", "調査", str(tmp_path))

        assert claude.start_calls == [("opus[1m]", "medium", "delegate")]
        assert response["engine"] == "claude"
        assert response["status"] == "running"
        assert response["excluded_candidates"] == [
            {
                "engine": "codex",
                "model": "gpt-6.1-sol",
                "effort": "medium",
                "reason": "modelRejected",
                "session_id": "codex-session",
            }
        ]


@pytest.mark.asyncio
async def test_late_codex_model_rejection_is_carried_over_to_next_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """起動時の待機の後にモデルIDの拒否で終了した候補を除外記録へ残し、呼び直した`start`で次候補へ進む。"""
    candidates = [("codex", "gpt-6.1-sol", "medium"), ("codex", "gpt-6-sol", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    async with server_manager.AgentsServerManager() as manager:
        failure_release = asyncio.Event()
        delayed = DelayedUnavailableBackend(
            manager.sessions, "codex", manager._condition, error=_codex_model_rejected_error(), failure_release=failure_release
        )
        _install_backend(manager, "codex", delayed)

        failed = await manager.start("execute", "調査", str(tmp_path))
        failure_release.set()
        await asyncio.gather(*delayed.pending)
        assert failed["status"] == "running"
        assert await manager.stop(failed["session_id"]) == {}
        assert unavailable_candidates.load_unavailable_candidates(
            "execute", "delegate", now=datetime.datetime.now(datetime.UTC)
        ) == {("codex", "gpt-6.1-sol", "medium"): "modelRejected"}
        available = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", available)

        response = await manager.start("execute", "再試行", str(tmp_path))

        assert response["model"] == "gpt-6-sol"
        assert available.start_calls == [("gpt-6-sol", "medium", "delegate")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        _codex_model_rejected_error(param="reasoning_effort"),
        {"message": "model gpt-6.1-sol is not available", "codexErrorInfo": "other"},
        {"message": json.dumps(["param", "model"]), "codexErrorInfo": "other"},
    ],
)
async def test_codex_failure_other_than_model_rejection_keeps_candidate(
    error: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """`error.param`が`model`以外の失敗と、`message`がJSONの`error`オブジェクトを持たない失敗では候補を進めない。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with server_manager.AgentsServerManager() as manager:
        codex = UnavailableStartBackend(manager.sessions, "codex", error=error)
        _install_backend(manager, "codex", codex)

        response = await manager.start("plan", "調査", str(tmp_path))

        assert codex.start_calls == [("first", "high", "delegate")]
        assert response["status"] == "failed"
        assert response["model"] == "first"


@pytest.mark.asyncio
@pytest.mark.parametrize("api_error_status", [429, 529])
async def test_start_advances_candidate_when_claude_reports_unavailable_status(
    api_error_status: int,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Claudeが可用性由来のHTTPステータスで終了した候補を除外し、次候補で起動する。"""
    candidates = [("claude", "first", "high"), ("codex", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with _manager_with_fake("codex") as (manager, codex):
        claude = UnavailableStartBackend(
            manager.sessions,
            "claude",
            error={"message": "api error", "apiErrorStatus": api_error_status},
        )
        _install_backend(manager, "claude", claude)

        response = await manager.start("plan", "調査", str(tmp_path))

        assert claude.start_calls == [("first", "high", "delegate")]
        assert codex.start_calls == [("second", "medium", "delegate")]
        assert response["engine"] == "codex"
        assert response["model"] == "second"
        assert response["status"] == "running"
        assert manager.sessions[response["session_id"]].excluded_candidates == frozenset({candidates[0]})


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [{"message": "bad request", "apiErrorStatus": 400}, {"message": "bad request"}])
async def test_start_keeps_claude_failure_that_does_not_depend_on_the_candidate(
    error: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Claudeが可用性由来でないHTTPステータスで終了した場合と、状態を持たない場合は次候補へ進まない。"""
    candidates = [("claude", "first", "high"), ("claude", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with server_manager.AgentsServerManager() as manager:
        claude = UnavailableStartBackend(manager.sessions, "claude", error=error)
        _install_backend(manager, "claude", claude)

        response = await manager.start("plan", "調査", str(tmp_path))

        assert claude.start_calls == [("first", "high", "delegate")]
        assert response["status"] == "failed"
        assert response["model"] == "first"
