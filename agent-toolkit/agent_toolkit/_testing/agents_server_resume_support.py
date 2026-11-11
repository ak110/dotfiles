"""自動再開試験の制御可能クライアントと状態遷移の補助。"""

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access

import asyncio
import contextlib
import json
import pathlib
from collections.abc import AsyncGenerator, Coroutine
from types import SimpleNamespace
from typing import Any

import pytest

from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import shared_roots, state, status_file
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_support import claude_manager, install_backend

_STREAM_END = object()


# 状態遷移の観測は経過時間で打ち切る。反復回数で打ち切ると、CPU競合時に
# 実時間の待機量が不足して自動再開の送信前に打ち切られる。
_STATE_TIMEOUT = 10.0


# 自動再開を観測するテストでは、_STATE_TIMEOUTより長い待機上限をmanager.waitへ与える。
# 観測前にwaitが期限切れになると自動再開の送信自体が発生しない。
_RESUME_WAIT_TIMEOUT = 30.0


def _wait_with_timeout(manager: server_manager.AgentsServerManager, timeout: float) -> Coroutine[Any, Any, dict[str, Any]]:
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

        Claude Code CLIはturnを終えた後に受けた中断要求へ`ResultMessage`もtaskの終了通知も返さない
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


@contextlib.asynccontextmanager
async def _manager(
    client: ControlledClaudeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[server_manager.AgentsServerManager, claude_backend.ClaudeServerManager]]:
    async with claude_manager(monkeypatch, client) as (manager, backend):
        yield manager, backend


async def _start(
    manager: server_manager.AgentsServerManager,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> state.SessionState:
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.01)
    monkeypatch.setattr(
        _atk_config,
        "parse_unresolved_model_candidates",
        lambda _model_type, **_kwargs: [("claude", "model", "high")],
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
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)


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
    manager: server_manager.AgentsServerManager,
    client: ControlledClaudeClient,
    session: state.SessionState,
    child_session_id: str,
) -> dict[str, Any]:
    """孫sessionを終了させ、継続指示後の結果を返す。"""
    wait_task = asyncio.create_task(_wait_with_timeout(manager, _RESUME_WAIT_TIMEOUT))
    await _await_state(lambda: session.awaiting_auto_resume)
    assert wait_task.done() is False
    session_registry.publish(child_session_id, state=session_registry.PublicationState.RESULT_COMPLETED)
    await _await_state(lambda: len(client.queries) == 2)
    client.emit(ResultMessage("孫session確認後の結果"))
    return await wait_task


@contextlib.asynccontextmanager
async def _codex_parent_with_child(
    tmp_path: pathlib.Path,
) -> AsyncGenerator[
    tuple[server_manager.AgentsServerManager, codex_backend.AppServerManager, state.SessionState, status_file.StatusFileWriter]
]:
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with (
        server_manager.AgentsServerManager(writer) as manager,
        codex_backend.AppServerManager(manager.sessions, manager._condition) as backend,
    ):
        install_backend(manager, "codex", backend)
        session = state.SessionState(
            "codex-parent",
            str(tmp_path),
            engine="codex",
            codex_state_provider="openai",
            model_type="execute",
            turn_seq=1,
            turn_id="turn-1",
        )
        manager.sessions[session.session_id] = session
        writer.activate()
        yield manager, backend, session, writer


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
