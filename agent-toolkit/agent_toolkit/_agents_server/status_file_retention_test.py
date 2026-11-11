"""status_fileのretentionの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import datetime
import json
import pathlib

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import retained_results, shared_layout, shared_roots, state
from agent_toolkit._agents_server import status_file as subject
from agent_toolkit._testing.agents_server_status_file_support import (
    _completed_with_retention_deadline,
    _root_writer,
    _shown_sessions,
    _terminal_parent,
    _until,
    _wait_now,
    _write_descendant_file,
)

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


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
    # 期限で予約した保存はファイル操作のスレッドを経て完了するため、固定時間でなく状態ファイルの反映を待つ。
    await _until(lambda: not json.loads(writer.path.read_text(encoding="utf-8"))["sessions"])
    assert result_path.exists()
    writer.deactivate()
    assert result_path.exists()


@pytest.mark.asyncio
async def test_writer_retains_result_without_live_session_after_deadline(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """破棄済みsessionから保持した結果も期限到達後に維持する。"""
    session = _completed_with_retention_deadline("stopped", tmp_path)
    writer = _root_writer({}, tmp_path)
    writer.activate()
    writer.retain_result(session)
    writer.flush()
    result_path = shared_layout.results_directory("root", tmp_path) / "stopped.json"
    assert result_path.exists()
    assert writer._retention_handle is None

    deadline = session.retention_deadline
    assert deadline is not None
    with monkeypatch.context() as clock_patch:
        clock_patch.setattr(asyncio.get_running_loop(), "time", lambda: deadline + 1)
        writer.flush()

    assert result_path.exists()
    writer.deactivate()
    assert result_path.exists()


@pytest.mark.asyncio
async def test_writer_removes_waited_result_at_retention_deadline(tmp_path: pathlib.Path) -> None:
    """waitで回収済みになった結果を次のflushで削除する。"""
    session = _completed_with_retention_deadline("waited", tmp_path, turn_seq=1)
    writer = _root_writer({session.session_id: session}, tmp_path)
    async with server_manager.AgentsServerManager(writer) as manager:
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


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
async def test_writer_keeps_collected_parent_while_grandchild_runs(tmp_path: pathlib.Path, status: str) -> None:
    """結果を回収した終了の親も、孫の稼働中は実際の終了状態のまま表示し、結果は再公開しない。"""
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
    """結果を回収した場合の対照として、未回収かつ表示期限内の親は孫の終了後も従来どおり表示に残る。"""
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
    """複数の孫の一部だけが終了した場合と、終了した孫の先で稼働が続く場合も親を残す。"""
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
