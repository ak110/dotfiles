"""起動し直したClaude CLIから復元通知の後に依頼turnの結果を受け取る。"""

# pylint: disable=protected-access,useless-import-alias

import asyncio
import contextlib
import pathlib
from types import SimpleNamespace

import pytest

from agent_toolkit._testing import dependency_logging, isolation
from agent_toolkit._testing.agents_server_resume_support import (
    AssistantMessage,
    ControlledClaudeClient,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    _await_state,
    _session_state,
    _start,
    _wait_with_timeout,
)
from agent_toolkit._testing.agents_server_resume_support import _isolate_session_registry as _isolate_session_registry
from agent_toolkit._testing.agents_server_support_claude import claude_manager

isolated_path_value = isolation.isolated_path_value
isolate_development_state = isolation.isolate_development_state
host_environ = isolation.host_environ
share_package_caches = isolation.share_package_caches
quiet_dependency_loggers = dependency_logging.quiet_dependency_loggers

SID = "claude-replaced"


def _replacement(*, idle_after_empty: bool) -> ControlledClaudeClient:
    client = ControlledClaudeClient(SID)
    client.messages = asyncio.Queue()
    # observe1.jsonl 29〜33行目の順序
    client.emit(TaskNotificationMessage("task-1", "stopped"))
    client.emit(_session_state("running"))
    client.emit(SystemMessage(SID))
    client.emit(ResultMessage("", origin={"kind": "task-notification"}, num_turns=0))
    if idle_after_empty:
        client.emit(_session_state("idle"))
    client.emit(SystemMessage(SID))
    return client


@contextlib.asynccontextmanager
async def _held_then_resumed(monkeypatch, tmp_path, *, idle_after_empty: bool, via: str):
    stopped = ControlledClaudeClient(SID, report_state=True)
    replacement = _replacement(idle_after_empty=idle_after_empty)
    clients = iter([stopped, replacement])
    async with claude_manager(monkeypatch, None, client_factory=lambda _options: next(clients)) as (manager, backend):
        session = await _start(manager, tmp_path, monkeypatch)
        stopped.emit(TaskStartedMessage("task-1"))
        stopped.finish_turn(ResultMessage("待機中: task-1"))
        await _await_state(lambda: session.awaiting_auto_resume and session.cli_turn_state == "idle")
        if via == "resume_claude_wait":
            await manager._resume_claude_wait(session, "続行")
        else:
            await backend._resume_next_turn(session, "続行")
        assert stopped.disconnected
        yield manager, replacement


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["resume_claude_wait", "resume_next_turn"])
async def test_observed_order_publishes_requested_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, via: str
) -> None:
    async with _held_then_resumed(monkeypatch, tmp_path, idle_after_empty=False, via=via) as (manager, replacement):
        await _await_state(replacement.messages.empty)
        await asyncio.sleep(0.3)
        early = await _wait_with_timeout(manager, 0.5)
        assert early["status"] == "running", early
        replacement.emit(AssistantMessage([SimpleNamespace(text="最終結果")]))
        replacement.emit(ResultMessage("最終結果"))
        replacement.emit(_session_state("idle"))
        result = await _wait_with_timeout(manager, 2)
        assert result["status"] == "completed", result
        assert result["agent_message"] == "最終結果", result
        assert "error" not in result, result


@pytest.mark.asyncio
async def test_idle_between_empty_result_and_requested_turn(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    async with _held_then_resumed(monkeypatch, tmp_path, idle_after_empty=True, via="resume_claude_wait") as (manager, _):
        early = await _wait_with_timeout(manager, 0.5)
        assert early["status"] == "running", early
