"""status_fileのmanagerの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import json
import pathlib

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import shared_layout, shared_roots
from agent_toolkit._agents_server import status_file as subject
from agent_toolkit._testing.agents_server_status_file_support import (
    _DelayedUnavailableStatusBackend,
    _FakeStatusBackend,
    _status_writer,
    _UnavailableStatusBackend,
    _use_candidates,
    _wait_now,
)
from agent_toolkit._testing.agents_server_support import install_backend

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


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
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = _FakeStatusBackend(manager.sessions)
        install_backend(manager, "codex", backend)
        _use_candidates(monkeypatch, ("codex", "gpt-5.6-terra", "medium"))
        monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
        writer.activate()

        started = await manager.start("execute", "\n  実装を開始\n続き", str(tmp_path))
        await manager.start_explore("調査する", str(tmp_path))
        await manager.start_shell("pytest -q", str(tmp_path), "結果を要約")
        writer.flush()
        payload = json.loads(writer.path.read_text(encoding="utf-8"))
        assert [item["launch_kind"] for item in payload["sessions"]] == ["delegate", "explore", "shell"]
        assert [item["label"] for item in payload["sessions"]] == ["実装を開始", "explore", "shell-pytest"]

        session = manager.sessions[started["session_id"]]
        session.status = "completed"
        session.agent_message = "完了"
        session.record_turn_completed()
        session.touch()
        await _wait_now(manager)
        writer.flush()
        result_path = shared_layout.results_directory("root", tmp_path) / f"{session.session_id}.json"
        assert not result_path.exists()
        remaining = json.loads(writer.path.read_text(encoding="utf-8"))["sessions"]
        assert session.session_id not in {item["session_id"] for item in remaining}
        await manager.close()
        # 公開済み結果を回収した場合は共有claim lockが残るため、自身の射影の削除を判定する。
        assert not writer.path.exists()


@pytest.mark.asyncio
async def test_manager_removes_previous_result_when_new_turn_starts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """同じsessionの新しいturnを開始した時点で前の結果を削除する。"""
    writer = _status_writer(tmp_path)
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = _FakeStatusBackend(manager.sessions)
        install_backend(manager, "codex", backend)
        _use_candidates(monkeypatch, ("codex", "model", "medium"))
        monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
        writer.activate()
        started = await manager.start("execute", "実装", str(tmp_path))
        session = manager.sessions[started["session_id"]]
        session.status = "completed"
        session.agent_message = "前の結果"
        session.record_turn_completed()
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
    async with server_manager.AgentsServerManager(writer) as manager:
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

        response = await manager.start("execute", "実装", str(tmp_path))
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
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = _UnavailableStatusBackend(manager.sessions, unavailable_models={"first", "second"})
        install_backend(manager, "codex", backend)
        _use_candidates(monkeypatch, ("codex", "first", "high"), ("codex", "second", "high"))
        monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
        writer.activate()

        response = await manager.start("execute", "実装", str(tmp_path))
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
    """killで返した結果だけを除き、未回収の終了時の結果は表示に残す。"""
    writer = _status_writer(tmp_path)
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = _FakeStatusBackend(manager.sessions)
        install_backend(manager, "codex", backend)
        _use_candidates(monkeypatch, ("codex", "model", "medium"))
        monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))
        writer.activate()
        killed = await manager.start("execute", "kill対象", str(tmp_path))
        uncollected = await manager.start("execute", "未回収", str(tmp_path))
        for session_id in (killed["session_id"], uncollected["session_id"]):
            session = manager.sessions[session_id]
            session.status = "completed"
            session.agent_message = "完了"
            session.record_turn_completed()
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
    async with server_manager.AgentsServerManager(None) as manager:
        install_backend(manager, "codex", _FakeStatusBackend(manager.sessions))
        _use_candidates(monkeypatch, ("codex", "model", "medium"))
        monkeypatch.setattr(manager, "_await_start_outcome", lambda _session: asyncio.sleep(0))

        await manager.start("execute", "実装", str(tmp_path))

        assert not list(tmp_path.rglob("*.json"))
        await manager.close()
