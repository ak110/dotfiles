"""claudeの分割試験が共有する準備処理。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import contextlib
import os
import pathlib
import sys
import types
from collections.abc import AsyncGenerator, AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from agent_toolkit._agents_server import claude, claude_options, state
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._common import plugin_environment
from agent_toolkit._testing.agents_server_support import (
    BlockingContinuationClaudeClient,
    FailingClaudeClient,
    FakeClaudeClient,
    SystemMessage,
    install_backend,
)


class _SilentClient:
    """接続を保ったままinitメッセージを送らないSDKクライアントのスタブ。

    切断でCLIの子プロセスを終了する実クライアントの契約を模す。
    実クライアントは取り消された実行での切断では子プロセスの終了処理へ到達しないため、
    取り消し要求が残るtaskからの切断では、終了を記録せず例外を送出する。
    """

    def __init__(self) -> None:
        self.terminated = False

    async def connect(self) -> None:
        return None

    async def query(self, prompt: str) -> None:
        del prompt  # noqa

    async def receive_messages(self) -> AsyncIterator[Any]:
        await asyncio.Event().wait()
        yield None  # pragma: no cover - 待機が解けないことを表す到達不能の分岐

    async def disconnect(self) -> None:
        task = asyncio.current_task()
        assert task is not None
        if task.cancelling() > 0:
            raise asyncio.CancelledError
        await asyncio.sleep(0)
        self.terminated = True


def _capture_options(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """`_build_options`がSDKへ渡す引数を捕捉する。"""
    captured: dict[str, object] = {}

    class Options:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", types.SimpleNamespace(ClaudeAgentOptions=Options))
    monkeypatch.setattr(claude_options, "_parent_settings", lambda: None)
    return captured


def _lightweight_environment(launch_kind: str) -> dict[str, str]:
    """軽量起動の委譲先へ渡す、起動区分と保持済みrootのCLIとデータ領域を含む環境を返す。"""
    return {
        "AGENT_TOOLKIT_DELEGATED_SESSION": "1",
        "AGENT_TOOLKIT_DELEGATED_LAUNCH_KIND": launch_kind,
        "PATH": str(claude.plugin_roots.SERVER_PLUGIN_ROOT / "scripts") + os.pathsep + os.environ["PATH"],
        plugin_environment.RUNTIME_DATA_ENV: str(plugin_environment.runtime_data(os.environ)),
        "CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_PROMPT_CACHE_TTL": "5m",
    }


class _TransportClient(FakeClaudeClient):
    """SDKの転送が起動したCLIの子プロセスを持つ偽クライアント。"""

    def __init__(self, streams: list[list[Any]], *, pid: int) -> None:
        super().__init__(streams)
        self._transport = SimpleNamespace(_process=SimpleNamespace(pid=pid))


@contextlib.asynccontextmanager
async def _finished_failed_claude_session(
    tmp_path: pathlib.Path,
) -> AsyncGenerator[tuple[server_manager.AgentsServerManager, claude.ClaudeServerManager, state.SessionState]]:
    """所有タスクが失敗で終わったClaude sessionと、そのbackendを保持するmanagerを返す。

    同じsessionへの次のturnは、失敗しないクライアントで始まる。
    """
    async with server_manager.AgentsServerManager() as manager:
        continuation = BlockingContinuationClaudeClient()
        continuation.streams = [[SystemMessage("claude-failed")]]
        clients = [FailingClaudeClient([]), continuation]

        def client_factory(_options: Any) -> FakeClaudeClient:
            return clients.pop(0)

        async with claude.ClaudeServerManager(
            manager.sessions,
            manager._condition,
            client_factory=client_factory,
            expire_session=manager._expire_session,
        ) as backend:
            install_backend(manager, "claude", backend)
            session = await backend.start("調査", str(tmp_path))
            for _ in range(20):
                if session.result_available and session.session_id not in backend._channels:
                    break
                await asyncio.sleep(0.01)
            yield manager, backend, session
