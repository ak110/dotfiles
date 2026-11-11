# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""Claude backendのバックグラウンドタスクと保留結果を検証する。"""

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access

import asyncio
import json
import pathlib
from types import SimpleNamespace

import pytest

from agent_toolkit._agents_server import resume_waits, state
from agent_toolkit._common import session_registry
from agent_toolkit._testing.agents_server_resume_support import (
    _RESUME_WAIT_TIMEOUT,
    AssistantMessage,
    ControlledClaudeClient,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    UserMessage,
    _await_state,
    _emit_child_start,
    _manager,
    _session_state,
    _start,
    _wait_with_timeout,
)
from agent_toolkit._testing.agents_server_resume_support import _isolate_session_registry as _isolate_session_registry


@pytest.mark.asyncio
async def test_wait_returns_last_result_after_chained_auto_resumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """再開turnが背景taskを残して終わる間は結果を返さず、最後の再開turnの結果だけを返す。

    最後のtaskの終了から再開turnの結果までの間に待機の刻みが到来しても、保留中の結果を確定しない。
    """
    client = ControlledClaudeClient("claude-chain")
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        client.emit(TaskStartedMessage("task-1"))
        client.emit(ResultMessage("待機中: task-1"))
        await _await_state(lambda: session.awaiting_auto_resume)

        client.emit(TaskNotificationMessage("task-1", "completed"))
        client.emit(TaskStartedMessage("task-2"))
        client.emit(ResultMessage("待機中: task-2", origin={"kind": "task-notification"}))
        await _await_state(
            lambda: (
                set(session.live_tasks) == {"task-2"}
                and session.pending_result is not None
                and session.pending_result["agent_message"] == "待機中: task-2"
            )
        )
        assert wait_task.done() is False

        client.emit(TaskNotificationMessage("task-2", "completed"))
        await _await_state(lambda: not session.live_tasks)
        await manager._advance_child_session_wait(session)
        await asyncio.sleep(0)
        assert wait_task.done() is False
        assert session.awaiting_auto_resume is True

        client.emit(ResultMessage("最終結果", origin={"kind": "task-notification"}))
        result = await wait_task

        assert result["status"] == "completed"
        assert result["agent_message"] == "最終結果"


@pytest.mark.asyncio
async def test_wait_skips_initial_result_and_returns_auto_resumed_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """バックグラウンドタスクが残る初回結果を返さず、自動再開turnの結果を返す。"""
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.05)
    client = ControlledClaudeClient()
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        client.emit(TaskStartedMessage("task-1"))
        client.emit(ResultMessage("初回結果"))
        await _await_state(lambda: session.awaiting_auto_resume)

        assert wait_task.done() is False
        assert session.status == "running"
        assert session.turn_completed is False

        client.emit(TaskUpdatedMessage("task-1", "completed"))
        client.emit(TaskNotificationMessage("task-1", "completed"))
        client.emit(ResultMessage("再開結果", origin={"kind": "task-notification"}))
        result = await wait_task

        assert result["status"] == "completed"
        assert result["agent_message"] == "再開結果"
        assert not session.live_tasks
        await _await_state(lambda: session.session_id not in manager.sessions)
        assert await _wait_with_timeout(manager, 0) == {"status": "expired"}


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("remaining", [None, "task", "child"])
async def test_claude_collects_background_wait_before_holding_result(
    saved: bool,
    remaining: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """背景waitの終了・読取・turn終了の順序で回収を反映し、残る対象だけを待って結果を公開する。"""
    client = ControlledClaudeClient("claude-collected-wait")
    async with _manager(client, monkeypatch) as (manager, _backend):
        output = tmp_path / "background-output.txt"
        output.write_text("", encoding="utf-8")
        child = "collected-alternate-child"
        session = await _start(manager, tmp_path, monkeypatch)
        _emit_child_start(client, child)
        if remaining == "child":
            session_registry.publish("remaining-child", state=session_registry.PublicationState.PENDING_RUNNING)
            _emit_child_start(client, "remaining-child")
        elif remaining == "task":
            client.emit(TaskStartedMessage("remaining-task"))
        client.emit(AssistantMessage([SimpleNamespace(id="wait-tool", name="Bash", input={"command": "atk agents wait"})]))
        client.emit(
            UserMessage(
                [
                    SimpleNamespace(
                        tool_use_id="wait-tool",
                        content=f"Command running in background with ID: wait-job. Output is being written to: {output}.",
                    )
                ]
            )
        )
        await _await_state(lambda: str(output) in session.agents_wait_background_outputs)
        terminal = json.dumps({"session_id": child, "status": "completed", "agent_message": "子の結果"})
        if saved:
            saved_output = tmp_path / "saved-result.jsonl"
            saved_output.write_text(terminal, encoding="utf-8")
            output.write_text(f"保存先: {saved_output}\n", encoding="utf-8")
        else:
            output.write_text(terminal, encoding="utf-8")
        client.emit(TaskUpdatedMessage("wait-job", "completed"))
        client.emit(ResultMessage("実装完了"))
        await _await_state(lambda: session.turn_completed or session.awaiting_auto_resume)

        assert child not in session.live_child_session_ids
        assert session.awaiting_auto_resume is (remaining is not None)
        if remaining == "task":
            assert "remaining-task" in session.live_tasks
            client.emit(TaskNotificationMessage("remaining-task", "completed"))
            client.emit(ResultMessage("残作業後の結果", origin={"kind": "task-notification"}))
        elif remaining == "child":
            session_registry.publish("remaining-child", state=session_registry.PublicationState.RESULT_COMPLETED)
        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        if remaining == "child":
            await _await_state(lambda: len(client.queries) == 2)
            assert child not in client.queries[-1]
            client.emit(ResultMessage("残作業後の結果"))
        result = await wait_task
        assert result["status"] == "completed"
        assert result["agent_message"] == ("実装完了" if remaining is None else "残作業後の結果")
        assert "error" not in result
        if remaining is None:
            assert len(client.queries) == 1


@pytest.mark.asyncio
async def test_result_without_background_task_is_immediately_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """バックグラウンドタスクが無いturnは従来どおり最初の結果で終了する。"""
    client = ControlledClaudeClient()
    async with _manager(client, monkeypatch) as (manager, _backend):
        await _start(manager, tmp_path, monkeypatch)
        client.emit(ResultMessage("通常結果"))
        result = await _wait_with_timeout(manager, 1)

        assert result["agent_message"] == "通常結果"


@pytest.mark.asyncio
async def test_pending_result_waits_for_background_task_up_to_bash_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保留の期限は背景実行のBashの上限より後に置き、その前に届いた完了通知の再開turnの結果を返す。

    期限が上限より短いと、規範どおり上限内に収まるバックグラウンドタスクを待つ保留が打ち切られ、待機表明が返る。
    """
    client = ControlledClaudeClient("claude-bash-limit")
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        client.emit(TaskStartedMessage("long-ci-wait"))
        client.emit(ResultMessage("待機中: long-ci-wait"))
        await _await_state(lambda: session.awaiting_auto_resume)
        deadline = session.auto_resume_deadline
        assert deadline is not None
        assert deadline - asyncio.get_running_loop().time() >= resume_waits.CLAUDE_BACKGROUND_BASH_MAX_SECONDS

        client.emit(TaskNotificationMessage("long-ci-wait", "completed"))
        client.emit(ResultMessage("CIの結果を確認した", origin={"kind": "task-notification"}))
        result = await wait_task

        assert result["agent_message"] == "CIの結果を確認した"
        assert "error" not in result


@pytest.mark.parametrize("task_type", ["local_agent", "unknown_future_task"])
@pytest.mark.asyncio
async def test_background_agent_survives_bash_notification_deadline(
    task_type: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Bashの期限を超えた背景エージェントも終了通知と再開turnの最終結果まで追跡する。"""
    client = ControlledClaudeClient("claude-unbounded-task", report_state=True)
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        client.emit(TaskStartedMessage("agent", task_type=task_type))
        client.finish_turn(ResultMessage("背景エージェントを待機中"))
        await _await_state(lambda: session.awaiting_auto_resume and session.cli_turn_state == "idle")
        session.set_background_deadline(0.0)
        # メッセージ受信を進め、backendとmanager双方の期限判定を通す。
        client.emit(TaskUpdatedMessage("agent", "running"))
        assert (await _wait_with_timeout(manager, 0))["status"] == "running"
        assert session.awaiting_auto_resume
        client.emit(TaskNotificationMessage("agent", "completed"))
        client.emit(_session_state("running"))
        client.finish_turn(ResultMessage("背景エージェントの成果を反映した", origin={"kind": "task-notification"}))
        result = await _wait_with_timeout(manager, 1)
        assert result["agent_message"] == "背景エージェントの成果を反映した"
        assert "error" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", ["deadline", "stream_end"])
async def test_pending_result_is_finalized_without_auto_resume(
    completion: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """自動再開が届かない場合は期限またはストリーム終端で初回結果を確定し、残ったバックグラウンドタスクを`error`に示す。

    確定した結果は待機表明であり、印が無いと委譲元は再開したturnの結果と取り違えて作業を進める。
    """
    monkeypatch.setattr(resume_waits, "AUTO_RESUME_DEADLINE_SECONDS", 0.03)
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.03)
    client = ControlledClaudeClient(f"claude-{completion}")
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        client.emit(TaskStartedMessage("task-1"))
        client.emit(ResultMessage("保留結果"))
        await _await_state(lambda: session.awaiting_auto_resume)
        if completion == "stream_end":
            client.end_stream()

        result = await _wait_with_timeout(manager, 1)

        assert result["agent_message"] == "保留結果"
        assert result["error"] == {"unfinishedBackgroundTasks": ["task-1"], "heldResultFinalized": True}
        assert session.awaiting_auto_resume is False
        assert session.pending_result is None
        await _await_state(lambda: session.session_id not in manager.sessions)
        assert await _wait_with_timeout(manager, 0) == {"status": "expired"}


@pytest.mark.asyncio
async def test_kill_interrupts_while_initial_result_is_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保留中の中断要求はSDKへ配送し、CLI回収後に保留本文付きの停止結果を返して同じ会話の継続を受け付ける。

    SDKはturnの外で受けた中断要求へ何も返さないため、保留結果を確定しないと`kill`はtimeoutまで待つ。
    """
    client = ControlledClaudeClient("claude-kill")
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        client.emit(TaskStartedMessage("task-1"))
        client.emit(ResultMessage("初回結果"))
        await _await_state(lambda: session.awaiting_auto_resume)

        result = await manager.kill(session.session_id, timeout=1)

        assert result["status"] == "interrupted"
        assert result["agent_message"] == "初回結果"
        assert result["error"] == {"unfinishedBackgroundTasks": ["task-1"], "heldResultFinalized": True}
        assert result["kill_requested"] is True
        assert client.interrupts == 1

        assert client.disconnected
        client.emit(SystemMessage(session.session_id))
        response = await manager.send_message(session.session_id, "続行", timeout=1)
        assert response["delivery"] == "reply_started"


@pytest.mark.asyncio
async def test_show_reports_held_result_and_live_background_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """結果を保留している間だけ、`show`が保留と追跡中のバックグラウンドタスクを返す。"""
    client = ControlledClaudeClient("claude-show")
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        client.emit(TaskStartedMessage("task-b", "local_agent", "子エージェント"))
        client.emit(TaskStartedMessage("task-a", "local_bash", "cat > /dev/null"))
        await _await_state(lambda: len(session.live_tasks) == 2)
        running = await manager.show_session(session.session_id)
        assert "result_held" not in running
        assert "live_background_tasks" not in running

        client.emit(ResultMessage("初回結果"))
        await _await_state(lambda: session.awaiting_auto_resume)
        held = await manager.show_session(session.session_id)

        assert held["status"] == "running"
        assert held["result_held"] is True
        tasks = held["live_background_tasks"]
        assert [(task["task_id"], task["task_type"], task["description"]) for task in tasks] == [
            ("task-a", "local_bash", "cat > /dev/null"),
            ("task-b", "local_agent", "子エージェント"),
        ]
        assert all(isinstance(task["seconds_since_start"], int) for task in tasks)

        await manager.kill(session.session_id, timeout=1)
        finalized = await manager.show_session(session.session_id)
        assert "result_held" not in finalized
        assert "live_background_tasks" not in finalized


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_auto_resume", [False, True])
async def test_new_reply_can_auto_resume_after_prior_auto_resume(
    initial_auto_resume: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """通常または自動再開済みsessionへのreplyは再度自動再開できる。"""
    client = ControlledClaudeClient("claude-repeat")
    async with _manager(client, monkeypatch) as (manager, _backend):
        session = await _start(manager, tmp_path, monkeypatch)
        if initial_auto_resume:
            client.emit(TaskStartedMessage("task-1"))
            client.emit(ResultMessage("初回結果"))
            client.emit(TaskNotificationMessage("task-1", "completed"))
            client.emit(ResultMessage("再開結果", origin={"kind": "task-notification"}))
        else:
            client.emit(ResultMessage("通常結果"))
        await _wait_with_timeout(manager, 1)

        response = await manager.send_message(session.session_id, "次の作業", timeout=1)
        assert response["delivery"] == "reply_started"

        client.emit(TaskStartedMessage("task-2"))
        client.emit(ResultMessage("次の初回結果"))
        await _await_state(lambda: session.awaiting_auto_resume)
        client.emit(TaskNotificationMessage("task-2", "completed"))
        client.emit(ResultMessage("次の再開結果", origin={"kind": "task-notification"}))
        result = await _wait_with_timeout(manager, 1)

        assert result["agent_message"] == "次の再開結果"
