"""`_agents_server/manager_registry.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import pathlib

import pytest

from agent_toolkit._agents_server import agents_wait, shared_layout, shared_roots, state, status_file, wait_targets
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common import session_registry
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    _complete,
    _install_backend,
    _publish_recovered_session,
    _without_body_path,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_recovered_session_restores_persisted_result_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """再起動後の最初の待機だけが保存済みの終了時の結果本文を返す。"""
    session_id = "recovered-wait"
    _publish_recovered_session(monkeypatch, tmp_path, session_id, "failed")
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    persisted = state.SessionState(session_id, str(tmp_path), engine="codex", turn_seq=2)
    _complete(persisted, message="永続結果", error={"message": "失敗結果"})
    writer.retain_result(persisted)
    result_path = shared_layout.results_directory("root-session", tmp_path) / f"{session_id}.json"
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", backend)

        assert (
            agents_wait.wait_for_result(
                environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
                state_root=tmp_path,
            )
            == 0
        )
        restored = _without_body_path(json.loads(capsys.readouterr().out))
        second = await manager.kill(session_id, timeout=0)

        assert "`send_message`" in restored.pop("next_action")

        assert restored == {
            "session_id": session_id,
            "status": "failed",
            "agent_message": "永続結果",
            "error": {"message": "失敗結果"},
            "engine": "codex",
            "model": None,
            "effort": None,
            "model_type": None,
        }
        assert second == {"status": "failed", "recovery": "result_unavailable", "kill_requested": False}
        assert not result_path.exists()
        assert backend.send_calls == 0
        assert not backend.resume_calls
        assert not backend.release_calls
        await manager.close()


@pytest.mark.asyncio
async def test_recovered_session_marks_persisted_result_as_uncollected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    session_id = "recovered-result"
    _publish_recovered_session(monkeypatch, tmp_path, session_id, "completed")
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "current.json", None),
        state_root=tmp_path,
    )
    persisted = state.SessionState(session_id, str(tmp_path), engine="codex", turn_seq=2)
    _complete(persisted, message="永続結果")
    writer.retain_result(persisted)
    async with server_manager.AgentsServerManager(writer) as manager:
        resume_state = await manager._restore_registry_session(session_id)

        assert resume_state is not None
        assert resume_state.result_delivered is False
        await manager.close()


@pytest.mark.asyncio
async def test_recovered_session_kill_reports_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    _publish_recovered_session(monkeypatch, tmp_path, "recovered-kill", "failed")
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", backend)

        response = await manager.kill("recovered-kill", timeout=0)

        assert response == {
            "status": "failed",
            "recovery": "result_unavailable",
            "kill_requested": False,
        }
        assert backend.interrupt_calls == 0
        assert not backend.release_calls
        await manager.close()


@pytest.mark.asyncio
async def test_recovered_session_stop_discards_previous_process_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    session_id = "recovered-stop"
    _publish_recovered_session(monkeypatch, tmp_path, session_id, "completed")
    previous_writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "previous.json", None),
        state_root=tmp_path,
    )
    previous_session = state.SessionState(session_id, str(tmp_path), engine="codex", turn_seq=2)
    _complete(previous_session, message="前プロセスの結果")
    previous_writer.retain_result(previous_session)
    result_path = shared_layout.results_directory("root-session", tmp_path) / f"{session_id}.json"
    previous_result = result_path.read_bytes()

    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "current.json", None),
        state_root=tmp_path,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", backend)

        assert await manager.stop(session_id, retain_result=True) == {}
        assert result_path.read_bytes() == previous_result

        assert await manager.stop(session_id) == {}
        assert not result_path.exists()

        assert await manager.stop(session_id) == {}
        assert not result_path.exists()

        assert not backend.release_calls
        await manager.close()


@pytest.mark.asyncio
async def test_recovered_stop_finds_older_turns_after_latest_was_collected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    session_id = "recovered-stop"
    _publish_recovered_session(monkeypatch, tmp_path, session_id, "completed")
    session_registry.publish(
        session_id,
        state=session_registry.PublicationState.RESULT_COMPLETED,
        engine="codex",
        turn_seq=2,
        owner_root_session_id="original",
        launcher_session_id="parent",
    )
    shared_roots.write_host_alias("original", "writer", "parent", tmp_path)
    original, neighbor, current = [
        status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity(root, name, None), state_root=tmp_path)
        for root, name in (("original", "writer.json"), ("original", "other.json"), ("current", "writer.json"))
    ]
    for writer, body in ((original, "停止対象"), (neighbor, "別の所有者"), (current, "別ルート")):
        session = state.SessionState(session_id, str(tmp_path), engine="codex", turn_seq=1)
        _complete(session, message=body)
        writer.retain_result(session)
        wait_targets.retain_wait_targets(writer.root_session_id, writer.path.name, [session_id], tmp_path)

    async with server_manager.AgentsServerManager(current) as manager:
        assert await manager.stop(session_id) == {}
        assert await manager.stop(session_id) == {}
        assert session_id in current._published_results
        assert original.take_result(session_id, collector="cli") == (None, None)
        assert wait_targets.read_wait_targets("original", "writer.json", tmp_path) == (set(), None)
        session_registry.publish(
            session_id, state=session_registry.PublicationState.RESULT_COMPLETED, turn_seq=3, owner_root_session_id="original"
        )
        successor = state.SessionState(session_id, str(tmp_path), engine="codex", turn_seq=3)
        _complete(successor, message="再開後")
        original.retain_result(successor)
        with pytest.raises(ActionableError, match="所有者またはturn"):
            await manager.stop(session_id)
    result, error = original.take_result(session_id, collector="cli")
    assert error is None and result is not None and result["agent_message"] == "再開後"
    for writer, body in ((neighbor, "別の所有者"), (current, "別ルート")):
        assert wait_targets.read_wait_targets(writer.root_session_id, writer.path.name, tmp_path) == ({session_id}, None)
        result, error = writer.take_result(session_id, collector="cli")
        assert error is None and result is not None and result["agent_message"] == body


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["completed", "failed", "interrupted"])
async def test_recovered_session_restores_each_terminal_status(
    terminal_status: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    session_id = f"recovered-{terminal_status}"
    _publish_recovered_session(monkeypatch, tmp_path, session_id, terminal_status)
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        response = await manager.kill(session_id, timeout=0)

        assert response == {"status": terminal_status, "recovery": "result_unavailable", "kill_requested": False}
        await manager.close()
