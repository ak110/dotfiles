"""`_agents_server/manager.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import session_errors, state
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    OutputObservedBackend,
    _actionable_message,
    _complete,
    _install_backend,
    _manager_with_fake,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_start_resolves_codex_family_from_existing_backend_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """委譲起動へ系列名を渡さず、既存のbackendが持つモデル一覧から解決した完全IDを使う。"""
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda _model_type, **_kwargs: [("codex", "sol", "medium")],
    )
    async with _manager_with_fake("codex") as (manager, backend):
        result = await manager.start("execute", "調査", str(tmp_path))

        assert result["status"] == "running"
        assert backend.start_calls == [("gpt-6-sol", "medium", "delegate")]


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_start_rejects_unknown_model_type_before_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """未知の工程別モデル設定をbackend開始前に拒否する。"""
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(
            _atk_config,
            "parse_unresolved_model_candidates",
            lambda model_type, **_kwargs: (
                [("codex", model_type, "medium")]
                if model_type in {"plan", "execute"}
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
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", "codex:gpt-6-sol/medium")
    async with _manager_with_fake("codex") as (manager, backend):
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
        start_task = asyncio.create_task(manager.start("execute", "調査", str(tmp_path)))
        await registered.wait()

        listed = (await manager.list_sessions())["sessions"]
        assert [(item["session_id"], item["status"]) for item in listed] == [("codex-session", "starting")]
        assert (await manager.show_session("codex-session"))["status"] == "starting"

        finish_initialization.set()
        response = await start_task
        assert response["status"] == "running"
        assert (await manager.show_session("codex-session"))["status"] == "running"


@pytest.mark.asyncio
async def test_start_does_not_advance_candidate_when_backend_start_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """backend開始の例外では、資源の二重作成を避けて候補を進めない。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with _manager_with_fake("codex") as (manager, backend):
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
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with _manager_with_fake("codex") as (manager, backend):
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
        monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with _manager_with_fake("codex") as (manager, backend):
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
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with _manager_with_fake("agy") as (manager, backend):

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
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    monkeypatch.setattr(state, "SESSION_INITIALIZATION_ATTEMPTS", 3)
    async with _manager_with_fake("codex") as (manager, backend):
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
async def test_start_returns_running_once_model_output_is_observed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """終了しないsessionでも、最初のモデル出力を観測した時点で上限を待たずに実行中として返す。"""
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [("codex", "only", "high")]
    )
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 5.0)
    async with server_manager.AgentsServerManager() as manager:
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
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with _manager_with_fake("codex") as (manager, backend):
        with pytest.raises(ValueError, match=message):
            await manager.start(
                "plan",
                "" if invalid == "prompt" else "調査",
                str(tmp_path) if invalid == "prompt" else str(tmp_path / "missing"),
            )
        assert not backend.start_calls


@pytest.mark.asyncio
async def test_start_explore_selects_default_chores_fast_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """探索起動でモデルを指定しない場合はchores_fast設定を選ぶ。"""
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda model_type, **_kwargs: [("codex", model_type, "medium")],
    )
    async with _manager_with_fake("codex") as (manager, backend):
        explored = await manager.start_explore("探索", str(tmp_path))
        assert explored["model_type"] == "chores_fast"
        assert backend.start_calls[-1] == ("chores_fast", "medium", "explore")


@pytest.mark.asyncio
async def test_explore_uses_first_available_agy_candidate(tmp_path: pathlib.Path) -> None:
    """探索でも通常の候補順でAntigravityを開始する。"""
    async with _manager_with_fake("claude") as (manager, claude):
        agy = FakeBackend(manager.sessions, "agy")
        _install_backend(manager, "agy", agy)
        result = await manager.start(
            "agy:gemini-3.8-flash/medium,claude:opus[1m]/medium", "探索", str(tmp_path), launch_kind="explore"
        )
        assert agy.start_calls == [("gemini-3.8-flash", "medium", "explore")]
        assert not claude.start_calls
        assert result["engine"] == "agy"
        assert "excluded_candidates" not in result
        await manager.close()


@pytest.mark.asyncio
async def test_start_shell_runs_command_on_the_chores_fast_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """シェル実行委譲は軽量な候補で開始し、依頼と要約方針を委譲先へ渡して結果を観測させる。"""
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda model_type, **_kwargs: [("codex", model_type, "medium")],
    )
    async with _manager_with_fake("codex") as (manager, backend):
        started = await manager.start_shell("make test", str(tmp_path), "終了状態と警告だけ")
        assert started["model_type"] == "chores_fast"
        assert backend.start_calls[-1] == ("chores_fast", "medium", "shell")
        session = manager.sessions[started["session_id"]]
        assert session.launch_kind == "shell"
        _complete(session, message="終了コード0")

        observed = await manager.wait()
        assert observed["status"] == "completed"
        assert observed["agent_message"] == "終了コード0"


@pytest.mark.asyncio
async def test_start_shell_rejects_empty_command_and_summary_policy(tmp_path: pathlib.Path) -> None:
    """コマンドと要約方針のいずれかが空のシェル実行委譲は開始前に拒否する。"""
    async with _manager_with_fake("codex") as (manager, backend):
        with pytest.raises(ValueError, match="command must be a non-empty string"):
            await manager.start_shell("   ", str(tmp_path), "終了状態だけ")
        with pytest.raises(ValueError, match="summary_policy must be a non-empty string"):
            await manager.start_shell("make test", str(tmp_path), "")
        assert not backend.start_calls


def test_initialization_failure_resolves_before_host_moves_call_to_background() -> None:
    """初期化の失敗は、ホストが呼び出しをバックグラウンドタスクへ移す閾値より前に確定する。

    この関係が成立しなくなると、委譲元は`start`の失敗を受け取らないまま待機へ進む。
    上限値を0などへ置換せずに、現行の定数どうしの関係だけを判定する。
    """
    failure_path = state.SESSION_INITIALIZATION_TIMEOUT * state.SESSION_INITIALIZATION_ATTEMPTS
    assert failure_path + server_manager.START_AVAILABILITY_TIMEOUT < state.HOST_BACKGROUND_THRESHOLD_SECONDS
