"""Codexのreleaseを検証する。"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import sys

import psutil
import pytest

from agent_toolkit._agents_server import codex as subject
from agent_toolkit._common import session_registry
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing import fake_codex_app_server
from agent_toolkit._testing.codex_backend_support import (
    _create_integrated_lane,
    _fake_app_server_manager,
    _invoke_lane,
    _wait_terminal,
)

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_stop_terminates_owned_app_server_and_mcp(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """終了したCodex委譲のstopは、成功時点で所有するApp ServerとthreadのMCPを終了させ、同じ回でレーンを回収できる。

    模擬App Serverは実Codexと同じく、購読解除の後もMCPサーバー役の子プロセスを生存させる。
    """
    worktree = _create_integrated_lane(tmp_path, monkeypatch, capsys)
    async with _fake_app_server_manager(tmp_path, monkeypatch) as (manager, _backend, fake):
        started = await manager.start("plan", "担当", str(worktree))
        session_id = started["session_id"]
        await _wait_terminal(manager, session_id, fake)
        server_pid = fake.server_pid(session_id)
        mcp_pids = fake.mcp_pids(session_id)
        assert mcp_pids and [record["cwd"] for record in fake.events("mcp")] == [str(worktree)]
        assert fake.alive(server_pid) and all(fake.alive(pid) for pid in mcp_pids)

        assert await manager.stop(session_id) == {}

        assert not fake.alive(server_pid)
        assert not any(fake.alive(pid) for pid in mcp_pids)
        assert session_registry.resolve(session_id).state is session_registry.Resolution.RELEASED
        code, result, stderr = await asyncio.to_thread(_invoke_lane, capsys, "delete")
        assert code == 0, (result, stderr)
        assert result["removed"] == ["lane-01"]
        assert not worktree.exists()


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_release_keeps_other_session_and_resumes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """一方の委譲のstopは他方のApp Serverを残し、他方への追送と破棄した会話の再開がどちらも成功する。"""
    async with _fake_app_server_manager(tmp_path, monkeypatch) as (manager, _backend, fake):
        stopped_id = (await manager.start("plan", "破棄する担当", str(tmp_path)))["session_id"]
        other_id = (await manager.start("plan", "継続する担当", str(tmp_path)))["session_id"]
        await _wait_terminal(manager, stopped_id, fake)
        await _wait_terminal(manager, other_id, fake)
        stopped_server, other_server = fake.server_pid(stopped_id), fake.server_pid(other_id)
        assert stopped_server != other_server

        assert await manager.stop(stopped_id) == {}

        assert not fake.alive(stopped_server) and not any(fake.alive(pid) for pid in fake.mcp_pids(stopped_id))
        assert fake.alive(other_server) and all(fake.alive(pid) for pid in fake.mcp_pids(other_id))
        assert (await manager.send_message(other_id, "追加指示"))["delivery"] == "reply_started"
        other = await _wait_terminal(manager, other_id, fake)
        assert other.agent_message.startswith("完了: ") and "追加指示" in other.agent_message
        assert fake.events("turn/start")[-1]["server"] == other_server

        resumed_response = await manager.send_message(stopped_id, "再開指示")
        assert resumed_response["delivery"] == "reply_started", (resumed_response, manager.sessions[stopped_id].error)
        resumed = await _wait_terminal(manager, stopped_id, fake)
        assert resumed.agent_message.startswith("完了: ") and "再開指示" in resumed.agent_message
        resume_events = [record for record in fake.events("thread/resume") if record["thread"] == stopped_id]
        assert len(resume_events) == 1
        assert resume_events[0]["server"] not in {stopped_server, other_server}
        assert fake.alive(resume_events[0]["server"]) and fake.alive(other_server)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["retention", "candidate", "close"])
async def test_other_release_paths_terminate_owned_resources(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """保持期限の失効、候補切替前の断念、managerの終了も、所有するApp ServerとMCPの終了まで確かめる。"""
    candidates = [("codex", "gpt-test", "high")]
    if path == "candidate":
        candidates.insert(0, ("codex", fake_codex_app_server.UNAVAILABLE_MODEL, "high"))
    async with _fake_app_server_manager(tmp_path, monkeypatch, candidates) as (manager, _backend, fake):
        started = await manager.start("plan", "担当", str(tmp_path))
        session_id = started["session_id"]
        await _wait_terminal(manager, session_id, fake)
        threads = [record["thread"] for record in fake.events("thread/start")]
        if path == "candidate":
            # 可用性の失敗で断念した候補の資源は、次の候補の開始より前に解放済みである。
            assert len(threads) == 2 and threads[1] == session_id
            abandoned = threads[0]
            assert not fake.alive(fake.server_pid(abandoned))
            assert not any(fake.alive(pid) for pid in fake.mcp_pids(abandoned))
            assert fake.alive(fake.server_pid(session_id))
            return
        assert threads == [session_id]
        server_pid, mcp_pids = fake.server_pid(session_id), fake.mcp_pids(session_id)
        assert fake.alive(server_pid) and all(fake.alive(pid) for pid in mcp_pids)
        if path == "retention":
            manager._expire_session(session_id)
            await manager._resource_release_tasks[session_id]
            assert session_id in manager.expired_sessions
            assert session_registry.resolve(session_id).state is session_registry.Resolution.RELEASED
        else:
            await manager.close()
        assert not fake.alive(server_pid)
        assert not any(fake.alive(pid) for pid in mcp_pids)


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_unresolved_generation_stop_is_retried_instead_of_reported_released(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """生成の停止を確認できなかった停止は再試行できる失敗として返し、再発行で同じ所有対象を改めて回収する。

    POSIXでは再試行でも辿れない子が残り得るため、解放後も確認できない旨を残存診断へ記録する。
    """
    async with _fake_app_server_manager(tmp_path, monkeypatch) as (manager, _backend, fake):
        session_id = (await manager.start("plan", "担当", str(tmp_path)))["session_id"]
        await _wait_terminal(manager, session_id, fake)
        server_pid, mcp_pids = fake.server_pid(session_id), fake.mcp_pids(session_id)
        original_suspend, original_terminate, original_kill = (
            psutil.Process.suspend,
            psutil.Process.terminate,
            psutil.Process.kill,
        )
        suspends: list[int] = []
        job_stops: list[bool] = []
        first_stop = True
        original_job_terminate = subject.windows_job.WindowsJob.terminate

        def terminate_job(job, **kwargs):
            job_stops.append(first_stop)
            if first_stop:
                raise OSError("生成の停止を確認できない: Jobの終了要求を不受理")
            original_job_terminate(job, **kwargs)

        def suspend(process: psutil.Process) -> None:
            suspends.append(process.pid)
            # 1回目の停止ではApp Serverの一時停止と終了を拒否し、生成の停止を確認できないまま残る状態を作る。
            if first_stop and process.pid == server_pid:
                raise psutil.AccessDenied(process.pid)
            original_suspend(process)

        def terminate(process: psutil.Process) -> None:
            if not (first_stop and process.pid == server_pid):
                original_terminate(process)

        def kill(process: psutil.Process) -> None:
            if not (first_stop and process.pid == server_pid):
                original_kill(process)

        monkeypatch.setattr(psutil.Process, "suspend", suspend)
        monkeypatch.setattr(psutil.Process, "terminate", terminate)
        monkeypatch.setattr(psutil.Process, "kill", kill)
        if sys.platform == "win32":
            monkeypatch.setattr(subject.windows_job.WindowsJob, "terminate", terminate_job)
        with pytest.raises(ActionableError) as raised:
            await manager.stop(session_id)
        assert "生成の停止を確認できない" in str(raised.value)
        assert session_registry.resolve(session_id).state is not session_registry.Resolution.RELEASED
        stopped_once = len(job_stops if sys.platform == "win32" else suspends)
        first_stop = False

        with caplog.at_level(logging.WARNING, logger="agent_toolkit._common.process_tree"):
            assert await manager.stop(session_id) == {}
        retry_warnings = [record.getMessage() for record in caplog.records if "再試行でも確認できない" in record.getMessage()]
        assert bool(retry_warnings) is (sys.platform != "win32")
        # 解放済みの後の案内は、回収されない停止操作の再実行を勧めない。
        assert all("停止操作を再実行しても回収されない" in message for message in retry_warnings)

        # 再発行では成功を前提にせず、所有対象から子孫を改めて回収した。
        assert len(job_stops if sys.platform == "win32" else suspends) > stopped_once
        assert not fake.alive(server_pid)
        assert not any(fake.alive(pid) for pid in mcp_pids)
        assert session_registry.resolve(session_id).state is session_registry.Resolution.RELEASED


@pytest.mark.usefixtures("agents_server_isolation")
@pytest.mark.asyncio
async def test_release_failure_is_retryable(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """子孫を回収できないstopは再試行の方法とともに失敗し、解放済みと記録せず、再発行で回収を完了する。"""
    async with _fake_app_server_manager(tmp_path, monkeypatch) as (manager, _backend, fake):
        session_id = (await manager.start("plan", "担当", str(tmp_path)))["session_id"]
        session = await _wait_terminal(manager, session_id, fake)
        server_pid, mcp_pids = fake.server_pid(session_id), fake.mcp_pids(session_id)
        # 1回目は子孫が終了要求にも強制終了にも応じない状態を作る。
        unkillable = set(mcp_pids)
        original_terminate, original_kill = psutil.Process.terminate, psutil.Process.kill
        original_job_terminate = subject.windows_job.WindowsJob.terminate

        def terminate_job(job, **kwargs):
            if unkillable:
                raise TimeoutError("Jobの終了未確認: " + ", ".join(map(str, sorted(unkillable))))
            original_job_terminate(job, **kwargs)

        def terminate(process: psutil.Process) -> None:
            if process.pid not in unkillable:
                original_terminate(process)

        def kill(process: psutil.Process) -> None:
            if process.pid not in unkillable:
                original_kill(process)

        monkeypatch.setattr(psutil.Process, "terminate", terminate)
        monkeypatch.setattr(psutil.Process, "kill", kill)
        if sys.platform == "win32":
            monkeypatch.setattr(subject.windows_job.WindowsJob, "terminate", terminate_job)
        with pytest.raises(ActionableError) as raised:
            await manager.stop(session_id)
        assert session_id in str(raised.value)
        assert "`stop`を再発行" in raised.value.next_action
        assert all(str(pid) in str(raised.value) for pid in mcp_pids)
        assert manager.sessions[session_id] is session
        assert session_id not in manager.stopped_sessions
        assert session_registry.resolve(session_id).state is not session_registry.Resolution.RELEASED
        assert not fake.alive(server_pid)
        assert all(fake.alive(pid) for pid in mcp_pids)

        unkillable.clear()
        assert await manager.stop(session_id) == {}

        assert not any(fake.alive(pid) for pid in mcp_pids)
        assert session_registry.resolve(session_id).state is session_registry.Resolution.RELEASED
