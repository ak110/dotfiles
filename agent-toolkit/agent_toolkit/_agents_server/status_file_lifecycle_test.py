# pylint: disable=protected-access

import argparse
import asyncio
import json
import pathlib
import threading
from typing import Any

import pytest

from agent_toolkit._agents_server import (
    codex,
    commands,
    manager,
    shared_layout,
    shared_roots,
    state,
    status_file,
    turn_state,
    unavailable_candidates,
)
from agent_toolkit._atk import config_settings as atk_config
from agent_toolkit._common import atomic_file, session_registry, state_paths
from agent_toolkit._testing.agents_server_support import FakeBackend, FakeCodexClient, _complete, install_backend
from agent_toolkit._testing.status_file_checkpoints import (
    StartedBackend,
    TerminalUnavailableBackend,
    UnavailableBackend,
    WindowsReplaceError,
    wait_for_checkpoint,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.asyncio
async def test_checkpoint_observes_notification_when_its_wakeup_races_timeout(
    monkeypatch: pytest.MonkeyPatch, published: bool
) -> None:
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    clock = loop.time
    offset = 0.0

    def advance() -> None:
        nonlocal offset
        offset = 6.0

    with monkeypatch.context() as patch:
        patch.setattr(loop, "time", lambda: clock() + offset)
        if published:
            loop.call_later(4, entered.set)
        loop.call_soon(advance)
        if published:
            await wait_for_checkpoint(entered)
        else:
            with pytest.raises(TimeoutError):
                await wait_for_checkpoint(entered)


@pytest.mark.parametrize("winerror", [5, 32])
@pytest.mark.parametrize("route", ["alias-start", "alias-resume", "candidate", "wait-target", "release", "config"])
@pytest.mark.asyncio
async def test_auxiliary_save_retry_runs_outside_loop(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, winerror: int, route: str
) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    async with manager.AgentsServerManager(writer) as owner:
        install_backend(owner, "claude", StartedBackend(owner.sessions, "claude"))
        await owner.activate_async()
        async with codex.AppServerManager(root_session_id="root") as direct:
            fake_client = FakeCodexClient()

            async def ensure_client(*_args: Any, **_kwargs: Any) -> FakeCodexClient:
                return fake_client

            monkeypatch.setattr(direct, "_ensure_client", ensure_client)
            if route == "alias-resume":
                await direct.start("処理", str(tmp_path), "gpt-6-sol", "medium")
            if route == "release":
                started = await owner.start("claude:test/medium", "処理", str(tmp_path))
                session = owner.sessions[started["session_id"]]
                session.status = "completed"
                session.record_turn_completed()
                session.touch()
                await session.drain_registration()
            config_path = tmp_path / "legacy-config.json"
            if route == "config":
                config_path.write_text(
                    json.dumps({"execute_engine": "claude", "claude_high_tier_model": "test/medium"}), encoding="utf-8"
                )
                monkeypatch.setattr(atk_config, "config_file_path", lambda: config_path)
            replace = atomic_file.os.replace
            attempts: list[str] = []
            delays: list[float] = []
            saved: dict[pathlib.Path, bytes] = {}
            loop_thread = threading.get_ident()

            def matches(target: pathlib.Path) -> bool:
                if route.startswith("alias"):
                    return target.parent.name == "hosts"
                if route == "candidate":
                    return target == unavailable_candidates.unavailable_candidates_path(tmp_path)
                if route == "wait-target":
                    return target == shared_layout.wait_targets_directory("root", "root.json", tmp_path) / "claude-session.json"
                if route == "config":
                    return target == config_path
                return target == session_registry.registry_directory(tmp_path) / "claude-session.json"

            def fail_then_replace(source: str, target: pathlib.Path, **_kwargs: Any) -> None:
                if matches(target):
                    attempts.append(source)
                    if len(attempts) == 1:
                        raise WindowsReplaceError(winerror)
                content = pathlib.Path(source).read_bytes()
                replace(source, target)
                if matches(target):
                    saved[target] = content

            def observe_sleep(delay: float, **_kwargs: Any) -> None:
                assert threading.get_ident() != loop_thread
                delays.append(delay)

            original_write = status_file.atomic_write

            def reject_projection(path: pathlib.Path, content: str, **kwargs: Any) -> None:
                if path == writer.path:
                    raise PermissionError("表示を保存できない")
                original_write(path, content, **kwargs)

            monkeypatch.setattr(atomic_file.os, "replace", fail_then_replace)
            monkeypatch.setattr(atomic_file.time, "sleep", observe_sleep)
            try:
                if route == "alias-start":
                    await direct.start("処理", str(tmp_path), "gpt-6-sol", "medium")
                elif route == "alias-resume":
                    await direct.resume("thread-codex", state.ResumePrompt("続ける"), str(tmp_path), "gpt-6-sol", "medium")
                elif route == "candidate":
                    install_backend(owner, "codex", UnavailableBackend(owner.sessions, "codex"))
                    started = await owner.start(
                        "codex:gpt-6-sol/medium,claude:test/medium", "処理", str(tmp_path), launch_kind="explore"
                    )
                    assert started["session_id"] == "claude-session"
                elif route == "wait-target":
                    monkeypatch.setattr(status_file, "atomic_write", reject_projection)
                    started = await owner.start("claude:test/medium", "処理", str(tmp_path))
                    assert {warning["scope"] for warning in started["persistence_warnings"]} == {"projection"}
                elif route == "release":
                    await owner.stop("claude-session")
                    assert session_registry.resolve("claude-session").state is session_registry.Resolution.RELEASED
                else:
                    started = await owner.start("execute", "処理", str(tmp_path))
                    assert started["session_id"] == "claude-session"
                    assert "claude_high_tier_model" not in json.loads(config_path.read_text(encoding="utf-8"))
                assert 1 < len(attempts) < 20
                assert delays and all(delay > 0 for delay in delays)
                assert saved
                for destination, content in saved.items():
                    assert destination.read_bytes() == content
                    assert not list(destination.parent.glob(f".{destination.name}.*.tmp"))
            finally:
                monkeypatch.setattr(status_file, "atomic_write", original_write)
                await direct.close()
                await owner.close()


@pytest.mark.asyncio
async def test_public_responses_finish_registration_before_cli_observation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with manager.AgentsServerManager(None) as owner:
        install_backend(owner, "claude", StartedBackend(owner.sessions, "claude"))

        def shown() -> dict[str, Any]:
            assert (
                commands.dispatch(argparse.Namespace(agents_subcommand="show", session_id="claude-session"), environment={})
                == 0
            )
            return json.loads(capsys.readouterr().out)

        started = await owner.start("claude:test/medium", "処理", str(tmp_path))
        assert started["status"] == "running"
        assert shown()["status"] == "running"
        session = owner.sessions[started["session_id"]]
        session.status = "completed"
        session.record_turn_completed()
        session.touch()
        sent = await owner.send_message(session.session_id, "新turn")
        assert sent["delivery"] == "reply_started"
        info = session_registry.resolve(session.session_id).resume_info
        assert info is not None and info.turn_seq == 2
        assert shown()["status"] == "running"
        await owner.close()
        assert shown()["status"] == "interrupted"
        assert session_registry.resolve(session.session_id).state is session_registry.Resolution.TERMINAL
        await owner.stop(session.session_id)
        assert session_registry.resolve(session.session_id).state is session_registry.Resolution.RELEASED


@pytest.mark.asyncio
async def test_candidate_save_failure_is_reported_by_started_response(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with manager.AgentsServerManager(None) as owner:
        install_backend(owner, "codex", TerminalUnavailableBackend(owner.sessions, "codex"))
        replace = atomic_file.os.replace

        def reject_candidate(source: str, destination: pathlib.Path, **_kwargs: Any) -> None:
            if destination == unavailable_candidates.unavailable_candidates_path(tmp_path):
                raise PermissionError("候補記録を保存できない")
            replace(source, destination)

        monkeypatch.setattr(atomic_file.os, "replace", reject_candidate)
        response = await owner.start("codex:gpt-6-sol/medium", "処理", str(tmp_path))
        assert response["session_id"] == "codex-session"
        assert response["status"] == "failed"
        assert any(
            warning["scope"] == "shared-state" and "候補記録を保存できない" in warning["message"]
            for warning in response["persistence_warnings"]
        )


@pytest.mark.parametrize("terminal_save_fails", [False, True])
@pytest.mark.asyncio
async def test_close_propagates_candidate_failure_without_masking_terminal_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, terminal_save_fails: bool
) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    async with manager.AgentsServerManager(writer) as owner:
        install_backend(owner, "codex", FakeBackend(owner.sessions, "codex"))
        await writer.activate_async()
        session = state.SessionState(
            "session",
            str(tmp_path),
            model="gpt-6-sol",
            effort="medium",
            model_type="execute",
            publish_registry=True,
            announced=True,
        )
        owner.sessions[session.session_id] = session
        replace = atomic_file.os.replace

        def reject(source: str, destination: pathlib.Path, **_kwargs: Any) -> None:
            if destination == unavailable_candidates.unavailable_candidates_path(tmp_path):
                raise PermissionError("候補記録を保存できない")
            if terminal_save_fails and destination.parent.name == "results":
                raise PermissionError("終了時の結果を保存できない")
            replace(source, destination)

        monkeypatch.setattr(atomic_file.os, "replace", reject)
        session.status = "failed"
        session.record_turn_completed()
        session.error = {"codexErrorInfo": "usageLimitExceeded"}
        session.touch()
        expected = "終了時の結果を保存できない" if terminal_save_fails else "候補記録を保存できない"
        with pytest.raises(PermissionError, match=expected) as raised:
            await owner.close()
        if terminal_save_fails:
            assert any("候補除外記録の保存にも失敗" in note for note in raised.value.__notes__)
        assert not writer.path.exists()
        assert not owner._candidate_tasks


@pytest.mark.asyncio
async def test_cancelled_close_finishes_worker_and_deactivates_writer(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    async with manager.AgentsServerManager(writer) as owner:
        await writer.activate_async()
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        replace = atomic_file.os.replace

        def block_replace(source: str, destination: pathlib.Path, **_kwargs: Any) -> None:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            replace(source, destination)

        monkeypatch.setattr(atomic_file.os, "replace", block_replace)
        closing = asyncio.create_task(owner.close())
        try:
            await wait_for_checkpoint(entered)
            closing.cancel()
            await asyncio.sleep(0)
            assert not closing.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await closing
        assert not writer.path.exists()
        assert not writer._active
        assert not writer._flush_tasks


@pytest.mark.parametrize("observer", ["wait", "close"])
@pytest.mark.asyncio
async def test_expiry_save_failure_reaches_observer_after_backend_release(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, observer: str
) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session = state.SessionState("session", str(tmp_path), announced=True, publish_registry=True)
    _complete(session, message="")
    writer = status_file.StatusFileWriter(
        {"session": session}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path
    )
    async with manager.AgentsServerManager(writer) as owner:
        backend = FakeBackend(owner.sessions, "codex")
        install_backend(owner, "codex", backend)
        await writer.activate_async()
        replace = atomic_file.os.replace
        registry_path = session_registry.registry_directory(tmp_path) / "session.json"

        def reject_release(source: str, destination: pathlib.Path, **_kwargs: Any) -> None:
            if destination == registry_path:
                raise PermissionError("期限解放の登録を保存できない")
            replace(source, destination)

        monkeypatch.setattr(atomic_file.os, "replace", reject_release)
        owner._expire_session(session.session_id)
        task = owner._resource_release_tasks[session.session_id]
        await asyncio.gather(task, return_exceptions=True)
        assert backend.release_calls == [session.session_id]
        with pytest.raises(PermissionError, match="期限解放の登録を保存できない"):
            if observer == "wait":
                await owner.wait()
            else:
                await owner.close()
        await owner.close()
        assert not owner._resource_release_tasks
        assert not writer.path.exists()


@pytest.mark.asyncio
async def test_close_waits_for_other_candidate_worker_after_first_save_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with manager.AgentsServerManager(None) as owner:
        install_backend(owner, "codex", FakeBackend(owner.sessions, "codex"))
        entered = asyncio.Event()
        later_entered = asyncio.Event()
        draining = asyncio.Event()
        fail = threading.Event()
        release = threading.Event()
        later_finished = threading.Event()
        closed = threading.Event()
        wrote_after_close = threading.Event()
        loop = asyncio.get_running_loop()
        replace = atomic_file.os.replace
        destination = unavailable_candidates.unavailable_candidates_path(tmp_path)

        def replace_candidates(source: str, target: pathlib.Path, **_kwargs: Any) -> None:
            if target != destination:
                replace(source, target)
                return
            payload = json.loads(pathlib.Path(source).read_text(encoding="utf-8"))
            if payload["entries"][-1]["model"] == "first":
                loop.call_soon_threadsafe(entered.set)
                assert fail.wait(5)
                raise PermissionError("先行候補の保存失敗")
            loop.call_soon_threadsafe(later_entered.set)
            try:
                assert release.wait(5)
                if closed.is_set():
                    wrote_after_close.set()
                replace(source, target)
            finally:
                later_finished.set()

        original_drain = owner._drain_candidate_records

        async def observed_drain() -> None:
            draining.set()
            await original_drain()

        monkeypatch.setattr(atomic_file.os, "replace", replace_candidates)
        monkeypatch.setattr(owner, "_drain_candidate_records", observed_drain)
        for model in ("first", "second"):
            session = state.SessionState(
                model,
                str(tmp_path),
                model=model,
                effort="medium",
                model_type="execute",
                status="failed",
                turn_end=turn_state.TurnEnd.COMPLETED,
                error={"codexErrorInfo": "usageLimitExceeded"},
            )
            owner.sessions[session.session_id] = session
            session.touch()
            if model == "first":
                await wait_for_checkpoint(entered)
        tasks = tuple(owner._candidate_tasks)
        closing = asyncio.create_task(owner.close())

        def mark_closed(_task: asyncio.Task[None]) -> None:
            closed.set()

        closing.add_done_callback(mark_closed)
        try:
            await wait_for_checkpoint(draining)
            fail.set()
            await wait_for_checkpoint(later_entered)
            done, _ = await asyncio.wait({closing}, timeout=0.1)
            returned_early = bool(done)
        finally:
            fail.set()
            release.set()
            with pytest.raises(PermissionError, match="先行候補の保存失敗"):
                await closing
            await asyncio.gather(*tasks, return_exceptions=True)
        assert not returned_early
        assert later_finished.is_set()
        assert not wrote_after_close.is_set()
        assert all(task.done() for task in tasks)
        assert not owner._candidate_tasks


@pytest.mark.asyncio
async def test_scheduled_writer_drain_waits_for_other_worker_after_first_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = status_file.StatusFileWriter(
        {}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path, aggregate_seconds=0
    )
    await writer.activate_async()
    entered = asyncio.Event()
    later_entered = asyncio.Event()
    scheduled = asyncio.Event()
    draining = asyncio.Event()
    fail = threading.Event()
    release = threading.Event()
    later_finished = threading.Event()
    deactivated = threading.Event()
    wrote_after_close = threading.Event()
    loop = asyncio.get_running_loop()
    replace = atomic_file.os.replace
    calls = 0

    def replace_projection(source: str, target: pathlib.Path, **_kwargs: Any) -> None:
        nonlocal calls
        if target != writer.path:
            replace(source, target)
            return
        calls += 1
        if calls == 1:
            loop.call_soon_threadsafe(entered.set)
            assert fail.wait(5)
            raise RuntimeError("先行予約flushの保存失敗")
        loop.call_soon_threadsafe(later_entered.set)
        try:
            assert release.wait(5)
            if deactivated.is_set():
                wrote_after_close.set()
            replace(source, target)
        finally:
            later_finished.set()

    original_callback = writer._flush_scheduled
    original_drain = writer._drain

    def observed_callback() -> None:
        original_callback()
        if len(writer._flush_tasks) == 2:
            scheduled.set()

    async def observed_drain(*, cancel_scheduled: bool) -> None:
        draining.set()
        await original_drain(cancel_scheduled=cancel_scheduled)

    async def close_writer() -> None:
        try:
            await writer.drain(cancel_scheduled=True)
        finally:
            await wait_for_checkpoint(later_entered)
            writer.deactivate()
            deactivated.set()

    monkeypatch.setattr(atomic_file.os, "replace", replace_projection)
    monkeypatch.setattr(writer, "_flush_scheduled", observed_callback)
    monkeypatch.setattr(writer, "_drain", observed_drain)
    writer.schedule()
    await wait_for_checkpoint(entered)
    writer.schedule()
    await wait_for_checkpoint(scheduled)
    tasks = tuple(writer._flush_tasks)
    closing = asyncio.create_task(close_writer())
    try:
        await wait_for_checkpoint(draining)
        fail.set()
        await wait_for_checkpoint(later_entered)
        done, _ = await asyncio.wait({closing}, timeout=0.1)
        returned_early = bool(done)
    finally:
        fail.set()
        release.set()
        with pytest.raises(RuntimeError, match="先行予約flushの保存失敗"):
            await closing
        await asyncio.gather(*tasks, return_exceptions=True)
    assert not returned_early
    assert later_finished.is_set()
    assert not wrote_after_close.is_set()
    assert all(task.done() for task in tasks)
    assert not writer.path.exists()


@pytest.mark.asyncio
async def test_persistent_replace_failure_is_finite_and_preserves_saved_content(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "state.json"
    destination.write_text("旧本文", encoding="utf-8")
    attempts = 0

    def reject_replace(_source: str, _target: pathlib.Path, **_kwargs: Any) -> None:
        nonlocal attempts
        attempts += 1
        assert attempts < 100, "再試行が有限の予算内で終了しない"
        raise WindowsReplaceError(32)

    monkeypatch.setattr(atomic_file.os, "replace", reject_replace)
    monkeypatch.setattr(atomic_file.time, "sleep", lambda _delay: None)
    with pytest.raises(WindowsReplaceError):
        await asyncio.to_thread(atomic_file.atomic_write, destination, "新本文")
    assert 1 < attempts < 100
    assert destination.read_text(encoding="utf-8") == "旧本文"
    assert not list(tmp_path.glob(".state.json.*.tmp"))
