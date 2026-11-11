"""`_agents_server/manager.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import contextlib
import logging
import pathlib
import threading
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import mcp_tools, shared_roots, state, status_file
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_support import FakeBackend, _complete, _manager_with_fake, install_backend

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize("outcome", ["success", "body_failure", "start_failure"])
@pytest.mark.asyncio
async def test_context_releases_resources_on_every_exit(
    outcome: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """正常終了、本文の例外、起動失敗で監視とwriterとbackendを回収する。"""
    writer = status_file.StatusFileWriter(
        {}, shared_roots.StatusFileIdentity("lifetime-root", "writer.json", None), state_root=tmp_path
    )
    closed = AsyncMock()
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [("codex", "model", "high")]
    )
    expected = contextlib.nullcontext() if outcome == "success" else pytest.raises(RuntimeError, match="寿命の試験")
    with expected:
        async with server_manager.AgentsServerManager(writer) as manager:
            backend = FakeBackend(manager.sessions, "codex")
            monkeypatch.setattr(backend, "close", closed)
            install_backend(manager, "codex", backend)
            await manager.activate_async()
            monitor = manager._auto_resume_task
            heartbeat = manager._heartbeat_task
            assert writer.path.exists()
            if outcome == "start_failure":
                monkeypatch.setattr(backend, "start", AsyncMock(side_effect=RuntimeError("寿命の試験")))
                await manager.start("plan", "起動の確認", str(tmp_path))
            elif outcome == "body_failure":
                raise RuntimeError("寿命の試験")
    closed.assert_awaited_once()
    assert monitor is not None and monitor.done()
    assert heartbeat is not None and heartbeat.done()
    assert not writer.path.exists()
    assert writer.schedule not in state._TOUCH_LISTENERS
    assert manager._record_resources not in state._LIFECYCLE_LISTENERS


@pytest.mark.asyncio
async def test_concurrent_close_shares_failure_and_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """同時停止は同じ失敗を受け取り、再試行成功後の停止は資源へ再送しない。"""
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async with server_manager.AgentsServerManager(None) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)

        async def close_backend() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
                raise OSError("最初の停止失敗")

        monkeypatch.setattr(backend, "close", close_backend)
        first = asyncio.create_task(manager.close())
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(manager.close())
        await asyncio.sleep(0)
        release.set()
        outcomes = await asyncio.gather(first, second, return_exceptions=True)
        assert len(outcomes) == 2 and all(isinstance(outcome, OSError) for outcome in outcomes)
        assert calls == 1
        await asyncio.gather(manager.close(), manager.close())
        assert calls == 2
    assert calls == 2


def test_server_stop_joins_worker_threads(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """サーバー終了の返却時には永続ログのworkerと出力が完了する。"""
    from agent_toolkit._agents_server import logging_config  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    before = set(threading.enumerate())
    workers: list[threading.Thread] = []

    def observe_workers() -> None:
        workers.extend(
            thread for thread in threading.enumerate() if thread not in before and thread.name == "agents-server-log"
        )

    monkeypatch.setattr(claude_backend, "check_dependencies", observe_workers)
    monkeypatch.setattr(mcp_tools, "_prepare_child_environment", lambda: None)
    assert mcp_tools.serve(check_dependencies=True) == 0
    assert "agents_serverが正常終了しました" in (tmp_path / "agents-server.log").read_text(encoding="utf-8")
    assert workers and all(not worker.is_alive() for worker in workers)
    assert not any(
        isinstance(handler, logging_config._LogFileHandler)  # noqa: SLF001  # pylint: disable=protected-access
        for handler in logging.getLogger("agent-toolkit.agents-server").handlers
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude", "agy"])
async def test_retention_expiry_releases_backend_once_without_tool_call(
    engine: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """終了の保持期限到達は照会なしで解放を呼び、後からstopしても重ねて解放しない。"""
    async with _manager_with_fake(engine) as (manager, backend):
        released = asyncio.Event()
        original_release = backend.release_session

        async def release(session: state.SessionState) -> None:
            await original_release(session)
            released.set()

        monkeypatch.setattr(backend, "release_session", release)
        monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.02)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("closure_failed", [False, True])
async def test_expiry_registration_is_released_only_after_backend_closure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, closure_failed: bool
) -> None:
    """保持期限の解放中・失敗時は登録簿の資源を保持し、閉鎖成功だけを解放済みとして公開する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with _manager_with_fake("codex") as (manager, backend):
        entered = asyncio.Event()
        finish = asyncio.Event()

        async def release(_session: state.SessionState) -> None:
            entered.set()
            await finish.wait()
            if closure_failed:
                raise RuntimeError("thread closure failed")

        monkeypatch.setattr(backend, "release_session", release)
        monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.02)
        session = await backend.start("調査", str(tmp_path), None, None)
        session.publish_registry = True
        _complete(session)
        await session.drain_registration()
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert session_registry.resolve(session.session_id).state is session_registry.Resolution.TERMINAL
            task = manager._resource_release_tasks[session.session_id]
            finish.set()
            if closure_failed:
                with pytest.raises(RuntimeError, match="thread closure failed"):
                    await task
                assert session_registry.resolve(session.session_id).state is session_registry.Resolution.TERMINAL
                assert manager.sessions[session.session_id] is session
                assert session.session_id not in manager.expired_sessions
                assert session.retention_deadline is None
                # 保存された解放失敗を一度観測した後、公開stopから所有資源を再解放できる。
                with pytest.raises(RuntimeError, match="thread closure failed"):
                    await manager.stop(session.session_id)
                monkeypatch.setattr(backend, "release_session", AsyncMock())
                assert await manager.stop(session.session_id) == {}
                assert session_registry.resolve(session.session_id).state is session_registry.Resolution.RELEASED
            else:
                await task
                assert session_registry.resolve(session.session_id).state is session_registry.Resolution.RELEASED
        finally:
            finish.set()
            await manager.close()


@pytest.mark.asyncio
async def test_reply_cancels_old_retention_deadline(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """保持期限より前に再開したturnへ、旧turnの解放を遅れて適用しない。"""
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.01)
        session = await backend.start("調査", str(tmp_path), None, None)
        loop = asyncio.get_running_loop()
        completed_at = loop.time()
        # 診断・保存の所要によらず、返信の受付を旧turnの保持期限より前へ固定する。
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(loop, "time", lambda: completed_at)
            _complete(session)
            await manager.send_message(session.session_id, "続行")
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(loop, "time", lambda: completed_at + 1)
            manager._expire_retained_session(session)  # pylint: disable=protected-access
            await asyncio.sleep(0)
        assert not backend.release_calls
        assert manager.sessions[session.session_id] is session
        assert session.status == "running"


@pytest.mark.parametrize("failing_step", ["backend_close", "writer_drain", "diagnostics"])
@pytest.mark.asyncio
async def test_close_runs_required_cleanup_after_failure(
    failing_step: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """backendの停止かwriterの回収が失敗しても、listener解除とwriterの停止へ到達し、失敗を呼出元へ返す。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        other_backend = FakeBackend(manager.sessions, "claude")
        install_backend(manager, "codex", backend)
        install_backend(manager, "claude", other_backend)
        writer.activate()
        other_closed = asyncio.Event()

        async def fail(*_args: Any, **_kwargs: Any) -> None:
            raise OSError("注入した失敗")

        async def close_other() -> None:
            other_closed.set()

        with monkeypatch.context() as failure_patch:
            if failing_step == "backend_close":
                failure_patch.setattr(backend, "close", fail)
            elif failing_step == "writer_drain":
                failure_patch.setattr(writer, "drain", fail)
            else:
                failure_patch.setattr(manager, "_drain_diagnostics", fail)
            failure_patch.setattr(other_backend, "close", close_other)

            with pytest.raises(OSError, match="注入した失敗"):
                await manager.close()

            assert other_closed.is_set()
            assert manager._carry_over_unavailable_candidate not in state._TERMINAL_LISTENERS
            assert manager._record_resources not in state._LIFECYCLE_LISTENERS
            assert writer.schedule not in state._TOUCH_LISTENERS
            assert not writer._active


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked", ["claude", "codex", "agy", "opencode", "release"])
async def test_close_starts_all_backends_before_waiting(blocked: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """1つのbackendか資源解放taskが停止しても、全backendへ停止開始を配送する。"""
    release = asyncio.Event()
    started = {engine: asyncio.Event() for engine in ("claude", "codex", "agy", "opencode")}
    async with server_manager.AgentsServerManager(None) as manager:
        for engine, event in started.items():
            backend = FakeBackend(manager.sessions, engine)

            async def close(*, name: str = engine, ready: asyncio.Event = event, **_kwargs: Any) -> None:
                ready.set()
                if name == blocked:
                    await release.wait()

            monkeypatch.setattr(backend, "close", close)
            install_backend(manager, engine, backend)
        if blocked == "release":

            async def wait_release() -> None:
                await release.wait()

            manager._resource_release_tasks["test"] = asyncio.create_task(wait_release())
        closing = asyncio.create_task(manager.close())
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 2)
        finally:
            release.set()
            await closing


@pytest.mark.asyncio
async def test_close_preserves_backend_failure_when_cleanup_also_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """後始末の失敗は最初の停止失敗へ注記し、writerを停止する。"""
    writer = status_file.StatusFileWriter(
        {}, shared_roots.StatusFileIdentity("root-session", "root.json", None), state_root=tmp_path, aggregate_seconds=0
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        writer.activate()

        async def fail_backend() -> None:
            raise OSError("backend停止失敗")

        async def fail_drain(**_kwargs: Any) -> None:
            raise OSError("writer回収失敗")

        with monkeypatch.context() as failure_patch:
            failure_patch.setattr(backend, "close", fail_backend)
            failure_patch.setattr(writer, "drain", fail_drain)
            with pytest.raises(OSError, match="backend停止失敗") as caught:
                await manager.close()
            assert any("writer回収失敗" in note for note in caught.value.__notes__)
            assert not writer._active
