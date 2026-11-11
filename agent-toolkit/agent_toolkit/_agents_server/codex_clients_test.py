"""Codexのclientsを検証する。"""

from __future__ import annotations

import asyncio
import contextlib
import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agent_toolkit._agents_server import codex as subject
from agent_toolkit._agents_server import codex_clients, session_errors, state
from agent_toolkit._testing.agents_server_support import FakeCodexClient
from agent_toolkit._testing.codex_backend_support import _return, _SilentClient

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


@pytest.mark.parametrize("close_failure", ["residual", "exception"])
@pytest.mark.asyncio
async def test_start_failure_retains_unassigned_client_for_close_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, close_failure: str
) -> None:
    """初期化失敗直後の回収が失敗しても、managerの停止で同じ接続を再回収する。"""
    close = AsyncMock(side_effect=[["unreleased-process"] if close_failure == "residual" else OSError("停止失敗"), []])
    client = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("初期化失敗")), close=close)
    monkeypatch.setattr(codex_clients, "JsonRpcProcess", Mock(return_value=client))
    async with subject.AppServerManager() as manager:
        with pytest.raises(RuntimeError if close_failure == "residual" else OSError):
            await manager.start("起動の寿命確認", str(tmp_path))
        close.assert_awaited_once()
        assert not manager.sessions
        await manager.close()
    client.start.assert_awaited_once()
    assert close.await_count == 2


@pytest.mark.asyncio
async def test_close_attempts_other_connections_after_one_connection_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """接続の停止失敗を保持して残る接続を回収し、次の停止で残存接続だけを再試行する。"""
    first, second = FakeCodexClient(), FakeCodexClient()
    first_cleanup, second_cleanup = first.close, second.close
    first_close = AsyncMock(side_effect=[OSError("最初の接続停止失敗"), []])
    second_close = AsyncMock(return_value=[])
    monkeypatch.setattr(first, "close", first_close)
    monkeypatch.setattr(second, "close", second_close)
    async with contextlib.AsyncExitStack() as cleanup, subject.AppServerManager() as manager:
        cleanup.push_async_callback(first_cleanup)
        cleanup.push_async_callback(second_cleanup)
        monkeypatch.setattr(manager, "_ensure_client", AsyncMock(side_effect=[first, second]))
        first_prompt, second_prompt = state.ResumePrompt("最初の会話"), state.ResumePrompt("次の会話")
        try:
            await manager.resume("first-thread", first_prompt, str(tmp_path))
            await manager.resume("second-thread", second_prompt, str(tmp_path))
        finally:
            first_prompt.close()
            second_prompt.close()
        with pytest.raises(OSError, match="最初の接続停止失敗"):
            await manager.close()
        first_close.assert_awaited_once()
        second_close.assert_awaited_once()
        await manager.close()
    assert first_close.await_count == 2
    second_close.assert_awaited_once()


@pytest.mark.parametrize("blocked", ["background", "client"])
@pytest.mark.asyncio
async def test_close_starts_all_clients_before_waiting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, blocked: str
) -> None:
    """background taskと1つの接続が停止していても、他の所有接続の停止を先に開始する。"""
    release = asyncio.Event()
    clients = [FakeCodexClient(), FakeCodexClient()]
    started = [asyncio.Event(), asyncio.Event()]
    async with subject.AppServerManager() as manager:
        monkeypatch.setattr(manager, "_ensure_client", AsyncMock(side_effect=clients))
        for name in ("first", "second"):
            prompt = state.ResumePrompt("停止試験")
            try:
                await manager.resume(name, prompt, str(tmp_path))
            finally:
                prompt.close()
        for index, client in enumerate(clients):
            original_close = client.close

            async def close(*, position: int = index, original: Any = original_close) -> list[str]:
                started[position].set()
                if blocked == "client" and position == 0:
                    await release.wait()
                return await original()

            monkeypatch.setattr(client, "close", close)

        async def background() -> None:
            while not release.is_set():
                with contextlib.suppress(asyncio.CancelledError):
                    await release.wait()

        if blocked == "background":
            task = asyncio.create_task(background())
            manager._background_tasks.add(task)
            await asyncio.sleep(0)
        closing = asyncio.create_task(manager.close())
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 2)
        finally:
            release.set()
            await closing


@pytest.mark.asyncio
async def test_start_aborts_when_thread_start_never_returns(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """thread/startの応答が返らない起動を上限で打ち切り、session状態を登録せず、起動したApp Serverを終了させて例外で返す。"""
    monkeypatch.setattr(state, "SESSION_INITIALIZATION_TIMEOUT", 0.05)
    async with subject.AppServerManager() as manager:
        client = _SilentClient()
        monkeypatch.setattr(manager, "_ensure_client", lambda *_args, **_kwargs: _return(client))

        with pytest.raises(session_errors.SessionInitializationTimeoutError):
            await manager.start("実装する", str(tmp_path))

        assert not manager.sessions
        assert client.closed and not manager._clients
        await manager.close()
