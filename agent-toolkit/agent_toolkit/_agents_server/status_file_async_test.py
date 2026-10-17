"""非同期のファイル保存で、置換再試行と状態・終了の競合を検証する。"""

# 保存境界の故障注入と管理中taskの回収を確認する。
# pylint: disable=protected-access

import argparse
import asyncio
import json
import pathlib
import threading
from typing import Any

import pytest

from agent_toolkit._agents_server import (
    backends,
    codex,
    commands,
    manager,
    retained_results,
    shared_layout,
    shared_roots,
    state,
    status_file,
    unavailable_candidates,
    wait_output_tracking,
)
from agent_toolkit._atk import config as atk_config
from agent_toolkit._common import atomic_file, session_registry, state_paths
from agent_toolkit._testing.agents_server_support import FakeBackend, FakeCodexClient, install_backend

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_terminal_notification_finishes_io_before_result_becomes_available(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """終端通知の読取待機中にもwriterを動かし、終端結果と確定時刻の一貫性を保つ。"""
    session = state.SessionState("session", str(tmp_path), engine="codex", announced=True, status="running", turn_id="turn")
    sessions = {session.session_id: session}
    session.agents_wait_background_outputs.add(str(tmp_path / "output"))
    writer = status_file.StatusFileWriter(
        sessions, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path
    )
    backend = codex.AppServerManager(sessions)
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
        await asyncio.wait_for(entered.wait(), 5)
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


class _WindowsReplaceError(OSError):
    winerror: int

    def __init__(self, winerror: int) -> None:
        super().__init__("共有違反")
        self.winerror = winerror


class _StartedBackend(FakeBackend):
    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.model_output_observed = True
        session.publish_registry = True
        return session


class _UnavailableBackend(FakeBackend):
    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        del args, kwargs
        raise backends.StartCandidateUnavailable("exploreSandboxNamespaceDenied")


class _TerminalUnavailableBackend(_StartedBackend):
    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.status = "failed"
        session.turn_completed = True
        session.error = {"codexErrorInfo": "usageLimitExceeded"}
        session.touch()
        return session


def _block_named_replace(monkeypatch: pytest.MonkeyPatch, name: str) -> tuple[asyncio.Event, threading.Event]:
    """指定ファイルの置換直前でworkerを止め、loopの到達イベントを設定する。"""
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
    """実際の保存で共有違反の解消を待つ間もloopを止めず、同じ一時ファイルで6回試行し5回待機して終える。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    sessions: dict[str, state.SessionState] = {}
    writer = status_file.StatusFileWriter(
        sessions, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path, aggregate_seconds=0
    )
    owner = manager.AgentsServerManager(writer)
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
    loop_thread = threading.get_ident()
    completed = asyncio.Event()
    loop = asyncio.get_running_loop()

    def fail_then_replace(source: str, target: pathlib.Path, **_kwargs: Any) -> None:
        if target == destination:
            attempts.append(source)
            if len(attempts) <= 5:
                raise _WindowsReplaceError(winerror)
        replace(source, target)
        if target == destination:
            loop.call_soon_threadsafe(completed.set)

    def observe_sleep(delay: float, **_kwargs: Any) -> None:
        assert threading.get_ident() != loop_thread, "再試行待機がloop上で実行された"
        delays.append(delay)

    monkeypatch.setattr(atomic_file.os, "replace", fail_then_replace)
    monkeypatch.setattr(atomic_file.time, "sleep", observe_sleep)
    try:
        if route == "scheduled":
            writer.schedule()
            await asyncio.wait_for(completed.wait(), 5)
            await writer.drain()
        elif route == "heartbeat":
            monkeypatch.setattr(status_file, "HEARTBEAT_INTERVAL_SECONDS", 0.001)
            await asyncio.wait_for(completed.wait(), 5)
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
            session.turn_completed = True
            session.touch()
            if route == "terminal":
                await writer.flush_async()
            else:
                await writer.retain_result_async(state.SessionResumeState.from_session(session))
        elif route == "activate":
            await owner.activate_async()
        else:
            await owner.close()
        assert len(attempts) == 6
        assert len(set(attempts)) == 1
        assert delays == [0.01, 0.02, 0.04, 0.08, 0.16]
        assert not list(destination.parent.glob(f".{destination.name}.*.tmp"))
    finally:
        await owner.close()


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
        await asyncio.wait_for(entered.wait(), 5)
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
    """保存キャンセルはworkerの終端を待ち、close後に表示ファイルを再作成しない。"""
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    owner = manager.AgentsServerManager(writer)
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
        await asyncio.wait_for(entered.wait(), 5)
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


@pytest.mark.parametrize("race", ["collected", "new-turn"])
@pytest.mark.asyncio
async def test_result_changed_during_save_is_not_republished(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    """loop上で回収済みになった結果を、保存完了の反映で再公開しない。"""
    session = state.SessionState("session", str(tmp_path), announced=True, status="completed", turn_completed=True)
    session.touch()
    writer = status_file.StatusFileWriter(
        {"session": session}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path
    )
    await writer.activate_async()
    writer.delete_result("session", collector="prepare")
    entered, release = _block_named_replace(monkeypatch, "session.json")
    flush = asyncio.create_task(writer.flush_async())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        if race == "collected":
            session.result_delivered = True
        else:
            state.begin_reply(session)
    finally:
        release.set()
        await flush
        await writer.drain()
        writer.deactivate()
    assert not writer.result_exists("session")
    assert writer.result_state("session") == "unpublished"


@pytest.mark.asyncio
async def test_expiry_does_not_restore_result_collected_during_registration_release(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """登録解放の保存中にCLIが回収した結果を、期限解放の保持処理が再公開しない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session = state.SessionState(
        "session", str(tmp_path), announced=True, publish_registry=True, status="completed", turn_completed=True
    )
    session.touch()
    writer = status_file.StatusFileWriter(
        {"session": session}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path
    )
    owner = manager.AgentsServerManager(writer)
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
        await asyncio.wait_for(entered.wait(), 5)
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
    """キャンセル中に成功した保存をloopで記録し、CLI回収後のcloseで再公開しない。"""
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    owner = manager.AgentsServerManager(writer)
    await writer.activate_async()
    session = state.SessionState("session", str(tmp_path), status="completed", turn_completed=True, announced=True)
    session.touch()
    owner.sessions[session.session_id] = session
    entered, release = _block_named_replace(monkeypatch, "session.json")
    flush = asyncio.create_task(writer.flush_async())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        flush.cancel()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await flush
    assert writer.result_state(session.session_id) == "published"
    result, error = retained_results.take_result("root", "session", "root.json", collector="agents-wait", state_root=tmp_path)
    assert result is not None and error is None
    await owner.close()
    assert not writer.result_exists(session.session_id)


@pytest.mark.asyncio
async def test_kill_waits_for_terminal_save_started_after_initial_drain(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最初のdrain後に保存が始まっても、killはworkerを回収してから結果を削除して返す。"""
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    owner = manager.AgentsServerManager(writer)
    install_backend(owner, "codex", FakeBackend(owner.sessions, "codex"))
    await writer.activate_async()
    session = state.SessionState("session", str(tmp_path), status="completed", turn_completed=True, announced=True)
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
            await asyncio.wait_for(entered.wait(), 5)

    monkeypatch.setattr(writer, "drain", save_after_drain)
    killing = asyncio.create_task(owner.kill(session.session_id, timeout=0))
    try:
        await asyncio.wait_for(entered.wait(), 5)
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


@pytest.mark.parametrize("winerror", [5, 32])
@pytest.mark.parametrize("route", ["alias-start", "alias-resume", "candidate", "wait-target", "release", "config"])
@pytest.mark.asyncio
async def test_auxiliary_save_retry_runs_outside_loop(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, winerror: int, route: str
) -> None:
    """開始・再開・候補切替・保存失敗後の待機登録・stopも再試行待機をloopへ持ち込まない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    owner = manager.AgentsServerManager(writer)
    install_backend(owner, "claude", _StartedBackend(owner.sessions, "claude"))
    await owner.activate_async()
    direct = codex.AppServerManager(root_session_id="root")
    fake_client = FakeCodexClient()

    async def ensure_client() -> FakeCodexClient:
        return fake_client

    monkeypatch.setattr(direct, "_ensure_client", ensure_client)
    if route == "alias-resume":
        await direct.start("処理", str(tmp_path), "gpt-6-sol", "medium")
    if route == "release":
        started = await owner.start("claude:test/medium", "処理", str(tmp_path))
        session = owner.sessions[started["session_id"]]
        session.status = "completed"
        session.turn_completed = True
        session.touch()
        await session.drain_registration()
    config_path = tmp_path / "legacy-config.json"
    if route == "config":
        config_path.write_text(json.dumps({"high_tier_model": "claude:test/medium"}), encoding="utf-8")
        monkeypatch.setattr(atk_config, "_config_file_path", lambda: config_path)
    replace = atomic_file.os.replace
    attempts: list[str] = []
    delays: list[float] = []
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
            if len(attempts) <= 5:
                raise _WindowsReplaceError(winerror)
        replace(source, target)

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
            install_backend(owner, "codex", _UnavailableBackend(owner.sessions, "codex"))
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
            started = await owner.start("high_tier", "処理", str(tmp_path))
            assert started["session_id"] == "claude-session"
            assert "high_tier_model" not in json.loads(config_path.read_text(encoding="utf-8"))
        assert len(attempts) == 6
        assert len(set(attempts)) == 1
        assert delays == [0.01, 0.02, 0.04, 0.08, 0.16]
    finally:
        monkeypatch.setattr(status_file, "atomic_write", original_write)
        await direct.close()
        await owner.close()


@pytest.mark.asyncio
async def test_public_responses_finish_registration_before_cli_observation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """start・新turnへのsend_message・stop・closeが返った登録をCLIのshowで直ちに読める。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    owner = manager.AgentsServerManager(None)
    install_backend(owner, "claude", _StartedBackend(owner.sessions, "claude"))

    def shown() -> dict[str, Any]:
        assert commands.dispatch(argparse.Namespace(agents_subcommand="show", session_id="claude-session"), environment={}) == 0
        return json.loads(capsys.readouterr().out)

    try:
        started = await owner.start("claude:test/medium", "処理", str(tmp_path))
        assert started["status"] == "running"
        assert shown()["status"] == "running"
        session = owner.sessions[started["session_id"]]
        session.status = "completed"
        session.turn_completed = True
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
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_candidate_save_failure_is_reported_by_started_response(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """候補記録の非同期保存失敗も起動応答の保存警告へ残し、ログだけで成功扱いにしない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    owner = manager.AgentsServerManager(None)
    install_backend(owner, "codex", _TerminalUnavailableBackend(owner.sessions, "codex"))
    replace = atomic_file.os.replace

    def reject_candidate(source: str, destination: pathlib.Path, **_kwargs: Any) -> None:
        if destination == unavailable_candidates.unavailable_candidates_path(tmp_path):
            raise PermissionError("候補記録を保存できない")
        replace(source, destination)

    monkeypatch.setattr(atomic_file.os, "replace", reject_candidate)
    try:
        response = await owner.start("codex:gpt-6-sol/medium", "処理", str(tmp_path))
        assert response["session_id"] == "codex-session"
        assert response["status"] == "failed"
        assert any(
            warning["scope"] == "shared-state" and "候補記録を保存できない" in warning["message"]
            for warning in response["persistence_warnings"]
        )
    finally:
        await owner.close()


@pytest.mark.parametrize("terminal_save_fails", [False, True])
@pytest.mark.asyncio
async def test_close_propagates_candidate_failure_without_masking_terminal_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, terminal_save_fails: bool
) -> None:
    """closeは候補保存の失敗を成功へ変えず、同時に終端保存が失敗した場合は元の例外を保持する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    owner = manager.AgentsServerManager(writer)
    install_backend(owner, "codex", FakeBackend(owner.sessions, "codex"))
    await writer.activate_async()
    session = state.SessionState(
        "session",
        str(tmp_path),
        model="gpt-6-sol",
        effort="medium",
        model_type="high_tier",
        publish_registry=True,
        announced=True,
    )
    owner.sessions[session.session_id] = session
    replace = atomic_file.os.replace

    def reject(source: str, destination: pathlib.Path, **_kwargs: Any) -> None:
        if destination == unavailable_candidates.unavailable_candidates_path(tmp_path):
            raise PermissionError("候補記録を保存できない")
        if terminal_save_fails and destination.parent.name == "results":
            raise PermissionError("終端結果を保存できない")
        replace(source, destination)

    monkeypatch.setattr(atomic_file.os, "replace", reject)
    session.status = "failed"
    session.turn_completed = True
    session.error = {"codexErrorInfo": "usageLimitExceeded"}
    session.touch()
    expected = "終端結果を保存できない" if terminal_save_fails else "候補記録を保存できない"
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
    """close自体のキャンセルも、workerと書込主体の後始末が終わってから返す。"""
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path)
    owner = manager.AgentsServerManager(writer)
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
        await asyncio.wait_for(entered.wait(), 5)
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
    """期限解放の保存失敗はbackend解放後も保持し、waitまたはcloseで成功へ変換しない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session = state.SessionState(
        "session", str(tmp_path), announced=True, publish_registry=True, status="completed", turn_completed=True
    )
    session.touch()
    writer = status_file.StatusFileWriter(
        {"session": session}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path
    )
    owner = manager.AgentsServerManager(writer)
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
    """先行候補の保存失敗を返す前に、後続の実保存workerも終了し、close後に書き込まない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    owner = manager.AgentsServerManager(None)
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
            model_type="high_tier",
            status="failed",
            turn_completed=True,
            error={"codexErrorInfo": "usageLimitExceeded"},
        )
        owner.sessions[session.session_id] = session
        session.touch()
        if model == "first":
            await asyncio.wait_for(entered.wait(), 5)
    tasks = tuple(owner._candidate_tasks)
    closing = asyncio.create_task(owner.close())

    def mark_closed(_task: asyncio.Task[None]) -> None:
        closed.set()

    closing.add_done_callback(mark_closed)
    try:
        await asyncio.wait_for(draining.wait(), 5)
        fail.set()
        await asyncio.wait_for(later_entered.wait(), 5)
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
    """予約flushの先行失敗後も後続workerを回収してからdrainを返し、deactivate後の置換を残さない。"""
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
            await asyncio.wait_for(later_entered.wait(), 5)
            writer.deactivate()
            deactivated.set()

    monkeypatch.setattr(atomic_file.os, "replace", replace_projection)
    monkeypatch.setattr(writer, "_flush_scheduled", observed_callback)
    monkeypatch.setattr(writer, "_drain", observed_drain)
    writer.schedule()
    await asyncio.wait_for(entered.wait(), 5)
    writer.schedule()
    await asyncio.wait_for(scheduled.wait(), 5)
    tasks = tuple(writer._flush_tasks)
    closing = asyncio.create_task(close_writer())
    try:
        await asyncio.wait_for(draining.wait(), 5)
        fail.set()
        await asyncio.wait_for(later_entered.wait(), 5)
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
