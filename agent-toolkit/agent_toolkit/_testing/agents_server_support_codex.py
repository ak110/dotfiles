"""Codex App Serverを再現する共有クライアント。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import contextlib
import json
import pathlib
import tempfile
from collections.abc import AsyncGenerator, Callable
from typing import Any

import pytest

from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import state
from agent_toolkit._testing.agents_server_support_common import install_backend


class FakeCodexClient:
    """Codex App Server要求を記録する偽クライアント。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.reader_failure: BaseException | None = None
        self._turn_count = 0
        self.initial_provider: str | None = None
        self.rollout_path: pathlib.Path | None = None
        self._rollout_paths: dict[str, pathlib.Path] = {}
        self._rollout_resources = contextlib.ExitStack()
        self._rollout_directory: str | None = None

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        params = params or {}
        self.requests.append((method, params))
        if on_sent is not None:
            on_sent()
        if method == "config/read":
            return {"config": {"model_provider": getattr(self, "primary", "openai")}}
        if method == "thread/read":
            thread_id = params["threadId"]
            path = self.rollout_path or self._rollout_paths.get(thread_id)
            if path is None:
                if self._rollout_directory is None:
                    self._rollout_directory = self._rollout_resources.enter_context(
                        tempfile.TemporaryDirectory(prefix="fake-codex-rollout-")
                    )
                path = pathlib.Path(self._rollout_directory) / f"{thread_id}.jsonl"
                # Codex CLI 0.164.0のsession_metaを保存し、thread/readのpathとして返す。
                path.write_text(
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {"id": thread_id, "model_provider": self.initial_provider or "openai"},
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                self._rollout_paths[thread_id] = path
            return {"thread": {"id": thread_id, "path": str(path), "turns": []}}
        if method in {"thread/start", "thread/resume"}:
            thread_id = params.get("threadId", "thread-codex")
            provider = params.get("modelProvider", getattr(self, "primary", "openai"))
            if method == "thread/start" and self.initial_provider is None:
                self.initial_provider = provider
            return {"thread": {"id": thread_id}, "modelProvider": provider}
        if method == "turn/start":
            self._turn_count += 1
            return {"turn": {"id": f"turn-{self._turn_count}"}}
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        if method == "thread/unsubscribe":
            return {"status": "notLoaded"}
        return {}

    async def close(self) -> list[str]:
        """実クライアントと同じ終了インターフェースを提供する。終了を確認できない対象は持たない。"""
        self.closed = True
        self._rollout_resources.close()
        self._rollout_directory = None
        self._rollout_paths.clear()
        return []


class BlockingResumeCodexClient(FakeCodexClient):
    """最初のthread/resume応答を明示イベントまで保留する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.resume_started = asyncio.Event()
        self.release_resume = asyncio.Event()
        self.resume_finished = asyncio.Event()

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method == "thread/resume":
            self.resume_started.set()
            await self.release_resume.wait()
        result = await super().request(method, params, on_sent=on_sent)
        if method == "thread/resume":
            self.resume_finished.set()
        return result


class LostTurnStartClient(FakeCodexClient):
    """thread/start成功後にturn/start応答を喪失する偽クライアント。"""

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method == "turn/start":
            raise codex_backend.AppServerError("turn/start response lost")
        return await super().request(method, params, on_sent=on_sent)


class SteerRaceClient(FakeCodexClient):
    """steer拒否とturn終了の競合を再現する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.steer_called = asyncio.Event()

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method == "turn/steer":
            self.steer_called.set()
            raise codex_backend.JsonRpcResponseError(method, -32600, "turn is not active")
        return await super().request(method, params, on_sent=on_sent)


class ServerRequestClient(FakeCodexClient):
    """server-initiated requestへの応答を記録する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[dict[str, Any]] = []

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


class InterruptErrorClient(FakeCodexClient):
    """turn/interruptへJSON-RPC errorを返す偽クライアント。"""

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method == "turn/interrupt":
            raise codex_backend.JsonRpcResponseError(method, -32600, "interrupt rejected")
        return await super().request(method, params, on_sent=on_sent)


class CompletingInterruptClient(FakeCodexClient):
    """中断要求の配送中に対象turnを終了させる偽クライアント。"""

    def __init__(self, backend: codex_backend.AppServerManager, session: state.SessionState) -> None:
        super().__init__()
        self.backend = backend
        self.session = session

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method == "turn/interrupt":
            await self.backend._handle_notification(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": self.session.session_id,
                        "turn": {"id": self.session.turn_id, "status": "interrupted", "error": None},
                    },
                }
            )
        return await super().request(method, params, on_sent=on_sent)


class HoldingTurnStartClient(FakeCodexClient):
    """turn/start受理後の応答を保留し、中断で実作業を終える偽クライアント。"""

    def __init__(self, backend: codex_backend.AppServerManager) -> None:
        super().__init__()
        self.backend = backend
        self.turn_start_received = asyncio.Event()
        self.active_turn = False

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method != "turn/start":
            if method == "turn/interrupt":
                assert params is not None
                self.active_turn = False
                await self.backend._handle_notification(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": params["threadId"],
                            "turn": {"id": params["turnId"], "status": "interrupted", "error": None},
                        },
                    }
                )
            return await super().request(method, params, on_sent=on_sent)

        params = params or {}
        self.requests.append((method, params))
        if on_sent is not None:
            on_sent()
        self._turn_count += 1
        turn_id = f"turn-{self._turn_count}"
        self.active_turn = True
        await self.backend._handle_notification(
            {
                "method": "turn/started",
                "params": {"threadId": params["threadId"], "turn": {"id": turn_id}},
            }
        )
        self.turn_start_received.set()
        await asyncio.Event().wait()
        raise AssertionError("保留中のturn/start応答が予期せず完了した")


class NaturallyCompletingTurnStartClient(FakeCodexClient):
    """turn/start応答待ちの取消中に対象turnを自然終了できる偽クライアント。"""

    def __init__(self, backend: codex_backend.AppServerManager) -> None:
        super().__init__()
        self.backend = backend
        self.turn_start_received = asyncio.Event()
        self.active_turn = False
        self.turn_id = "turn-natural"

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if method != "turn/start":
            return await super().request(method, params, on_sent=on_sent)

        params = params or {}
        self.requests.append((method, params))
        if on_sent is not None:
            on_sent()
        self.active_turn = True
        self.turn_start_received.set()
        await asyncio.Event().wait()
        raise AssertionError("保留中のturn/start応答が予期せず完了した")

    async def complete_turn(self, session_id: str) -> None:
        """開始通知と自然完了通知を連続配送する。"""
        await self.backend._handle_notification(
            {
                "method": "turn/started",
                "params": {"threadId": session_id, "turn": {"id": self.turn_id}},
            }
        )
        self.active_turn = False
        await self.backend._handle_notification(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": session_id,
                    "turn": {"id": self.turn_id, "status": "completed", "error": None},
                },
            }
        )


@contextlib.asynccontextmanager
async def _pending_codex_manager[PendingCodexClient: (HoldingTurnStartClient, NaturallyCompletingTurnStartClient)](
    monkeypatch: pytest.MonkeyPatch, client_type: type[PendingCodexClient]
) -> AsyncGenerator[tuple[server_manager.AgentsServerManager, codex_backend.AppServerManager, PendingCodexClient]]:
    """保留したturn/start応答を持つCodex再開テストの共通入力を作成する。"""
    async with server_manager.AgentsServerManager() as manager:
        backend = codex_backend.AppServerManager(manager.sessions, manager._condition)
        client = client_type(backend)

        async def ensure_client(*_args: Any, **_kwargs: Any) -> PendingCodexClient:
            return client

        monkeypatch.setattr(backend, "_ensure_client", ensure_client)
        install_backend(manager, "codex", backend)
        yield manager, backend, client


async def _start_blocked_codex_resume(
    manager: server_manager.AgentsServerManager,
    client: HoldingTurnStartClient | NaturallyCompletingTurnStartClient,
    session_id: str,
) -> None:
    """turn開始を確認した後、別の継続要求の短い期限切れを起こす。"""
    manager._start_resume(manager.expired_sessions[session_id], "準備指示", None)  # pylint: disable=protected-access
    await asyncio.wait_for(client.turn_start_received.wait(), timeout=1)
    with pytest.raises(TimeoutError, match=f"send_message timed out: {session_id}"):
        await manager.send_message(session_id, "再開指示", timeout=0.01)
    assert client.active_turn is True
