"""公開`start`がマシン全体の上限で待ち、空きで進み、待った秒数を起動結果へ返すことを確かめる。"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

from agent_toolkit._agents_server import admission, mcp_tools
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common import state_paths
from agent_toolkit._testing import agents_server_support


@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_public_start_waits_for_free_slot_and_reports_wait(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上限1で作業中が1件ある間は公開startが待ち、空いたら起動して待ち時間を応答へ載せる。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_AGENTS_CONCURRENCY", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CHORES_FAST_MODEL", "claude:test/medium")
    monkeypatch.setattr(admission, "POLL_SECONDS", 0.05)
    busy = {"working": 1}
    original = admission.observe

    def observe(state_root: pathlib.Path | None = None, *, now: float | None = None) -> admission.Observation:
        observed = original(state_root, now=now)
        # 別の委譲先が作業中である状態を、状態ファイルの集計結果へ加えて表す。
        return admission.Observation(observed.working + busy["working"], observed.admitted, observed.waiting)

    monkeypatch.setattr(admission, "observe", observe)
    async with server_manager.AgentsServerManager(None) as current:
        backend = agents_server_support.FakeBackend(current.sessions, "claude")
        agents_server_support.install_backend(current, "claude", backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        launch = asyncio.create_task(mcp_tools.start(mode="explore", cwd=str(tmp_path), prompt="依頼"))
        deadline = asyncio.get_running_loop().time() + 10
        while original(tmp_path / "state").waiting != 1:
            assert asyncio.get_running_loop().time() < deadline, "起動待ちとして登録されない"
            await asyncio.sleep(0.02)
        assert not launch.done() and backend.start_calls == []
        busy["working"] = 0
        response = await asyncio.wait_for(launch, timeout=10)
    assert len(backend.start_calls) == 1
    assert response["admission_wait_seconds"] >= admission.POLL_SECONDS
    assert response["admission_limit"].startswith("agents_concurrency=1")


def test_resume_wait_is_recorded_in_last_action() -> None:
    """止まったsessionを上限待ちの後に動かすと、一覧とshowが表示する最後の操作へ待ちが残る。"""
    from agent_toolkit._agents_server import manager_resume  # pylint: disable=import-outside-toplevel
    from agent_toolkit._agents_server.state import SessionState  # pylint: disable=import-outside-toplevel

    session = SessionState.__new__(SessionState)
    session.last_action = ""
    manager_resume.note_admission_wait(session, admission.Admission(None, 3.2, admission.Limit(1, "agents_concurrency=1")))
    assert "3.2秒" in session.last_action and "agents_concurrency=1" in session.last_action
    manager_resume.note_admission_wait(session, admission.Admission(None, 0.0, admission.Limit(1, "x")))
    assert "3.2秒" in session.last_action  # 待ちが無ければ書き換えない
