"""Claude SDKを再現する共有クライアントと利用上限の補助。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import contextlib
import pathlib
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast

import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._testing.agents_server_support_common import FakeBackend, _install_backend, install_backend


@contextlib.asynccontextmanager
async def claude_manager(
    monkeypatch: pytest.MonkeyPatch, client: Any
) -> AsyncGenerator[tuple[server_manager.AgentsServerManager, claude_backend.ClaudeServerManager]]:
    """指定clientを使うClaude backendとManagerを生成直後から所有する。"""
    async with (
        server_manager.AgentsServerManager() as manager,
        claude_backend.ClaudeServerManager(
            manager.sessions,
            manager._condition,
            client_factory=lambda _options: client,
            expire_session=manager._expire_session,
        ) as backend,
    ):
        install_backend(manager, "claude", backend)
        monkeypatch.setattr(claude_backend, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        yield manager, backend


class SystemMessage:
    """Claude SDK initメッセージの偽型。"""

    subtype = "init"

    def __init__(self, session_id: str) -> None:
        self.data = {"session_id": session_id}


class AssistantMessage:
    """Claude SDK assistantメッセージの偽型。"""

    def __init__(self, text: str) -> None:
        self.content = [SimpleNamespace(text=text)]


class MultipleBlockAssistantMessage:
    """複数TextBlockを持つClaude assistantメッセージの偽型。"""

    def __init__(self, *texts: str) -> None:
        self.content = [SimpleNamespace(text=text) for text in texts]


class StreamEvent:
    """Claude SDKの部分出力イベントの偽型。"""

    def __init__(self, event_type: str) -> None:
        self.event = {"type": event_type}


class ErrorAssistantMessage(AssistantMessage):
    """API失敗を表すClaude assistantメッセージの偽型。"""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.error = "rate_limit"


class ResultMessage:
    """Claude SDK resultメッセージの偽型。"""

    def __init__(
        self,
        result: str,
        terminal_reason: str | None = None,
        *,
        is_error: bool = False,
        api_error_status: int | None = None,
    ) -> None:
        self.result = result
        self.errors: list[str] = []
        self.terminal_reason = terminal_reason
        self.is_error = is_error
        self.api_error_status = api_error_status


class FakeClaudeClient:
    """ClaudeSDKClientの最小互換。"""

    def __init__(self, streams: list[list[Any]]) -> None:
        self.streams = [list(stream) for stream in streams]
        self.queries: list[str] = []
        self.interrupts = 0
        self.connected = False
        self.disconnected = False

    async def connect(self) -> None:
        self.connected = True

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def interrupt(self) -> None:
        self.interrupts += 1

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for message in messages:
                yield message

        return stream()

    async def disconnect(self) -> None:
        self.disconnected = True


class QueueClaudeClient(FakeClaudeClient):
    """queryと背景イベントが同じ長命なSDK受信列へ入るクライアント。"""

    def __init__(self, streams: list[list[Any]]) -> None:
        super().__init__(streams)
        self.messages: asyncio.Queue[Any] = asyncio.Queue()
        self.readers = 0
        self.receive_calls = 0

    async def query(self, prompt: str) -> None:
        await super().query(prompt)
        for message in self.streams.pop(0):
            self.messages.put_nowait(message)

    def receive_messages(self):
        self.receive_calls += 1

        async def stream():
            self.readers += 1
            try:
                while True:
                    yield await self.messages.get()
            finally:
                self.readers -= 1

        return stream()


class DelayedClaudeClient(FakeClaudeClient):
    """init後の各メッセージを、試験側の観測と解放に合わせて供給する。"""

    def __init__(self, streams):
        super().__init__(streams)
        self.requested: asyncio.Queue[int] = asyncio.Queue()
        self.release: asyncio.Queue[None] = asyncio.Queue()
        self.cancelled = asyncio.Event()

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            try:
                for index, message in enumerate(messages):
                    if index:
                        self.requested.put_nowait(index)
                        await asyncio.wait_for(self.release.get(), timeout=5)
                    yield message
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

        return stream()


class OpenStreamClaudeClient(FakeClaudeClient):
    """指定したメッセージを返した後、結果を返さずにstreamを開いたまま保つ偽クライアント。"""

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for message in messages:
                yield message
            await asyncio.Event().wait()

        return stream()


class InterruptAwareClaudeClient(FakeClaudeClient):
    """interruptの受理後に中断結果を返す偽クライアント。"""

    def __init__(self) -> None:
        super().__init__([[SystemMessage("claude-interrupted")]])
        self.interrupt_event = asyncio.Event()

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for message in messages:
                yield message
            await self.interrupt_event.wait()
            yield ResultMessage("中断結果", "aborted_streaming")

        return stream()

    async def interrupt(self) -> None:
        self.interrupts += 1
        self.interrupt_event.set()


class FailingClaudeClient(FakeClaudeClient):
    """init後にmessage stream例外を発生させる偽クライアント。"""

    def receive_messages(self):
        async def stream():
            yield SystemMessage("claude-failed")
            raise RuntimeError("stream failed")

        return stream()


class SynchronizedFailingClaudeClient(FakeClaudeClient):
    """同じ待機バッチでmessage stream例外を発生させる偽クライアント。"""

    def __init__(self) -> None:
        super().__init__([])
        self.message_waiting = asyncio.Event()
        self.release_message = asyncio.Event()

    def receive_messages(self):
        async def stream():
            yield SystemMessage("claude-batch-failed")
            self.message_waiting.set()
            await self.release_message.wait()
            raise RuntimeError("stream failed")

        return stream()


class BlockingContinuationClaudeClient(FakeClaudeClient):
    """継続入力のqueryを停止して所有タスクの取り消しを再現する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__([[SystemMessage("claude-blocking")]])
        self.message_waiting = asyncio.Event()
        self.query_started = asyncio.Event()
        self.release_query = asyncio.Event()

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        if len(self.queries) > 1:
            self.query_started.set()
            await self.release_query.wait()

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for message in messages:
                yield message
            self.message_waiting.set()
            await self.release_query.wait()

        return stream()


class BlockingResumeClaudeClient(FakeClaudeClient):
    """resumeの最初のqueryを記録前に保留する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__([])
        self.query_started = asyncio.Event()
        self.query_cancelled = asyncio.Event()
        self.release_query = asyncio.Event()
        self.stop_stream = asyncio.Event()
        self.connect_calls = 0
        self.query_calls = 0

    async def connect(self) -> None:
        self.connect_calls += 1
        await super().connect()

    async def query(self, prompt: str) -> None:
        self.query_calls += 1
        if self.query_calls == 1:
            self.query_started.set()
            try:
                await self.release_query.wait()
            except asyncio.CancelledError:
                self.query_cancelled.set()
                raise
        await super().query(prompt)

    def receive_messages(self):
        async def stream():
            yield SystemMessage("claude-pending")
            await self.stop_stream.wait()

        return stream()


@contextlib.asynccontextmanager
async def _blocking_resume_claude_manager(
    monkeypatch: pytest.MonkeyPatch, client: BlockingResumeClaudeClient
) -> AsyncGenerator[tuple[server_manager.AgentsServerManager, claude_backend.ClaudeServerManager]]:
    """保留したClaude queryを持つ再開テストの共通入力を作成する。"""
    async with claude_manager(monkeypatch, client) as (manager, backend):
        yield manager, backend


class BlockingResultClaudeClient(FakeClaudeClient):
    """init後の結果を明示イベントまで保留する偽クライアント。"""

    def __init__(self, session_id: str) -> None:
        super().__init__([])
        self.session_id = session_id
        self.release_result = asyncio.Event()

    def receive_messages(self):
        async def stream():
            yield SystemMessage(self.session_id)
            await self.release_result.wait()
            yield ResultMessage("新しい結果")

        return stream()


async def _start_claude_until_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    messages: list[Any],
    availability_timeout: float,
) -> tuple[dict[str, Any], float, bool]:
    """指定メッセージの後に結果を返さないClaude sessionを`start`で起動し、応答、所要秒数、モデル出力の観測を返す。"""
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", availability_timeout)
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [("claude", "model", "high")]
    )
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    client = OpenStreamClaudeClient([messages])
    async with server_manager.AgentsServerManager() as manager:
        backend = claude_backend.ClaudeServerManager(
            manager.sessions, manager._condition, client_factory=lambda _options: client
        )
        _install_backend(manager, "claude", backend)
        loop = asyncio.get_running_loop()
        started = loop.time()
        response = await manager.start("plan", "調査", str(tmp_path))
        elapsed = loop.time() - started
        return response, elapsed, backend.sessions[response["session_id"]].model_output_observed


def _rate_limit_event(status: str, limit_type: str | None, resets_at: int | None = None) -> Any:
    """Claude Agent SDKが利用枠の報告として返すイベント。"""
    info = claude_agent_sdk.RateLimitInfo(status=cast(Any, status), rate_limit_type=cast(Any, limit_type), resets_at=resets_at)
    return claude_agent_sdk.RateLimitEvent(rate_limit_info=info, uuid="rate-limit", session_id="")


def _usage_limit_result(text: str = "You've hit your limit", api_error_status: int = 429) -> ResultMessage:
    """利用上限で拒否されたturnの`ResultMessage`。"""
    return ResultMessage(text, is_error=True, api_error_status=api_error_status)


def _rejected_stream(limit_type: str, resets_at: int | None, *, before_init: bool, session_id: str) -> list[Any]:
    event = _rate_limit_event("rejected", limit_type, resets_at)
    failure = [ErrorAssistantMessage("API Error: 429 usage limit"), _usage_limit_result()]
    if before_init:
        return [event, SystemMessage(session_id), *failure]
    return [SystemMessage(session_id), event, *failure]


@contextlib.asynccontextmanager
async def _usage_limited_manager(
    monkeypatch: pytest.MonkeyPatch, streams: list[list[Any]]
) -> AsyncGenerator[tuple[server_manager.AgentsServerManager, FakeClaudeClient, FakeBackend]]:
    """先頭のClaude候補が利用上限で拒否され、後続にCodex候補を持つ起動条件のManagerを返す。"""
    candidates = [("claude", "opus", "high"), ("codex", "gpt-6.1-sol", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _m, **_kwargs: candidates)
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 5.0)
    monkeypatch.setattr(claude_backend, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    session_id = next(
        (message.data["session_id"] for stream in streams for message in stream if isinstance(message, SystemMessage)), None
    )
    if session_id is None:
        raise ValueError("利用上限の受信列に初期化済みsessionの識別子が必要である")
    # 利用上限の解除後は所有CLIを交換して同じ会話をresumeするため、各接続の受信列にinitを含める。
    owned_streams = [
        list(stream) if any(isinstance(message, SystemMessage) for message in stream) else [SystemMessage(session_id), *stream]
        for stream in streams
    ]
    client = FakeClaudeClient(owned_streams)
    async with server_manager.AgentsServerManager() as manager:
        backend = claude_backend.ClaudeServerManager(
            manager.sessions, manager._condition, client_factory=lambda _options: client
        )
        _install_backend(manager, "claude", backend)
        codex = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", codex)
        yield manager, client, codex


_CLAUDE_CONNECTION_ENVIRONMENT = (
    "AGENT_TOOLKIT_CONFIG_CLAUDE_CREDITS_ENV_FILE",
    "AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE",
    "AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS",
    "AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_CONNECTIONS",
)


def configure_claude_payg(
    monkeypatch: pytest.MonkeyPatch, env_file: str, *, orchestrator: str = "payg", worker: str = "subscription,payg"
) -> None:
    """Claudeの従量課金APIのenvファイルと役割別の使用順を環境変数で与える。

    使用順の省略時は、統括担当をAPIだけ、委譲先をサブスクリプション、APIの順にする。
    `env_file`が空文字列の場合は接続設定を全て外し、親環境を引き継ぐ`inherit`接続にする。
    """
    for name in _CLAUDE_CONNECTION_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    if not env_file:
        return
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE", env_file)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", orchestrator)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_CONNECTIONS", worker)
