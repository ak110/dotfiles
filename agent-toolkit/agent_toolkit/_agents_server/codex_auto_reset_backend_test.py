"""実readerと模擬App Serverで、自動resetから同じ会話の継続までを検証する。

RPC標本はhttps://learn.chatgpt.com/docs/app-server（2026-11-05取得）による。
process生成と送信だけをメモリー内transportへ差し替え、creditを消費しない。
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import time
from asyncio.subprocess import Process
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from types import FunctionType, SimpleNamespace
from typing import Any, cast

import pytest

from agent_toolkit._agents_server import (
    codex,
    codex_auto_reset,
    codex_clients,
    codex_reset,
    manager,
    service_io,
    service_move_source,
    service_projection,
    service_state,
    service_turns,
    session_handoff,
    shared_roots,
    state,
    turn_state,
)
from agent_toolkit._agents_server.service_resources import ResourceBroker
from agent_toolkit._testing.agents_server_support import FakeBackend, install_backend

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
        self.silent: set[str] = set()
        self.thread_id = "reset-thread"
        self.close_release: asyncio.Event | None = None
        self.stopped = False

    async def start(self) -> None:
        self.process = cast(Process, SimpleNamespace(stdin=object(), stdout=self.stream, pid=None))
        self._reader_task = asyncio.create_task(self._read_stdout())

    async def _send(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if not isinstance(method, str):
            return
        params = message.get("params", {})
        self.requests.append((method, params))
        if method in self.silent:
            return
        if method in {"thread/start", "thread/resume"}:
            result: dict[str, Any] = {"thread": {"id": self.thread_id}, "modelProvider": "openai"}
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
                "primary": {"usedPercent": self.primary_usage, "windowDurationMins": 300, "resetsAt": 1899990000},
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
            self.emit("turn/started", {"threadId": self.thread_id, "turn": {"id": f"turn-{self.turn_count}"}})
        elif method == "turn/interrupt":
            # 実Codexと同じく、応答の後に中断したturnの終了を通知する。
            turn = {"id": params["turnId"], "status": "interrupted", "error": None, "items": []}
            self.emit("turn/completed", {"threadId": params["threadId"], "turn": turn})

    def emit(self, method: str, params: dict[str, Any]) -> None:
        self.stream.feed_data((json.dumps({"method": method, "params": params}) + "\n").encode())

    async def close(self) -> list[str]:
        self._closed = True
        if self.close_release is not None:
            await self.close_release.wait()
        # 読取の失敗から停止する読取task自身は待たない。
        if self._reader_task is not None and self._reader_task is not asyncio.current_task():
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        self.process = None
        self.stopped = True
        return []


@contextlib.asynccontextmanager
async def _environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, permitted: bool = True, policy: str | None = None
) -> AsyncGenerator[tuple[manager.AgentsServerManager, codex.AppServerManager, list[_MemoryAppServer]]]:
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_USAGE_RESET_PERMISSIONS", json.dumps([_PERMISSION] if permitted else []))
    if policy is None:
        monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_CODEX_USAGE_LIMIT_WAIT", raising=False)
    else:
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_USAGE_LIMIT_WAIT", policy)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    clients: list[_MemoryAppServer] = []

    async def isolated_file_operation[T](operation: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return operation(*args, **kwargs)

    # 隔離済みの読取をexecutor経由にせず、実readerとRPCの状態遷移を確かめる。  # pyfltr: allow-comment
    monkeypatch.setattr(codex_reset, "run_file_operation", isolated_file_operation)
    monkeypatch.setattr(codex_auto_reset, "run_file_operation", isolated_file_operation)

    def client_factory(*args: Any, **kwargs: Any) -> _MemoryAppServer:
        client = _MemoryAppServer(*args, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(codex_clients, "JsonRpcProcess", client_factory)
    async with manager.AgentsServerManager(None) as owner, codex.AppServerManager(owner.sessions, owner._condition) as backend:
        install_backend(owner, "codex", backend)
        yield owner, backend, clients


async def _until(backend: codex.AppServerManager, predicate: Callable[[], bool]) -> None:
    try:
        async with backend._condition:
            await asyncio.wait_for(backend._condition.wait_for(predicate), timeout=5)
    except TimeoutError as error:
        observed = []
        for session in backend.sessions.values():
            client = backend._owned_client(session)
            task = backend._auto_reset_checks.get(session.session_id)
            observed.append(
                {
                    "status": session.status,
                    "pending": session.codex_auto_reset_pending,
                    "awaiting_auto_resume": session.awaiting_auto_resume,
                    "turn_completed": session.turn_completed,
                    "result_available": session.result_available,
                    "requests": [name for name, _ in client.requests] if isinstance(client, _MemoryAppServer) else [],
                    "check_stack": (
                        [(frame.f_code.co_name, frame.f_lineno) for frame in task.get_stack()] if task is not None else []
                    ),
                }
            )
        expected = predicate.__code__.co_names if isinstance(predicate, FunctionType) else type(predicate).__name__
        raise AssertionError(f"待機条件 {expected} に未到達。観測: {observed}") from error


def _fail(
    client: _MemoryAppServer, session: state.SessionState, tmp_path: Path, *, minutes: int = 10080, both_windows: bool = False
) -> None:
    directory = tmp_path / "codex" / "sessions" / "2026" / "11" / "05"
    directory.mkdir(parents=True, exist_ok=True)
    limits = {"secondary": {"used_percent": 100, "window_minutes": minutes, "resets_at": 1900000000}}
    if both_windows:
        limits["primary"] = {"used_percent": 100, "window_minutes": 300, "resets_at": 1899990000}
    records = [
        {"type": "token_count", "rate_limits": limits},
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
                    "items": [{"type": "agentMessage", "id": "answer", "text": "状態: completed\n結果: 継続後の成果"}],
                },
            },
        )
        await _until(backend, lambda: session.result_available)
        assert (await owner.wait())["agent_message"] == "状態: completed\n結果: 継続後の成果"
        await backend.release_session(session)
        assert not backend._auto_reset_monitors and not backend._auto_reset_checks
        assert client.closed


@pytest.mark.asyncio
async def test_weekly_reset_waits_for_remaining_five_hour_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _environment(tmp_path, monkeypatch) as (owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        client.primary_usage = 100
        _fail(client, session, tmp_path, both_windows=True)
        await _until(backend, lambda: session.turn_completed and not session.codex_auto_reset_pending)
        assert session.awaiting_auto_resume and not session.result_available
        assert client.weekly_usage == 0 and client.primary_usage == 100
        assert session.api_error is not None and session.api_error["limit_type"] == "five_hour"
        assert session.pending_result is not None
        assert session.pending_result["error"]["usageLimit"]["type"] == "five_hour"
        assert session.usage_limit_resume_at is not None
        remaining = session.usage_limit_resume_at - asyncio.get_running_loop().time()
        assert remaining == pytest.approx(1899990000 - time.time(), abs=1)
        await owner._advance_child_session_wait(session)
        assert session.session_id == "reset-thread" and client.turn_count == 1


@pytest.mark.parametrize("primary_usage", [10, "unknown"])
@pytest.mark.asyncio
async def test_unused_bucket_format_does_not_block_reset_and_unverified_window_blocks_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, primary_usage: Any
) -> None:
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
    ("permitted", "count", "outcome", "policy", "minutes", "waits", "consumed"),
    [
        (False, 1, "reset", None, 10080, False, False),
        (False, 1, "reset", None, 300, True, False),
        (True, 0, "reset", None, 10080, False, False),
        (True, 0, "reset", None, 300, True, False),
        (True, 1, "noCredit", None, 10080, False, True),
        (True, 0, "reset", "always", 10080, True, False),
        (True, 1, "nothingToReset", None, 10080, False, True),
    ],
)
@pytest.mark.asyncio
async def test_reset_outcomes_preserve_normal_wait_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    permitted: bool,
    count: int,
    outcome: str,
    policy: str | None,
    minutes: int,
    waits: bool,
    consumed: bool,
) -> None:
    async with _environment(tmp_path, monkeypatch, permitted=permitted, policy=policy) as (_, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        client.available, client.outcome = count, outcome
        _fail(client, session, tmp_path, minutes=minutes)
        await _until(
            backend, lambda: not session.codex_auto_reset_pending and (session.result_available or session.awaiting_auto_resume)
        )
        assert session.awaiting_auto_resume is waits
        assert session.result_available is not waits
        assert any(method == "account/rateLimitResetCredit/consume" for method, _ in client.requests) is consumed
        if not permitted:
            assert session.usage_limit_wait_count == int(waits)
            assert not any(method.startswith("account/") for method, _ in client.requests)
        if not waits:
            assert session.status == "failed" and session.error["codexErrorInfo"] == "usageLimitExceeded"


@pytest.mark.asyncio
async def test_legacy_reset_flag_does_not_authorize_consumption(tmp_path, monkeypatch):
    async with _environment(tmp_path, monkeypatch, permitted=False, policy="never") as (_, backend, clients):
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_AUTO_RESET", "true")
        session = await backend.start("作業", str(tmp_path))
        _fail(clients[0], session, tmp_path)
        await _until(backend, lambda: session.result_available and not session.codex_auto_reset_pending)
        assert not any(method == "account/rateLimitResetCredit/consume" for method, _ in clients[0].requests)


@pytest.mark.asyncio
async def test_periodic_check_resets_permitted_exhausted_window_without_model_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_reset, "AUTO_RESET_CHECK_INTERVAL", 0.01)
    async with _environment(tmp_path, monkeypatch) as (_, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        client.weekly_usage = 100
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
        assert session.usage_limit_resume_at is not None
        remaining = session.usage_limit_resume_at - asyncio.get_running_loop().time()
        assert remaining == pytest.approx(1900000000 - time.time(), abs=1)
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
        assert session.usage_limit_wait_count == 0


@pytest.mark.parametrize(
    "override",
    [
        {"account": "other@example.invalid"},
        {"scope": "other"},
        {"until": "2001-01-01T00:00:00+00:00"},
        {"count": 0},
    ],
)
@pytest.mark.asyncio
async def test_inapplicable_permission_does_not_hold_weekly_failure(tmp_path, monkeypatch, override):
    async with _environment(tmp_path, monkeypatch) as (_, backend, clients):
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_USAGE_RESET_PERMISSIONS", json.dumps([{**_PERMISSION, **override}]))
        session = await backend.start("作業", str(tmp_path))
        _fail(clients[0], session, tmp_path)
        await _until(backend, lambda: session.result_available)
        assert session.usage_limit_wait_count == 0
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
    monkeypatch.setattr(codex_reset, "AUTO_RESET_CHECK_INTERVAL", 0.01)
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
                    "items": [{"type": "agentMessage", "id": "answer", "text": "状態: completed\n結果: 完了"}],
                },
            },
        )
        await _until(backend, lambda: session.result_available)
        client.account_release.set()
        await _until(backend, lambda: not session.codex_auto_reset_pending)
        assert not client.consume_started.is_set()


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
                    "items": [{"type": "agentMessage", "id": "answer", "text": "状態: completed\n結果: 再取得後の成果"}],
                },
            },
        )
        await _until(backend, lambda: session.result_available)
        assert (await owner.wait())["agent_message"] == "状態: completed\n結果: 再取得後の成果"


_LOCAL_ORIGIN = {"generation": "G1", "process": "local"}
_PERMISSION = {
    "id": "permitted-period",
    "engine": "codex",
    "account": "example@example.invalid",
    "scope": "codex",
    "count": 1,
    "from": "2000-01-01T00:00:00+00:00",
    "until": "2100-01-01T00:00:00+00:00",
}


class _Hub:
    def __init__(self, state_root: Path) -> None:
        self.state_root = state_root
        self._codec = SimpleNamespace(encode=lambda value: value)

    async def request(self, _identity: Any, _operation: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"published": [], "delivered": []}


async def _no_save() -> None:
    pass


def _flood(client: _MemoryAppServer) -> None:

    for index in range(300):
        client.emit("thread/tokenUsage/updated", {"index": index})


@contextlib.asynccontextmanager
async def _service_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, save: Callable[[], Awaitable[None]]
) -> AsyncGenerator[tuple[manager.AgentsServerManager, codex.AppServerManager, list[_MemoryAppServer]]]:
    with service_io.bind(service_io.Runtime("G1", _LOCAL_ORIGIN, ResourceBroker("G1"), save)):
        async with _environment(tmp_path, monkeypatch) as environment:
            yield environment


@pytest.mark.parametrize("permitted", [False, True])
@pytest.mark.asyncio
async def test_overall_permission_deadline_does_not_cancel_confirmed_consume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, permitted: bool
) -> None:
    monkeypatch.setattr(codex_reset, "AUTO_RESET_RPC_TIMEOUT", 8.0)
    async with _service_environment(tmp_path, monkeypatch, _no_save) as (_, backend, clients):
        if not permitted:
            permission = {**_PERMISSION, "account": "other@example.invalid"}
            monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_USAGE_RESET_PERMISSIONS", json.dumps([permission]))
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        loop = asyncio.get_running_loop()
        start_check = backend._start_auto_reset_check

        def start_with_resume_deadline(pending: state.SessionState) -> None:
            assert isinstance(pending.hold, turn_state.UsageLimitWait)
            pending.hold_result(dataclasses.replace(pending.hold, resume_at=loop.time() + 1))
            start_check(pending)

        monkeypatch.setattr(backend, "_start_auto_reset_check", start_with_resume_deadline)
        account_release = asyncio.Event()
        consume_release = asyncio.Event()
        client.account_release = account_release
        client.consume_release = consume_release
        if not permitted:
            client.silent.add("account/rateLimits/read")
        try:
            _fail(client, session, tmp_path)
            await asyncio.wait_for(client.account_read.wait(), timeout=5)
            reset_check = backend._auto_reset_checks[session.session_id]
            deadline = session.usage_limit_resume_at
            assert deadline is not None
            account_release.set()
            if permitted:
                await asyncio.wait_for(client.consume_started.wait(), timeout=5)
                await asyncio.sleep(max(0, deadline - loop.time() + 0.05))
                assert not client.consume_cancelled.is_set() and session.codex_auto_reset_pending
            else:
                await _until(backend, lambda: session.result_available)
                assert loop.time() >= deadline
        finally:
            account_release.set()
            consume_release.set()
        # 消費の確定後は、状態更新と通知を行う確認タスク自体を待つ。  # pyfltr: allow-comment
        done, _ = await asyncio.wait({reset_check}, timeout=5)
        assert reset_check in done, f"reset確認が未終了: pending={session.codex_auto_reset_pending}"
        reset_check.result()
        assert not session.codex_auto_reset_pending
        if permitted:
            assert client.weekly_usage == 0 and session.awaiting_auto_resume
        else:
            assert session.result_available and not session.awaiting_auto_resume
            assert session.error["usageLimit"]["type"] == "seven_day"
            assert not client.consume_started.is_set()


@pytest.mark.asyncio
async def test_service_transfer_during_permission_check_expires_to_weekly_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", "codex:gpt-6-sol/low,claude:opus/low")
    monkeypatch.setattr(session_handoff, "prepare_prompt", lambda _session: "前任の試験結果")
    async with _service_environment(tmp_path, monkeypatch, _no_save) as (owner, backend, clients):
        session = await backend.start("作業", str(tmp_path), model="gpt-6-sol", effort="low", model_type="execute")
        client = clients[0]
        client.account_release = asyncio.Event()
        loop = asyncio.get_running_loop()
        before_failure = loop.time()
        try:
            _fail(client, session, tmp_path)
            await asyncio.wait_for(client.account_read.wait(), timeout=5)
            assert session.usage_limit_resume_at is not None
            assert before_failure <= session.usage_limit_resume_at <= loop.time() + codex_reset.AUTO_RESET_RPC_TIMEOUT
            runtime = service_io.current()
            assert runtime is not None
            gate = service_turns.TurnGate(runtime.generation, runtime.target)
            source = service_move_source.MoveSource(backend._owned_client(session), backend, session)
            await source.detach(gate, source_is_current=False)
            transferred = service_state.restore(await service_state.snapshot(session))
        finally:
            client.account_release.set()
        assert source.descriptor is not None
        await backend.adopt_session(transferred, source.descriptor, source.auxiliary)
        assert not transferred.codex_auto_reset_pending and transferred.usage_limit_wait_count == 0
        expiry = transferred.usage_limit_resume_at
        assert expiry is not None
        with monkeypatch.context() as clock:
            clock.setattr(loop, "time", lambda: expiry - 0.1)
            await owner._advance_child_session_wait(transferred)
            assert transferred.awaiting_auto_resume and not transferred.result_available
            clock.setattr(loop, "time", lambda: expiry + 0.1)
            await owner._advance_child_session_wait(transferred)
        assert transferred.result_available and not transferred.awaiting_auto_resume
        assert owner._unavailable_reason(transferred) == "usageLimitExceeded"
        assert client.turn_count == 1 and not client.consume_started.is_set()

        fallback = FakeBackend(owner.sessions, "claude")
        install_backend(owner, "claude", fallback)
        started = await owner.start_handoff(transferred.session_id)
        assert started["engine"] == "claude"
        assert fallback.start_calls == [("opus", "low", "delegate")]


@pytest.mark.asyncio
async def test_service_usage_limit_completion_and_reset_survive_full_broker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(codex_reset, "AUTO_RESET_RPC_TIMEOUT", 1.0)
    saving_completion, allow_save = asyncio.Event(), asyncio.Event()
    session: state.SessionState | None = None

    async def save() -> None:
        if session is not None and session.turn_completed:
            saving_completion.set()
            await allow_save.wait()

    async with _service_environment(tmp_path, monkeypatch, save) as (owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        client = clients[0]
        account_release = asyncio.Event()
        client.account_release = account_release
        resource = cast(service_io.CodexResource, backend._clients[session.session_id])
        try:
            _fail(client, session, tmp_path)
            await asyncio.wait_for(client.account_read.wait(), timeout=5)
            _flood(client)
            async with asyncio.timeout(5):
                while resource.diagnostic()["backlog"] != "256/256":
                    await asyncio.sleep(0.001)
            assert saving_completion.is_set()
            assert session.turn_completed and session.awaiting_auto_resume and session.codex_auto_reset_pending
            events = await resource.resource.operation("events", after_sequence=resource._sequence)
            assert any(
                event["payload"]["kind"] == "notification" and event["payload"]["value"].get("method") == "turn/completed"
                for event in events
            )
        finally:
            allow_save.set()
            account_release.set()
        await _until(backend, lambda: session.turn_completed and not session.codex_auto_reset_pending)
        assert client.weekly_usage == 0 and session.awaiting_auto_resume
        await owner._advance_child_session_wait(session)
        assert session.session_id == "reset-thread" and client.turn_count == 2


def _timeout_record(caplog: pytest.LogCaptureFixture) -> logging.LogRecord:
    [record] = [record for record in caplog.records if "照会が時間内に終わりません" in record.getMessage()]
    return record


@pytest.mark.parametrize("permitted", [True, False])
@pytest.mark.asyncio
async def test_account_read_proceeds_while_save_waits_registration_under_event_flood(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, permitted: bool
) -> None:
    first = state.SessionState("first", str(tmp_path), engine="codex")
    second = state.SessionState("second", str(tmp_path), engine="codex")
    writer = service_projection.ServiceStatusWriter(
        {"first": first, "second": second},
        shared_roots.StatusFileIdentity("root", "root.json", None),
        cast(service_projection.ProjectionHub, _Hub(tmp_path)),
    )
    writer._active = True
    raced = asyncio.Event()

    async def publish_second(**_kwargs: Any) -> None:

        if not raced.is_set():
            raced.set()
            first._registration_task = asyncio.create_task(asyncio.sleep(0))

    monkeypatch.setattr(second, "publish_registration_async", publish_second)
    raws: list[_MemoryAppServer] = []
    failures: list[BaseException] = []

    async def ignore(_message: dict) -> None:
        pass

    async def failed(error: BaseException) -> None:
        failures.append(error)

    def factory(*handlers: Any) -> _MemoryAppServer:
        raws.append(_MemoryAppServer(*handlers))
        return raws[-1]

    runtime = service_io.Runtime("G1", _LOCAL_ORIGIN, ResourceBroker("G1"), writer.flush_async)
    client = service_io.CodexResource.create(runtime, factory, ignore, ignore, failed)
    await client.start()
    try:
        _flood(raws[0])
        result = await codex_auto_reset.check_auto_reset(
            client.request, state_dir=tmp_path / "state", permissions=[_PERMISSION] if permitted else [], rpc_timeout=5
        )
    finally:
        await client.close()
    consumed = [method for method, _ in raws[0].requests].count("account/rateLimitResetCredit/consume")
    assert raced.is_set()
    assert not failures
    assert (result.reset_confirmed, consumed) == ((True, 1) if permitted else (False, 0))


@pytest.mark.parametrize("service", [False, True])
@pytest.mark.asyncio
async def test_unanswered_account_read_ends_with_response_stage_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, service: bool
) -> None:
    monkeypatch.setattr(codex_reset, "AUTO_RESET_RPC_TIMEOUT", 0.5)
    environment = _service_environment(tmp_path, monkeypatch, _no_save) if service else _environment(tmp_path, monkeypatch)
    async with environment as (_owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        clients[0].silent.add("account/read")
        with caplog.at_level(logging.ERROR, logger="agent-toolkit.agents-server.codex"):
            backend._start_auto_reset_check(session)
            await _until(backend, lambda: not session.codex_auto_reset_pending)
        assert not clients[0].consume_started.is_set()
    record = _timeout_record(caplog)
    assert "method=account/read stage=応答待ち" in record.getMessage()
    assert "'reader_failure': None" in record.getMessage()
    assert isinstance(record.exc_info, tuple)
    assert isinstance(record.exc_info[1], TimeoutError)
    assert "deadline=許可確認全体" in record.getMessage()


@pytest.mark.asyncio
async def test_account_read_reports_reply_wait_and_full_event_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(codex_reset, "AUTO_RESET_RPC_TIMEOUT", 0.5)
    hold = asyncio.Event()
    release = asyncio.Event()

    async def save() -> None:
        if hold.is_set():
            await release.wait()

    async with _service_environment(tmp_path, monkeypatch, save) as (_owner, backend, clients):
        session = await backend.start("作業", str(tmp_path))
        resource = cast(service_io.CodexResource, backend._clients[session.session_id]).resource
        hold.set()
        _flood(clients[0])
        try:
            async with asyncio.timeout(5):
                while resource.runtime.broker.backlog(resource.identifier) != (256, 256):
                    await asyncio.sleep(0.01)
            with caplog.at_level(logging.ERROR, logger="agent-toolkit.agents-server.codex"):
                backend._start_auto_reset_check(session)
                await _until(backend, lambda: not session.codex_auto_reset_pending)
        finally:
            release.set()
        assert not clients[0].consume_started.is_set()
    message = _timeout_record(caplog).getMessage()
    assert "method=account/read stage=応答待ち" in message
    assert "'consumer': '稼働中'" in message
    assert "'backlog': '256/256'" in message
