"""`_agents_server/manager.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import json
import pathlib
from types import SimpleNamespace
from typing import Any

import pytest

from agent_toolkit._agents_server import agents_wait, shared_layout, shared_roots, state, status_file
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_support import (
    BlockingInterruptBackend,
    FakeBackend,
    _actionable_message,
    _complete,
    _default_identity_fields,
    _manager_with_fake,
    _write_notice,
    install_backend,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_kill_timeout_zero_returns_request_state(tmp_path: pathlib.Path) -> None:
    """killのtimeout=0は中断要求の受理後に現在状態を返す。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        manager.sessions[session.session_id] = session

        response = await manager.kill(session.session_id, timeout=0)

        assert response == {
            "status": "running",
            "kill_requested": True,
        }
        assert backend.interrupt_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("with_notice", [False, True])
async def test_kill_includes_notices_only_when_available(
    with_notice: bool,
    tmp_path: pathlib.Path,
) -> None:
    """killは回収した通知がある場合だけnoticesを返す。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1", announced=True)
        manager.sessions[session.session_id] = session
        writer.activate()
        if with_notice:
            notices = shared_layout.notices_directory("root-session", tmp_path)
            _write_notice(notices, session.session_id, 1, "2026-09-06T00:00:01+00:00", "中断前の通知")

        response = await manager.kill(session.session_id, timeout=0)

        expected: dict[str, Any] = {"status": "running", "kill_requested": True}
        if with_notice:
            expected["notices"] = [{"body": "中断前の通知"}]
        assert response == expected
        await manager.close()


@pytest.mark.asyncio
async def test_kill_waits_for_terminal_result_and_preserves_request_marker(tmp_path: pathlib.Path) -> None:
    """正のtimeoutを指定したkillは終了時の結果と要求済み状態を返す。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        manager.sessions[session.session_id] = session

        kill_task = asyncio.create_task(manager.kill(session.session_id, timeout=1))
        while backend.interrupt_calls == 0:
            await asyncio.sleep(0)
        _complete(session, message="中断結果")
        await manager._notify_waiters()

        assert await kill_task == {
            "status": "completed",
            "agent_message": "中断結果",
            "kill_requested": True,
            **_default_identity_fields(),
        }


@pytest.mark.asyncio
async def test_kill_terminal_session_is_idempotent_without_backend_request(tmp_path: pathlib.Path) -> None:
    """終了済みsessionへのkillは要求を送らず結果を返す。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex")
        _complete(session, message="既存結果")
        manager.sessions[session.session_id] = session

        response = await manager.kill(session.session_id, timeout=0)

        assert response == {
            "status": "completed",
            "agent_message": "既存結果",
            "kill_requested": False,
            **_default_identity_fields(),
        }
        assert backend.interrupt_calls == 0


@pytest.mark.asyncio
async def test_concurrent_kill_requests_share_one_backend_request(tmp_path: pathlib.Path) -> None:
    """同一turnへの並行killは中断要求を1回だけ送る。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        manager.sessions[session.session_id] = session

        first, second = await asyncio.gather(
            manager.kill(session.session_id, timeout=0),
            manager.kill(session.session_id, timeout=0),
        )

        assert first["kill_requested"] is True
        assert second["kill_requested"] is True
        assert backend.interrupt_calls == 1


@pytest.mark.asyncio
async def test_kill_lock_wait_respects_positive_timeout(tmp_path: pathlib.Path) -> None:
    """正のtimeoutはturn制御ロックの取得待ちにも適用する。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        manager.sessions[session.session_id] = session

        async with session.turn_control_lock:
            with pytest.raises(TimeoutError, match="kill timed out: thread-1") as raised:
                await manager.kill(session.session_id, timeout=0.01)

        next_action = _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
        assert "atk agents wait" in next_action
        assert "`kill`" in next_action
        assert backend.interrupt_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -0.1])
async def test_send_message_rejects_non_positive_timeout(timeout: float, tmp_path: pathlib.Path) -> None:
    """継続要求のtimeoutは正の値だけを受理し、backendへ配送しない。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        manager.sessions[session.session_id] = session

        with pytest.raises(ValueError, match="timeout must be positive") as raised:
            await manager.send_message(session.session_id, "追加指示", timeout=timeout)

        assert "`timeout`を省略" in _actionable_message(raised.value)
        assert backend.send_calls == 0


@pytest.mark.asyncio
async def test_kill_rejects_negative_timeout_with_default_hint(tmp_path: pathlib.Path) -> None:
    """killの負のtimeoutは、引数を省略すれば待機上限を270秒とすることを次の操作で示す。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        manager.sessions[session.session_id] = session

        with pytest.raises(ValueError, match="timeout must be non-negative") as raised:
            await manager.kill(session.session_id, timeout=-1)

        assert "`timeout`を省略" in _actionable_message(raised.value)
        assert backend.interrupt_calls == 0


@pytest.mark.asyncio
async def test_kill_timeout_zero_bounds_turn_control_lock_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """killのtimeout=0でも中断要求配送前のロック待ちを有限時間で打ち切る。"""
    monkeypatch.setattr(server_manager, "DEFAULT_SEND_MESSAGE_TIMEOUT", 0.01, raising=False)
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        manager.sessions[session.session_id] = session

        async with session.turn_control_lock:
            with pytest.raises(TimeoutError, match="the interrupt request was not delivered"):
                await asyncio.wait_for(manager.kill(session.session_id, timeout=0), timeout=0.1)

        assert backend.interrupt_calls == 0


@pytest.fixture(name="kill_deadline_clock")
def _kill_deadline_clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """managerの残量計算だけを固定し、実際のwait_forタイマーは維持する。"""
    clock = SimpleNamespace(value=0.0)
    loop = SimpleNamespace(time=lambda: clock.value)
    proxy = SimpleNamespace(**{**vars(asyncio), "get_running_loop": lambda: loop})
    monkeypatch.setattr(server_manager, "asyncio", proxy)
    return clock


@pytest.mark.asyncio
@pytest.mark.usefixtures("kill_deadline_clock")
async def test_kill_timeout_zero_bounds_interrupt_delivery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """killのtimeout=0でも中断要求の配送待ちを有限時間で打ち切る。"""
    monkeypatch.setattr(server_manager, "DEFAULT_SEND_MESSAGE_TIMEOUT", 0.01, raising=False)
    async with server_manager.AgentsServerManager() as manager:
        backend = BlockingInterruptBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        manager.sessions[session.session_id] = session

        with pytest.raises(TimeoutError, match="interrupt delivery is undetermined"):
            await asyncio.wait_for(manager.kill(session.session_id, timeout=0), timeout=1)

        assert session.session_id in manager.sessions
        assert backend.interrupt_started.is_set()
        assert backend.interrupt_calls == 1
        assert session.interrupt_requested is False

        backend.release_interrupt.set()
        response = await manager.kill(session.session_id, timeout=0)

        assert response["kill_requested"] is True
        assert backend.interrupt_calls == 2


@pytest.mark.asyncio
async def test_send_message_timeout_covers_turn_control_lock(tmp_path: pathlib.Path) -> None:
    """継続要求のtimeoutはturn制御ロックの取得を含む操作全体へ適用する。"""
    async with (
        server_manager.AgentsServerManager() as manager,
        claude_backend.ClaudeServerManager(manager.sessions, manager._condition) as backend,
    ):
        install_backend(manager, "claude", backend)
        session = state.SessionState("claude-locked", str(tmp_path), engine="claude")
        manager.sessions[session.session_id] = session

        async with session.turn_control_lock:
            with pytest.raises(TimeoutError, match="send_message timed out: claude-locked"):
                await manager.send_message(session.session_id, "追加指示", timeout=0.01)


@pytest.mark.asyncio
@pytest.mark.usefixtures("kill_deadline_clock")
async def test_kill_timeout_after_delivery_distinguishes_terminal_wait(
    tmp_path: pathlib.Path,
) -> None:
    """中断要求の配送後に終了待ちが上限を超えた場合を、未配送と区別する。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        manager.sessions[session.session_id] = session

        with pytest.raises(
            TimeoutError,
            match="the interrupt request was delivered but the turn did not terminate",
        ):
            await manager.kill(session.session_id, timeout=0.01)

        assert backend.interrupt_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, 0.01])
async def test_kill_restores_state_when_delivery_budget_expires_before_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    kill_deadline_clock: SimpleNamespace,
    timeout: float,
) -> None:
    """配送前の処理で待機時間の上限に達した入力は、backendへ到達せず、状態が元へ戻ることを検証する。"""
    monkeypatch.setattr(server_manager, "DEFAULT_SEND_MESSAGE_TIMEOUT", 0.01)
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex", turn_id="turn-1")
        manager.sessions[session.session_id] = session
        original_touch = state.SessionState.touch

        def consume_delivery_budget(current: state.SessionState) -> None:
            if current.interrupt_requested:
                kill_deadline_clock.value += 1
            original_touch(current)

        monkeypatch.setattr(state.SessionState, "touch", consume_delivery_budget)
        with pytest.raises(TimeoutError, match="interrupt delivery is undetermined") as raised:
            await asyncio.wait_for(manager.kill(session.session_id, timeout=timeout), timeout=1)

        assert backend.interrupt_calls == 0
        assert session.session_id in manager.sessions
        assert session.interrupt_requested is False
        assert "`atk agents wait`" in _actionable_message(raised.value)


@pytest.mark.asyncio
async def test_kill_timeout_before_codex_turn_id_reports_not_delivered(tmp_path: pathlib.Path) -> None:
    """Codexのturn_idの待機が上限を超えた場合は、中断要求を未配送として報告する。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex")
        manager.sessions[session.session_id] = session

        with pytest.raises(TimeoutError, match="the interrupt request was not delivered"):
            await manager.kill(session.session_id, timeout=0.01)

        assert backend.interrupt_calls == 0


@pytest.mark.asyncio
async def test_kill_stop_discards_only_after_terminal_result(tmp_path: pathlib.Path) -> None:
    """stop=trueのkillがrunningを返す間はsessionを保持し、終了済みの応答を返した後だけ破棄する。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("kill-stop", str(tmp_path), engine="codex")
        session.turn_id = "turn"
        manager.sessions[session.session_id] = session

        running = await manager.kill(session.session_id, timeout=0, stop=True)
        assert running["status"] == "running"
        assert session.session_id in manager.sessions
        _complete(session)
        session.status = "interrupted"
        response = await manager.kill(session.session_id, timeout=0, stop=True)

        assert response["status"] == "interrupted"
        assert response["kill_requested"] is False
        assert session.session_id in manager.stopped_sessions
        retained = await manager.kill(session.session_id, timeout=0)
        assert retained["status"] == "interrupted"
        assert retained["agent_message"] == "完了"
        assert retained["kill_requested"] is False
        assert backend.release_calls == [session.session_id]


@pytest.mark.asyncio
async def test_kill_stop_retains_result_for_agents_wait(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """stop=trueで破棄した結果をatk agents waitからも回収できる。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        writer.activate()
        session = state.SessionState("wait-stop-cli", str(tmp_path), engine="codex", announced=True)
        _complete(session, message="CLI回収")
        manager.sessions[session.session_id] = session

        await manager.kill(session.session_id, timeout=0, stop=True)
        result_path = shared_layout.results_directory("root-session", tmp_path) / f"{session.session_id}.json"
        assert result_path.exists()
        assert (
            agents_wait.wait_for_result(
                environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
                state_root=tmp_path,
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out)["agent_message"] == "CLI回収"
        assert not result_path.exists()
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine", "model_type"), (("codex", None), ("claude", "implementation_fast")))
async def test_expired_session_kill_returns_success_response(
    engine: str,
    model_type: str | None,
    tmp_path: pathlib.Path,
) -> None:
    """保持期限切れsessionへのkillは中断対象が無い成功応答を返す。"""
    async with _manager_with_fake(engine) as (manager, backend):
        session = state.SessionState("expired", str(tmp_path), engine=engine, model_type=model_type)
        _complete(session)
        session.retention_deadline = asyncio.get_running_loop().time() - 1
        manager.sessions[session.session_id] = session

        response = await manager.kill(session.session_id, timeout=0)

        assert "atk agents wait" in response.pop("next_action")
        assert response == {"status": "expired", "kill_requested": False}
        assert "expired" not in manager.sessions
        assert manager.expired_sessions["expired"].session_id == "expired"
        assert backend.interrupt_calls == 0
