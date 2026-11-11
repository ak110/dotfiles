# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""Claude CLIのturn状態と完了通知の順序を検証する。"""

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access

import asyncio
import pathlib
from types import SimpleNamespace

import pytest

from agent_toolkit._agents_server import resume_waits
from agent_toolkit._testing.agents_server_resume_support import (
    _RESUME_WAIT_TIMEOUT,
    AssistantMessage,
    ControlledClaudeClient,
    ResultMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    _await_state,
    _manager,
    _session_state,
    _start,
    _wait_with_timeout,
)
from agent_toolkit._testing.agents_server_resume_support import _isolate_session_registry as _isolate_session_registry


@pytest.mark.asyncio
@pytest.mark.parametrize("state_before_init", [False, True])
@pytest.mark.parametrize("task_type", ["local_bash", "local_agent"])
@pytest.mark.parametrize("notification", ["before_result", "after_result"])
async def test_wait_returns_resumed_result_regardless_of_notification_order(
    notification: str,
    task_type: str,
    state_before_init: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """完了通知が待機表明の結果より先に届いても、再開turnの結果を返し待機表明を返さない。

    Stop hookの実行中にバックグラウンドタスクが終わると、完了通知が`ResultMessage`より先に届いて追跡集合が空になる。
    追跡集合だけで判定すると待機表明を公開し、CLIが続けて開始する再開turnの結果を失う。
    到着順だけを変えた対照として、完了通知が結果の後に届く順序も同じ結果になることを確かめる。
    """
    client = ControlledClaudeClient(
        f"claude-order-{notification}-{task_type}-{state_before_init}", report_state=True, state_before_init=state_before_init
    )
    async with _manager(client, monkeypatch) as (manager, _backend):
        caplog.set_level("WARNING", logger="agent-toolkit.agents-server.claude")
        session = await _start(manager, tmp_path, monkeypatch)
        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        client.emit(TaskStartedMessage("bg-1", task_type=task_type))
        if notification == "before_result":
            client.emit(TaskNotificationMessage("bg-1", "completed"))
            client.finish_turn(ResultMessage("待機中: bg-1"), next_turn_queued=True)
            await _await_state(lambda: session.awaiting_auto_resume)
            assert wait_task.done() is False
        else:
            client.finish_turn(ResultMessage("待機中: bg-1"))
            await _await_state(lambda: session.awaiting_auto_resume and session.cli_turn_state == "idle")
            assert wait_task.done() is False
            client.emit(TaskNotificationMessage("bg-1", "completed"))
            client.emit(_session_state("running"))
        client.finish_turn(ResultMessage("RESUMED", origin={"kind": "task-notification"}))
        result = await wait_task

        assert result["status"] == "completed"
        assert result["agent_message"] == "RESUMED"
        assert not [record for record in caplog.records if "claude_turn_state_unreported" in record.getMessage()]


@pytest.mark.asyncio
async def test_result_without_background_task_is_published_on_idle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """バックグラウンドタスクの無いturnは、結果の直後に届く`idle`で従来どおり公開する。"""
    client = ControlledClaudeClient("claude-idle", report_state=True)
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        client.finish_turn(ResultMessage("通常結果"))
        result = await _wait_with_timeout(manager, 1)

        assert result["agent_message"] == "通常結果"
        assert session.cli_turn_state == "idle"


@pytest.mark.asyncio
async def test_idle_with_live_shell_task_keeps_result_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """シェルのバックグラウンドタスクが動いている間に届く`idle`では確定せず、そのタスクの再開turnの結果を返す。"""
    client = ControlledClaudeClient("claude-idle-live-task", report_state=True)
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        client.emit(TaskStartedMessage("shell-1"))
        client.finish_turn(ResultMessage("待機中: shell-1"))
        await _await_state(lambda: session.awaiting_auto_resume and session.cli_turn_state == "idle")
        await manager._advance_child_session_wait(session)
        await asyncio.sleep(0)
        assert wait_task.done() is False
        assert session.pending_result is not None

        client.emit(TaskNotificationMessage("shell-1", "completed"))
        client.emit(_session_state("running"))
        client.finish_turn(ResultMessage("RESUMED", origin={"kind": "task-notification"}))
        result = await wait_task

        assert result["agent_message"] == "RESUMED"


@pytest.mark.asyncio
async def test_unreported_turn_state_falls_back_with_single_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CLIがturn状態を報告しない場合は追跡集合だけの従来判定で公開し、警告を1回だけ記録する。"""
    client = ControlledClaudeClient("claude-unreported")
    async with _manager(client, monkeypatch) as (manager, _backend):
        caplog.set_level("WARNING", logger="agent-toolkit.agents-server.claude")
        session = await _start(manager, tmp_path, monkeypatch)
        client.emit(TaskStartedMessage("task-1"))
        client.emit(ResultMessage("初回結果"))
        await _await_state(lambda: session.awaiting_auto_resume)
        client.emit(TaskNotificationMessage("task-1", "completed"))
        client.emit(ResultMessage("再開結果", origin={"kind": "task-notification"}))
        result = await _wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT)

        assert result["agent_message"] == "再開結果"
        assert session.cli_turn_state is None
        warnings = [record for record in caplog.records if "claude_turn_state_unreported" in record.getMessage()]
        assert len(warnings) == 1


@pytest.mark.asyncio
async def test_resumed_turn_is_not_finalized_by_previous_held_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """完了通知で再開したturnの実行中に前の保留期限が到来しても、古い待機表明を公開せず、再開したturnの結果を返す。

    前のturnの保留を残すと、backendの待機ループとMCP層の常駐監視が期限の到来で待機表明を`completed`として公開し、
    委譲元は再開したturnの作業が終わる前に結果を受け取る。
    """
    monkeypatch.setattr(resume_waits, "AUTO_RESUME_DEADLINE_SECONDS", 0.1)
    client = ControlledClaudeClient("claude-stale-deadline", report_state=True)
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        client.emit(TaskStartedMessage("bg-1"))
        client.finish_turn(ResultMessage("待機中: bg-1"))
        await _await_state(lambda: session.awaiting_auto_resume and session.cli_turn_state == "idle")

        client.emit(TaskNotificationMessage("bg-1", "completed"))
        client.emit(_session_state("running"))
        client.emit(AssistantMessage([SimpleNamespace(id="impl-tool", name="Bash", input={"command": "make test"})]))
        await _await_state(lambda: session.cli_turn_state == "running" and session.last_action != "")
        loop = asyncio.get_running_loop()
        after_deadline = loop.time() + 0.3
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(loop, "time", lambda: after_deadline)
            await manager._advance_child_session_wait(session)
            await asyncio.sleep(0)

        assert wait_task.done() is False
        assert session.pending_result is None
        assert (await manager.show_session(session.session_id))["status"] == "running"
        client.finish_turn(ResultMessage("RESUMED", origin={"kind": "task-notification"}))
        result = await wait_task

        assert (result["status"], result["agent_message"]) == ("completed", "RESUMED")
        assert "error" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("task_type", ["local_bash", "local_agent"])
@pytest.mark.parametrize("notification", ["before_result", "after_result"])
async def test_previous_held_result_is_released_on_resumed_turn_start(
    notification: str,
    task_type: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """完了通知が前の結果より先でも後でも、再開したturnの開始で前の保留を解除し、期限の到来で待機表明を公開しない。

    Stopの処理中に完了通知が先に届くと、CLIは`idle`を報告せずに次のturnを始め、`running`は保留の前に報告済みとなる。
    この順序では次のturnの開始をモデルの出力で観測するため、`running`だけを契機にすると保留が残る。
    """
    monkeypatch.setattr(resume_waits, "AUTO_RESUME_DEADLINE_SECONDS", 0.1)
    client = ControlledClaudeClient(f"claude-release-{notification}-{task_type}", report_state=True)
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        client.emit(TaskStartedMessage("bg-1", task_type=task_type))
        if notification == "before_result":
            client.emit(TaskNotificationMessage("bg-1", "completed"))
            client.finish_turn(ResultMessage("待機中: bg-1"), next_turn_queued=True)
            await _await_state(lambda: session.awaiting_auto_resume)
        else:
            client.finish_turn(ResultMessage("待機中: bg-1"))
            await _await_state(lambda: session.awaiting_auto_resume and session.cli_turn_state == "idle")
            client.emit(TaskNotificationMessage("bg-1", "completed"))
            client.emit(_session_state("running"))
        client.emit(AssistantMessage([SimpleNamespace(type="text", text="再開した作業を続ける")]))
        await _await_state(lambda: not session.awaiting_auto_resume)
        loop = asyncio.get_running_loop()
        after_deadline = loop.time() + 0.3
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr(loop, "time", lambda: after_deadline)
            await manager._advance_child_session_wait(session)
            await asyncio.sleep(0)
        assert wait_task.done() is False

        client.finish_turn(ResultMessage("RESUMED", origin={"kind": "task-notification"}))
        result = await wait_task

        assert (result["status"], result["agent_message"]) == ("completed", "RESUMED")
