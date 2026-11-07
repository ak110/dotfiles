"""実readerと模擬App Serverで、自動resetから同じ会話の継続までを検証する。

RPC標本はhttps://learn.chatgpt.com/docs/app-server（2026-11-05取得）による。
process生成と送信だけをメモリー内transportへ差し替え、creditを消費しない。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from asyncio.subprocess import Process
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from agent_toolkit._agents_server import codex, manager, state
from agent_toolkit._testing.agents_server_support import install_backend

# reader・所有taskの境界を駆動し、公開start/reply/waitへ結果を渡す。
# pylint: disable=protected-access
pytestmark = pytest.mark.usefixtures("agents_server_isolation")


class _MemoryAppServer(codex.JsonRpcProcess):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.stream = asyncio.StreamReader()
        self.turn_count = 0
        self.weekly_usage = 100
        self.available = 1
        self.account = {"type": "chatgpt", "email": "example@example.invalid"}
        self.outcome = "reset"
        self.consume_started = asyncio.Event()
        self.consume_release: asyncio.Event | None = None
        self.consume_cancelled = asyncio.Event()
        self.account_read = asyncio.Event()
        self.account_release: asyncio.Event | None = None
        self.request_error = False
        self.refresh_failures = 0
        self.primary_usage: Any = 10
        self.other_buckets: dict[str, Any] = {}

    async def start(self) -> None:
        self.process = cast(Process, SimpleNamespace(stdin=object(), stdout=self.stream, pid=None))
        self._reader_task = asyncio.create_task(self._read_stdout())

    async def _send(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if not isinstance(method, str):
            return
        params = message.get("params", {})
        self.requests.append((method, params))
        if method in {"thread/start", "thread/resume"}:
            result: dict[str, Any] = {"thread": {"id": "reset-thread"}, "modelProvider": "openai"}
        elif method == "config/read":
            result = {"config": {"model_provider": "openai"}}
        elif method == "turn/start":
            self.turn_count += 1
            result = {"turn": {"id": f"turn-{self.turn_count}"}}
        elif method == "turn/steer":
            result = {"turnId": params["expectedTurnId"]}
        elif method == "account/read":
            self.account_read.set()
            if self.account_release is not None:
                await self.account_release.wait()
            result = {"account": self.account}
        elif method == "account/rateLimits/read":
            if self.weekly_usage == 0 and self.refresh_failures:
                self.refresh_failures -= 1
                raise codex.AppServerError("reset後の利用枠取得失敗")
            bucket = {
                "limitId": "codex",
                "primary": {"usedPercent": self.primary_usage, "windowDurationMins": 300, "resetsAt": 1900000000},
                "secondary": {"usedPercent": self.weekly_usage, "windowDurationMins": 10080, "resetsAt": 1900000000},
            }
            result = {
                "rateLimits": bucket,
                "rateLimitResetCredits": {"availableCount": self.available, "credits": None},
            }
            if self.other_buckets:
                result["rateLimitsByLimitId"] = {"codex": bucket, **self.other_buckets}
        elif method == "account/rateLimitResetCredit/consume":
            self.consume_started.set()
            if self.consume_release is not None:
                try:
                    await self.consume_release.wait()
                except asyncio.CancelledError:
                    self.consume_cancelled.set()
                    raise
            if self.request_error:
                raise codex.AppServerError("模擬通信失敗")
            if self.outcome in {"reset", "alreadyRedeemed"}:
                self.weekly_usage = 0
                self.available = 0
            result = {"outcome": self.outcome}
        elif method == "thread/unsubscribe":
            result = {"status": "notLoaded"}
        else:
            result = {}
        self.stream.feed_data((json.dumps({"id": message["id"], "result": result}) + "\n").encode())
        if method == "turn/start":
            self.emit("turn/started", {"threadId": "reset-thread", "turn": {"id": f"turn-{self.turn_count}"}})

    def emit(self, method: str, params: dict[str, Any]) -> None:
        self.stream.feed_data((json.dumps({"method": method, "params": params}) + "\n").encode())

    async def close(self) -> list[str]:
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        self.process = None
        return []


@contextlib.asynccontextmanager
async def _environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True, policy: str = "five-hour"
) -> AsyncGenerator[tuple[manager.AgentsServerManager, codex.AppServerManager, list[_MemoryAppServer]]]:
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_AUTO_RESET", "true" if enabled else "false")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_USAGE_LIMIT_WAIT", policy)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    clients: list[_MemoryAppServer] = []

    def client_factory(*args: Any, **kwargs: Any) -> _MemoryAppServer:
        client = _MemoryAppServer(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(codex, "JsonRpcProcess", client_factory)
    async with manager.AgentsServerManager(None) as owner, codex.AppServerManager(owner.sessions, owner._condition) as backend:
        install_backend(owner, "codex", backend)
        yield owner, backend, clients


async def _until(backend: codex.AppServerManager, predicate: Callable[[], bool]) -> None:
    async with backend._condition:
        await asyncio.wait_for(backend._condition.wait_for(predicate), timeout=5)


def _fail(client: _MemoryAppServer, session: state.SessionState, tmp_path: Path, *, minutes: int = 10080) -> None:
    directory = tmp_path / "codex" / "sessions" / "2026" / "11" / "05"
    directory.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "type": "token_count",
            "rate_limits": {"secondary": {"used_percent": 100, "window_minutes": minutes, "resets_at": 1900000000}},
        },
        {"type": "task_complete", "error": {"codex_error_info": "usage_limit_exceeded"}},
    ]
    (directory / f"rollout-test-{session.session_id}.jsonl").write_text(
        "".join(json.dumps({"type": "event_msg", "payload": record}) + "\n" for record in records), encoding="utf-8"
    )
    client.emit(
        "turn/completed",
        {
            "threadId": session.session_id,
            "turn": {"id": session.turn_id, "status": "failed", "error": {"codexErrorInfo": "usageLimitExceeded"}, "items": []},
        },
    )


@pytest.mark.parametrize("explicit_subscription", [False, True])
@pytest.mark.asyncio
async def test_weekly_reset_uses_reader_and_resumes_same_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit_subscription: bool
) -> None:
    async with _environment(tmp_path, monkeypatch) as (owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        if explicit_subscription:
            session.codex_model_provider = session.codex_subscription_provider = "openai"
        _fail(client, session, tmp_path)
        await _until(backend, lambda: session.awaiting_auto_resume and not session.codex_auto_reset_pending)
        assert not session.result_available
        assert session.usage_limit_resume_at is not None and session.usage_limit_resume_at <= asyncio.get_running_loop().time()
        assert [method for method, _ in client.requests].count("account/rateLimitResetCredit/consume") == 1
        assert client.reader_failure is None
        await owner._advance_child_session_wait(session)
        assert session.turn_seq == 2
        assert session.session_id == "reset-thread"
        assert client.turn_count == 2
        client.emit(
            "turn/completed",
            {
                "threadId": session.session_id,
                "turn": {
                    "id": session.turn_id,
                    "status": "completed",
                    "error": None,
                    "items": [{"type": "agentMessage", "id": "answer", "text": "継続後の成果"}],
                },
            },
        )
        await _until(backend, lambda: session.result_available)
        assert (await owner.wait())["agent_message"] == "継続後の成果"
        await backend.release_session(session)
        assert not backend._auto_reset_monitors and not backend._auto_reset_checks
        assert client.closed


@pytest.mark.parametrize("primary_usage", [10, "unknown"])
@pytest.mark.asyncio
async def test_unused_bucket_format_does_not_block_reset_and_unverified_window_blocks_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, primary_usage: Any
) -> None:
    """未利用bucketの形式差は消費を止めず、使用率を確認できないwindowだけからは会話を再開しない。"""
    async with _environment(tmp_path, monkeypatch) as (owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        client.primary_usage = primary_usage
        client.other_buckets = {
            "other": {"limitId": "other", "secondary": {"usedPercent": "n/a", "windowDurationMins": "week", "resetsAt": None}}
        }
        _fail(client, session, tmp_path)
        await _until(
            backend, lambda: not session.codex_auto_reset_pending and (session.result_available or session.awaiting_auto_resume)
        )
        assert [method for method, _ in client.requests].count("account/rateLimitResetCredit/consume") == 1
        assert client.reader_failure is None
        await owner._advance_child_session_wait(session)
        if primary_usage == 10:
            assert client.turn_count == 2
        else:
            assert client.turn_count == 1
            assert session.result_available
            assert session.error["codexErrorInfo"] == "usageLimitExceeded"


@pytest.mark.parametrize(
    ("enabled", "count", "outcome", "policy", "waits", "consumed"),
    [
        (False, 1, "reset", "five-hour", False, False),
        (True, 0, "reset", "five-hour", False, False),
        (True, 1, "noCredit", "five-hour", False, True),
        (True, 0, "reset", "always", True, False),
        (True, 1, "nothingToReset", "five-hour", True, True),
    ],
)
@pytest.mark.asyncio
async def test_reset_outcomes_preserve_normal_wait_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    count: int,
    outcome: str,
    policy: str,
    waits: bool,
    consumed: bool,
) -> None:
    async with _environment(tmp_path, monkeypatch, enabled=enabled, policy=policy) as (_, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        client.available, client.outcome = count, outcome
        _fail(client, session, tmp_path)
        await _until(
            backend, lambda: not session.codex_auto_reset_pending and (session.result_available or session.awaiting_auto_resume)
        )
        assert session.awaiting_auto_resume is waits
        assert session.result_available is not waits
        assert any(method == "account/rateLimitResetCredit/consume" for method, _ in client.requests) is consumed
        if not enabled:
            assert not any(method.startswith("account/") for method, _ in client.requests)
        if not waits:
            assert session.status == "failed" and session.error["codexErrorInfo"] == "usageLimitExceeded"


@pytest.mark.asyncio
async def test_periodic_check_resets_near_threshold_without_model_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex, "AUTO_RESET_CHECK_INTERVAL", 0.01)
    async with _environment(tmp_path, monkeypatch) as (_, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        client.weekly_usage = 99
        await asyncio.wait_for(client.consume_started.wait(), timeout=5)
        await _until(backend, lambda: not session.codex_auto_reset_pending)
        assert client.weekly_usage == 0
        assert client.turn_count == 1 and session.turn_seq == 1
        assert len(backend._auto_reset_monitors) == 1
        assert (await backend.send_message(session, "追送"))["delivery"] == "steered"
        assert len(backend._auto_reset_monitors) == 1


@pytest.mark.parametrize("operation", ["send", "interrupt", "release", "close"])
@pytest.mark.asyncio
async def test_pending_check_is_cancelled_before_turn_control_or_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    async with _environment(tmp_path, monkeypatch) as (_, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        client.consume_release = asyncio.Event()
        _fail(client, session, tmp_path)
        await asyncio.wait_for(client.consume_started.wait(), timeout=5)
        assert session.codex_auto_reset_pending and not session.result_available
        if operation == "send":
            assert (await backend.send_message(session, "新しい作業"))["delivery"] == "reply_started"
            assert session.turn_seq == 2 and not session.awaiting_auto_resume
        elif operation == "interrupt":
            session.interrupt_requested = True
            await backend.interrupt(session)
            assert session.result_available
        elif operation == "release":
            await backend.release_session(session)
            assert client.closed
            assert not backend._auto_reset_monitors and not backend._auto_reset_checks
        else:
            await backend.close()
            assert client.closed
            assert not backend._auto_reset_monitors and not backend._auto_reset_checks
        assert client.consume_cancelled.is_set()
        assert not session.codex_auto_reset_pending


@pytest.mark.asyncio
async def test_transport_failure_does_not_report_reset_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _environment(tmp_path, monkeypatch) as (_, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        client.request_error = True
        _fail(client, session, tmp_path)
        await _until(backend, lambda: session.result_available)
        assert session.status == "failed"
        assert not session.codex_auto_reset_pending
        assert len([method for method, _ in client.requests if method == "account/rateLimitResetCredit/consume"]) == 1


@pytest.mark.parametrize("account", [{"type": "apiKey"}, {"type": "chatgpt", "email": None}])
@pytest.mark.asyncio
async def test_ineligible_account_keeps_weekly_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, account: dict[str, Any]
) -> None:
    async with _environment(tmp_path, monkeypatch) as (_, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        clients[0].account = account
        _fail(clients[0], session, tmp_path)
        await _until(backend, lambda: session.result_available)
        assert session.status == "failed"
        assert not clients[0].consume_started.is_set()


@pytest.mark.asyncio
async def test_external_provider_does_not_consume_chatgpt_credit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _environment(tmp_path, monkeypatch) as (_, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        session.codex_state_provider = "external-api"
        _fail(clients[0], session, tmp_path)
        await _until(backend, lambda: session.result_available)
        assert not clients[0].account_read.is_set()
        assert not clients[0].consume_started.is_set()


@pytest.mark.asyncio
async def test_completed_turn_cannot_start_a_later_consumption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex, "AUTO_RESET_CHECK_INTERVAL", 0.01)
    async with _environment(tmp_path, monkeypatch) as (_, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        client.account_release = asyncio.Event()
        await asyncio.wait_for(client.account_read.wait(), timeout=5)
        client.emit(
            "turn/completed",
            {
                "threadId": session.session_id,
                "turn": {
                    "id": session.turn_id,
                    "status": "completed",
                    "error": None,
                    "items": [{"type": "agentMessage", "id": "answer", "text": "完了"}],
                },
            },
        )
        await _until(backend, lambda: session.result_available)
        client.account_release.set()
        await _until(backend, lambda: not session.codex_auto_reset_pending)
        assert not client.consume_started.is_set()


@pytest.mark.asyncio
async def test_nothing_to_reset_is_rechecked_by_owned_monitor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex, "AUTO_RESET_CHECK_INTERVAL", 0.01)
    async with _environment(tmp_path, monkeypatch) as (_, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        client.outcome = "nothingToReset"
        _fail(client, session, tmp_path)
        await _until(backend, lambda: session.awaiting_auto_resume and not session.codex_auto_reset_pending)
        assert not session.result_available
        client.outcome = "reset"
        await _until(
            backend,
            lambda: (
                session.usage_limit_resume_at is not None and session.usage_limit_resume_at <= asyncio.get_running_loop().time()
            ),
        )
        params = [params for method, params in client.requests if method == "account/rateLimitResetCredit/consume"]
        assert len(params) >= 2
        assert params[-1]["idempotencyKey"] != params[0]["idempotencyKey"]


@pytest.mark.asyncio
async def test_cold_resume_recreates_one_monitor_for_owned_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _environment(tmp_path, monkeypatch) as (_, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        await backend.release_session(session)
        resumed = await backend.resume(
            session.session_id, state.ResumePrompt("再開"), str(tmp_path), state_provider="openai", turn_seq=session.turn_seq
        )
        assert resumed.session_id == session.session_id and resumed.turn_seq == 2
        assert len(clients) == 2 and clients[0].closed
        assert len(backend._auto_reset_monitors) == 1


@pytest.mark.parametrize("policy", ["five-hour", "always"])
@pytest.mark.parametrize("outcome", ["reset", "alreadyRedeemed"])
@pytest.mark.asyncio
async def test_refresh_failure_recovers_same_session_without_second_consumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str, outcome: str
) -> None:
    """消費成功後の取得失敗は会話を保持し、次の定期確認で同じ会話を再開する。"""
    async with _environment(tmp_path, monkeypatch, policy=policy) as (owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        client.outcome = outcome
        client.refresh_failures = 1
        _fail(client, session, tmp_path)
        await _until(backend, lambda: session.awaiting_auto_resume and not session.codex_auto_reset_pending)
        assert not session.result_available
        assert session.usage_limit_resume_at is not None
        assert session.usage_limit_resume_at > asyncio.get_running_loop().time()
        backend._start_auto_reset_check(session)
        await _until(backend, lambda: not session.codex_auto_reset_pending)
        assert session.usage_limit_resume_at <= asyncio.get_running_loop().time()
        assert [method for method, _ in client.requests].count("account/rateLimitResetCredit/consume") == 1
        await owner._advance_child_session_wait(session)
        assert session.session_id == "reset-thread" and session.turn_seq == 2 and client.turn_count == 2
        client.emit(
            "turn/completed",
            {
                "threadId": session.session_id,
                "turn": {
                    "id": session.turn_id,
                    "status": "completed",
                    "error": None,
                    "items": [{"type": "agentMessage", "id": "answer", "text": "再取得後の成果"}],
                },
            },
        )
        await _until(backend, lambda: session.result_available)
        assert (await owner.wait())["agent_message"] == "再取得後の成果"
