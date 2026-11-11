"""status_fileのfailuresの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import pathlib

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import mcp_tools, shared_layout, shared_roots, state
from agent_toolkit._agents_server import status_file as subject
from agent_toolkit._testing.agents_server_status_file_support import (
    _replace_failure_logged,
    _ReplaceRefusal,
    _TerminalBeforeResponseBackend,
    _until,
)
from agent_toolkit._testing.agents_server_support import install_backend

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


@pytest.mark.asyncio
async def test_flush_failures_preserve_diagnostics_and_heartbeat(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, diagnostic_log: pathlib.Path
) -> None:
    """予約flushとheartbeatの置換失敗をスタック・両パス・書込主体・sessionとともに残し、次周期で保存を再開する。"""
    monkeypatch.setattr(subject, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "nested-host.json", "nested-host"),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        session = state.SessionState("session-1", str(tmp_path))
        session.status = "running"
        session.announced = True
        manager.sessions[session.session_id] = session
        manager.activate()
        before = json.loads(writer.path.read_text(encoding="utf-8"))["heartbeat_at"]
        refusal = _ReplaceRefusal(monkeypatch, writer.path)
        try:
            writer.schedule()
            messages = ("予約した共有状態の保存に失敗しました", "heartbeatの保存に失敗しました")
            await _until(lambda: all(message in diagnostic_log.read_text(encoding="utf-8") for message in messages))
            heartbeat_task = manager._heartbeat_task
            assert heartbeat_task is not None and not heartbeat_task.done()
            assert json.loads(writer.path.read_text(encoding="utf-8"))["heartbeat_at"] == before
            log_text = diagnostic_log.read_text(encoding="utf-8")
            for message in messages:
                line = next(line for line in log_text.splitlines() if message in line)
                assert f"writer={writer.path}" in line and "session-1" in line
            assert "Traceback (most recent call last)" in log_text
            assert _replace_failure_logged(log_text, writer.path)

            refusal.active = False
            await _until(lambda: json.loads(writer.path.read_text(encoding="utf-8"))["heartbeat_at"] > before)
            assert not heartbeat_task.done()
            assert not list(writer.path.parent.glob(f".{writer.path.name}.*.tmp"))
        finally:
            refusal.active = False
            await manager.close()


@pytest.mark.asyncio
async def test_terminal_result_write_failure_is_not_reported_as_projection_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, diagnostic_log: pathlib.Path
) -> None:
    """終了時の結果の保存失敗を、起動応答・直接flush・予約flushで表示失敗と区別し、解消後に結果を保存する。"""
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        install_backend(manager, "claude", _TerminalBeforeResponseBackend(manager.sessions, "claude"))
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        writer.activate()
        result_path = shared_layout.results_directory("root-session", tmp_path) / "claude-session.json"
        refusal = _ReplaceRefusal(monkeypatch, result_path)
        try:
            started = await mcp_tools.start(mode="delegate", cwd=str(tmp_path), prompt="処理", model_type="claude:test/medium")
            assert started["session_id"] == "claude-session" and started["status"] == "completed"
            scopes = {warning["scope"] for warning in started["persistence_warnings"]}
            assert "shared-state" in scopes and "projection" not in scopes
            assert not result_path.exists()

            with pytest.raises(OSError) as raised:
                writer.flush()
            assert not isinstance(raised.value, subject.ProjectionWriteError)
            writer.schedule()
            await _until(lambda: "予約した共有状態の保存に失敗しました" in diagnostic_log.read_text(encoding="utf-8"))
            log_text = diagnostic_log.read_text(encoding="utf-8")
            line = next(line for line in log_text.splitlines() if "開始済みsessionの結果・共有状態保存に失敗" in line)
            assert "session_id=claude-session" in line and f"writer={writer.path}" in line
            assert _replace_failure_logged(log_text, result_path)

            refusal.active = False
            writer.flush()
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            assert saved["status"] == "completed" and saved["owner_status_file"] == "root.json"
        finally:
            refusal.active = False
            await manager.close()
