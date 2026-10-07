"""agents_serverのstatusline向け状態ファイル契約を検証する。"""

# テストでは共有managerと状態モデルの内部境界も直接検証する。
# pylint: disable=protected-access

import asyncio
import datetime
import json
import logging
import os
import pathlib
import typing

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import (
    retained_results,
    shared_layout,
    shared_roots,
    state,
)
from agent_toolkit._agents_server import status_file as subject
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._testing.agents_server_support import install_backend


async def _wait_now(manager: server_manager.AgentsServerManager) -> dict[str, typing.Any]:
    """待機せずに現在の終端状態を返すwaitを発行する。"""
    manager._wait_timeouts["main"] = 0.0  # pylint: disable=protected-access
    return await manager.wait()


@pytest.mark.asyncio
async def test_writer_logs_result_write_and_delete_without_body(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """result操作へsessionと書込主体を記録し、結果本文を含めない。"""
    session = state.SessionState("session-1", str(tmp_path))
    session.status = "completed"
    session.agent_message = "秘密の結果本文"
    session.turn_completed = True
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
        model_type="high_tier",
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
    visible.turn_completed = True
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


def _completed_with_retention_deadline(session_id: str, tmp_path: pathlib.Path, *, turn_seq: int = 0) -> state.SessionState:
    """完了した結果を持ち、0.03秒後に保持期限へ達するsessionを返す。"""
    session = state.SessionState(session_id, str(tmp_path), announced=True, turn_seq=turn_seq)
    session.status = "completed"
    session.agent_message = "完了"
    session.turn_completed = True
    session.touch()
    session.retention_deadline = asyncio.get_running_loop().time() + 0.03
    return session


def _root_writer(sessions: dict[str, state.SessionState], tmp_path: pathlib.Path) -> subject.StatusFileWriter:
    """ルート`root`の状態ファイルを`tmp_path`配下へ即時に書くwriterを返す。"""
    return subject.StatusFileWriter(
        sessions,
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )


@pytest.mark.asyncio
async def test_writer_removes_session_at_retention_deadline(tmp_path: pathlib.Path) -> None:
    """期限到達後はsession表示を除き、未回収の結果を保持する。"""
    session = _completed_with_retention_deadline("retained", tmp_path, turn_seq=1)
    writer = _root_writer({session.session_id: session}, tmp_path)
    writer.activate()

    assert json.loads(writer.path.read_text(encoding="utf-8"))["sessions"][0]["session_id"] == "retained"
    result_path = shared_layout.results_directory("root", tmp_path) / "retained.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["agent_message"] == "完了"
    assert result["turn_seq"] == 1
    datetime.datetime.fromisoformat(result["finalized_at"])
    assert writer._retention_handle is not None
    await asyncio.sleep(0.05)
    assert not json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert result_path.exists()
    writer.deactivate()
    assert result_path.exists()


@pytest.mark.asyncio
async def test_writer_retains_result_without_live_session_after_deadline(tmp_path: pathlib.Path) -> None:
    """破棄済みsessionから保持した結果も期限到達後に維持する。"""
    session = _completed_with_retention_deadline("stopped", tmp_path)
    writer = _root_writer({}, tmp_path)
    writer.activate()
    writer.retain_result(session)
    writer.flush()
    result_path = shared_layout.results_directory("root", tmp_path) / "stopped.json"
    assert result_path.exists()
    assert writer._retention_handle is None

    await asyncio.sleep(0.05)

    assert result_path.exists()
    writer.deactivate()
    assert result_path.exists()


@pytest.mark.asyncio
async def test_writer_removes_waited_result_at_retention_deadline(tmp_path: pathlib.Path) -> None:
    """waitで回収済みになった結果を次のflushで削除する。"""
    session = _completed_with_retention_deadline("waited", tmp_path, turn_seq=1)
    writer = _root_writer({session.session_id: session}, tmp_path)
    manager = server_manager.AgentsServerManager(writer)
    writer.activate()

    result_path = shared_layout.results_directory("root", tmp_path) / "waited.json"
    assert (await _wait_now(manager))["agent_message"] == "完了"
    writer.flush()
    assert not result_path.exists()
    await manager.close()


@pytest.mark.asyncio
async def test_writer_excludes_already_expired_session(tmp_path: pathlib.Path) -> None:
    """再出力時点で保持期限を過ぎたsessionを表示対象から除く。"""
    session = state.SessionState("expired", str(tmp_path), announced=True)
    session.retention_deadline = asyncio.get_running_loop().time() - 1
    writer = subject.StatusFileWriter(
        {session.session_id: session},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    assert not json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    writer.deactivate()


def _write_descendant_file(
    tmp_path: pathlib.Path,
    file_name: str,
    host_session_id: str,
    statuses: dict[str, str],
    *,
    heartbeat_age: float = 0,
) -> pathlib.Path:
    """別の書込主体が書いた子孫の状態ファイルを置く。"""
    path = shared_layout.status_directory("root", tmp_path) / file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=heartbeat_age)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "host_session_id": host_session_id,
                "heartbeat_at": heartbeat.isoformat(),
                "updated_at": heartbeat.isoformat(),
                "sessions": [{"session_id": session_id, "status": status} for session_id, status in statuses.items()],
            }
        ),
        encoding="utf-8",
    )
    return path


def _terminal_parent(tmp_path: pathlib.Path, status: str) -> state.SessionState:
    """turnが終端した親sessionを返す。"""
    session = state.SessionState("parent", str(tmp_path), announced=True, turn_seq=1, label="lane-01-exec")
    session.status = status
    session.agent_message = "待機中: grandchild"
    session.turn_completed = True
    session.touch()
    return session


def _shown_sessions(writer: subject.StatusFileWriter) -> dict[str, str]:
    """状態ファイルへ書かれたsessionの識別子と状態を返す。"""
    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    return {item["session_id"]: item["status"] for item in payload["sessions"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
async def test_writer_keeps_collected_parent_while_grandchild_runs(tmp_path: pathlib.Path, status: str) -> None:
    """結果を回収した終端の親も、孫の稼働中は実際の終端状態のまま表示し、結果は再公開しない。"""
    parent = _terminal_parent(tmp_path, status)
    writer = subject.StatusFileWriter(
        {parent.session_id: parent},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    grandchild = _write_descendant_file(tmp_path, "parent-writer.json", "parent", {"grandchild": "running"})
    writer.activate()
    result_path = shared_layout.results_directory("root", tmp_path) / "parent.json"
    assert result_path.exists()

    # CLIの`atk agents wait`と同じ`take_result`で、書込主体の外から結果を取得する。
    assert retained_results.take_result("root", "parent", "root.json", collector="cli", state_root=tmp_path)[0] is not None
    writer.flush()

    assert _shown_sessions(writer) == {"parent": status}
    assert not result_path.exists()
    assert retained_results.take_result("root", "parent", "root.json", collector="cli", state_root=tmp_path) == (None, None)

    _write_descendant_file(tmp_path, grandchild.name, "parent", {"grandchild": "completed"})
    writer.flush()

    assert not _shown_sessions(writer)
    assert not result_path.exists()
    writer.deactivate()


@pytest.mark.asyncio
async def test_writer_returns_uncollected_parent_to_normal_rules_after_grandchild_ends(tmp_path: pathlib.Path) -> None:
    """結果を回収する対照: 未回収かつ表示期限内の親は孫の終端後も従来どおり残る。"""
    parent = _terminal_parent(tmp_path, "completed")
    parent.retention_deadline = asyncio.get_running_loop().time() + 60
    writer = subject.StatusFileWriter(
        {parent.session_id: parent},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    _write_descendant_file(tmp_path, "parent-writer.json", "parent", {"grandchild": "completed"})
    writer.activate()

    assert _shown_sessions(writer) == {"parent": "completed"}
    assert (shared_layout.results_directory("root", tmp_path) / "parent.json").exists()
    writer.deactivate()


@pytest.mark.asyncio
async def test_writer_keeps_parent_past_retention_deadline_while_descendant_runs(tmp_path: pathlib.Path) -> None:
    """表示期限の到達後も、本体を解放したsessionも、稼働中の子孫がある間は表示する。"""
    parent = _terminal_parent(tmp_path, "completed")
    parent.retention_deadline = asyncio.get_running_loop().time() - 1
    expired_parent = _terminal_parent(tmp_path, "failed")
    expired_parent.session_id = "expired-parent"
    expired = {"expired-parent": state.SessionResumeState.from_session(expired_parent)}
    writer = subject.StatusFileWriter(
        {parent.session_id: parent},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
        expired_sessions=expired,
    )
    _write_descendant_file(tmp_path, "parent-writer.json", "parent", {"grandchild": "running"})
    expired_writer = _write_descendant_file(tmp_path, "expired-writer.json", "expired-parent", {"other-grandchild": "running"})
    writer.activate()

    shown = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert {item["session_id"]: item["status"] for item in shown} == {"parent": "completed", "expired-parent": "failed"}
    assert next(item for item in shown if item["session_id"] == "expired-parent")["label"] == "lane-01-exec"

    _write_descendant_file(tmp_path, expired_writer.name, "expired-parent", {"other-grandchild": "completed"})
    writer.flush()

    assert _shown_sessions(writer) == {"parent": "completed"}
    writer.deactivate()


@pytest.mark.asyncio
async def test_writer_keeps_parent_for_partial_and_deep_descendants(tmp_path: pathlib.Path) -> None:
    """複数の孫の一部だけが終端した場合と、終端した孫の先で稼働が続く場合も親を残す。"""
    parent = _terminal_parent(tmp_path, "completed")
    parent.result_delivered = True
    writer = subject.StatusFileWriter(
        {parent.session_id: parent},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    children = _write_descendant_file(
        tmp_path, "parent-writer.json", "parent", {"grandchild-1": "completed", "grandchild-2": "running"}
    )
    writer.activate()
    assert _shown_sessions(writer) == {"parent": "completed"}

    _write_descendant_file(tmp_path, children.name, "parent", {"grandchild-1": "completed", "grandchild-2": "completed"})
    deep = _write_descendant_file(tmp_path, "grandchild-writer.json", "grandchild-2", {"great-grandchild": "running"})
    writer.flush()
    assert _shown_sessions(writer) == {"parent": "completed"}

    _write_descendant_file(tmp_path, deep.name, "grandchild-2", {"great-grandchild": "completed"})
    writer.flush()
    assert not _shown_sessions(writer)
    writer.deactivate()


@pytest.mark.asyncio
async def test_writer_ignores_running_descendant_with_expired_heartbeat(tmp_path: pathlib.Path) -> None:
    """生存の印が失効した子孫の古いrunning行では親を残さない。"""
    parent = _terminal_parent(tmp_path, "completed")
    parent.result_delivered = True
    writer = subject.StatusFileWriter(
        {parent.session_id: parent},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    _write_descendant_file(
        tmp_path,
        "parent-writer.json",
        "parent",
        {"grandchild": "running"},
        heartbeat_age=subject.HEARTBEAT_EXPIRY_SECONDS + 1,
    )
    writer.activate()

    assert not _shown_sessions(writer)
    writer.deactivate()


@pytest.mark.asyncio
async def test_root_writer_removes_stale_files_on_activate(tmp_path: pathlib.Path) -> None:
    """ルートwriterは自身と保持期限切れの共有ファイルだけを除く。"""
    directory = shared_layout.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    other_writer = directory / "other.json"
    other_writer.write_text("{}", encoding="utf-8")
    other_temporary = directory / ".other.json.token.tmp"
    other_temporary.write_text("temporary", encoding="utf-8")
    results = directory / "results"
    results.mkdir()
    stale_result = results / "stale-session.json"
    stale_result.write_text("{}", encoding="utf-8")
    retained_result = results / "retained-session.json"
    retained_result.write_text("{}", encoding="utf-8")
    notices = directory / "notices"
    notices.mkdir()
    stale_notice = notices / "stale-session.1.json"
    stale_notice.write_text("{}", encoding="utf-8")
    retained_notice = notices / "retained-session.1.json"
    retained_notice.write_text("{}", encoding="utf-8")
    stale_at = datetime.datetime.now(datetime.UTC).timestamp() - state.RESULT_RETENTION_SECONDS - 1
    for path in (stale_result, stale_notice):
        os.utime(path, (stale_at, stale_at))
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )

    writer.activate()
    assert other_writer.exists()
    assert other_temporary.exists()
    assert retained_result.exists()
    assert retained_notice.exists()
    assert stale_result.exists()
    assert not stale_notice.exists()
    writer.deactivate()
    assert other_writer.exists()
    assert other_temporary.exists()
    assert retained_result.exists()
    assert retained_notice.exists()
    assert stale_result.exists()


@pytest.mark.asyncio
async def test_writer_removes_state_file_with_expired_heartbeat(tmp_path: pathlib.Path) -> None:
    """生存の印が失効した他の状態ファイルを削除する。"""
    directory = shared_layout.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    stale = directory / "stale.json"
    stale.write_text(
        json.dumps(
            {
                "heartbeat_at": (
                    datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=subject.HEARTBEAT_EXPIRY_SECONDS + 1)
                ).isoformat()
            }
        ),
        encoding="utf-8",
    )
    live = directory / "live.json"
    live.write_text(
        json.dumps({"heartbeat_at": datetime.datetime.now(datetime.UTC).isoformat()}),
        encoding="utf-8",
    )
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )

    writer.activate()

    assert not stale.exists()
    assert live.exists()
    writer.deactivate()


@pytest.mark.asyncio
async def test_writer_preserves_state_file_without_heartbeat(tmp_path: pathlib.Path) -> None:
    """旧形式の状態ファイルは他の書込主体が回収しない。"""
    directory = shared_layout.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    legacy = directory / "legacy.json"
    legacy.write_text("{}", encoding="utf-8")
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )

    writer.activate()
    writer.flush()
    writer.deactivate()

    assert legacy.exists()


@pytest.mark.asyncio
async def test_manager_refreshes_heartbeat_until_close(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """managerは稼働中に生存の印を定期更新し、終了時に更新タスクを回収する。"""
    monkeypatch.setattr(subject, "HEARTBEAT_INTERVAL_SECONDS", 0.01)
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = server_manager.AgentsServerManager(writer)
    manager.activate()
    initial = json.loads(writer.path.read_text(encoding="utf-8"))["heartbeat_at"]

    await asyncio.sleep(0.03)

    refreshed = json.loads(writer.path.read_text(encoding="utf-8"))["heartbeat_at"]
    assert refreshed > initial
    await manager.close()
    assert manager._heartbeat_task is None


@pytest.mark.asyncio
async def test_nested_writer_preserves_root_file_on_deactivate(tmp_path: pathlib.Path) -> None:
    """入れ子の書込主体は自身のファイルだけを回収する。"""
    directory = shared_layout.status_directory("root", tmp_path)
    directory.mkdir(parents=True)
    root_file = directory / "root.json"
    root_file.write_text("{}", encoding="utf-8")
    notices = directory / "notices"
    notices.mkdir()
    notice_file = notices / "child.1.json"
    notice_file.write_text("{}", encoding="utf-8")
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "child.json", "child"),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    writer.activate()
    writer.deactivate()
    assert root_file.exists()
    assert not writer.path.exists()
    assert notice_file.exists()


@pytest.mark.asyncio
async def test_manager_writes_three_launch_kinds_and_removes_waited_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """3つの起動手段と結果回収を状態ファイルへ反映する。"""
    writer = subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = server_manager.AgentsServerManager(writer)
    backend = _FakeStatusBackend(manager.sessions)
    install_backend(manager, "codex", backend)
    _use_candidates(monkeypatch, ("codex", "gpt-5.6-terra", "medium"))
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
    writer.activate()

    started = await manager.start("high_tier", "\n  実装を開始\n続き", str(tmp_path))
    await manager.start_explore("調査する", str(tmp_path))
    await manager.start_shell("pytest -q", str(tmp_path), "結果を要約")
    writer.flush()
    payload = json.loads(writer.path.read_text(encoding="utf-8"))
    assert [item["launch_kind"] for item in payload["sessions"]] == ["delegate", "explore", "shell"]
    assert [item["label"] for item in payload["sessions"]] == ["実装を開始", "explore", "shell-pytest"]

    session = manager.sessions[started["session_id"]]
    session.status = "completed"
    session.agent_message = "完了"
    session.turn_completed = True
    session.touch()
    await _wait_now(manager)
    writer.flush()
    result_path = shared_layout.results_directory("root", tmp_path) / f"{session.session_id}.json"
    assert not result_path.exists()
    remaining = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert session.session_id not in {item["session_id"] for item in remaining}
    await manager.close()
    assert not shared_layout.status_directory("root", tmp_path).exists()


@pytest.mark.asyncio
async def test_manager_removes_previous_result_when_new_turn_starts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """同じsessionの新しいturnを開始した時点で前の結果を削除する。"""
    writer = _status_writer(tmp_path)
    manager = server_manager.AgentsServerManager(writer)
    backend = _FakeStatusBackend(manager.sessions)
    install_backend(manager, "codex", backend)
    _use_candidates(monkeypatch, ("codex", "model", "medium"))
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
    writer.activate()
    started = await manager.start("high_tier", "実装", str(tmp_path))
    session = manager.sessions[started["session_id"]]
    session.status = "completed"
    session.agent_message = "前の結果"
    session.turn_completed = True
    session.touch()
    writer.flush()
    result_path = shared_layout.results_directory("root", tmp_path) / f"{session.session_id}.json"
    assert result_path.exists()

    await manager.send_message(session.session_id, "続行")

    assert not result_path.exists()
    await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("delayed", [False, True])
async def test_manager_writes_only_announced_candidate_after_fallback(
    delayed: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """候補切替で除外した試行を隠し、委譲元へ返したsessionだけを書く。"""
    writer = _status_writer(tmp_path)
    manager = server_manager.AgentsServerManager(writer)
    backend: _FakeStatusBackend = (
        _DelayedUnavailableStatusBackend(
            manager.sessions,
            condition=manager._condition,
            unavailable_models={"first"},
        )
        if delayed
        else _UnavailableStatusBackend(manager.sessions, unavailable_models={"first"})
    )
    install_backend(manager, "codex", backend)
    _use_candidates(monkeypatch, ("codex", "first", "high"), ("codex", "second", "high"))
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.01)
    writer.activate()

    response = await manager.start("high_tier", "実装", str(tmp_path))
    writer.flush()

    sessions = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert [item["session_id"] for item in sessions] == [response["session_id"]]
    assert sessions[0]["model"] == "second"
    assert backend.release_calls == ["session-1"]
    await manager.close()


@pytest.mark.asyncio
async def test_manager_writes_only_last_failure_when_all_candidates_are_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """全候補が利用不能なら、応答へ載せた最後の失敗sessionだけを書く。"""
    writer = _status_writer(tmp_path)
    manager = server_manager.AgentsServerManager(writer)
    backend = _UnavailableStatusBackend(manager.sessions, unavailable_models={"first", "second"})
    install_backend(manager, "codex", backend)
    _use_candidates(monkeypatch, ("codex", "first", "high"), ("codex", "second", "high"))
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
    writer.activate()

    response = await manager.start("high_tier", "実装", str(tmp_path))
    writer.flush()

    sessions = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert [item["session_id"] for item in sessions] == [response["session_id"]]
    assert sessions[0]["model"] == "second"
    assert sessions[0]["status"] == "failed"
    assert backend.release_calls == ["session-1"]
    await manager.close()


@pytest.mark.asyncio
async def test_manager_removes_kill_result_but_keeps_uncollected_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """killで返した結果だけを除き、未回収の終端結果は表示に残す。"""
    writer = _status_writer(tmp_path)
    manager = server_manager.AgentsServerManager(writer)
    backend = _FakeStatusBackend(manager.sessions)
    install_backend(manager, "codex", backend)
    _use_candidates(monkeypatch, ("codex", "model", "medium"))
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
    writer.activate()
    killed = await manager.start("high_tier", "kill対象", str(tmp_path))
    uncollected = await manager.start("high_tier", "未回収", str(tmp_path))
    for session_id in (killed["session_id"], uncollected["session_id"]):
        session = manager.sessions[session_id]
        session.status = "completed"
        session.agent_message = "完了"
        session.turn_completed = True
        session.touch()

    response = await manager.kill(killed["session_id"], timeout=0)
    writer.flush()

    assert response["agent_message"] == "完了"
    results = shared_layout.results_directory("root", tmp_path)
    assert not (results / f"{killed['session_id']}.json").exists()
    assert (results / f"{uncollected['session_id']}.json").exists()
    sessions = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
    assert [item["session_id"] for item in sessions] == [uncollected["session_id"]]
    await manager.close()


@pytest.mark.asyncio
async def test_manager_without_writer_does_not_create_status_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """書込主体が無効なmanagerはsession開始後も状態ファイルを作成しない。"""
    manager = server_manager.AgentsServerManager(None)
    install_backend(manager, "codex", _FakeStatusBackend(manager.sessions))
    _use_candidates(monkeypatch, ("codex", "model", "medium"))
    monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))

    await manager.start("high_tier", "実装", str(tmp_path))

    assert not list(tmp_path.rglob("*.json"))
    await manager.close()


def _use_candidates(monkeypatch: pytest.MonkeyPatch, *candidates: tuple[str, str, str]) -> None:
    """session開始時に解決するモデル候補列を固定する。"""
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type: list(candidates))


def _status_writer(tmp_path: pathlib.Path) -> subject.StatusFileWriter:
    """公開managerを通した操作に使うルートwriterを返す。"""
    return subject.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )


class _FakeStatusBackend:
    """状態ファイルの公開フローだけを通す偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState]) -> None:
        self.sessions = sessions
        self.count = 0
        self.release_calls: list[str] = []

    async def start(
        self,
        _prompt: str,
        cwd: str,
        model: str,
        effort: str,
        *,
        model_type: str,
        launch_kind: state.LaunchKind,
        excluded_candidates: frozenset[state.ModelCandidate],
    ) -> state.SessionState:
        self.count += 1
        session = state.SessionState(
            f"session-{self.count}",
            cwd,
            model=model,
            effort=effort,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            turn_seq=1,
        )
        self.sessions[session.session_id] = session
        state.initialize_turn(session)
        return session

    async def close(self) -> None:
        """外部資源を持たないため何もしない。"""

    async def release_session(self, session_id: str) -> None:
        """解放対象を検証用に記録する。"""
        self.release_calls.append(session_id)

    async def send_message(self, session: state.SessionState, _prompt: str) -> dict[str, object]:
        """新しいreply turnを開始する。"""
        session.turn_seq += 1
        state.initialize_turn(session)
        return {"delivery": "reply_started", "previous_result": None}


class _UnavailableStatusBackend(_FakeStatusBackend):
    """指定モデルをengine利用不能として終端させる偽バックエンド。"""

    def __init__(self, sessions: dict[str, state.SessionState], *, unavailable_models: set[str]) -> None:
        super().__init__(sessions)
        self._unavailable_models = unavailable_models

    async def start(
        self,
        _prompt: str,
        cwd: str,
        model: str,
        effort: str,
        *,
        model_type: str,
        launch_kind: state.LaunchKind,
        excluded_candidates: frozenset[state.ModelCandidate],
    ) -> state.SessionState:
        session = await super().start(
            _prompt,
            cwd,
            model,
            effort,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
        )
        if session.model in self._unavailable_models:
            session.status = "failed"
            session.error = {"message": "usage limit", "codexErrorInfo": "usageLimitExceeded"}
            session.turn_completed = True
            session.touch()
        return session


class _DelayedUnavailableStatusBackend(_FakeStatusBackend):
    """起動応答後に指定モデルを利用不能として終端させる偽バックエンド。"""

    def __init__(
        self,
        sessions: dict[str, state.SessionState],
        *,
        condition: asyncio.Condition,
        unavailable_models: set[str],
    ) -> None:
        super().__init__(sessions)
        self._condition = condition
        self._unavailable_models = unavailable_models
        self._pending: list[asyncio.Task[None]] = []

    async def start(
        self,
        _prompt: str,
        cwd: str,
        model: str,
        effort: str,
        *,
        model_type: str,
        launch_kind: state.LaunchKind,
        excluded_candidates: frozenset[state.ModelCandidate],
    ) -> state.SessionState:
        session = await super().start(
            _prompt,
            cwd,
            model,
            effort,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
        )
        if session.model in self._unavailable_models:
            self._pending.append(asyncio.create_task(self._fail_after_response(session)))
        return session

    async def _fail_after_response(self, session: state.SessionState) -> None:
        await asyncio.sleep(0)
        session.status = "failed"
        session.error = {"message": "usage limit", "codexErrorInfo": "usageLimitExceeded"}
        session.turn_completed = True
        session.touch()
        async with self._condition:
            self._condition.notify_all()

    async def close(self) -> None:
        await asyncio.gather(*self._pending)
