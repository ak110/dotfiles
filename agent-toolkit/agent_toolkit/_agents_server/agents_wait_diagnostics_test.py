# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""待機CLIの中断時診断を検証する。"""

import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import agents_wait, logging_config, shared_roots, state, status_file, wait_targets
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_support import FakeBackend, _install_backend, exited_process_identity
from agent_toolkit._testing.agents_wait_support import _STOPPED_SERVER
from agent_toolkit._testing.agents_wait_support import _short_wait as _short_wait


def test_wait_absent_reports_interrupted_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    wait_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """所有者消失の中断で待機対象を解放した終了コード10の診断は、対象の中断turnと終了診断への参照を返す。"""
    state_root = wait_environment.parents[2]
    diagnostics_path = logging_config.instance_path(_STOPPED_SERVER, state_root)
    logging_config.append_instance_event(diagnostics_path, writer="launcher", event="process_exited", returncode=-15)
    wait_targets.retain_wait_targets("root-session", "root.json", ["session-1"], state_root)
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", _STOPPED_SERVER)
    session_registry.publish(
        "session-1",
        state=session_registry.PublicationState.PENDING_RUNNING,
        turn_seq=4,
        processes=(exited_process_identity(), exited_process_identity()),
        state_root=state_root,
    )

    with pytest.raises(SystemExit, match="10"):
        atk.main(["agents", "wait"])

    err = capsys.readouterr().err
    payload = err.split("interrupted_sessions=", 1)[1].split(NEXT_ACTION_PREFIX, 1)[0].strip()
    [interrupted] = json.loads(payload)
    assert interrupted["session_id"] == "session-1"
    assert interrupted["turn_seq"] == 4
    assert interrupted["interruptionReason"] == session_registry.OWNER_PROCESSES_EXITED
    assert interrupted["diagnostics"]["status"] == "collected"
    assert interrupted["diagnostics"]["path"] == str(diagnostics_path)


@pytest.mark.asyncio
async def test_wait_returns_stopped_interruption_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """停止で中断した結果を`atk agents wait`で回収すると、停止した起動単位の終了診断の参照を返す。

    回収後の`atk agents show`も`interrupted_turns`で同じ参照を返す。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(logging_config, "_CURRENT_INSTANCE", None)
    instance = logging_config.start_server_instance(environment={}, state_root=tmp_path)
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", instance.instance_id)
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root-session", "root.json", None))
    async with server_manager.AgentsServerManager(writer) as manager:
        _install_backend(manager, "claude", FakeBackend(manager.sessions, "claude"))
        writer.activate()
        session_id = "c0ffee00-0000-4000-8000-000000000001"
        running = state.SessionState(session_id, str(tmp_path), engine="claude", turn_seq=1, publish_registry=True)
        state.initialize_turn(running)
        running.announced = True
        manager.sessions[session_id] = running
        running.touch()
        await running.drain_registration()

        await manager.close()
        logging_config.record_instance_event("exited", outcome="normal", exception_type=None, message=None)

        assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0
        [row] = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
        assert row["status"] == "interrupted"
        assert row["error"]["interruptionReason"] == session_registry.OWNER_SERVER_STOPPED
        assert row["error"]["diagnostics"]["server_instance"] == instance.instance_id
        # 停止の時点では終了の記録が無くても、表示時点の診断ファイルから状態を求め直す。
        assert row["error"]["diagnostics"]["status"] == "collected"
        events = [json.loads(line)["event"] for line in instance.path.read_text(encoding="utf-8").splitlines()]
        assert "sessions_interrupted" in events

        with pytest.raises(SystemExit, match="0"):
            atk.main(["agents", "show", session_id])
        [turn] = json.loads(capsys.readouterr().out)["interrupted_turns"]
        assert turn["turn_seq"] == 1
        assert turn["interruptionReason"] == session_registry.OWNER_SERVER_STOPPED
        assert turn["diagnostics"]["path"] == str(instance.path)
