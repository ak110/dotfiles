"""所有移管と入れ子待機が同じ登録簿の世代を観測することを検証する。"""

import asyncio
import json
import multiprocessing
import os
import pathlib
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

# テストでは共通監視と所有移管の内部境界を直接検証する。
# pylint: disable=protected-access
from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, codex, responses, resume_waits, shared_sweep, state, wait_output_tracking
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    FakeCodexClient,
    _complete,
    _resume_test_manager,
    install_backend,
)
from agent_toolkit._testing.lock_probe import probe_lock

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.fixture(autouse=True)
def _restore_registry_owner() -> Generator[None]:
    """所有移管の模擬を、同一worker内の後続テストへ残さない。"""
    owner = session_registry.owner_server_instance()
    try:
        yield
    finally:
        session_registry.set_owner_server_instance(owner)


@pytest.mark.asyncio
async def test_retention_callback_keeps_new_owner_running_turn(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧managerの保持期限callbackは、資源解放中に再開した新ownerの登録を保持する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.set_owner_server_instance("old-owner")
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        previous = state.SessionState("retained", str(tmp_path), engine="codex", turn_seq=1, publish_registry=True)
        _complete(previous)
        manager.sessions[previous.session_id] = previous
        await previous.drain_registration()
        started, finish = asyncio.Event(), asyncio.Event()
        release = backend.release_session

        async def release_after_resume(session: state.SessionState, **_kwargs: Any) -> None:
            started.set()
            await finish.wait()
            await release(session)

        monkeypatch.setattr(backend, "release_session", release_after_resume)
        manager._expire_session(previous.session_id)
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            session_registry.set_owner_server_instance("new-owner")
            session_registry.publish(previous.session_id, terminal=False, turn_seq=2)
        finally:
            session_registry.set_owner_server_instance("old-owner")
            finish.set()
        await manager._wait_for_resource_release(previous.session_id)
        current = session_registry.resolve(previous.session_id)
        assert current.state is session_registry.Resolution.RUNNING
        assert current.server_instance == "new-owner"
        assert current.resume_info is not None and current.resume_info.turn_seq == 2


@pytest.mark.parametrize("registry_state", ["missing", "released", "unreadable", "terminal"])
@pytest.mark.asyncio
async def test_child_collected_during_registry_lookup_is_not_reintroduced(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, registry_state: str
) -> None:
    """照会のawait中に公開waitが回収した子を、照会前の写しから再追跡・再配送しない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    if registry_state == "released":
        session_registry.release("child", reason="stopped")
    elif registry_state == "terminal":
        session_registry.publish("child", terminal=True)
    elif registry_state == "unreadable":
        path = session_registry.registry_directory() / "child.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{", encoding="utf-8")
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        parent = state.SessionState("parent", str(tmp_path), engine="codex")
        parent.live_child_session_ids.add("child")
        resume_waits.begin_auto_resume_wait(parent, {"status": "completed", "agent_message": "待機表明", "error": None})
        manager.sessions[parent.session_id] = parent
        resolve = session_registry.resolve

        def resolve_and_collect(identifier: str, **kwargs: Any) -> session_registry.SessionResolution:
            resolution = resolve(identifier, **kwargs)
            wait_output_tracking.consume_agents_wait_output(parent, json.dumps({"session_id": "child", "status": "completed"}))
            return resolution

        monkeypatch.setattr(session_registry, "resolve", resolve_and_collect)
        await manager._advance_child_session_wait(parent)
        assert not parent.live_child_session_ids
        assert not parent.terminal_child_session_ids
        assert backend.send_calls == 0


@pytest.mark.asyncio
async def test_stop_keeps_new_owner_and_discards_stale_local_state(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """資源解放中に所有移管された停止は、新turnと結果を保ち現在状態の照会を案内する。"""
    async with _resume_test_manager(monkeypatch, tmp_path) as (manager, writer):
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        session = state.SessionState("changing-owner", str(tmp_path), engine="codex", turn_seq=1, publish_registry=True)
        _complete(session, message="前turnの結果")
        manager.sessions[session.session_id] = session
        await session.drain_registration()
        writer.flush()
        release_resources = backend.release_session

        async def transfer_owner(previous: state.SessionState, **_kwargs: Any) -> None:
            await release_resources(previous)
            session_registry.set_owner_server_instance("new-owner")
            session_registry.publish(previous.session_id, terminal=False, turn_seq=2)

        monkeypatch.setattr(backend, "release_session", transfer_owner)
        with pytest.raises(ActionableError) as changed:
            await manager.stop(session.session_id)
        resolution = session_registry.resolve(session.session_id)
        assert resolution.state is session_registry.Resolution.RUNNING
        assert resolution.resume_info is not None and resolution.resume_info.turn_seq == 2
        assert session.session_id not in manager.stopped_sessions
        assert "show" in changed.value.next_action
        assert writer.result_exists(session.session_id)


@pytest.mark.parametrize("engine", ["codex", "claude", "agy", "opencode"])
@pytest.mark.parametrize("reason", ["stopped", "retention_expired"])
def test_old_owner_cannot_release_or_republish_new_turn(
    tmp_path: pathlib.Path, engine: str, reason: session_registry.ReleaseReason
) -> None:
    """旧所有者の遅延解放と公開が、新所有者の実行中turnを書き換えない。"""
    session_registry.set_owner_server_instance("old-owner")
    session_registry.publish("child", terminal=True, engine=engine, turn_seq=1, state_root=tmp_path)
    session_registry.set_owner_server_instance("new-owner")
    session_registry.publish("child", terminal=False, engine=engine, turn_seq=2, state_root=tmp_path)
    path = session_registry.registry_directory(tmp_path) / "child.json"
    running = path.read_bytes()
    assert not session_registry.release("child", reason=reason, expected_owner=("old-owner", 1), state_root=tmp_path)
    session_registry.set_owner_server_instance("old-owner")
    session_registry.publish("child", terminal=True, engine=engine, turn_seq=1, state_root=tmp_path)
    assert path.read_bytes() == running
    resolution = session_registry.resolve("child", state_root=tmp_path)
    assert resolution.state is session_registry.Resolution.RUNNING
    assert resolution.server_instance == "new-owner"
    assert resolution.resume_info is not None
    assert resolution.resume_info.turn_seq == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude", "agy", "opencode"])
@pytest.mark.parametrize("missing", ["missing", "released", "unreadable"])
async def test_unobserved_child_stays_pending_until_terminal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str, missing: str
) -> None:
    """観測不能になった子は、実際の終了でだけ親を1回継続し最終結果を返す。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    directory = session_registry.registry_directory(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    if missing == "released":
        session_registry.release("child", reason="retention_expired")
    elif missing == "unreadable":
        (directory / "child.json").write_text("{", encoding="utf-8")
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        backend = FakeBackend(manager.sessions, engine)
        install_backend(manager, engine, backend)
        parent = state.SessionState("parent", str(tmp_path), engine=engine)
        parent.live_child_session_ids.add("child")
        resume_waits.begin_auto_resume_wait(parent, {"status": "completed", "agent_message": "待機表明", "error": None})
        manager.sessions[parent.session_id] = parent
        for _ in range(2):
            await manager._advance_child_session_wait(parent)
            assert parent.awaiting_auto_resume
            assert parent.live_child_session_ids == {"child"}
            assert not parent.result_available
            assert backend.send_calls == 0
        session_registry.publish("child", terminal=False, turn_seq=2)
        await manager._advance_child_session_wait(parent)
        assert backend.send_calls == 0
        session_registry.publish("child", terminal=True, turn_seq=2)
        await manager._advance_child_session_wait(parent)
        assert backend.send_calls == 1
        assert "child" in backend.prompts[0]
        _complete(parent, message="子を確認した最終結果")
        response = await manager.wait()
        assert response["agent_message"] == "子を確認した最終結果"
        assert "error" not in response
        await manager._advance_child_session_wait(parent)
        assert backend.send_calls == 1


def _lock_is_available(path: pathlib.Path) -> bool:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    with receiver, sender:
        process = context.Process(target=probe_lock, args=(str(path), sender))
        process.start()
        sender.close()
        try:
            for expected in ("ready", "result"):
                assert receiver.poll(10), f"ロック照会の{expected}が未到達: exitcode={process.exitcode}"
                try:
                    kind, value = receiver.recv()
                except EOFError as error:
                    process.join(10)
                    raise AssertionError(f"ロック照会が{expected}の前に終了: exitcode={process.exitcode}") from error
                assert kind == expected, f"ロック照会の{expected}で{kind}: {value}"
            assert isinstance(value, bool)
            process.join(10)
            assert process.exitcode == 0
            return value
        finally:
            if process.is_alive():
                process.terminate()
                process.join(10)
            if process.is_alive():
                process.kill()
                process.join(10)
            process.close()


@pytest.mark.parametrize("phase", ["ready", "result"])
def test_lock_probe_timeout_identifies_phase(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, phase: str) -> None:
    """起動未完了と、起動後の無応答を時間経過なしで識別する。"""
    receiver, sender = MagicMock(), MagicMock()
    receiver.poll.side_effect = [False] if phase == "ready" else [True, False]
    receiver.recv.return_value = ("ready", None)
    process = Mock(exitcode=None)
    process.is_alive.return_value = False
    context = Mock()
    context.Pipe.return_value = (receiver, sender)
    context.Process.return_value = process
    monkeypatch.setattr(multiprocessing, "get_context", Mock(return_value=context))
    with pytest.raises(AssertionError, match=f"{phase}が未到達"):
        _lock_is_available(tmp_path / "lock")
    sender.close.assert_called_once()
    process.close.assert_called_once()


def test_lock_probe_early_exit_reports_child_status(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """準備通知前の子の終了をロック取得不能として扱わない。"""
    receiver, sender = MagicMock(), MagicMock()
    receiver.poll.return_value = True
    receiver.recv.side_effect = EOFError
    process = Mock(exitcode=1)
    process.is_alive.return_value = False
    context = Mock()
    context.Pipe.return_value = (receiver, sender)
    context.Process.return_value = process
    monkeypatch.setattr(multiprocessing, "get_context", Mock(return_value=context))
    with pytest.raises(AssertionError, match="readyの前に終了: exitcode=1"):
        _lock_is_available(tmp_path / "lock")
    process.close.assert_called_once()


def test_lock_probe_io_error_is_not_contention(tmp_path: pathlib.Path) -> None:
    """子が報告したファイル到達不能を、排他保持中のFalseと区別する。"""
    with pytest.raises(AssertionError, match="resultでerror: FileNotFoundError"):
        _lock_is_available(tmp_path / "missing" / "lock")


def test_lock_probe_spawn_exception_before_ready(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """子のtargetへ不適合な引数を渡し、実際の起動後例外と終了コードを観測する。"""
    monkeypatch.setattr(f"{__name__}.probe_lock", os.stat)
    with pytest.raises(AssertionError, match="readyの前に終了: exitcode=1"):
        _lock_is_available(tmp_path / "lock")


@pytest.mark.parametrize("operation", ["publish", "release", "interrupted", "sweep"])
def test_registry_updates_hold_process_shared_lock(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """公開・解放・中断追記・掃引の更新時点が別OSプロセスとの排他区間にある。"""
    session_registry.publish("child", terminal=True, status="interrupted", turn_seq=1, state_root=tmp_path)
    path = session_registry.registry_directory(tmp_path) / "child.json"
    lock = path.parent / ".locks/child.lock"
    observed: list[bool] = []
    original_write = session_registry.atomic_write
    original_unlink = pathlib.Path.unlink

    def write(target: pathlib.Path, content: str, **kwargs: Any) -> None:
        observed.append(_lock_is_available(lock))
        original_write(target, content, **kwargs)

    def unlink(target: pathlib.Path, **kwargs: Any) -> None:
        if target == path:
            observed.append(_lock_is_available(lock))
        original_unlink(target, **kwargs)

    monkeypatch.setattr(session_registry, "atomic_write", write)
    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    if operation == "publish":
        session_registry.publish("child", terminal=False, turn_seq=2, state_root=tmp_path)
    elif operation == "release":
        session_registry.release("child", reason="stopped", state_root=tmp_path)
    elif operation == "interrupted":
        session_registry.record_interrupted_turn(
            "child", turn_seq=1, reason=session_registry.OWNER_SERVER_STOPPED, state_root=tmp_path
        )
    else:
        stamp = time.time() - 8 * 24 * 60 * 60
        os.utime(path, (stamp, stamp))
        shared_sweep.sweep_stale_shared_state(keep_root_session_id=None, state_root=tmp_path)
    assert observed == [False]
    assert _lock_is_available(lock)


def test_registry_sweep_failure_preserves_record_and_guides_operator(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """登録簿の排他取得に失敗しても原本を保持し、確認操作と報告先を通知する。"""
    session_registry.publish("child", terminal=True, status="interrupted", turn_seq=1, state_root=tmp_path)
    path = session_registry.registry_directory(tmp_path) / "child.json"

    def denied(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError("権限不足")

    monkeypatch.setattr(session_registry, "registry_lock", denied)
    shared_sweep.sweep_stale_shared_state(keep_root_session_id=None, state_root=tmp_path)
    assert path.is_file()
    assert str(path) in caplog.text and "権限不足" in caplog.text
    assert "使用状況と書込権限を確認" in caplog.text and "管理者へ報告" in caplog.text


@pytest.mark.asyncio
async def test_public_wait_holds_unknown_child_then_collects_resumed_result_once(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開CLIは観測不能の子を待つ本文を回収せず、再開した親の最終結果を1回だけ回収する。"""
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0)
    monkeypatch.setattr(agents_wait, "is_human_terminal", lambda: False)
    async with (
        _resume_test_manager(monkeypatch, tmp_path) as (manager, writer),
        codex.AppServerManager(
            manager.sessions, manager._condition, publish_registry=True, root_session_id="root-session"
        ) as backend,
    ):
        install_backend(manager, "codex", backend)
        client = FakeCodexClient()
        monkeypatch.setattr(backend, "_ensure_client", AsyncMock(return_value=client))
        parent = await backend.start("親の作業", str(tmp_path))
        parent.announced = True
        item = {
            "type": "mcpToolCall",
            "id": "reply-child",
            "server": "agents_server",
            "tool": "send_message",
            "arguments": {"session_id": "child", "prompt": "残作業を続ける"},
            "result": {
                "structuredContent": responses.public_send_message_response({"delivery": "reply_started", "label": "子"})
            },
        }
        await backend._handle_notification(
            {"method": "item/completed", "params": {"threadId": parent.session_id, "item": item}}
        )
        assert parent.live_child_session_ids == {"child"}

        async def complete(message: str) -> None:
            parent.agent_message = message
            await backend._handle_notification(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": parent.session_id,
                        "turn": {"id": parent.turn_id, "status": "completed", "error": None},
                    },
                }
            )

        await complete("待機表明")
        await manager._advance_child_session_wait(parent)
        await parent.drain_registration()
        writer.flush()
        with pytest.raises(SystemExit) as waiting:
            atk.main(["agents", "wait", "--root-session-id", "root-session"])
        assert waiting.value.code == 3
        assert "待機表明" not in capsys.readouterr().out
        session_registry.publish("child", terminal=True, turn_seq=2)
        await manager._advance_child_session_wait(parent)
        assert sum(method == "turn/start" for method, _params in client.requests) == 2
        await complete("残作業を完了した結果")
        await parent.drain_registration()
        writer.flush()
        with pytest.raises(SystemExit) as collected:
            atk.main(["agents", "wait", "--root-session-id", "root-session"])
        assert collected.value.code == 0
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
        result = next(row for row in rows if row.get("session_id") == parent.session_id)
        assert pathlib.Path(result["agent_message_path"]).read_text(encoding="utf-8") == "残作業を完了した結果"
        with pytest.raises(SystemExit) as consumed:
            atk.main(["agents", "wait", "--root-session-id", "root-session"])
        assert consumed.value.code == 3
        assert "agent_message_path" not in capsys.readouterr().out
        assert not writer.result_exists(parent.session_id)
        assert sum(method == "turn/start" for method, _params in client.requests) == 2


@pytest.mark.parametrize("kind", ["root状態", "共有状態ファイル", "コンパクション記録と対応するlock"])
def test_shared_sweep_failure_preserves_original_and_guides_operator(
    kind: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """同じ掃引の各回収失敗で対象、例外、次操作、報告先と原本保持を確認する。"""
    (tmp_path / "agents-server").mkdir()
    if kind == "root状態":
        path = tmp_path / "agents-server" / "old-root" / "original"
    elif kind == "共有状態ファイル":
        path = session_registry.registry_directory(tmp_path) / "original.txt"
    else:
        path = shared_sweep.compaction_metrics.record_directory(tmp_path) / "original.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("原本", encoding="utf-8")
    os.utime(path, (1, 1))

    def denied(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError("回収権限不足")

    if kind == "root状態":
        monkeypatch.setattr(shared_sweep.shutil, "rmtree", denied)
        target = path.parent
    else:
        monkeypatch.setattr(pathlib.Path, "unlink", denied)
        target = path
    shared_sweep.sweep_stale_shared_state(keep_root_session_id=None, state_root=tmp_path)
    assert path.read_text(encoding="utf-8") == "原本"
    assert str(target) in caplog.text and "回収権限不足" in caplog.text
    assert f"次の操作: {kind}の使用状況と書込権限を確認" in caplog.text
    assert "使用中なら保持する" in caplog.text
    assert "解消できない場合はこのログを添えて管理者へ報告する" in caplog.text
