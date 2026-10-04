"""Claude backendのバックグラウンドタスク完了後の自動再開を検証する。"""

# テストでは自動再開の内部状態を直接検証する。
# pylint: disable=protected-access

import asyncio
import json
import pathlib
from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import agent_toolkit.agents_server_mcp as subject
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import session_registry, state, status_file
from agent_toolkit._testing.helpers import delivery_payload

_STREAM_END = object()

# 状態遷移の観測は経過時間で打ち切る。反復回数で打ち切ると、CPU競合時に
# 実時間の待機量が不足して自動再開の送信前に打ち切られる。
_STATE_TIMEOUT = 10.0
# 自動再開を観測するテストでは、_STATE_TIMEOUTより長い待機上限をmanager.waitへ与える。
# 観測前にwaitが期限切れになると自動再開の送信自体が発生しない。
_RESUME_WAIT_TIMEOUT = 30.0


def _wait_with_timeout(manager: subject.AgentsServerManager, timeout: float) -> Coroutine[Any, Any, dict[str, Any]]:
    """待機上限を確定してから引数なしのwaitを発行する。

    `wait`は待機上限を入力として受け取らないため、上限の導出結果だけをテスト用の値へ差し替える。
    """
    manager._wait_timeouts["main"] = timeout
    return manager.wait()


class SystemMessage:
    """Claude SDKのシステムメッセージ（初期化とturn状態の報告）を再現する。"""

    def __init__(self, session_id: str | None = None, *, subtype: str = "init", data: dict[str, Any] | None = None) -> None:
        self.subtype = subtype
        self.data = {"session_id": session_id} if data is None else data


def _session_state(value: str) -> SystemMessage:
    """CLIのturn状態の報告（`session_state_changed`）を再現する。"""
    return SystemMessage(subtype="session_state_changed", data={"state": value})


class TaskStartedMessage:
    """バックグラウンドタスクの開始を再現する。"""

    def __init__(self, task_id: str, task_type: str = "local_bash", description: str = "") -> None:
        self.task_id = task_id
        self.task_type = task_type
        self.description = description


class TaskUpdatedMessage:
    """バックグラウンドタスクの状態更新を再現する。"""

    def __init__(self, task_id: str, status: str) -> None:
        self.task_id = task_id
        self.status = status


class TaskNotificationMessage(TaskUpdatedMessage):
    """バックグラウンドタスクの完了通知を再現する。"""


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

    def __init__(self, session_id: str = "claude-auto", *, report_state: bool = False, state_before_init: bool = False) -> None:
        self.messages: asyncio.Queue[Any] = asyncio.Queue()
        # 真の場合は実CLIと同じ順序でturn状態を報告する。turnの開始で`running`、
        # 次のturnがキューに無い`ResultMessage`の直後に`idle`を発行する（`finish_turn`）。
        self.report_state = report_state
        # 真の場合は最初のturnの`running`を初期化メッセージより先に送る。CLIはコマンドループの先頭で
        # `running`を発行するため、初期化メッセージとの前後は固定されない。
        self._skip_next_running = state_before_init
        if state_before_init:
            self.messages.put_nowait(_session_state("running"))
        self.messages.put_nowait(SystemMessage(session_id))
        self.queries: list[str] = []
        self.interrupts = 0
        self.disconnected = False
        # 直前のturnの`ResultMessage`を発行済みで次の`query`を受けていない間は偽とする。
        self.turn_active = False

    async def connect(self) -> None:
        """接続のダミー。"""

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        self.turn_active = True
        if self.report_state and not self._skip_next_running:
            self.messages.put_nowait(_session_state("running"))
        self._skip_next_running = False

    async def interrupt(self) -> None:
        """実行中のturnだけを中断する。

        Claude Code CLIはturnを終えた後に受けた中断要求へ`ResultMessage`もtaskの終端通知も返さない
        （claude_agent_sdk 0.2.161で背景Bashを残してturnを終えた後の`interrupt()`で68秒間観測した）。
        """
        self.interrupts += 1
        if self.turn_active:
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
        if isinstance(message, ResultMessage):
            self.turn_active = False
        self.messages.put_nowait(message)

    def end_stream(self) -> None:
        self.messages.put_nowait(_STREAM_END)

    def finish_turn(self, result: ResultMessage, *, next_turn_queued: bool = False) -> None:
        """turnの結果を送り、実CLIと同じ順序でturn状態を報告する。

        Claude Code 2.1.289とClaude Agent SDK 0.2.163の観測では、キューに完了通知が残らない場合は
        `ResultMessage`の直後に`idle`が届く。turnの終了前に完了通知がキューへ入った場合は`idle`を間に送らず
        次のturnを開始し、そのturnの`ResultMessage`の後に`idle`が届く。
        """
        self.emit(result)
        if not self.report_state:
            return
        if next_turn_queued:
            self.turn_active = True
        else:
            self.emit(_session_state("idle"))


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
        "parse_unresolved_model_candidates",
        lambda _model_type: [("claude", "model", "high")],
    )
    response = await manager.start("plan", "調査", str(tmp_path))
    return manager.sessions[response["session_id"]]


async def _await_state(predicate: Any, timeout: float = _STATE_TIMEOUT) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        if predicate():
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("状態遷移が完了しなかった")
        await asyncio.sleep(0.01)


@pytest.fixture(autouse=True)
def _isolate_session_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """各テストのsession登録簿を一時ディレクトリへ隔離する。"""
    monkeypatch.setattr(session_registry._atk_config, "state_dir", lambda: tmp_path)


def _emit_child_start(client: ControlledClaudeClient, session_id: str) -> None:
    """agents_server.startの利用と結果をClaudeメッセージとして投入する。"""
    client.emit(
        AssistantMessage(
            [
                SimpleNamespace(
                    id="tool-start",
                    name="mcp__agents_server__start",
                    input={"model_type": "high_tier"},
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
    wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
    await _await_state(lambda: session.awaiting_auto_resume)
    assert wait_task.done() is False
    session_registry.publish(child_session_id, terminal=True)
    await _await_state(lambda: len(client.queries) == 2)
    client.emit(ResultMessage("孫session確認後の結果"))
    return await wait_task


@pytest.mark.asyncio
async def test_run_resume_removes_previous_result_file(tmp_path: pathlib.Path) -> None:
    """状態writerを伴う再開はbackend応答後に前turnの結果を削除する。"""
    writer = status_file.StatusFileWriter(
        {},
        status_file.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = subject.AgentsServerManager(writer)

    class ResumeBackend:
        async def resume(
            self,
            session_id: str,
            _prompt: state.ResumePrompt,
            cwd: str,
            model: str | None,
            effort: str | None,
            **kwargs: Any,
        ) -> state.SessionState:
            resumed = state.SessionState(
                session_id,
                cwd,
                model=model,
                effort=effort,
                engine="codex",
                model_type=kwargs["model_type"],
                turn_seq=kwargs["turn_seq"] + 1,
            )
            manager.sessions[session_id] = resumed
            return resumed

        async def close(self) -> None:
            """外部資源を持たないため何もしない。"""

    manager._codex = ResumeBackend()
    source = state.SessionState(
        "resume-result",
        str(tmp_path),
        model="model",
        effort="medium",
        engine="codex",
        model_type="high_tier",
        turn_seq=1,
    )
    result_path = status_file.results_directory("root", tmp_path) / f"{source.session_id}.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text("{}\n", encoding="utf-8")
    writer.activate()
    try:
        resumed = await manager._run_resume(
            state.SessionResumeState.from_session(source),
            state.ResumePrompt("続行"),
        )

        assert resumed.turn_seq == 2
        assert not result_path.exists()
    finally:
        await manager.close()


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
async def test_wait_returns_result_when_child_session_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """登録簿から消えた孫sessionがあっても初回結果を返す。"""
    client = ControlledClaudeClient("claude-child-missing")
    manager, backend = _manager(client, monkeypatch)
    child_session_id = "child-missing"
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("孫session不在時の結果"))

        result = await _wait_with_timeout(manager, 1)

        assert result["status"] == "completed"
        assert result["error"] == {"unobservedSessions": [child_session_id]}
        assert session.awaiting_auto_resume is False
        assert not session.live_child_session_ids
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_wait_returns_result_when_child_session_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """解釈できない登録簿を持つ孫sessionがあっても初回結果を返す。"""
    client = ControlledClaudeClient("claude-child-unreadable")
    manager, backend = _manager(client, monkeypatch)
    child_session_id = "child-unreadable"
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        registry_path = session_registry.registry_directory() / f"{child_session_id}.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text("{", encoding="utf-8")
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("孫session読取不能時の結果"))

        result = await _wait_with_timeout(manager, 1)

        assert result["status"] == "completed"
        assert result["error"] == {"unobservedSessions": [child_session_id]}
        assert session.awaiting_auto_resume is False
        assert not session.live_child_session_ids
    finally:
        await backend.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("unobserved_state", ["missing", "unreadable"])
async def test_wait_preserves_unobserved_child_sessions_after_auto_resume(
    unobserved_state: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """終端済みと未観測の孫sessionが混在しても未観測IDを再開結果へ残す。"""
    client = ControlledClaudeClient(f"claude-child-mixed-{unobserved_state}")
    manager, backend = _manager(client, monkeypatch)
    terminal_session_id = f"child-terminal-{unobserved_state}"
    unobserved_session_id = f"child-{unobserved_state}"
    try:
        await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(terminal_session_id, terminal=True)
        if unobserved_state == "unreadable":
            registry_path = session_registry.registry_directory() / f"{unobserved_session_id}.json"
            registry_path.parent.mkdir(parents=True, exist_ok=True)
            registry_path.write_text("{", encoding="utf-8")
        _emit_child_start(client, terminal_session_id)
        _emit_child_start(client, unobserved_session_id)
        client.emit(ResultMessage("孫session混在時の初回結果"))

        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        await _await_state(lambda: len(client.queries) == 2)
        client.emit(ResultMessage("孫session混在時の再開結果"))
        result = await wait_task

        assert result["status"] == "completed"
        assert result["agent_message"] == "孫session混在時の再開結果"
        assert result["error"] == {"unobservedSessions": [unobserved_session_id]}
        assert terminal_session_id in client.queries[1]
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_wait_accumulates_unobserved_child_sessions_across_auto_resumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """連続する自動再開で追跡を打ち切った全ての孫sessionを結果へ残す。"""
    client = ControlledClaudeClient("claude-child-repeated-mixed")
    manager, backend = _manager(client, monkeypatch)
    first_terminal_session_id = "child-terminal-first"
    missing_session_id = "child-missing-first"
    second_terminal_session_id = "child-terminal-second"
    unreadable_session_id = "child-unreadable-second"
    try:
        await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(first_terminal_session_id, terminal=True)
        _emit_child_start(client, first_terminal_session_id)
        _emit_child_start(client, missing_session_id)
        client.emit(ResultMessage("1回目の孫session混在結果"))

        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        await _await_state(lambda: len(client.queries) == 2)

        session_registry.publish(second_terminal_session_id, terminal=True)
        registry_path = session_registry.registry_directory() / f"{unreadable_session_id}.json"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text("{", encoding="utf-8")
        _emit_child_start(client, second_terminal_session_id)
        _emit_child_start(client, unreadable_session_id)
        client.emit(ResultMessage("2回目の孫session混在結果"))
        await _await_state(lambda: len(client.queries) == 3)

        client.emit(ResultMessage("連続自動再開後の結果"))
        result = await wait_task

        assert result["status"] == "completed"
        assert result["agent_message"] == "連続自動再開後の結果"
        assert result["error"] == {
            "unobservedSessions": [missing_session_id, unreadable_session_id],
        }
        assert first_terminal_session_id in client.queries[1]
        assert second_terminal_session_id in client.queries[2]
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_wait_returns_last_result_after_chained_auto_resumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """再開turnが背景taskを残して終わる間は結果を返さず、最後の再開turnの結果だけを返す。

    最後のtaskの終端から再開turnの結果までの間に待機の刻みが到来しても、保留中の結果を確定しない。
    """
    client = ControlledClaudeClient("claude-chain")
    manager, backend = _manager(client, monkeypatch)
    try:
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
        # 自動再開の監視と待機は0.1秒刻みで保留中の結果を判定するため、刻みを複数回経過させる。
        await asyncio.sleep(0.35)
        assert wait_task.done() is False
        assert session.awaiting_auto_resume is True

        client.emit(ResultMessage("最終結果", origin={"kind": "task-notification"}))
        result = await wait_task

        assert result["status"] == "completed"
        assert result["agent_message"] == "最終結果"
        assert session.auto_resume_consumed is True
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

        assert set(result) == {"session_id", "agent_message", "status"}
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_unobserved_child_sessions_appear_in_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """自動再開の待機期限まで終端しない孫sessionをerrorへ示す。"""
    monkeypatch.setattr(state, "AUTO_RESUME_DEADLINE_SECONDS", 0.03)
    client = ControlledClaudeClient("claude-child-deadline")
    manager, backend = _manager(client, monkeypatch)
    child_session_id = "child-deadline"
    try:
        await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(child_session_id, terminal=False)
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("孫session待機中"))

        result = await _wait_with_timeout(manager, 1)

        assert result["error"] == {"unobservedSessions": [child_session_id]}
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_unobserved_child_sessions_merge_into_existing_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """既存の失敗内容を保ったまま未観測sessionをerrorへ併合する。"""
    monkeypatch.setattr(state, "AUTO_RESUME_DEADLINE_SECONDS", 0.03)
    client = ControlledClaudeClient("claude-child-error")
    manager, backend = _manager(client, monkeypatch)
    child_session_id = "child-error"
    try:
        await _start(manager, tmp_path, monkeypatch)
        session_registry.publish(child_session_id, terminal=False)
        _emit_child_start(client, child_session_id)
        client.emit(ResultMessage("失敗結果", is_error=True, errors=["既存エラー"]))

        result = await _wait_with_timeout(manager, 1)

        assert result["error"] == {
            "message": "既存エラー",
            "unobservedSessions": [child_session_id],
        }
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_unobserved_child_sessions_merge_with_existing_identifiers(tmp_path: pathlib.Path) -> None:
    """既存の未観測session識別子を保ったまま新しい識別子を併合する。"""
    session = state.SessionState("existing-unobserved", str(tmp_path))
    session.error = {
        "message": "既存エラー",
        "unobservedSessions": ["child-existing"],
    }

    state.record_unobserved_sessions(session, {"child-existing", "child-new"})

    assert session.error == {
        "message": "既存エラー",
        "unobservedSessions": ["child-existing", "child-new"],
    }


def _codex_parent_with_child(
    tmp_path: pathlib.Path,
) -> tuple[subject.AgentsServerManager, codex_backend.AppServerManager, state.SessionState, status_file.StatusFileWriter]:
    writer = status_file.StatusFileWriter(
        {},
        status_file.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = subject.AgentsServerManager(writer)
    backend = codex_backend.AppServerManager(manager.sessions, manager._condition)
    manager._codex = backend
    session = state.SessionState(
        "codex-parent",
        str(tmp_path),
        engine="codex",
        model_type="high_tier",
        turn_seq=1,
        turn_id="turn-1",
    )
    manager.sessions[session.session_id] = session
    writer.activate()
    return manager, backend, session, writer


async def _complete_codex_turn_with_child(
    backend: codex_backend.AppServerManager, session: state.SessionState, child_session_id: str
) -> None:
    """Codexの委譲先が孫sessionを起動し、回収せずに待機表明でturnを終える通知をbackendへ渡す。"""
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
                    "arguments": {"mode": "shell", "command": "sleep 5", "summary_policy": "終了状態"},
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
            "method": "item/completed",
            "params": {
                "threadId": session.session_id,
                "turnId": session.turn_id,
                "item": {"id": "msg-1", "type": "agentMessage", "text": f"待機中: {child_session_id}"},
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


@pytest.mark.asyncio
async def test_codex_child_session_holds_result_until_single_auto_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Codexは孫sessionが残るturnの待機表明を公開せず保留し、孫の終端後に同じsessionを一度だけ再開する。

    委譲元は手動の再開指示なしに、再開したturnの完了報告を受け取る。
    """
    manager, backend, session, _writer = _codex_parent_with_child(tmp_path)
    child_session_id = "codex-child"
    session_registry.publish(child_session_id, terminal=False)
    await _complete_codex_turn_with_child(backend, session, child_session_id)

    assert session.awaiting_auto_resume is True
    assert session.result_available is False
    assert session.status == "running"
    assert session.pending_result is not None
    assert session.pending_result["agent_message"] == f"待機中: {child_session_id}"
    assert session.live_child_session_ids == {child_session_id}

    prompts: list[str] = []

    async def resume(target: state.SessionState, prompt: str) -> dict[str, Any]:
        prompts.append(prompt)
        state._begin_reply(target)
        target.status = "completed"
        target.agent_message = "AUTO_RESUME_COMPLETED"
        target.turn_completed = True
        target.touch()
        return {"delivery": "reply_started", "previous_result": {}}

    monkeypatch.setattr(backend, "send_message", resume)
    try:
        session_registry.publish(child_session_id, terminal=True)
        result = await _wait_with_timeout(manager, 5)
        assert result["status"] == "completed"
        assert result["agent_message"] == "AUTO_RESUME_COMPLETED"
        assert session.turn_seq == 2
        assert "error" not in result
        assert len(prompts) == 1
        assert child_session_id in prompts[0]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_codex_kill_during_hold_returns_pending_result_with_unobserved_child(tmp_path: pathlib.Path) -> None:
    """保留中のCodex sessionへの中断は要求を送らず、保留した結果を未観測の孫の識別子とともに確定する。"""
    manager, backend, session, _writer = _codex_parent_with_child(tmp_path)
    child_session_id = "codex-child"
    session_registry.publish(child_session_id, terminal=False)
    await _complete_codex_turn_with_child(backend, session, child_session_id)
    try:
        await backend.interrupt(session)
        assert session.result_available is True
        assert session.awaiting_auto_resume is False
        assert session.agent_message == f"待機中: {child_session_id}"
        assert session.error == {"unobservedSessions": [child_session_id]}
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_codex_auto_resume_failure_reports_unobserved_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """再開の配送に失敗した場合は、待機表明を成功として扱わず、失敗と孫の識別子を返す。"""
    manager, backend, session, _writer = _codex_parent_with_child(tmp_path)
    child_session_id = "codex-child"
    session_registry.publish(child_session_id, terminal=False)
    await _complete_codex_turn_with_child(backend, session, child_session_id)
    monkeypatch.setattr(backend, "send_message", AsyncMock(side_effect=RuntimeError("resume unavailable")))
    try:
        session_registry.publish(child_session_id, terminal=True)
        result = await _wait_with_timeout(manager, 5)
        assert result["status"] == "failed"
        assert result["error"]["unobservedSessions"] == [child_session_id]
        assert "resume unavailable" in result["error"]["message"]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_codex_auto_resume_deadline_publishes_pending_result_with_unobserved_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """孫が終端しないまま保留期限に達した場合は、保留した結果と未観測の孫の識別子を返す。"""
    manager, backend, session, _writer = _codex_parent_with_child(tmp_path)
    child_session_id = "codex-child"
    session_registry.publish(child_session_id, terminal=False)
    await _complete_codex_turn_with_child(backend, session, child_session_id)
    session.auto_resume_deadline = 0.0
    send_message = AsyncMock()
    monkeypatch.setattr(backend, "send_message", send_message)
    try:
        result = await _wait_with_timeout(manager, 5)
        assert result["status"] == "completed"
        assert result["error"] == {"unobservedSessions": [child_session_id]}
        send_message.assert_not_awaited()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_wait_skips_initial_result_and_returns_auto_resumed_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """バックグラウンドタスクが残る初回結果を返さず、自動再開turnの結果を返す。"""
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.05)
    client = ControlledClaudeClient()
    manager, backend = _manager(client, monkeypatch)
    try:
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
        assert session.auto_resume_consumed is True
        assert not session.live_tasks
        await _await_state(lambda: session.session_id not in manager.sessions)
        assert await _wait_with_timeout(manager, 0) == {"status": "expired"}
    finally:
        await backend.close()


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
    manager, backend = _manager(client, monkeypatch)
    output = tmp_path / "background-output.txt"
    output.write_text("", encoding="utf-8")
    child = "collected-alternate-child"
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        _emit_child_start(client, child)
        if remaining == "child":
            session_registry.publish("remaining-child", terminal=False)
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
            session_registry.publish("remaining-child", terminal=True)
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
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_result_without_background_task_is_immediately_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """バックグラウンドタスクが無いturnは従来どおり最初の結果で終端する。"""
    client = ControlledClaudeClient()
    manager, backend = _manager(client, monkeypatch)
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        client.emit(ResultMessage("通常結果"))
        result = await _wait_with_timeout(manager, 1)

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
    """自動再開が届かない場合は期限またはストリーム終端で初回結果を確定する。"""
    monkeypatch.setattr(state, "AUTO_RESUME_DEADLINE_SECONDS", 0.03)
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

        result = await _wait_with_timeout(manager, 1)

        assert result["agent_message"] == "保留結果"
        assert session.awaiting_auto_resume is False
        assert session.pending_result is None
        await _await_state(lambda: session.session_id not in manager.sessions)
        assert await _wait_with_timeout(manager, 0) == {"status": "expired"}
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_kill_interrupts_while_initial_result_is_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保留中の中断要求はSDKへ配送したうえで保留していた結果を返し、以後の継続入力を受け付ける。

    SDKはturnの外で受けた中断要求へ何も返さないため、保留結果を確定しないと`kill`はtimeoutまで待つ。
    """
    client = ControlledClaudeClient("claude-kill")
    manager, backend = _manager(client, monkeypatch)
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        client.emit(TaskStartedMessage("task-1"))
        client.emit(ResultMessage("初回結果"))
        await _await_state(lambda: session.awaiting_auto_resume)

        result = await manager.kill(session.session_id, timeout=1)

        assert result["status"] == "completed"
        assert result["agent_message"] == "初回結果"
        assert result["kill_requested"] is True
        assert client.interrupts == 1
        assert session.auto_resume_consumed is True

        response = await manager.send_message(session.session_id, "続行", timeout=1)
        assert response["delivery"] == "reply_started"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_show_reports_held_result_and_live_background_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """結果を保留している間だけ、`show`が保留と追跡中のバックグラウンドタスクを返す。"""
    client = ControlledClaudeClient("claude-show")
    manager, backend = _manager(client, monkeypatch)
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        client.emit(TaskStartedMessage("task-b", "local_agent", "子エージェント"))
        client.emit(TaskStartedMessage("task-a", "local_bash", "cat > /dev/null"))
        await _await_state(lambda: len(session.live_tasks) == 2)
        running = manager.show_session(session.session_id)
        assert "result_held" not in running
        assert "live_background_tasks" not in running

        client.emit(ResultMessage("初回結果"))
        await _await_state(lambda: session.awaiting_auto_resume)
        held = manager.show_session(session.session_id)

        assert held["status"] == "running"
        assert held["result_held"] is True
        tasks = held["live_background_tasks"]
        assert [(task["task_id"], task["task_type"], task["description"]) for task in tasks] == [
            ("task-a", "local_bash", "cat > /dev/null"),
            ("task-b", "local_agent", "子エージェント"),
        ]
        assert all(isinstance(task["seconds_since_start"], int) for task in tasks)

        await manager.kill(session.session_id, timeout=1)
        finalized = manager.show_session(session.session_id)
        assert "result_held" not in finalized
        assert "live_background_tasks" not in finalized
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
        assert set(session.live_tasks) == {"task-1"}
        assert [delivery_payload(value) for value in client.queries] == ["調査", "続行"]

        client.emit(TaskUpdatedMessage("task-1", "completed"))
        client.emit(ResultMessage("reply結果"))
        result = await _wait_with_timeout(manager, 1)
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
    """通常または自動再開済みsessionへのreplyは再度自動再開できる。"""
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
        await _wait_with_timeout(manager, 1)

        response = await manager.send_message(session.session_id, "次の作業", timeout=1)
        assert response["delivery"] == "reply_started"
        assert session.auto_resume_consumed is False

        client.emit(TaskStartedMessage("task-2"))
        client.emit(ResultMessage("次の初回結果"))
        await _await_state(lambda: session.awaiting_auto_resume)
        client.emit(TaskNotificationMessage("task-2", "completed"))
        client.emit(ResultMessage("次の再開結果", origin={"kind": "task-notification"}))
        result = await _wait_with_timeout(manager, 1)

        assert result["agent_message"] == "次の再開結果"
    finally:
        await backend.close()


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
    manager, backend = _manager(client, monkeypatch)
    caplog.set_level("WARNING", logger="agent-toolkit.agents-server.claude")
    try:
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
        assert session.auto_resume_consumed is True
        assert not [record for record in caplog.records if "claude_turn_state_unreported" in record.getMessage()]
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_result_without_background_task_is_published_on_idle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """バックグラウンドタスクの無いturnは、結果の直後に届く`idle`で従来どおり公開する。"""
    client = ControlledClaudeClient("claude-idle", report_state=True)
    manager, backend = _manager(client, monkeypatch)
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        client.finish_turn(ResultMessage("通常結果"))
        result = await _wait_with_timeout(manager, 1)

        assert result["agent_message"] == "通常結果"
        assert session.cli_turn_state == "idle"
        assert session.auto_resume_consumed is False
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_idle_with_live_shell_task_keeps_result_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """シェルのバックグラウンドタスクが動いている間に届く`idle`では確定せず、そのタスクの再開turnの結果を返す。"""
    client = ControlledClaudeClient("claude-idle-live-task", report_state=True)
    manager, backend = _manager(client, monkeypatch)
    try:
        session = await _start(manager, tmp_path, monkeypatch)
        wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
        client.emit(TaskStartedMessage("shell-1"))
        client.finish_turn(ResultMessage("待機中: shell-1"))
        await _await_state(lambda: session.awaiting_auto_resume and session.cli_turn_state == "idle")
        await asyncio.sleep(0.05)
        assert wait_task.done() is False
        assert session.pending_result is not None

        client.emit(TaskNotificationMessage("shell-1", "completed"))
        client.emit(_session_state("running"))
        client.finish_turn(ResultMessage("RESUMED", origin={"kind": "task-notification"}))
        result = await wait_task

        assert result["agent_message"] == "RESUMED"
    finally:
        await backend.close()


@pytest.mark.asyncio
async def test_unreported_turn_state_falls_back_with_single_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CLIがturn状態を報告しない場合は追跡集合だけの従来判定で公開し、警告を1回だけ記録する。"""
    client = ControlledClaudeClient("claude-unreported")
    manager, backend = _manager(client, monkeypatch)
    caplog.set_level("WARNING", logger="agent-toolkit.agents-server.claude")
    try:
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
    finally:
        await backend.close()
