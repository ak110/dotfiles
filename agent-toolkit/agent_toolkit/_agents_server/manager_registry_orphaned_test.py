"""`_agents_server/manager_registry.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import (
    agents_wait,
    logging_config,
    resume_waits,
    retained_results,
    shared_roots,
    state,
    status_file,
)
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    ThreadReadBackend,
    _complete,
    _install_backend,
    _manager_with_fake,
    _publish_orphaned_codex,
    _show_through_tool,
    exited_process_identity,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude", "agy"])
async def test_close_publishes_running_session_as_interrupted_for_restart(
    engine: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """実行中sessionを持つマネージャーの停止で登録簿と結果ファイルへ終了を公開し、別のマネージャーが再開できる。

    停止時に公開しないと登録簿が`running`のまま残り、再起動後の`show`・`send_message`が実行中の可能性として拒否し続け、
    委譲元の`atk agents wait`も待機上限での終了を繰り返す。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root-session", "root.json", None))
    async with server_manager.AgentsServerManager(writer) as manager:
        _install_backend(manager, engine, FakeBackend(manager.sessions, engine))
        writer.activate()
        session_id = f"{engine}-closing"
        running = state.SessionState(session_id, str(tmp_path), engine=engine, turn_seq=1, publish_registry=True)
        state.initialize_turn(running)
        running.turn_id = "turn-9"
        running.announced = True
        manager.sessions[session_id] = running
        running.touch()
        await running.drain_registration()
        assert session_registry.resolve(session_id).state is session_registry.Resolution.RUNNING

        await manager.close()

        resolution = session_registry.resolve(session_id)
        assert resolution.state is session_registry.Resolution.TERMINAL
        assert resolution.resume_info is not None and resolution.resume_info.status == "interrupted"
        # 再起動後の引き継ぎが`thread/read`の元turnと比べられるよう、Codexのturn識別子も登録簿へ残す。
        assert resolution.resume_info.turn_id == "turn-9"
        assert retained_results.read_retained_result("root-session", session_id, tmp_path) is not None
        assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0

        async with _manager_with_fake(engine) as (restarted, backend):
            assert (await _show_through_tool(monkeypatch, restarted, session_id))["status"] == "interrupted"
            response = await restarted.send_message(session_id, "続行")
            assert response["delivery"] == "reply_started"
            assert backend.resume_calls == [session_id]
            await restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("turn_id", "turns", "status"),
    [
        ("turn-2", [("turn-1", "completed"), ("turn-2", "interrupted")], "interrupted"),
        ("turn-1", [("turn-1", "completed")], "completed"),
        (None, [("turn-1", "completed"), ("turn-2", "interrupted")], "running"),
    ],
    ids=["recorded-turn", "single-turn", "legacy-record-all-terminal"],
)
async def test_orphaned_codex_record_is_taken_over_when_turn_has_ended(
    turn_id: str | None,
    turns: list[tuple[str, str]],
    status: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """所有者が不在で`thread/read`が元turnの終了を示す残存記録は、`show`と`send_message`で終了として復元する。"""
    session_id = "01a0f920-43f0-7cb3-b1e6-e06cd9bb7cad"
    _publish_orphaned_codex(monkeypatch, tmp_path, session_id, turn_id)
    async with server_manager.AgentsServerManager(None) as manager:
        backend = ThreadReadBackend(manager.sessions, turns)
        _install_backend(manager, "codex", backend)

        if turn_id is None:
            with pytest.raises(ActionableError, match="still running"):
                await _show_through_tool(monkeypatch, manager, session_id)
            resolution = session_registry.resolve(session_id)
            assert resolution.state is session_registry.Resolution.RUNNING
            assert not backend.resume_calls
            return
        assert (await _show_through_tool(monkeypatch, manager, session_id))["status"] == status
        resolution = session_registry.resolve(session_id)
        assert resolution.state is session_registry.Resolution.TERMINAL
        assert resolution.resume_info is not None and resolution.resume_info.status == status

        response = await manager.send_message(session_id, "続行")
        assert response["delivery"] == "reply_started"
        assert backend.resume_calls == [session_id]
        await manager.close()


_ORPHANED_SESSION_ID = "a0278936-c56f-4a22-9f34-2a5683019a57"


def _publish_record_with_processes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    engine: str,
    processes: tuple[session_registry.ProcessIdentity, ...],
) -> None:
    """所有するagents_serverと委譲先CLIの識別を持つ`running`の記録を、停止処理を経ずに残った形で保存する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(
        _ORPHANED_SESSION_ID,
        state=session_registry.PublicationState.PENDING_RUNNING,
        engine=engine,
        cwd=str(tmp_path),
        turn_seq=2,
        processes=processes,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude", "codex", "agy"])
async def test_record_whose_owner_and_executor_exited_is_interrupted_on_every_path(
    engine: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """所有プロセスと委譲先CLIがともに終了した`running`の記録は、次の照会のいずれでも`interrupted`の終了として扱う。

    `atk agents show`は中断を返し、自動再開は終了した子として扱う。
    MCPの`show`は終了の理由を`error`で返し、`send_message`は拒否せず通常の復元へ進む。
    """
    _publish_record_with_processes(monkeypatch, tmp_path, engine, (exited_process_identity(), exited_process_identity()))
    record = session_registry.registry_directory() / f"{_ORPHANED_SESSION_ID}.json"
    assert json.loads(record.read_text(encoding="utf-8"))["terminal"] is False

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", _ORPHANED_SESSION_ID])
    shown_by_cli = json.loads(capsys.readouterr().out)
    assert shown_by_cli["status"] == "interrupted"
    assert shown_by_cli["error"]["interruptionReason"] == session_registry.OWNER_PROCESSES_EXITED
    # 照会側の解決は登録簿を書き換えない。
    assert json.loads(record.read_text(encoding="utf-8"))["terminal"] is False

    async with _manager_with_fake("claude") as (parent_manager, parent_backend):
        parent = state.SessionState("parent-session", str(tmp_path), engine="claude")
        parent.live_child_session_ids.add(_ORPHANED_SESSION_ID)
        resume_waits.begin_auto_resume_wait(parent, {"status": "completed", "agent_message": "保留本文", "error": None})
        parent_manager.sessions[parent.session_id] = parent
        await parent_manager._advance_child_session_wait(parent)  # pylint: disable=protected-access
        assert _ORPHANED_SESSION_ID not in parent.live_child_session_ids
        assert any(f"終了したsession/thread: {_ORPHANED_SESSION_ID}" in prompt for prompt in parent_backend.prompts)
        await parent_manager.close()

        async with server_manager.AgentsServerManager(None) as manager:
            backend = ThreadReadBackend(manager.sessions, None) if engine == "codex" else FakeBackend(manager.sessions, engine)
            _install_backend(manager, engine, backend)

            shown = await _show_through_tool(monkeypatch, manager, _ORPHANED_SESSION_ID)
            assert shown["status"] == "interrupted"
            assert shown["error"]["interruptionReason"] == session_registry.OWNER_PROCESSES_EXITED
            published = json.loads(record.read_text(encoding="utf-8"))
            assert published["terminal"] is True and published["status"] == "interrupted"
            assert published["interruption"] == session_registry.OWNER_PROCESSES_EXITED

            response = await manager.send_message(_ORPHANED_SESSION_ID, "続行")
            assert response["delivery"] == "reply_started"
            assert backend.resume_calls == [_ORPHANED_SESSION_ID]
            if isinstance(backend, ThreadReadBackend):
                assert not backend.read_calls
            await manager.close()


@pytest.mark.asyncio
async def test_record_with_a_live_recorded_process_keeps_running_on_every_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """記録したプロセスが1つでも存在する記録は、同じ照会のいずれでも実行中として扱う。

    所有サーバーが終了し委譲先CLIが生存する記録は、所有サーバーを失った実行中として案内を返し、追送は再開せずに拒否する。
    """
    owner = session_registry.owner_process()
    assert owner is not None
    _publish_record_with_processes(monkeypatch, tmp_path, "claude", (exited_process_identity(), owner))

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", _ORPHANED_SESSION_ID])
    shown_by_cli = json.loads(capsys.readouterr().out)
    assert shown_by_cli["status"] == "running"
    assert shown_by_cli["ownership"] == "owner_lost"

    async with _manager_with_fake("claude") as (parent_manager, parent_backend):
        parent = state.SessionState("parent-session", str(tmp_path), engine="claude")
        parent.live_child_session_ids.add(_ORPHANED_SESSION_ID)
        resume_waits.begin_auto_resume_wait(parent, {"status": "completed", "agent_message": "保留本文", "error": None})
        parent_manager.sessions[parent.session_id] = parent
        await parent_manager._advance_child_session_wait(parent)  # pylint: disable=protected-access
        assert _ORPHANED_SESSION_ID in parent.live_child_session_ids
        assert not parent_backend.prompts
        await parent_manager.close()

        async with server_manager.AgentsServerManager(None) as manager:
            backend = FakeBackend(manager.sessions, "claude")
            _install_backend(manager, "claude", backend)
            shown = await _show_through_tool(monkeypatch, manager, _ORPHANED_SESSION_ID)
            assert shown["status"] == "running"
            assert shown["ownership"] == "owner_lost"
            with pytest.raises(ValueError, match="error.recovery=owner_lost"):
                await manager.send_message(_ORPHANED_SESSION_ID, "続行")
            assert session_registry.resolve(_ORPHANED_SESSION_ID).state is session_registry.Resolution.RUNNING
            assert not backend.resume_calls
            await manager.close()


_DEAD_SERVER = "20261018T111410000000Z-4242-0a0b0c0d"


_NEXT_SERVER = "20261018T120000000000Z-4343-1a1b1c1d"


def _record_dead_server_diagnostics(tmp_path: pathlib.Path) -> pathlib.Path:
    """停止前にSIGTERMを受けた起動単位の終了診断を作成する。"""
    path = logging_config.instance_path(_DEAD_SERVER, tmp_path)
    logging_config.append_instance_event(path, writer="server", event="started", pid=4242)
    logging_config.append_instance_event(path, writer="server", event="signal_received", signal="SIGTERM")
    return path


def _publish_dead_owner_record(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, engine: str) -> None:
    """起動単位を持つ所有者が終了を公開せずに終わった`running`の記録を残し、照会側を別の起動単位にする。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", _DEAD_SERVER)
    session_registry.publish(
        _ORPHANED_SESSION_ID,
        state=session_registry.PublicationState.PENDING_RUNNING,
        engine=engine,
        cwd=str(tmp_path),
        turn_seq=2,
        processes=(exited_process_identity(), exited_process_identity()),
        launch_info=session_registry.LaunchInfo(label="worker", prompt="作業する"),
    )
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", _NEXT_SERVER)


def _cli_show(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", _ORPHANED_SESSION_ID])
    return json.loads(capsys.readouterr().out)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude", "codex", "agy"])
async def test_interrupted_show_references_server_diagnostics(
    engine: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """所有者消失の中断を返すMCPの`show`と`atk agents show`は、中断turnを所有した起動単位の終了診断を同じ参照で返す。"""
    diagnostics_path = _record_dead_server_diagnostics(tmp_path)
    _publish_dead_owner_record(monkeypatch, tmp_path, engine)
    expected = {
        "server_instance": _DEAD_SERVER,
        "turn_seq": 2,
        "path": str(diagnostics_path),
        "status": "collected",
    }

    by_cli = _cli_show(capsys)["error"]
    assert by_cli["interruptionReason"] == session_registry.OWNER_PROCESSES_EXITED
    assert {key: by_cli["diagnostics"][key] for key in expected} == expected
    assert f"sed -n '1,200p' -- {diagnostics_path}" in by_cli["diagnostics"]["next_action"]

    async with server_manager.AgentsServerManager(None) as manager:
        backend = ThreadReadBackend(manager.sessions, None) if engine == "codex" else FakeBackend(manager.sessions, engine)
        _install_backend(manager, engine, backend)
        by_mcp = (await _show_through_tool(monkeypatch, manager, _ORPHANED_SESSION_ID))["error"]
        assert by_mcp["interruptionReason"] == session_registry.OWNER_PROCESSES_EXITED
        assert {key: by_mcp["diagnostics"][key] for key in expected} == expected
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude", "codex", "agy"])
async def test_interrupted_turn_reference_survives_resume_and_release(
    engine: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """別の起動単位が引き継ぎ・再開・解放した後も、`show`は元の中断turnと元の起動単位の参照を返す。"""
    diagnostics_path = _record_dead_server_diagnostics(tmp_path)
    _publish_dead_owner_record(monkeypatch, tmp_path, engine)
    expected = [{"turn_seq": 2, "interruptionReason": "owner_processes_exited", "server_instance": _DEAD_SERVER}]

    def summary(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "turn_seq": turn["turn_seq"],
                "interruptionReason": turn["interruptionReason"],
                "server_instance": turn["diagnostics"]["server_instance"],
            }
            for turn in turns
        ]

    async with server_manager.AgentsServerManager(None) as manager:
        backend = ThreadReadBackend(manager.sessions, None) if engine == "codex" else FakeBackend(manager.sessions, engine)
        backend.publish_registry = True
        _install_backend(manager, engine, backend)
        await _show_through_tool(monkeypatch, manager, _ORPHANED_SESSION_ID)
        response = await manager.send_message(_ORPHANED_SESSION_ID, "続行")
        assert response["delivery"] == "reply_started"
        resumed = manager.sessions[_ORPHANED_SESSION_ID]
        await resumed.drain_registration()
        assert session_registry.resolve(_ORPHANED_SESSION_ID).server_instance == _NEXT_SERVER

        shown = await _show_through_tool(monkeypatch, manager, _ORPHANED_SESSION_ID)
        assert summary(shown["interrupted_turns"]) == expected
        assert shown["interrupted_turns"][0]["diagnostics"]["path"] == str(diagnostics_path)
        assert summary(_cli_show(capsys)["interrupted_turns"]) == expected

        _complete(resumed, message="再開後の結果")
        resumed.touch()
        await resumed.drain_registration()
        await manager.stop(_ORPHANED_SESSION_ID)
        assert session_registry.resolve(_ORPHANED_SESSION_ID).state is session_registry.Resolution.RELEASED
        assert summary(_cli_show(capsys)["interrupted_turns"]) == expected

        async with server_manager.AgentsServerManager(None) as restarted:
            with pytest.raises(ActionableError) as exc_info:
                await _show_through_tool(monkeypatch, restarted, _ORPHANED_SESSION_ID)
            message = str(exc_info.value)
            assert "interrupted_turns=" in message
            assert _DEAD_SERVER in message and str(diagnostics_path) in message
            await manager.close()
            await restarted.close()


@pytest.mark.asyncio
async def test_legacy_interruption_reports_not_collected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """起動単位を持たない旧記録の中断は`not_collected`を返し、記録が残るが終了を示さない`incomplete`と区別する。"""
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", None)
    _publish_record_with_processes(monkeypatch, tmp_path, "claude", (exited_process_identity(),))
    legacy = _cli_show(capsys)["error"]["diagnostics"]
    assert legacy["status"] == "not_collected"
    assert legacy["server_instance"] is None and legacy["path"] is None

    logging_config.append_instance_event(
        logging_config.instance_path(_DEAD_SERVER, tmp_path), writer="server", event="started", pid=1
    )
    _publish_dead_owner_record(monkeypatch, tmp_path, "claude")
    assert _cli_show(capsys)["error"]["diagnostics"]["status"] == "incomplete"
