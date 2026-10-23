"""Claudeの途中再試行を公開状態、同一会話の接続切替と停止まで通す。"""

from __future__ import annotations

# 模擬CLIと公開Managerへ同じ通知用Conditionを渡す。
# pylint: disable=protected-access
import asyncio
import datetime
import json
import pathlib
import sys
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from claude_agent_sdk._internal.message_parser import parse_message

from agent_toolkit._agents_server import claude, manager, state
from agent_toolkit._atk import claude_connection
from agent_toolkit._common import host_homes, process_tree
from agent_toolkit._testing.agents_server_support import ResultMessage, install_backend

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


class _RetryCLI:
    """終端しない再試行と、停止後の遅延結果を所有する模擬CLI。"""

    def __init__(self, session_id: str, *, initial: list[Any] | None = None, fail_on_interrupt: bool = False) -> None:
        self.messages: asyncio.Queue[Any] = asyncio.Queue()
        self.queries: list[str] = []
        self.disconnected = False
        self.interrupted = asyncio.Event()
        self.fail_on_interrupt = fail_on_interrupt
        self._transport: Any = None
        for message in initial or []:
            self.messages.put_nowait(message)
        self.messages.put_nowait(parse_message({"type": "system", "subtype": "init", "session_id": session_id}))

    async def connect(self) -> None:
        """通信開始を模す。"""

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    def receive_messages(self):
        async def stream():
            while True:
                yield await self.messages.get()

        return stream()

    async def interrupt(self) -> None:
        self.interrupted.set()
        if self.fail_on_interrupt:
            failure = ResultMessage("遅れて届いた利用上限", is_error=True, api_error_status=429)
            self.messages.put_nowait(failure)

    async def disconnect(self) -> None:
        self.disconnected = True


def _retry(status: int = 429) -> Any:
    # Claude Code 2.1.309のsystem/api_error→api_retry変換が返す形を実SDK parserへ渡す。
    return parse_message(
        {
            "type": "system",
            "subtype": "api_retry",
            "attempt": 2,
            "max_retries": 301,
            "retry_delay_ms": 21600000,
            "error_status": status,
            "error": "rate_limit_error" if status == 429 else "overloaded_error",
        }
    )


def _limit(kind: str) -> Any:
    return parse_message(
        {
            "type": "rate_limit_event",
            "uuid": "limit",
            "session_id": "retry-flow",
            "rate_limit_info": {"status": "rejected", "rateLimitType": kind, "resetsAt": 2000000000},
        }
    )


async def _wait_reason(owner: manager.AgentsServerManager, session_id: str) -> dict[str, Any]:
    async with asyncio.timeout(5):
        while True:
            shown = await owner.show_session(session_id)
            if shown.get("api_error"):
                return shown
            await asyncio.sleep(0)


@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "write", "shell"])
@pytest.mark.parametrize("source", ["before-init", "after-init", "after-retry", "record"])
@pytest.mark.asyncio
async def test_weekly_retry_continues_same_session_on_api(
    launch_kind: state.LaunchKind, source: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """途中通知だけの週間拒否から旧CLIを回収し、同じ会話のAPI応答を公開waitで受け取る。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_USAGE_LIMIT_WAIT", "five-hour")
    api = tmp_path / "api.env"
    api.write_text("ANTHROPIC_API_KEY=fixture\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(api))
    monkeypatch.setattr(claude_connection, "subscription_available", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    cli = _RetryCLI("retry-flow", initial=[_limit("seven_day"), _retry()] if source == "before-init" else None)
    resumed = _RetryCLI("retry-flow")
    clients = iter([cli, resumed])
    async with (
        manager.AgentsServerManager(None) as owner,
        claude.ClaudeServerManager(owner.sessions, owner._condition, client_factory=lambda _options: next(clients)) as backend,
    ):
        install_backend(owner, "claude", backend)
        started = await owner.start("claude:opus/medium", "残る作業を完遂する", str(tmp_path), launch_kind=launch_kind)
        await owner.activate_async()
        session_id = started["session_id"]
        if source == "record":
            record = host_homes.claude_config_dir() / "projects" / "fixture" / f"{session_id}.jsonl"
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(
                json.dumps(
                    {
                        "type": "system",
                        "subtype": "api_error",
                        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                        "retryAttempt": 2,
                        "maxRetries": 301,
                        "retryInMs": 21600000,
                        "error": {"status": 429, "rateLimits": {"rateLimitType": "seven_day", "resetsAt": 2000000000}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        elif source == "after-init":
            cli.messages.put_nowait(_limit("seven_day"))
        if source != "before-init":
            cli.messages.put_nowait(_retry())
        if source == "after-retry":
            cli.messages.put_nowait(_limit("seven_day"))
        async with asyncio.timeout(5):
            while not resumed.queries:
                await asyncio.sleep(0)
        assert cli.disconnected
        assert owner.sessions[session_id].claude_connection == "api"
        resumed.messages.put_nowait(ResultMessage("APIで残る作業を完了した"))
        owner._wait_timeouts["main"] = 5  # pylint: disable=protected-access
        result = await owner.wait()
        assert (result["session_id"], result["agent_message"]) == (session_id, "APIで残る作業を完了した")


@pytest.mark.parametrize("late_failure", [False, True])
@pytest.mark.asyncio
async def test_retry_kill_reclaims_owner_without_rescheduling(
    late_failure: bool, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """再試行中のkillは、終端未送出と停止後の失敗通知の双方で所有CLIを回収して停止を返す。"""
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    cli = _RetryCLI("retry-flow", fail_on_interrupt=late_failure)
    async with (
        manager.AgentsServerManager(None) as owner,
        claude.ClaudeServerManager(owner.sessions, owner._condition, client_factory=lambda _options: cli) as backend,
    ):
        install_backend(owner, "claude", backend)
        started = await owner.start("claude:opus/medium", "処理", str(tmp_path))
        cli.messages.put_nowait(_limit("five_hour"))
        cli.messages.put_nowait(_retry())
        shown = await _wait_reason(owner, started["session_id"])
        assert shown["api_error"]["limit_type"] == "five_hour"
        result = await owner.kill(started["session_id"], timeout=5)
        assert result["status"] == "interrupted"
        assert cli.disconnected
        session = owner.sessions[started["session_id"]]
        assert session.usage_limit_resume_at is None
        assert not session.claude_api_resume_pending
        assert len(cli.queries) == 1


@pytest.mark.parametrize("status", [429, 529])
@pytest.mark.asyncio
async def test_unclassified_retry_is_published_and_cleared_on_response(
    status: int, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """利用枠の根拠が無い429と529は一般再試行として公開し、正常応答で理由を解除する。"""
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    cli = _RetryCLI("retry-flow")
    async with (
        manager.AgentsServerManager(None) as owner,
        claude.ClaudeServerManager(owner.sessions, owner._condition, client_factory=lambda _options: cli) as backend,
    ):
        install_backend(owner, "claude", backend)
        started = await owner.start("claude:opus/medium", "処理", str(tmp_path))
        cli.messages.put_nowait(_retry(status))
        shown = await _wait_reason(owner, started["session_id"])
        assert shown["api_error"]["http_status"] == status
        assert "limit_type" not in shown["api_error"]
        assert not owner.sessions[started["session_id"]].awaiting_auto_resume
        cli.messages.put_nowait(
            parse_message(
                {
                    "type": "stream_event",
                    "event": {"type": "message_start"},
                    "uuid": "start",
                    "session_id": "retry-flow",
                }
            )
        )
        async with asyncio.timeout(5):
            while (await owner.show_session(started["session_id"])).get("api_error"):
                await asyncio.sleep(0)
        assert not cli.disconnected


@pytest.mark.asyncio
async def test_retry_kill_verifies_cli_process_exit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, host_environ: Callable[[], dict[str, str]]
) -> None:
    """SDKが切断後もCLIを残した場合も、公開killが所有CLIの終了を確認して停止結果を返す。"""
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    with process_tree.process_scope([sys.executable, "-c", "import time; time.sleep(60)"], env=host_environ()) as process:
        cli = _RetryCLI("retry-flow")
        # ClaudeSDKClientのtransportが所有する、起動済みのCLI processと同じ境界を渡す。
        cli._transport = SimpleNamespace(_process=process)  # pylint: disable=protected-access
        async with (
            manager.AgentsServerManager(None) as owner,
            claude.ClaudeServerManager(owner.sessions, owner._condition, client_factory=lambda _options: cli) as backend,
        ):
            install_backend(owner, "claude", backend)
            started = await owner.start("claude:opus/medium", "処理", str(tmp_path))
            cli.messages.put_nowait(_retry(529))
            await _wait_reason(owner, started["session_id"])
            result = await owner.kill(started["session_id"], timeout=15)
            assert result["status"] == "interrupted"
            assert process.wait(timeout=0) is not None


@pytest.mark.parametrize("switch_fails", [False, True])
@pytest.mark.asyncio
async def test_api_switch_serializes_kill_and_reports_switch_failure(
    switch_fails: bool, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接続交換中の停止は同じ所有境界で受け付け、交換の失敗は元の拒否とともに公開する。"""
    api = tmp_path / "api.env"
    api.write_text("ANTHROPIC_API_KEY=fixture\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(api))
    monkeypatch.setattr(claude_connection, "subscription_available", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    cli = _RetryCLI("retry-flow")
    resumed = _RetryCLI("retry-flow")
    connect_started = asyncio.Event()
    release_connect = asyncio.Event()

    async def connect() -> None:
        connect_started.set()
        await release_connect.wait()
        if switch_fails:
            raise RuntimeError("API接続の初期化失敗")

    monkeypatch.setattr(resumed, "connect", connect)
    clients = iter([cli, resumed])
    async with (
        manager.AgentsServerManager(None) as owner,
        claude.ClaudeServerManager(owner.sessions, owner._condition, client_factory=lambda _options: next(clients)) as backend,
    ):
        install_backend(owner, "claude", backend)
        started = await owner.start("claude:opus/medium", "処理", str(tmp_path))
        await owner.activate_async()
        cli.messages.put_nowait(_limit("seven_day"))
        cli.messages.put_nowait(_retry())
        await asyncio.wait_for(connect_started.wait(), timeout=5)
        if switch_fails:
            release_connect.set()
            owner._wait_timeouts["main"] = 5  # pylint: disable=protected-access
            result = await owner.wait()
            assert result["status"] == "failed"
            assert "初期化失敗" in result["error"]["message"]
            assert result["error"]["cause"]["apiErrorStatus"] == 429
        else:
            kill = asyncio.create_task(owner.kill(started["session_id"], timeout=5))
            release_connect.set()
            result = await kill
            assert result["status"] == "interrupted"
        assert cli.disconnected and resumed.disconnected
