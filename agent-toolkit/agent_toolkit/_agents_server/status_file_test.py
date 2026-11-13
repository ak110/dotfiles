"""status_fileの基本動作の契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import datetime
import json
import logging
import os
import pathlib

import pytest

from agent_toolkit._agents_server import retained_results, shared_layout, shared_roots, state
from agent_toolkit._agents_server import status_file as subject

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


@pytest.mark.asyncio
async def test_writer_logs_result_write_and_delete_without_body(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """result操作へsessionと書込主体を記録し、結果本文を含めない。"""
    session = state.SessionState("session-1", str(tmp_path))
    session.status = "completed"
    session.agent_message = "秘密の結果本文"
    session.record_turn_completed()
    session.touch()
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )

    with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.status-file"):
        writer.retain_result(session)
        writer.delete_result(session.session_id, collector="mcp-wait")

    assert "result_written session_id=session-1 writer=root.json" in caplog.text
    assert "result_deleted session_id=session-1 writer=root.json collector=mcp-wait" in caplog.text
    assert "秘密の結果本文" not in caplog.text


@pytest.mark.asyncio
async def test_status_file_includes_updated_at(tmp_path: pathlib.Path) -> None:
    """状態ファイルのsession射影は最終活動時刻を含む。"""
    session = state.SessionState("session-1", str(tmp_path))
    session.announced = True
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )

    writer.activate()
    saved = json.loads(writer.path.read_text(encoding="utf-8"))

    assert saved["sessions"][0]["updated_at"] == session.updated_at
    writer.deactivate()


@pytest.mark.asyncio
async def test_inner_writer_projects_parent_thread_id_into_host_session_id(tmp_path: pathlib.Path) -> None:
    """内側の3つのmode（delegate・explore・shell）のsessionを親thread識別子へ射影して1つの状態ファイルへ集約する。"""
    identity = shared_roots.resolve_status_file_identity(
        {"AGENT_TOOLKIT_OWNER_SESSION": "root", "AGENT_TOOLKIT_STATUS_HOST_SESSION": "writer"}
    )
    assert identity is not None
    sessions = {
        launch_kind: state.SessionState(
            f"{launch_kind}-session",
            str(tmp_path),
            launch_kind=launch_kind,
            announced=True,
        )
        for launch_kind in ("delegate", "explore", "shell")
    }
    writer = subject.StatusFileWriter(sessions, identity, state_root=tmp_path, aggregate_seconds=0)
    writer.activate()
    assert json.loads(writer.path.read_text(encoding="utf-8"))["host_session_id"] == "writer"

    shared_roots.write_host_alias("root", "writer", "parent-thread", tmp_path)
    writer.flush()

    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    assert payload["host_session_id"] == "parent-thread"
    assert len(payload["sessions"]) == 3
    assert writer.path in shared_layout.list_status_files("root", tmp_path)
    writer.deactivate()


@pytest.mark.asyncio
async def test_api_error_record_reaches_status_file_without_activity(tmp_path: pathlib.Path) -> None:
    """API失敗の診断は活動時刻を変えず、CLIが読む状態ファイルへ届く。"""
    session = state.SessionState("claude-session", str(tmp_path), engine="claude", announced=True)
    session.updated_at = "2000-01-01T00:00:00+00:00"
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()

    session.record_api_error("rate_limit_error", 429)
    writer.flush()

    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    recorded = payload["sessions"][0]
    assert recorded["updated_at"] == "2000-01-01T00:00:00+00:00"
    assert recorded["api_error"]["type"] == "rate_limit_error"
    assert recorded["api_error"]["http_status"] == 429
    assert recorded["api_error"]["count"] == 1
    writer.deactivate()


@pytest.mark.asyncio
async def test_hosts_entries_are_removed_after_retention(tmp_path: pathlib.Path) -> None:
    """保持期限を過ぎた書込主体索引をactivate時に回収する。"""
    host_path = shared_layout.hosts_directory("root", tmp_path) / "writer.json"
    host_path.parent.mkdir(parents=True)
    host_path.write_text('{"version": 1, "host_session_id": "thread"}', encoding="utf-8")
    stale_at = datetime.datetime.now(datetime.UTC).timestamp() - state.RESULT_RETENTION_SECONDS - 1
    os.utime(host_path, (stale_at, stale_at))
    writer = subject.StatusFileWriter(
        {}, shared_roots.StatusFileIdentity("root", "root.json", None), state_root=tmp_path, aggregate_seconds=0
    )

    writer.activate()

    assert not host_path.exists()
    assert not host_path.parent.exists()
    writer.deactivate()


@pytest.mark.asyncio
async def test_host_alias_outlives_uncollected_nested_result(tmp_path: pathlib.Path) -> None:
    """別書込主体の起動が古い索引を回収しても、複数turn後の結果の所有者を失わない。"""
    shared_roots.write_host_alias("root", "writer", "thread", tmp_path)
    host_path = shared_layout.hosts_directory("root", tmp_path) / "writer.json"
    stale_at = datetime.datetime.now(datetime.UTC).timestamp() - state.RESULT_RETENTION_SECONDS - 1
    os.utime(host_path, (stale_at, stale_at))
    results = shared_layout.results_directory("root", tmp_path)
    results.mkdir(exist_ok=True)
    result_path = results / "nested.json"
    result_path.write_text(
        json.dumps({"status": "completed", "agent_message": "完了", "turn_seq": 3, "owner_status_file": "writer.json"}),
        encoding="utf-8",
    )
    other = subject.StatusFileWriter(
        {}, shared_roots.StatusFileIdentity("root", "other.json", "other"), state_root=tmp_path, aggregate_seconds=0
    )

    other.activate()

    assert host_path.exists()
    assert shared_roots.resolve_wait_identity(
        {"AGENT_TOOLKIT_OWNER_SESSION": "root", "CODEX_THREAD_ID": "thread"}, None, tmp_path
    ) == shared_roots.StatusFileIdentity("root", "writer.json", "writer")
    assert retained_results.take_result("root", "nested", "writer.json", collector="test", state_root=tmp_path)[0] == {
        "status": "completed",
        "agent_message": "完了",
    }
    other.deactivate()
    assert not host_path.exists()


@pytest.mark.asyncio
async def test_writer_serializes_announced_sessions_and_removes_delivered(
    tmp_path: pathlib.Path,
) -> None:
    """公開済みで未回収のsessionだけを状態ファイルへ書く。"""
    sessions: dict[str, state.SessionState] = {}
    writer = subject.StatusFileWriter(
        sessions,
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    hidden = state.SessionState("hidden", str(tmp_path), announced=False)
    visible = state.SessionState(
        "visible",
        str(tmp_path),
        engine="claude",
        model="sonnet[1m]",
        effort="low",
        model_type="execute",
        launch_kind="delegate",
        label="実装",
        announced=True,
    )
    sessions.update(hidden=hidden, visible=visible)
    visible.set_progress("進捗")
    writer.flush()

    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["host_session_id"] is None
    datetime.datetime.fromisoformat(payload["heartbeat_at"])
    assert [item["session_id"] for item in payload["sessions"]] == ["visible"]
    assert payload["sessions"][0]["progress"] == "進捗"
    datetime.datetime.fromisoformat(payload["sessions"][0]["started_at"])

    visible.status = "completed"
    visible.agent_message = "完了"
    visible.record_turn_completed()
    visible.touch()
    writer.flush()
    result_path = shared_layout.results_directory("root", tmp_path) / "visible.json"
    assert result_path.exists()
    result_path.unlink()
    writer.flush()
    assert not json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert visible.result_delivered is True
    assert not result_path.exists()
    writer.deactivate()
    assert not writer.path.parent.exists()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_terminal_error_saved_redacted_without_changing_live_result(tmp_path: pathlib.Path, asynchronous: bool) -> None:
    """同期・非同期の結果保存でエラーだけを伏せ、会話本文と判定コードを保つ。"""
    error = {
        "message": "password=fake-terminal",
        "stderr": "api_key=fake-stderr",
        "detail": [{"api_key": "fake-bare-terminal", "max_tokens": 123}],
        "code": 429,
    }
    session = state.SessionState("session-redaction", str(tmp_path), error=error, status="failed")
    session.agent_message = "会話 password=fake-conversation"
    session.record_turn_completed()
    session.touch()
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    if asynchronous:
        await writer.retain_result_async(session)
    else:
        writer.retain_result(session)
    saved = writer.read_result(session.session_id)
    assert saved is not None
    assert "fake-terminal" not in str(saved["error"]) and "fake-stderr" not in str(saved["error"])
    assert saved["error"]["code"] == 429
    assert "fake-bare-terminal" not in str(saved["error"])
    assert saved["error"]["detail"][0]["max_tokens"] == 123
    assert saved["agent_message"] == session.agent_message
    assert session.error == error
    assert "fake-terminal" not in (session.terminal_error or "")
