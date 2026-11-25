"""非同期のファイル保存で、置換再試行と状態・終了の競合を検証する。"""

# 保存境界の故障注入と管理中taskの回収を確認する。
# pylint: disable=protected-access

import asyncio
import pathlib
import threading
from typing import Any

import pytest

from agent_toolkit._agents_server import (
    codex,
    manager,
    retained_results,
    shared_roots,
    state,
    status_file,
    turn_state,
    wait_output_tracking,
)
from agent_toolkit._common import atomic_file, session_registry, state_paths
from agent_toolkit._testing.agents_server_support import FakeBackend, _complete, install_backend
from agent_toolkit._testing.status_file_checkpoints import (
    WindowsReplaceError,
    wait_for_checkpoint,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_terminal_notification_finishes_io_before_result_becomes_available(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """終了通知の読取待機中にもwriterを動かし、終了時の結果と確定時刻の一貫性を保つ。"""
    session = state.SessionState("session", str(tmp_path), engine="codex", announced=True, status="running", turn_id="turn")
    sessions = {session.session_id: session}
    session.agent_message = "状態: completed"
    session.agents_wait_background_outputs.add(str(tmp_path / "output"))
    writer = status_file.StatusFileWriter(
        sessions, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path
    )
    async with codex.AppServerManager(sessions) as backend:
        await writer.activate_async()
        loop = asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()

        def read_outputs(_paths: tuple[str, ...], **_kwargs: Any) -> set[str]:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
            return set()

        monkeypatch.setattr(wait_output_tracking, "_read_background_outputs", read_outputs)
        completed = asyncio.create_task(
            backend._handle_notification(
                {
                    "method": "turn/completed",
                    "params": {"threadId": "session", "turn": {"id": "turn", "status": "completed"}},
                }
            )
        )
        try:
            await wait_for_checkpoint(entered)
            assert not session.result_available
            await writer.flush_async()
            assert not writer.result_exists("session")
        finally:
            release.set()
            await completed
            await writer.flush_async()
            await backend.close()
            await writer.deactivate_async()
        result = writer.read_result("session")
        assert result is not None and result["status"] == "completed" and result["finalized_at"]


def _block_named_replace(monkeypatch: pytest.MonkeyPatch, name: str) -> tuple[asyncio.Event, threading.Event]:
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    replace = atomic_file.os.replace

    def block_replace(source: str, destination: pathlib.Path, **_kwargs: Any) -> None:
        if destination.name == name:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
        replace(source, destination)

    monkeypatch.setattr(atomic_file.os, "replace", block_replace)
    return entered, release


@pytest.mark.parametrize("winerror", [5, 32])
@pytest.mark.parametrize("route", ["scheduled", "heartbeat", "registration", "terminal", "activate", "close", "retain"])
@pytest.mark.asyncio
async def test_replace_retry_runs_outside_loop(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, winerror: int, route: str
) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    sessions: dict[str, state.SessionState] = {}
    writer = status_file.StatusFileWriter(
        sessions, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path, aggregate_seconds=0
    )
    async with manager.AgentsServerManager(writer) as owner:
        backend = FakeBackend(sessions, "codex")
        install_backend(owner, "codex", backend)
        session: state.SessionState | None = None
        if route != "activate":
            await owner.activate_async()
        if route in {"registration", "terminal", "close", "retain"}:
            session = state.SessionState(
                "session", str(tmp_path), publish_registry=route in {"registration", "close"}, announced=True, turn_seq=1
            )
            sessions[session.session_id] = session
        destination = writer.path
        if route == "registration":
            destination = session_registry.registry_directory(tmp_path) / "session.json"
        elif route in {"terminal", "retain"}:
            destination = tmp_path / "agents-server" / "root" / "results" / "session.json"
        replace = atomic_file.os.replace
        attempts: list[str] = []
        delays: list[float] = []
        saved: dict[pathlib.Path, bytes] = {}
        loop_thread = threading.get_ident()
        completed = asyncio.Event()
        loop = asyncio.get_running_loop()

        def fail_then_replace(source: str, target: pathlib.Path, **_kwargs: Any) -> None:
            if target == destination:
                attempts.append(source)
                if len(attempts) == 1:
                    raise WindowsReplaceError(winerror)
            content = pathlib.Path(source).read_bytes()
            replace(source, target)
            if target == destination:
                saved[target] = content
                loop.call_soon_threadsafe(completed.set)

        def observe_sleep(delay: float, **_kwargs: Any) -> None:
            assert threading.get_ident() != loop_thread, "再試行待機がloop上で実行された"
            delays.append(delay)

        monkeypatch.setattr(atomic_file.os, "replace", fail_then_replace)
        monkeypatch.setattr(atomic_file.time, "sleep", observe_sleep)
        if route == "scheduled":
            writer.schedule()
            await wait_for_checkpoint(completed)
            await writer.drain()
        elif route == "heartbeat":
            monkeypatch.setattr(status_file, "HEARTBEAT_INTERVAL_SECONDS", 0.001)
            await wait_for_checkpoint(completed)
            assert owner._heartbeat_task is not None
            owner._heartbeat_task.cancel()
            await asyncio.gather(owner._heartbeat_task, return_exceptions=True)
            await writer.drain(cancel_scheduled=True)
        elif route == "registration":
            assert session is not None
            session.touch()
            await session.drain_registration()
        elif route in {"terminal", "retain"}:
            assert session is not None
            session.status = "completed"
            session.record_turn_completed()
            session.touch()
            if route == "terminal":
                await writer.flush_async()
            else:
                await writer.retain_result_async(state.SessionResumeState.from_session(session))
        elif route == "activate":
            await owner.activate_async()
        else:
            await owner.close()
        assert 1 < len(attempts) < 20
        assert delays and all(delay > 0 for delay in delays)
        if route == "close":
            assert not destination.exists()
            retained = retained_results.read_retained_result("root", "session", tmp_path)
            assert retained is not None and retained["status"] == "interrupted"
        else:
            assert destination.read_bytes() == saved[destination]
        assert not list(destination.parent.glob(f".{destination.name}.*.tmp"))


@pytest.mark.asyncio
async def test_registration_updates_during_retry_do_not_mark_new_turn_saved(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧snapshotの成功は旧値として覚え、待機中に始めた新turnを続けて保存する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session = state.SessionState("session", str(tmp_path), publish_registry=True, turn_seq=1)
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    replace = atomic_file.os.replace
    first = True

    def block_replace(source: str, destination: pathlib.Path, **_kwargs: Any) -> None:
        nonlocal first
        if first:
            first = False
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
        replace(source, destination)

    monkeypatch.setattr(atomic_file.os, "replace", block_replace)
    session.touch()
    try:
        await wait_for_checkpoint(entered)
        state.begin_reply(session)
    finally:
        release.set()
        await session.drain_registration()
    record = session_registry.resolve(session.session_id, state_root=tmp_path)
    assert record.resume_info is not None and record.resume_info.turn_seq == 2
    assert session._published_registry_turn_seq == 2


@pytest.mark.parametrize("worker_fails", [False, True])
@pytest.mark.asyncio
async def test_close_after_cancelled_flush_waits_for_worker_and_removes_projection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, worker_fails: bool
) -> None:
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    async with manager.AgentsServerManager(writer) as owner:
        await owner.activate_async()
        entered = asyncio.Event()
        release = threading.Event()
        finished = threading.Event()
        loop = asyncio.get_running_loop()
        replace = atomic_file.os.replace
        first = True

        def block_replace(source: str, destination: pathlib.Path, **_kwargs: Any) -> None:
            nonlocal first
            if first:
                first = False
                loop.call_soon_threadsafe(entered.set)
                try:
                    assert release.wait(5)
                    if worker_fails:
                        raise PermissionError("キャンセル中の保存失敗")
                    replace(source, destination)
                finally:
                    finished.set()
            else:
                replace(source, destination)

        monkeypatch.setattr(atomic_file.os, "replace", block_replace)
        flush = asyncio.create_task(writer.flush_async())
        closing: asyncio.Task[None] | None = None
        try:
            await wait_for_checkpoint(entered)
            flush.cancel()
            closing = asyncio.create_task(owner.close())
            await asyncio.sleep(0)
            assert not flush.done()
            assert not closing.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await flush
            if closing is not None:
                await closing
            else:
                await owner.close()
        assert finished.is_set()
        assert not writer.path.exists()
        assert not list(writer.path.parent.glob(".root.json.*.tmp"))


@pytest.mark.asyncio
async def test_collecting_one_end_preserves_a_later_end_with_the_same_turn_number(tmp_path, monkeypatch):
    session = state.SessionState("child", str(tmp_path), announced=True, turn_seq=1)
    writer = status_file.StatusFileWriter(
        {"child": session}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path
    )
    _complete(session, message="先の終了")
    first_end = session.finalized_at
    await writer.activate_async()
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    stash = tmp_path / "run" / "results" / "child.json"
    write = retained_results.atomic_write

    def save(path, *args, **kwargs):
        write(path, *args, **kwargs)
        if path == stash:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)

    monkeypatch.setattr(retained_results, "atomic_write", save)
    taking = asyncio.create_task(
        asyncio.to_thread(
            retained_results.take_result, "root", "child", "root.json", state_root=tmp_path, collector="cli", stash_path=stash
        )
    )
    try:
        await wait_for_checkpoint(entered)
        session.finalized_at = None
        _complete(session, message="後の終了")
        assert session.finalized_at != first_end
        await writer.flush_async()
    finally:
        release.set()
        first, error = await taking
        await writer.drain(cancel_scheduled=True)
    assert error is None and first is not None and first["agent_message"] == "先の終了"
    second, error = await writer.take_result_async(
        "child", collector="wait", expected_turn_seq=1, expected_finalized_at=session.finalized_at
    )
    assert error is None and second is not None and second["agent_message"] == "後の終了"
    await writer.flush_async()
    assert session.result_delivered
    assert not writer.result_exists("child")
    await writer.deactivate_async()


@pytest.mark.parametrize("race", ["collected", "new-turn"])
@pytest.mark.asyncio
async def test_result_changed_during_save_is_not_republished(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    session = state.SessionState(
        "session", str(tmp_path), announced=True, status="completed", turn_end=turn_state.TurnEnd.COMPLETED
    )
    session.touch()
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    await writer.activate_async()
    writer.sessions["session"] = session
    entered, release = _block_named_replace(monkeypatch, "session.json")
    flush = asyncio.create_task(writer.flush_async())
    try:
        await wait_for_checkpoint(entered)
        if race == "collected":
            session.result_delivered = True
        else:
            state.begin_reply(session)
    finally:
        release.set()
        await flush
        await writer.drain()
        writer.deactivate()
    assert writer.result_exists("session") == (race == "new-turn")
    assert writer.result_state("session") == "unpublished"


@pytest.mark.asyncio
async def test_expiry_does_not_restore_result_collected_during_registration_release(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登録解放の保存中にCLIが回収した結果を、期限解放の保持処理が再公開しない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session = state.SessionState("session", str(tmp_path), announced=True, publish_registry=True)
    _complete(session, message="")
    writer = status_file.StatusFileWriter(
        {"session": session}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path
    )
    async with manager.AgentsServerManager(writer) as owner:
        install_backend(owner, "codex", FakeBackend(owner.sessions, "codex"))
        await writer.activate_async()
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        replace = atomic_file.os.replace
        registry_path = session_registry.registry_directory(tmp_path) / "session.json"

        def block_replace(source: str, destination: pathlib.Path, **_kwargs: Any) -> None:
            if destination == registry_path:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(5)
            replace(source, destination)

        monkeypatch.setattr(atomic_file.os, "replace", block_replace)
        owner._expire_session(session.session_id)
        try:
            await wait_for_checkpoint(entered)
            result, error = retained_results.take_result(
                "root", "session", "root.json", collector="agents-wait", state_root=tmp_path
            )
            assert result is not None and error is None
        finally:
            release.set()
            await owner._wait_for_resource_release(session.session_id)
            await owner.close()
        assert not writer.result_exists(session.session_id)


@pytest.mark.asyncio
async def test_cancelled_terminal_save_reflects_success_before_next_flush(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    async with manager.AgentsServerManager(writer) as owner:
        await writer.activate_async()
        session = state.SessionState(
            "session", str(tmp_path), status="completed", turn_end=turn_state.TurnEnd.COMPLETED, announced=True
        )
        session.touch()
        owner.sessions[session.session_id] = session
        entered, release = _block_named_replace(monkeypatch, "session.json")
        flush = asyncio.create_task(writer.flush_async())
        try:
            await wait_for_checkpoint(entered)
            flush.cancel()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await flush
        assert writer.result_state(session.session_id) == "published"
        result, error = retained_results.take_result(
            "root", "session", "root.json", collector="agents-wait", state_root=tmp_path
        )
        assert result is not None and error is None
        await owner.close()
        assert not writer.result_exists(session.session_id)


@pytest.mark.asyncio
async def test_kill_waits_for_terminal_save_started_after_initial_drain(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    async with manager.AgentsServerManager(writer) as owner:
        install_backend(owner, "codex", FakeBackend(owner.sessions, "codex"))
        await writer.activate_async()
        session = state.SessionState(
            "session", str(tmp_path), status="completed", turn_end=turn_state.TurnEnd.COMPLETED, announced=True
        )
        session.agent_message = "回収する本文"
        session.touch()
        owner.sessions[session.session_id] = session
        entered, release = _block_named_replace(monkeypatch, "session.json")
        original_drain = writer.drain
        flush: asyncio.Task[None] | None = None

        async def save_after_drain(*, cancel_scheduled: bool = False) -> None:
            nonlocal flush
            await original_drain(cancel_scheduled=cancel_scheduled)
            if flush is None:
                flush = asyncio.create_task(writer.flush_async())
                await wait_for_checkpoint(entered)

        monkeypatch.setattr(writer, "drain", save_after_drain)
        killing = asyncio.create_task(owner.kill(session.session_id, timeout=0))
        try:
            await wait_for_checkpoint(entered)
            done, _ = await asyncio.wait({killing}, timeout=0.1)
            returned_early = bool(done)
        finally:
            release.set()
            response = await killing
            assert flush is not None
            await flush
            await owner.close()
        assert not returned_early
        assert response["agent_message"] == "回収する本文"
        assert session.result_delivered
        assert not writer.result_exists(session.session_id)
        assert flush.done()
