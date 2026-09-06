"""Claude backendの背景作業完了後の自動再開を検証する。"""

# テストでは自動再開の内部状態を直接検証する。
# pylint: disable=protected-access

import asyncio
import json
import pathlib
from types import SimpleNamespace
from typing import Any

import agents_server_mcp as subject
import pytest
from _agents_server import claude as claude_backend
from _agents_server import codex as codex_backend
from _agents_server import session_registry, state

_STREAM_END = object()


class SystemMessage:
    """Claude SDKの初期化メッセージを再現する。"""

    subtype = "init"

    def __init__(self, session_id: str) -> None:
        self.data = {"session_id": session_id}


class TaskStartedMessage:
    """背景タスクの開始を再現する。"""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id


class TaskUpdatedMessage:
    """背景タスクの状態更新を再現する。"""

    def __init__(self, task_id: str, status: str) -> None:
        self.task_id = task_id
        self.status = status


class TaskNotificationMessage(TaskUpdatedMessage):
    """背景タスクの完了通知を再現する。"""


class ResultMessage:
    """Claude SDKのturn結果を再現する。"""

    is_error = False
    errors: list[str] = []

    def __init__(
        self,
        result: str,
        *,
        origin: dict[str, str] | None = None,
        terminal_reason: str | None = None,
        is_error: bool = False,
        errors: list[str] | None = None,
    ) -> None:
        self.result = result
        self.origin = origin
        self.terminal_reason = terminal_reason
        self.is_error = is_error
        self.errors = [] if errors is None else errors


class AssistantMessage:
    """Claude SDKのツール利用を含むassistantメッセージを再現する。"""

    def __init__(self, content: list[Any]) -> None:
        self.content = content


class UserMessage(AssistantMessage):
    """Claude SDKのツール結果を含むuserメッセージを再現する。"""


class ControlledClaudeClient:
    """メッセージの到着時機をテストから制御するSDKクライアント。"""

    def __init__(self, session_id: str = "claude-auto") -> None:
        self.messages: asyncio.Queue[Any] = asyncio.Queue()
        self.messages.put_nowait(SystemMessage(session_id))
        self.queries: list[str] = []
        self.interrupts = 0
        self.disconnected = False

    async def connect(self) -> None:
        """接続のダミー。"""

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def interrupt(self) -> None:
        self.interrupts += 1
        self.emit(ResultMessage("中断結果", terminal_reason="aborted_streaming"))

    def receive_messages(self):
        async def stream():
            while True:
                message = await self.messages.get()
                if message is _STREAM_END:
                    return
                yield message

        return stream()

    async def disconnect(self) -> None:
        self.disconnected = True

    def emit(self, message: Any) -> None:
        self.messages.put_nowait(message)

    def end_stream(self) -> None:
        self.messages.put_nowait(_STREAM_END)


def _manager(
    client: ControlledClaudeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[subject.AgentsServerManager, claude_backend.ClaudeServerManager]:
    manager = subject.AgentsServerManager()
    backend = claude_backend.ClaudeServerManager(
        manager.sessions,
        manager._condition,
        client_factory=lambda _options: client,
        expire_session=manager._expire_session,
    )
    manager._claude = backend
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    return manager, backend


async def _start(
    manager: subject.AgentsServerManager,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> state.SessionState:
    monkeypatch.setattr(subject, "START_AVAILABILITY_TIMEOUT", 0.01)
    monkeypatch.setattr(
        subject._atk_config,
        "resolve_model_candidates",
        lambda _model_type: [("claude", "model", "high")],
    )
    response = await manager.start("plan", "調査", str(tmp_path))
    return manager.sessions[response["session_id"]]


async def _await_state(predicate: Any) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("状態遷移が完了しなかった")


@pytest.fixture(autouse=True)
def _isolate_session_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """各検体のsession登録簿を一時ディレクトリへ隔離する。"""
    monkeypatch.setattr(session_registry._atk_config, "state_dir", lambda: tmp_path)


def _emit_child_start(client: ControlledClaudeClient, session_id: str) -> None:
    """agents_server.startの利用と結果をClaudeメッセージとして投入する。"""
    client.emit(
        AssistantMessage(
            [
                SimpleNamespace(
                    id="tool-start",
                    name="mcp__agents_server__start",
                    input={"model_type": "execute"},
                )
            ]
        )
    )
    client.emit(
        UserMessage(
            [
                SimpleNamespace(
                    tool_use_id="tool-start",
                    content=[{"type": "text", "text": json.dumps({"session_id": session_id, "status": "running"})}],
                )
            ]
        )
    )


async def _auto_resume_after_child_termination(
    manager: subject.AgentsServerManager,
    client: ControlledClaudeClient,
    session: state.SessionState,
    child_session_id: str,
) -> dict[str, Any]:
    """孫sessionを終端させ、継続指示後の結果を返す。"""
    wait_task = asyncio.create_task(manager.wait(session.session_id, timeout=2))
    await _await_state(lambda: session.awaiting_auto_resume)
    assert wait_task.done() is False
    session_registry.publish(child_session_id, terminal=True)
    await _await_state(lambda: len(client.queries) == 2)
    client.emit(ResultMessage("孫session確認後の結果"))
    return await wait_task


@pytest.mark.asyncio
async def test_wait_defers_result_until_child_session_terminates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """孫sessionが終端するまで初回結果を返さず、確認後の結果を返す。"""
    client = ControlledClaudeClient("claude-child-wait")
    manager, backend = _manager(client, monkeypatch)
    child_session_id = "child-wait"
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(child_session_id, terminal=False)
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("孫session待機中"))

        result = await _auto_resume_after_child_termination(manager, client, session, child_session_id)

        assert result["status"] == "completed"
        assert result["agent_message"] == "孫session確認後の結果"
        assert child_session_id in client.queries[1]
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_wait_response_keys_are_unchanged_with_child_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """孫session追跡用の内部状態をwait応答へ追加しない。"""
    client = ControlledClaudeClient("claude-child-keys")
    manager, backend = _manager(client, monkeypatch)
    child_session_id = "child-keys"
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(child_session_id, terminal=False)
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("孫session待機中"))

        result = await _auto_resume_after_child_termination(manager, client, session, child_session_id)

        assert set(result) == {
            "agent_message",
            "engine",
            "model_type",
            "progress",
            "session_id",
            "status",
            "turn_seq",
        }
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_unobserved_child_sessions_appear_in_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保持期限まで終端しない孫sessionを既存のerror項目へ示す。"""
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.03)
    client = ControlledClaudeClient("claude-child-deadline")
    manager, backend = _manager(client, monkeypatch)
    child_session_id = "child-deadline"
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(child_session_id, terminal=False)
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("孫session待機中"))

        result = await manager.wait(session.session_id, timeout=1)

        assert result["error"] == {"unobservedSessions": [child_session_id]}
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_unobserved_child_sessions_merge_into_existing_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """既存の失敗内容を保ったまま未観測sessionをerrorへ併合する。"""
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.03)
    client = ControlledClaudeClient("claude-child-error")
    manager, backend = _manager(client, monkeypatch)
    child_session_id = "child-error"
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(child_session_id, terminal=False)
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("失敗結果", is_error=True, errors=["既存エラー"]))

        result = await manager.wait(session.session_id, timeout=1)

        assert result["error"] == {
            "message": "既存エラー",
            "unobservedSessions": [child_session_id],
        }
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_codex_child_session_triggers_auto_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """CodexのMCP完了項目から孫sessionを追跡して同じthreadを再開する。"""
    manager = subject.AgentsServerManager(status_writer=None)
    backend = codex_backend.AppServerManager(manager.sessions, manager._condition)
    manager._codex = backend
    session = state.SessionState(
        "codex-parent",
        str(tmp_path),
        engine="codex",
        model_type="execute",
        turn_seq=1,
        turn_id="turn-1",
    )
    manager.sessions[session.session_id] = session
    child_session_id = "codex-child"
    session_registry.publish(child_session_id, terminal=False)
    await backend._handle_notification(
        {
            "method": "item/completed",
            "params": {
                "threadId": session.session_id,
                "turnId": session.turn_id,
                "item": {
                    "id": "mcp-1",
                    "type": "mcpToolCall",
                    "server": "agents_server",
                    "tool": "start",
                    "arguments": {"model_type": "execute"},
                    "status": "completed",
                    "result": {
                        "content": [],
                        "structuredContent": {"session_id": child_session_id, "status": "running"},
                    },
                },
            },
        }
    )
    await backend._handle_notification(
        {
            "method": "turn/completed",
            "params": {
                "threadId": session.session_id,
                "turn": {"id": session.turn_id, "status": "completed", "error": None, "items": []},
            },
        }
    )

    async def complete_reply(actual: state.SessionState, prompt: str) -> dict[str, Any]:
        assert child_session_id in prompt
        state._begin_reply(actual)
        actual.status = "completed"
        actual.agent_message = "Codex再開結果"
        actual.turn_completed = True
        actual.touch()
        return {"delivery": "reply_started", **actual.public_status()}

    monkeypatch.setattr(backend, "send_message", complete_reply)
    session_registry.publish(child_session_id, terminal=True)
    try:
        result = await manager.wait(session.session_id, timeout=1)
        assert result["agent_message"] == "Codex再開結果"
        assert session.live_child_session_ids == set()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_wait_skips_initial_result_and_returns_auto_resumed_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """背景作業が残る初回結果を返さず、自動再開turnの結果を返す。"""
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.05)
    client = ControlledClaudeClient()
    manager, backend = _manager(client, monkeypatch)
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        wait_task = asyncio.create_task(manager.wait(session.session_id, timeout=1))
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
        assert session.auto_resume_consumed is True
        assert session.live_task_ids == set()
        await _await_state(lambda: session.session_id not in manager.sessions)
        with pytest.raises(ValueError, match="session retention expired"):
            await manager.wait(session.session_id, timeout=0)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_result_without_background_task_is_immediately_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """背景作業が無いturnは従来どおり最初の結果で終端する。"""
    client = ControlledClaudeClient()
    manager, backend = _manager(client, monkeypatch)
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        client.emit(ResultMessage("通常結果"))
        result = await manager.wait(session.session_id, timeout=1)

        assert result["agent_message"] == "通常結果"
        assert session.auto_resume_consumed is False
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("completion", ["deadline", "stream_end"])
async def test_pending_result_is_finalized_without_auto_resume(
    completion: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """自動再開が届かない場合は期限又はストリーム終端で初回結果を確定する。"""
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.03)
    client = ControlledClaudeClient(f"claude-{completion}")
    manager, backend = _manager(client, monkeypatch)
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        client.emit(TaskStartedMessage("task-1"))
        client.emit(ResultMessage("保留結果"))
        await _await_state(lambda: session.awaiting_auto_resume)
        if completion == "stream_end":
            client.end_stream()

        result = await manager.wait(session.session_id, timeout=1)

        assert result["agent_message"] == "保留結果"
        assert session.awaiting_auto_resume is False
        assert session.pending_result is None
        await _await_state(lambda: session.session_id not in manager.sessions)
        with pytest.raises(ValueError, match="session retention expired"):
            await manager.wait(session.session_id, timeout=0)
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_kill_interrupts_while_initial_result_is_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保留中の中断要求は初回結果を確定せずSDKへ配送する。"""
    client = ControlledClaudeClient("claude-kill")
    manager, backend = _manager(client, monkeypatch)
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        client.emit(TaskStartedMessage("task-1"))
        client.emit(ResultMessage("初回結果"))
        await _await_state(lambda: session.awaiting_auto_resume)

        result = await manager.kill(session.session_id, timeout=1)

        assert result["status"] == "interrupted"
        assert result["agent_message"] == "中断結果"
        assert result["kill_requested"] is True
        assert client.interrupts == 1
        assert session.auto_resume_consumed is True
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_send_message_finalizes_pending_result_before_starting_reply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保留中の継続入力は初回結果をprevious_resultとして新しいturnを始める。"""
    client = ControlledClaudeClient("claude-send")
    manager, backend = _manager(client, monkeypatch)
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        client.emit(TaskStartedMessage("task-1"))
        client.emit(ResultMessage("初回結果"))
        await _await_state(lambda: session.awaiting_auto_resume)

        response = await manager.send_message(session.session_id, "続行", timeout=1)

        assert response["delivery"] == "reply_started"
        assert response["previous_result"]["agent_message"] == "初回結果"
        assert session.status == "running"
        assert session.auto_resume_consumed is False
        assert session.live_task_ids == {"task-1"}
        assert client.queries == ["調査", "続行"]

        client.emit(TaskUpdatedMessage("task-1", "completed"))
        client.emit(ResultMessage("reply結果"))
        result = await manager.wait(session.session_id, timeout=1)
        assert result["agent_message"] == "reply結果"
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_auto_resume", [False, True])
async def test_new_reply_can_auto_resume_after_prior_auto_resume(
    initial_auto_resume: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """通常又は自動再開済みsessionへのreplyは再度自動再開できる。"""
    client = ControlledClaudeClient("claude-repeat")
    manager, backend = _manager(client, monkeypatch)
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        if initial_auto_resume:
            client.emit(TaskStartedMessage("task-1"))
            client.emit(ResultMessage("初回結果"))
            client.emit(TaskNotificationMessage("task-1", "completed"))
            client.emit(ResultMessage("再開結果", origin={"kind": "task-notification"}))
        else:
            client.emit(ResultMessage("通常結果"))
        await manager.wait(session.session_id, timeout=1)

        response = await manager.send_message(session.session_id, "次の作業", timeout=1)
        assert response["delivery"] == "reply_started"
        assert session.auto_resume_consumed is False

        client.emit(TaskStartedMessage("task-2"))
        client.emit(ResultMessage("次の初回結果"))
        await _await_state(lambda: session.awaiting_auto_resume)
        client.emit(TaskNotificationMessage("task-2", "completed"))
        client.emit(ResultMessage("次の再開結果", origin={"kind": "task-notification"}))
        result = await manager.wait(session.session_id, timeout=1)

        assert result["agent_message"] == "次の再開結果"
    finally:
        await backend.close()
