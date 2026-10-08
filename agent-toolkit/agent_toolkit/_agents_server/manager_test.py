"""`_agents_server/manager.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import dataclasses
import datetime
import json
import logging
import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import (
    agents_wait,
    responses,
    result_projection,
    session_errors,
    session_registry,
    shared_layout,
    shared_roots,
    state,
    status_file,
    unavailable_candidates,
)
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_support import (
    _LAUNCHER_ENVIRONMENT_NAMES,
    BlockingContinuationClaudeClient,
    BlockingInterruptBackend,
    DelayedUnavailableBackend,
    FailedResumeBackend,
    FakeBackend,
    OutputObservedBackend,
    UnavailableStartBackend,
    _actionable_message,
    _assert_no_forbidden_keys,
    _carry_over_late_unavailability,
    _codex_model_rejected_error,
    _complete,
    _default_identity_fields,
    _install_backend,
    _manager_with_fake,
    _RegistryBackend,
    _without_root,
    _write_notice,
    install_backend,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude", "agy"])
async def test_retention_expiry_releases_backend_once_without_tool_call(
    engine: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """終端の保持期限到達は照会なしで解放を呼び、後からstopしても重ねて解放しない。"""
    manager, backend = _manager_with_fake(engine)
    released = asyncio.Event()
    original_release = backend.release_session

    async def release(session: state.SessionState) -> None:
        await original_release(session)
        released.set()

    monkeypatch.setattr(backend, "release_session", release)
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.02)
    try:
        session = await backend.start("調査", str(tmp_path), None, None)
        _complete(session)
        await asyncio.wait_for(released.wait(), 1)
        assert backend.release_calls == [session.session_id]
        assert session.session_id not in manager.sessions
        assert session.session_id in manager.expired_sessions
        # release本体が送った通知の後に、taskの完了callbackを実行する。
        await asyncio.sleep(0)
        assert not manager._resource_release_tasks
        await manager.stop(session.session_id)
        assert backend.release_calls == [session.session_id]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_reply_cancels_old_retention_deadline(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """保持期限より前に再開したturnへ、旧turnの解放を遅れて適用しない。"""
    manager, backend = _manager_with_fake("codex")
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.01)
    try:
        session = await backend.start("調査", str(tmp_path), None, None)
        _complete(session)
        await manager.send_message(session.session_id, "続行")
        await asyncio.sleep(0.03)
        assert not backend.release_calls
        assert manager.sessions[session.session_id] is session
        assert session.status == "running"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_start_resolves_codex_family_from_existing_backend_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """委譲起動へ系列名を渡さず、既存のbackendが持つモデル一覧から解決した完全IDを使う。"""
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda _model_type: [("codex", "sol", "medium")],
    )
    manager, backend = _manager_with_fake("codex")

    result = await manager.start("high_tier", "調査", str(tmp_path))

    assert result["status"] == "running"
    assert backend.start_calls == [("gpt-6-sol", "medium", "delegate")]


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
    manager = server_manager.AgentsServerManager(writer)
    _install_backend(manager, engine, FakeBackend(manager.sessions, engine))
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: [(engine, "model", "high")])
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
    manager = server_manager.AgentsServerManager()
    _install_backend(manager, "codex", _RegistryBackend(manager.sessions, "codex"))
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: [("codex", "model", "high")])

    response = await manager.start("plan", "調査", str(tmp_path))
    session_id = str(response["session_id"])
    registry = session_registry.registry_directory(tmp_path) / f"{session_id}.json"

    assert json.loads(registry.read_text(encoding="utf-8"))["launcher_session_id"] == expected
    _complete(manager.sessions[session_id])
    assert json.loads(registry.read_text(encoding="utf-8"))["status"] == "completed"
    assert json.loads(registry.read_text(encoding="utf-8"))["launcher_session_id"] == expected
    session_registry.release(session_id, reason="stopped")
    assert json.loads(registry.read_text(encoding="utf-8"))["launcher_session_id"] == expected
    assert session_registry.resolve(session_id).state is session_registry.Resolution.RELEASED


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_start_rejects_unknown_model_type_before_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """未知の工程別モデル設定をbackend開始前に拒否する。"""
    manager, backend = _manager_with_fake("codex")
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda model_type: (
            [("codex", model_type, "medium")]
            if model_type in {"plan", "high_tier"}
            else (_ for _ in ()).throw(ValueError("unknown model_type: unknown (available: plan)"))
        ),
    )
    with pytest.raises(ValueError, match="available: plan"):
        await manager.start("unknown", "調査", str(tmp_path))
    assert not backend.start_calls


@pytest.mark.asyncio
async def test_start_is_listed_as_starting_until_backend_initialization_finishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """session_id確定後はstartの応答前でもlistとshowがstartingを返す。"""
    manager, backend = _manager_with_fake("codex")
    original_start = backend.start
    registered = asyncio.Event()
    finish_initialization = asyncio.Event()

    async def blocked_start(*args: Any, **kwargs: Any) -> state.SessionState:
        session = await original_start(*args, **kwargs)
        session.status = "starting"
        session.touch()
        registered.set()
        await finish_initialization.wait()
        return session

    monkeypatch.setattr(backend, "start", blocked_start)
    start_task = asyncio.create_task(manager.start("high_tier", "調査", str(tmp_path)))
    await registered.wait()

    listed = manager.list_sessions()["sessions"]
    assert [(item["session_id"], item["status"]) for item in listed] == [("codex-session", "starting")]
    assert manager.show_session("codex-session")["status"] == "starting"

    finish_initialization.set()
    response = await start_task
    assert response["status"] == "running"
    assert manager.show_session("codex-session")["status"] == "running"


@pytest.mark.asyncio
async def test_start_does_not_advance_candidate_when_backend_start_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """backend開始の例外では、資源の二重作成を避けて候補を進めない。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager, backend = _manager_with_fake("codex")
    original_start = backend.start
    calls: list[tuple[str | None, str | None]] = []

    async def fail_first_start(
        prompt: str,
        cwd: str,
        model: str | None,
        effort: str | None,
        **kwargs: Any,
    ) -> state.SessionState:
        calls.append((model, effort))
        if model == "first":
            raise RuntimeError("backend unavailable")
        return await original_start(prompt, cwd, model, effort, **kwargs)

    monkeypatch.setattr(backend, "start", fail_first_start)
    with pytest.raises(RuntimeError, match="backend unavailable"):
        await manager.start("plan", "調査", str(tmp_path))

    assert calls == [("first", "high")]


@pytest.mark.asyncio
async def test_start_retries_same_candidate_when_initialization_times_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """初期化の上限超過では次候補へ進めず、同じ候補の再試行で起動を返す。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager, backend = _manager_with_fake("codex")
    original_start = backend.start
    calls: list[str | None] = []

    async def timeout_first_start(
        prompt: str,
        cwd: str,
        model: str | None,
        effort: str | None,
        **kwargs: Any,
    ) -> state.SessionState:
        calls.append(model)
        if len(calls) == 1:
            raise session_errors.SessionInitializationTimeoutError("initialization timed out")
        return await original_start(prompt, cwd, model, effort, **kwargs)

    monkeypatch.setattr(backend, "start", timeout_first_start)

    response = await manager.start("plan", "調査", str(tmp_path))

    assert calls == ["first", "first"]
    assert response["model"] == "first"
    assert response["session_id"] in manager.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidates",
    [None, [("unknown-engine", "model", "high")], []],
    ids=["unknown-model-type", "unsupported-engine", "no-candidates"],
)
async def test_start_rejects_unusable_model_type_with_accepted_format(
    candidates: list[tuple[str, str, str]] | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """解釈できない`model_type`は、受理する書式と`atk config get`での確認を次の操作で示す。"""
    if candidates is not None:
        monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager, backend = _manager_with_fake("codex")

    with pytest.raises(ValueError) as raised:
        await manager.start("not-a-model-type", "調査", str(tmp_path))

    next_action = _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
    assert "<claude|codex|agy>:<model>[/<effort>]" in next_action
    assert "atk config get" in next_action
    assert not backend.start_calls


@pytest.mark.asyncio
async def test_start_reports_retry_or_other_model_type_when_every_candidate_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """全候補が起動例外で除外された場合は、再試行か別の`model_type`の指定を次の操作で示す。"""
    candidates = [("agy", "first", "high"), ("agy", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager, backend = _manager_with_fake("agy")

    async def failing_start(*_args: Any, **_kwargs: Any) -> state.SessionState:
        raise RuntimeError("agy CLI failed")

    monkeypatch.setattr(backend, "start", failing_start)

    with pytest.raises(RuntimeError, match="no available model candidates") as raised:
        await manager.start("plan", "調査", str(tmp_path))

    next_action = _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
    assert "時間をおいて再試行" in next_action
    assert "`model_type`" in next_action


@pytest.mark.asyncio
async def test_start_raises_when_every_initialization_attempt_times_out(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """全試行が初期化の上限へ達した起動を、対象を示す例外で確定する。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    monkeypatch.setattr(state, "SESSION_INITIALIZATION_ATTEMPTS", 3)
    manager, backend = _manager_with_fake("codex")
    calls: list[str | None] = []

    async def always_timeout(
        _prompt: str,
        _cwd: str,
        model: str | None,
        _effort: str | None,
        **_kwargs: Any,
    ) -> state.SessionState:
        calls.append(model)
        raise session_errors.SessionInitializationTimeoutError(f"diagnostic-{len(calls)}")

    monkeypatch.setattr(backend, "start", always_timeout)

    with pytest.raises(session_errors.SessionInitializationTimeoutError, match="timed out on every attempt") as exc_info:
        await manager.start("plan", "調査", str(tmp_path))

    assert calls == ["first", "first", "first"]
    assert "attempt=1: diagnostic-1" in str(exc_info.value)
    assert "attempt=2: diagnostic-2" in str(exc_info.value)
    assert "attempt=3: diagnostic-3" in str(exc_info.value)
    assert not manager.sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed", [False, True])
async def test_start_advances_candidate_when_engine_reports_unavailable(
    delayed: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """利用上限で終端した候補を除外し、別engineの次候補で起動する。

    起動応答の前に終端した場合と、応答の後に終端した場合の双方を対象とする。
    """
    candidates = [("codex", "first", "high"), ("claude", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager, claude = _manager_with_fake("claude")
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
        {"engine": "codex", "model": "first", "effort": "high", "reason": "usageLimitExceeded", "session_id": "codex-session"}
    ]
    assert manager.sessions[response["session_id"]].excluded_candidates == frozenset({candidates[0]})


@pytest.mark.asyncio
async def test_agy_init_failure_advances_direct_candidate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """agyがinit前に標準エラー付きで終了しても、直接指定した次候補へ進む。"""
    manager, claude = _manager_with_fake("claude")
    agy = FakeBackend(manager.sessions, "agy")
    start_mock = AsyncMock(side_effect=RuntimeError("Antigravity CLI ended before the init event: stderr=model unavailable"))
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
        lambda _model_type: [("agy", "gemini-3.8-flash", "medium"), ("claude", "opus[1m]", "medium")],
    )
    manager, claude = _manager_with_fake("claude")
    agy = UnavailableStartBackend(manager.sessions, "agy", error)
    _install_backend(manager, "agy", agy)

    response = await manager.start("high_tier", "調査", str(tmp_path))

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
    manager = server_manager.AgentsServerManager()
    agy = FakeBackend(manager.sessions, "agy")
    monkeypatch.setattr(agy, "start", AsyncMock(side_effect=RuntimeError("stderr=model unavailable")))
    _install_backend(manager, "agy", agy)

    with pytest.raises(RuntimeError, match="stderr=model unavailable"):
        await manager.start("agy:gemini-3.8-flash/medium", "調査", str(tmp_path))


@pytest.mark.asyncio
async def test_agy_exhaustion_does_not_return_abandoned_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """失敗終端の後にinit前例外が続いた場合は、放棄済みsessionを返さない。"""
    manager = server_manager.AgentsServerManager()
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
    """候補切替の前に放棄sessionの保持・結果・backend資源を除く。"""
    candidates = [("codex", "first", "high"), ("claude", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = server_manager.AgentsServerManager(writer)
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
    listed = manager.list_sessions(include_terminated=True)["sessions"]
    assert [item["session_id"] for item in listed] == ["claude-session"]
    assert not abandoned_result.exists()
    assert codex.release_calls == ["codex-session"]
    await manager.close()


@pytest.mark.asyncio
async def test_start_returns_failed_session_when_every_candidate_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """全候補が利用上限で終端した場合だけ、最後の候補の失敗を返す。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager = server_manager.AgentsServerManager()
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
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager = server_manager.AgentsServerManager()
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
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager = server_manager.AgentsServerManager()
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
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    failing = server_manager.AgentsServerManager()
    _install_backend(
        failing,
        "claude",
        UnavailableStartBackend(failing.sessions, "claude", {"message": "auth", "apiErrorStatus": 401}),
    )
    _install_backend(failing, "codex", FakeBackend(failing.sessions, "codex"))
    await failing.start("plan", "調査", str(tmp_path))

    restarted = server_manager.AgentsServerManager()
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
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager = server_manager.AgentsServerManager()
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


@pytest.mark.asyncio
async def test_start_returns_running_once_model_output_is_observed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """終端しないsessionでも、最初のモデル出力を観測した時点で上限を待たずに実行中として返す。"""
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: [("codex", "only", "high")])
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 5.0)
    manager = server_manager.AgentsServerManager()
    backend = OutputObservedBackend(manager.sessions, "codex", manager._condition)
    _install_backend(manager, "codex", backend)
    loop = asyncio.get_running_loop()

    started = loop.time()
    response = await manager.start("plan", "調査", str(tmp_path))
    elapsed = loop.time() - started
    await asyncio.gather(*backend.pending)

    assert response["status"] == "running"
    assert elapsed < 1.0
    assert backend.start_calls == [("only", "high", "delegate")]


@pytest.mark.asyncio
async def test_late_engine_unavailability_carries_over_to_next_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """開始確認の上限後に失敗した候補を、同じ起動条件の次回から除外する。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "medium")]
    manager, available = await _carry_over_late_unavailability(monkeypatch, tmp_path, candidates)

    response = await manager.start("plan", "再試行", str(tmp_path))

    assert response["model"] == "second"
    assert response["effort"] == "medium"
    assert available.start_calls == [("second", "medium", "delegate")]


@pytest.mark.asyncio
async def test_carried_over_exclusion_survives_a_successful_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """後続の候補で成立しても、除外した候補は保持期間内は先頭から試さない。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "medium")]
    manager, _ = await _carry_over_late_unavailability(monkeypatch, tmp_path, candidates)

    recovered = await manager.start("plan", "再試行", str(tmp_path))
    following = await manager.start("plan", "通常起動", str(tmp_path))

    assert recovered["model"] == "second"
    assert following["model"] == "second"
    assert recovered["excluded_candidates"] == [
        {"engine": "codex", "model": "first", "effort": "high", "reason": "usageLimitExceeded"}
    ]


@pytest.mark.asyncio
async def test_carried_over_candidate_is_dropped_when_no_candidate_remains(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """持ち越し除外だけで候補が尽きる場合は除外を破棄して全候補を使う。"""
    candidates = [("codex", "only", "high")]
    manager, available = await _carry_over_late_unavailability(monkeypatch, tmp_path, candidates)

    response = await manager.start("plan", "再試行", str(tmp_path))

    assert response["model"] == "only"
    assert available.start_calls == [("only", "high", "delegate")]
    assert "excluded_candidates" not in response
    assert not unavailable_candidates.load_unavailable_candidates("plan", "delegate", now=datetime.datetime.now(datetime.UTC))


@pytest.mark.asyncio
async def test_shell_launch_carries_over_candidate_within_shell_launch_kind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """shell起動の持ち越し除外を同じ起動区分だけへ適用する。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    manager = server_manager.AgentsServerManager()
    delayed = DelayedUnavailableBackend(manager.sessions, "codex", manager._condition, delay=0.01)
    _install_backend(manager, "codex", delayed)

    await manager.start_shell("make test", str(tmp_path), "終了状態だけ")
    await asyncio.gather(*delayed.pending)
    await manager.wait()
    available = FakeBackend(manager.sessions, "codex")
    _install_backend(manager, "codex", available)

    explored = await manager.start_explore("探索", str(tmp_path))
    shell = await manager.start_shell("make test", str(tmp_path), "終了状態だけ")

    assert explored["model"] == "first"
    assert shell["model"] == "second"
    assert available.start_calls == [("first", "high", "explore"), ("second", "medium", "shell")]


@pytest.mark.asyncio
async def test_stop_without_wait_carries_over_unavailable_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """結果を受領せずstopで破棄した可用性失敗も、次回起動から除外する。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    manager = server_manager.AgentsServerManager()
    delayed = DelayedUnavailableBackend(manager.sessions, "codex", manager._condition, delay=0.01)
    _install_backend(manager, "codex", delayed)

    failed = await manager.start("plan", "調査", str(tmp_path))
    await asyncio.gather(*delayed.pending)
    assert await manager.stop(failed["session_id"]) == {}
    available = FakeBackend(manager.sessions, "codex")
    _install_backend(manager, "codex", available)

    response = await manager.start("plan", "再試行", str(tmp_path))

    assert response["model"] == "second"
    assert available.start_calls == [("second", "medium", "delegate")]


@pytest.mark.asyncio
async def test_expired_kill_keeps_carried_over_unavailable_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """期限切れsessionへのkillだけを経た可用性失敗も、次回起動から除外する。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    manager = server_manager.AgentsServerManager()
    delayed = DelayedUnavailableBackend(manager.sessions, "codex", manager._condition, delay=0.01)
    _install_backend(manager, "codex", delayed)

    failed = await manager.start("plan", "調査", str(tmp_path))
    await asyncio.gather(*delayed.pending)
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
    """engineの可用性以外で終端した候補では次候補へ進まない。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager = server_manager.AgentsServerManager()
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
    """接続先がモデルIDを拒否して起動時の待機内に終端したCodex候補を除外し、次候補で起動する。"""
    candidates = [("codex", "gpt-6.1-sol", "medium"), ("claude", "opus[1m]", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager, claude = _manager_with_fake("claude")
    codex = UnavailableStartBackend(manager.sessions, "codex", error=_codex_model_rejected_error())
    _install_backend(manager, "codex", codex)

    response = await manager.start("high_tier", "調査", str(tmp_path))

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
    """起動時の待機の後にモデルIDの拒否で終端した候補を除外記録へ残し、呼び直した`start`で次候補へ進む。"""
    candidates = [("codex", "gpt-6.1-sol", "medium"), ("codex", "gpt-6-sol", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    manager = server_manager.AgentsServerManager()
    delayed = DelayedUnavailableBackend(
        manager.sessions, "codex", manager._condition, delay=0.01, error=_codex_model_rejected_error()
    )
    _install_backend(manager, "codex", delayed)

    failed = await manager.start("high_tier", "調査", str(tmp_path))
    await asyncio.gather(*delayed.pending)
    assert failed["status"] == "running"
    assert await manager.stop(failed["session_id"]) == {}
    assert unavailable_candidates.load_unavailable_candidates(
        "high_tier", "delegate", now=datetime.datetime.now(datetime.UTC)
    ) == {("codex", "gpt-6.1-sol", "medium"): "modelRejected"}
    available = FakeBackend(manager.sessions, "codex")
    _install_backend(manager, "codex", available)

    response = await manager.start("high_tier", "再試行", str(tmp_path))

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
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager = server_manager.AgentsServerManager()
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
    """Claudeが可用性由来のHTTPステータスで終端した候補を除外し、次候補で起動する。"""
    candidates = [("claude", "first", "high"), ("codex", "second", "medium")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager, codex = _manager_with_fake("codex")
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
    """Claudeが可用性由来でないHTTPステータスで終端した場合と、状態を持たない場合は次候補へ進まない。"""
    candidates = [("claude", "first", "high"), ("claude", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager = server_manager.AgentsServerManager()
    claude = UnavailableStartBackend(manager.sessions, "claude", error=error)
    _install_backend(manager, "claude", claude)

    response = await manager.start("plan", "調査", str(tmp_path))

    assert claude.start_calls == [("first", "high", "delegate")]
    assert response["status"] == "failed"
    assert response["model"] == "first"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invalid", "message"),
    [("prompt", "prompt must be a non-empty string"), ("cwd", "cwd is not an existing directory")],
)
async def test_start_rejects_candidate_independent_input_before_any_backend(
    invalid: str,
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """候補を変えても結果が変わらない入力の不備は、どの候補も起動せずに拒否する。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: candidates)
    manager, backend = _manager_with_fake("codex")

    with pytest.raises(ValueError, match=message):
        await manager.start(
            "plan",
            "" if invalid == "prompt" else "調査",
            str(tmp_path) if invalid == "prompt" else str(tmp_path / "missing"),
        )
    assert not backend.start_calls


@pytest.mark.asyncio
async def test_start_explore_selects_default_low_tier_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """探索起動でモデルを指定しない場合はlow_tier設定を選ぶ。"""
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda model_type: [("codex", model_type, "medium")],
    )
    manager, backend = _manager_with_fake("codex")
    explored = await manager.start_explore("探索", str(tmp_path))
    assert explored["model_type"] == "low_tier"
    assert backend.start_calls[-1] == ("low_tier", "medium", "explore")


@pytest.mark.asyncio
async def test_start_shell_runs_command_on_the_low_tier_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """シェル実行委譲は軽量な候補で開始し、依頼と要約方針を委譲先へ渡して結果を観測させる。"""
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda model_type: [("codex", model_type, "medium")],
    )
    manager, backend = _manager_with_fake("codex")
    started = await manager.start_shell("make test", str(tmp_path), "終了状態と警告だけ")
    assert started["model_type"] == "low_tier"
    assert backend.start_calls[-1] == ("low_tier", "medium", "shell")
    session = manager.sessions[started["session_id"]]
    assert session.launch_kind == "shell"
    _complete(session, message="終了コード0")

    observed = await manager.wait()
    assert observed["status"] == "completed"
    assert observed["agent_message"] == "終了コード0"


@pytest.mark.asyncio
async def test_start_shell_rejects_empty_command_and_summary_policy(tmp_path: pathlib.Path) -> None:
    """コマンドと要約方針のいずれかが空のシェル実行委譲は開始前に拒否する。"""
    manager, backend = _manager_with_fake("codex")
    with pytest.raises(ValueError, match="command must be a non-empty string"):
        await manager.start_shell("   ", str(tmp_path), "終了状態だけ")
    with pytest.raises(ValueError, match="summary_policy must be a non-empty string"):
        await manager.start_shell("make test", str(tmp_path), "")
    assert not backend.start_calls


@pytest.mark.asyncio
async def test_send_message_continues_when_selected_candidate_remains(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """候補の順序が変わっても採用済み候補が残るsessionを継続する。"""
    current = [("codex", "first", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: current)
    manager, backend = _manager_with_fake("codex")
    response = await manager.start("plan", "調査", str(tmp_path))
    session_id = response["session_id"]
    current[:] = [("codex", "replacement", "high"), ("codex", "first", "high")]

    result = await manager.send_message(session_id, "続行")

    assert result["delivery"] == "steered"
    assert session_id in manager.sessions
    assert backend.send_calls == 1


@pytest.mark.asyncio
async def test_send_message_continues_without_model_type(tmp_path: pathlib.Path) -> None:
    """候補列を直接指定したsessionは工程別モデル設定を比較せず継続する。"""
    manager, backend = _manager_with_fake("codex")
    session = await backend.start("調査", str(tmp_path), "direct", "high")

    result = await manager.send_message(session.session_id, "続行")

    assert result["delivery"] == "steered"
    assert backend.send_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("updated", [[("claude", "replacement", "medium")], []])
async def test_send_message_continues_after_candidates_change(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    updated: list[tuple[str, str, str]],
) -> None:
    """起動後に候補列が別engineへ置換された場合と、候補が残らない場合のいずれでもsessionを継続する。"""
    current = [("codex", "first", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: current)
    manager, backend = _manager_with_fake("codex")
    response = await manager.start("plan", "調査", str(tmp_path))
    session_id = response["session_id"]
    current[:] = updated

    result = await manager.send_message(session_id, "続行")

    assert result["delivery"] == "steered"
    assert session_id in manager.sessions
    assert backend.send_calls == 1


@pytest.mark.asyncio
async def test_kill_timeout_zero_returns_request_state(tmp_path: pathlib.Path) -> None:
    """killのtimeout=0は中断要求の受理後に現在状態を返す。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    response = await manager.kill(session.session_id, timeout=0)

    assert response == {
        "status": "running",
        "kill_requested": True,
    }
    assert backend.interrupt_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("with_notice", [False, True])
async def test_kill_includes_notices_only_when_available(
    with_notice: bool,
    tmp_path: pathlib.Path,
) -> None:
    """killは回収した通知がある場合だけnoticesを返す。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    manager = server_manager.AgentsServerManager(writer)
    backend = FakeBackend(manager.sessions, "codex")
    install_backend(manager, "codex", backend)
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session
    if with_notice:
        notices = shared_layout.notices_directory("root-session", tmp_path)
        _write_notice(notices, session.session_id, 1, "2026-09-06T00:00:01+00:00", "中断前の通知")

    response = await manager.kill(session.session_id, timeout=0)

    expected: dict[str, Any] = {"status": "running", "kill_requested": True}
    if with_notice:
        expected["notices"] = [{"body": "中断前の通知"}]
    assert response == expected
    await manager.close()


@pytest.mark.asyncio
async def test_kill_waits_for_terminal_result_and_preserves_request_marker(tmp_path: pathlib.Path) -> None:
    """正のtimeoutを指定したkillは終端結果と要求済み状態を返す。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    kill_task = asyncio.create_task(manager.kill(session.session_id, timeout=1))
    while backend.interrupt_calls == 0:
        await asyncio.sleep(0)
    _complete(session, message="中断結果")
    await manager._notify_waiters()

    assert await kill_task == {
        "status": "completed",
        "agent_message": "中断結果",
        "kill_requested": True,
        **_default_identity_fields(),
    }


@pytest.mark.asyncio
async def test_kill_terminal_session_is_idempotent_without_backend_request(tmp_path: pathlib.Path) -> None:
    """終端済みsessionへのkillは要求を送らず結果を返す。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex")
    _complete(session, message="既存結果")
    manager.sessions[session.session_id] = session

    response = await manager.kill(session.session_id, timeout=0)

    assert response == {
        "status": "completed",
        "agent_message": "既存結果",
        "kill_requested": False,
        **_default_identity_fields(),
    }
    assert backend.interrupt_calls == 0


@pytest.mark.asyncio
async def test_concurrent_kill_requests_share_one_backend_request(tmp_path: pathlib.Path) -> None:
    """同一turnへの並行killは中断要求を1回だけ送る。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    first, second = await asyncio.gather(
        manager.kill(session.session_id, timeout=0),
        manager.kill(session.session_id, timeout=0),
    )

    assert first["kill_requested"] is True
    assert second["kill_requested"] is True
    assert backend.interrupt_calls == 1


@pytest.mark.asyncio
async def test_kill_lock_wait_respects_positive_timeout(tmp_path: pathlib.Path) -> None:
    """正のtimeoutはturn制御ロックの取得待ちにも適用する。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    async with session.turn_control_lock:
        with pytest.raises(TimeoutError, match="kill timed out: thread-1") as raised:
            await manager.kill(session.session_id, timeout=0.01)

    next_action = _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
    assert "atk agents wait" in next_action
    assert "`kill`" in next_action
    assert backend.interrupt_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -0.1])
async def test_send_message_rejects_non_positive_timeout(timeout: float, tmp_path: pathlib.Path) -> None:
    """継続要求のtimeoutは正の値だけを受理し、backendへ配送しない。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    with pytest.raises(ValueError, match="timeout must be positive") as raised:
        await manager.send_message(session.session_id, "追加指示", timeout=timeout)

    assert "`timeout`を省略" in _actionable_message(raised.value)
    assert backend.send_calls == 0


@pytest.mark.asyncio
async def test_kill_rejects_negative_timeout_with_default_hint(tmp_path: pathlib.Path) -> None:
    """killの負のtimeoutは、引数を省略すれば待機上限を270秒とすることを次の操作で示す。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    with pytest.raises(ValueError, match="timeout must be non-negative") as raised:
        await manager.kill(session.session_id, timeout=-1)

    assert "`timeout`を省略" in _actionable_message(raised.value)
    assert backend.interrupt_calls == 0


@pytest.mark.asyncio
async def test_kill_timeout_zero_bounds_turn_control_lock_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """killのtimeout=0でも中断要求配送前のロック待ちを有限時間で打ち切る。"""
    monkeypatch.setattr(server_manager, "DEFAULT_SEND_MESSAGE_TIMEOUT", 0.01, raising=False)
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    async with session.turn_control_lock:
        with pytest.raises(TimeoutError, match="the interrupt request was not delivered"):
            await asyncio.wait_for(manager.kill(session.session_id, timeout=0), timeout=0.1)

    assert backend.interrupt_calls == 0


@pytest.mark.asyncio
async def test_kill_timeout_zero_bounds_interrupt_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """killのtimeout=0でも中断要求の配送待ちを有限時間で打ち切る。"""
    monkeypatch.setattr(server_manager, "DEFAULT_SEND_MESSAGE_TIMEOUT", 0.01, raising=False)
    manager = server_manager.AgentsServerManager()
    backend = BlockingInterruptBackend(manager.sessions, "codex")
    install_backend(manager, "codex", backend)
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    with pytest.raises(TimeoutError, match="interrupt delivery is undetermined"):
        await asyncio.wait_for(manager.kill(session.session_id, timeout=0), timeout=0.1)

    assert session.session_id in manager.sessions
    assert backend.interrupt_calls == 1
    assert session.interrupt_requested is False

    backend.release_interrupt.set()
    response = await manager.kill(session.session_id, timeout=0)

    assert response["kill_requested"] is True
    assert backend.interrupt_calls == 2


@pytest.mark.asyncio
async def test_send_message_timeout_covers_turn_control_lock(tmp_path: pathlib.Path) -> None:
    """継続要求のtimeoutはturn制御ロックの取得を含む操作全体へ適用する。"""
    manager = server_manager.AgentsServerManager()
    backend = claude_backend.ClaudeServerManager(manager.sessions, manager._condition)
    install_backend(manager, "claude", backend)
    session = state.SessionState("claude-locked", str(tmp_path), engine="claude")
    manager.sessions[session.session_id] = session

    async with session.turn_control_lock:
        with pytest.raises(TimeoutError, match="send_message timed out: claude-locked"):
            await manager.send_message(session.session_id, "追加指示", timeout=0.01)


@pytest.mark.asyncio
async def test_kill_timeout_after_delivery_distinguishes_terminal_wait(tmp_path: pathlib.Path) -> None:
    """中断要求配送後の終端待ち超過を未配送と区別する。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    manager.sessions[session.session_id] = session

    with pytest.raises(
        TimeoutError,
        match="the interrupt request was delivered but the turn did not terminate",
    ):
        await manager.kill(session.session_id, timeout=0.01)

    assert backend.interrupt_calls == 1


@pytest.mark.asyncio
async def test_kill_timeout_before_codex_turn_id_reports_not_delivered(tmp_path: pathlib.Path) -> None:
    """Codexのturn_id待ち超過を中断要求未配送として報告する。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex")
    manager.sessions[session.session_id] = session

    with pytest.raises(TimeoutError, match="the interrupt request was not delivered"):
        await manager.kill(session.session_id, timeout=0.01)

    assert backend.interrupt_calls == 0


@pytest.mark.asyncio
async def test_send_message_rejects_active_interrupt_without_backend_call(tmp_path: pathlib.Path) -> None:
    """中断要求が有効な未終端turnへ継続入力を送らない。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
    session.interrupt_requested = True
    manager.sessions[session.session_id] = session

    with pytest.raises(ValueError, match="session is being interrupted: thread-1") as raised:
        await manager.send_message(session.session_id, "追加指示")

    assert "atk agents wait" in _actionable_message(raised.value)

    assert backend.send_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
@pytest.mark.parametrize("delivery", ["reply_started", "reply_failed", "reply_ambiguous"])
async def test_send_message_terminal_session_returns_previous_result_without_internal_fields(
    engine: str,
    delivery: str,
    tmp_path: pathlib.Path,
) -> None:
    """終端後の継続入力は回収済みフラグを要求せず、直前本文を退避する。"""
    manager, _ = _manager_with_fake(engine, delivery)
    session = state.SessionState("thread-1", str(tmp_path), engine=engine)
    _complete(session, message="直前の結果")
    manager.sessions[session.session_id] = session
    response = await manager.send_message(session.session_id, "続行")
    assert response["delivery"] == delivery
    assert response["previous_result"] == {
        "status": "completed",
        "agent_message": "直前の結果",
    }
    _assert_no_forbidden_keys(response)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
@pytest.mark.parametrize(("label", "guided"), [("pr-body-review", True), ("explore-pyfltr", False)])
async def test_send_message_previous_result_guides_review_findings(
    engine: str, label: str, guided: bool, tmp_path: pathlib.Path
) -> None:
    """レビューを目的とするsessionの未回収の完了結果を`previous_result`で返す場合も、採否確定の次の操作を加える。"""
    manager, _ = _manager_with_fake(engine, "reply_started")
    session = state.SessionState("thread-1", str(tmp_path), engine=engine, label=label)
    _complete(session, message="指摘1件")
    manager.sessions[session.session_id] = session

    response = await manager.send_message(session.session_id, "再レビュー")

    previous = response["previous_result"]
    assert (previous.get("next_action") == result_projection.REVIEW_RESULT_NEXT_ACTION) is guided
    if not guided:
        assert "next_action" not in previous


@pytest.mark.asyncio
async def test_stopped_review_session_previous_result_guides_review_findings(tmp_path: pathlib.Path) -> None:
    """破棄済みのレビューsessionの未回収結果を再開時に返す場合も、同じ次の操作を加える。"""
    resume_state = state.SessionResumeState(
        session_id="stopped-review",
        cwd=str(tmp_path),
        model="model",
        effort="medium",
        engine="codex",
        status="completed",
        agent_message="指摘1件",
        finalized_at="2026-10-01T00:00:00+00:00",
        label="lane-01-exec-review",
    )

    response = server_manager.AgentsServerManager._stopped_result_response(resume_state)

    assert response is not None
    assert response["next_action"] == result_projection.REVIEW_RESULT_NEXT_ACTION


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
@pytest.mark.parametrize("label", ["add-wi", "lane-01-exec-review"])
@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
async def test_send_message_previous_and_stopped_results_relay_improvements(
    engine: str, label: str, status: str, tmp_path: pathlib.Path
) -> None:
    """継続入力と破棄済みsessionの未回収結果が、同じ改善点の案内と本文を返す。"""
    manager, _ = _manager_with_fake(engine, "reply_started")
    message = "完了\n  気付いた改善点: 再読した\n気付いた改善点: 反復した"
    session = state.SessionState("thread-1", str(tmp_path), engine=engine, label=label)
    _complete(session, message=message)
    session.status = status
    manager.sessions[session.session_id] = session
    stopped = state.SessionResumeState.from_session(session)

    response = await manager.send_message(session.session_id, "続行")
    previous = response["previous_result"]
    stopped_result = server_manager.AgentsServerManager._stopped_result_response(stopped)

    assert previous == stopped_result
    assert previous["agent_message"] == message
    action = previous["next_action"]
    assert action.count(result_projection.IMPROVEMENT_RESULT_NEXT_ACTION) == 1
    assert "メインエージェントは次のユーザーへの発話へ" in action
    assert "委譲先は自身の返却の末尾へ" in action
    assert "字下げを除く行頭に`気付いた改善点:`を原文のまま置き" in action
    assert "標識とコロンの間へ報告元などの語を入れない" in action
    assert "標識行の原文の後ろか別の行に添える" in action
    assert "`agent-toolkit:delegation`の`references/receiving.md`「受領後の扱い」" in action
    assert (result_projection.REVIEW_RESULT_NEXT_ACTION in action) is (status == "completed" and label.endswith("-review"))


@pytest.mark.asyncio
async def test_send_message_omits_previous_result_after_wait_returned_result(tmp_path: pathlib.Path) -> None:
    """waitで回収した結果本文を継続入力の応答へ再送しない。"""
    manager, _ = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex")
    _complete(session, message="回収済み結果")
    manager.sessions[session.session_id] = session

    assert (await manager.wait())["agent_message"] == "回収済み結果"
    response = await manager.send_message(session.session_id, "続行")

    assert "previous_result" not in response


@pytest.mark.asyncio
async def test_send_message_omits_previous_result_after_kill_returned_result(tmp_path: pathlib.Path) -> None:
    """killで回収した結果本文を継続入力の応答へ再送しない。"""
    manager, _ = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex")
    _complete(session, message="回収済み結果")
    manager.sessions[session.session_id] = session

    assert (await manager.kill(session.session_id, timeout=0))["agent_message"] == "回収済み結果"
    response = await manager.send_message(session.session_id, "続行")

    assert "previous_result" not in response


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
async def test_send_message_keeps_previous_result_for_unwaited_second_turn(
    engine: str,
    tmp_path: pathlib.Path,
) -> None:
    """前turnの回収状態を次turnへ持ち越さず、未回収結果を返す。"""
    manager, _ = _manager_with_fake(engine)
    session = state.SessionState("thread-1", str(tmp_path), engine=engine)
    _complete(session, message="結果A")
    manager.sessions[session.session_id] = session

    await manager.wait()
    first = await manager.send_message(session.session_id, "1回目")
    assert "previous_result" not in first

    _complete(session, message="結果B")
    second = await manager.send_message(session.session_id, "2回目")
    assert second["previous_result"]["agent_message"] == "結果B"


@pytest.mark.asyncio
async def test_send_message_keeps_previous_result_after_wait_without_result(tmp_path: pathlib.Path) -> None:
    """結果本文を含まないwaitは後続の終端結果を回収済みにしない。"""
    manager, _ = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex")
    manager.sessions[session.session_id] = session

    assert "agent_message" not in await manager.wait()
    _complete(session, message="未回収結果")
    response = await manager.send_message(session.session_id, "続行")

    assert response["previous_result"]["agent_message"] == "未回収結果"


@pytest.mark.asyncio
async def test_send_message_keeps_previous_result_after_reply_failed_response(tmp_path: pathlib.Path) -> None:
    """send_messageのreply_failed応答だけでは結果本文を回収済みにしない。"""
    manager = server_manager.AgentsServerManager()
    backend = FailedResumeBackend(manager.sessions, "codex")
    install_backend(manager, "codex", backend)
    session_id = "thread-1"
    manager.expired_sessions[session_id] = state.SessionResumeState(
        session_id=session_id,
        cwd=str(tmp_path),
        model=None,
        effort=None,
        engine="codex",
    )

    first = await manager.send_message(session_id, "1回目")
    assert _without_root(first) == {"delivery": "reply_failed", "label": ""}

    second = await manager.send_message(session_id, "2回目")
    assert second["previous_result"]["agent_message"] == "reply失敗結果"


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude"])
@pytest.mark.parametrize(
    ("delivery", "expected_progress"),
    [("reply_started", ""), ("reply_ambiguous", ""), ("reply_failed", "直前の進捗")],
)
async def test_reply_resets_progress_only_after_delivery_is_accepted(
    engine: str,
    delivery: str,
    expected_progress: str,
    tmp_path: pathlib.Path,
) -> None:
    """reply失敗時は直前の進捗を保持し、開始済みまたは曖昧時だけ破棄する。"""
    manager, _ = _manager_with_fake(engine, delivery)
    session = state.SessionState("thread-1", str(tmp_path), engine=engine)
    session.set_progress("直前の進捗")
    _complete(session)
    manager.sessions[session.session_id] = session

    response = await manager.send_message(session.session_id, "続行")

    assert response["delivery"] == delivery
    assert session.progress == expected_progress


@pytest.mark.asyncio
async def test_send_message_steered_response_has_no_previous_result(tmp_path: pathlib.Path) -> None:
    """実行中turnへの追加指示はsteeredとして本文を退避しない。"""
    manager, _ = _manager_with_fake("codex")
    session = state.SessionState("thread-1", str(tmp_path), engine="codex")
    session.turn_id = "turn-1"
    manager.sessions[session.session_id] = session
    response = await manager.send_message(session.session_id, "追加指示")
    assert _without_root(response) == {"delivery": "steered", "label": ""}


@pytest.mark.asyncio
async def test_send_message_timeout_reports_undetermined_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """継続要求の配送結果が確定しない場合に指定上限で打ち切る。"""
    client = BlockingContinuationClaudeClient()
    manager = server_manager.AgentsServerManager()
    backend = claude_backend.ClaudeServerManager(
        manager.sessions,
        manager._condition,
        client_factory=lambda _options: client,
        expire_session=manager._expire_session,
    )
    install_backend(manager, "claude", backend)
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    try:
        session = await backend.start("調査", str(tmp_path))
        await asyncio.wait_for(client.message_waiting.wait(), timeout=0.1)

        with pytest.raises(TimeoutError, match="send_message timed out: claude-blocking; delivery is undetermined") as raised:
            await manager.send_message(session.session_id, "継続", timeout=0.01)
        assert "atk agents wait" in _actionable_message(raised.value)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_kill_stop_discards_only_after_terminal_result(tmp_path: pathlib.Path) -> None:
    """killのrunning応答は保持し、終端済み応答後だけ破棄する。"""
    manager, backend = _manager_with_fake("codex")
    session = state.SessionState("kill-stop", str(tmp_path), engine="codex")
    session.turn_id = "turn"
    manager.sessions[session.session_id] = session

    running = await manager.kill(session.session_id, timeout=0, stop=True)
    assert running["status"] == "running"
    assert session.session_id in manager.sessions
    _complete(session)
    session.status = "interrupted"
    response = await manager.kill(session.session_id, timeout=0, stop=True)

    assert response["status"] == "interrupted"
    assert response["kill_requested"] is False
    assert session.session_id in manager.stopped_sessions
    retained = await manager.kill(session.session_id, timeout=0)
    assert retained["status"] == "interrupted"
    assert retained["agent_message"] == "完了"
    assert retained["kill_requested"] is False
    assert backend.release_calls == [session.session_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
async def test_send_message_includes_stopped_previous_result_regardless_of_deadline(
    expired: bool,
    tmp_path: pathlib.Path,
) -> None:
    """stop=true後の再開は未回収であれば期限後も直前結果を返す。"""
    manager, _ = _manager_with_fake("codex")
    session = state.SessionState(f"stopped-send-{expired}", str(tmp_path), engine="codex")
    _complete(session, message="直前結果")
    manager.sessions[session.session_id] = session
    await manager.kill(session.session_id, timeout=0, stop=True)
    if expired:
        manager.stopped_sessions[session.session_id] = dataclasses.replace(
            manager.stopped_sessions[session.session_id],
            retention_deadline=asyncio.get_running_loop().time() - 1,
        )

    response = await manager.send_message(session.session_id, "再開")

    assert response["previous_result"]["agent_message"] == "直前結果"


@pytest.mark.asyncio
async def test_kill_stop_retains_result_for_agents_wait(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """stop=trueで破棄した結果をatk agents waitからも回収できる。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = server_manager.AgentsServerManager(writer)
    backend = FakeBackend(manager.sessions, "codex")
    install_backend(manager, "codex", backend)
    writer.activate()
    session = state.SessionState("wait-stop-cli", str(tmp_path), engine="codex", announced=True)
    _complete(session, message="CLI回収")
    manager.sessions[session.session_id] = session

    await manager.kill(session.session_id, timeout=0, stop=True)
    result_path = shared_layout.results_directory("root-session", tmp_path) / f"{session.session_id}.json"
    assert result_path.exists()
    assert (
        agents_wait.wait_for_result(
            environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
            state_root=tmp_path,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["agent_message"] == "CLI回収"
    assert not result_path.exists()
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine", "model_type"), (("codex", None), ("claude", "implementation_fast")))
async def test_expired_session_kill_returns_success_response(
    engine: str,
    model_type: str | None,
    tmp_path: pathlib.Path,
) -> None:
    """保持期限切れsessionへのkillは中断対象が無い成功応答を返す。"""
    manager, backend = _manager_with_fake(engine)
    session = state.SessionState("expired", str(tmp_path), engine=engine, model_type=model_type)
    _complete(session)
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    manager.sessions[session.session_id] = session

    response = await manager.kill(session.session_id, timeout=0)

    assert "atk agents wait" in response.pop("next_action")
    assert response == {"status": "expired", "kill_requested": False}
    assert "expired" not in manager.sessions
    assert manager.expired_sessions["expired"].session_id == "expired"
    assert backend.interrupt_calls == 0


def test_initialization_failure_resolves_before_host_moves_call_to_background() -> None:
    """初期化の失敗がホストがバックグラウンドタスクへ移す閾値より前に確定する。

    この関係が成立しなくなると、委譲元は`start`の失敗を受け取らないまま待機へ進む。
    上限値を0などへ置換せずに、現行の定数どうしの関係だけを判定する。
    """
    failure_path = state.SESSION_INITIALIZATION_TIMEOUT * state.SESSION_INITIALIZATION_ATTEMPTS
    assert failure_path + server_manager.START_AVAILABILITY_TIMEOUT < state.HOST_BACKGROUND_THRESHOLD_SECONDS
