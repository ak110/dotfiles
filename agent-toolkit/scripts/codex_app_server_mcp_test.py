"""Codex App Server MCPの公開契約と状態集約を検証する。"""

# テストでは内部状態集約も直接検証する。
# pylint: disable=protected-access

import asyncio
import pathlib
from typing import Any, cast

import codex_app_server_mcp as subject
import pytest


class FakeClient:
    """App Server要求を記録する偽クライアント。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self._closed = False
        self.close_loop: asyncio.AbstractEventLoop | None = None

    @property
    def closed(self) -> bool:
        """テスト用の接続状態を返す。"""
        return self._closed

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.requests.append((method, params or {}))
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "thread/resume":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            return {"turn": {"id": f"turn-{len(self.requests)}"}}
        return self.responses.pop(0) if self.responses else {}

    async def _send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    async def send(self, message: dict[str, Any]) -> None:
        """テスト用のJSON-RPC応答を記録する。"""
        await self._send(message)

    async def close(self) -> None:
        """テスト用の接続終了を同じイベントループへ記録する。"""
        self._closed = True
        self.close_loop = asyncio.get_running_loop()


class BlockingReplyClient(FakeClient):
    """replyのturn/startを停止して同一sessionの競合を再現する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.reply_turn_started = asyncio.Event()
        self.release_reply_turn = asyncio.Event()
        self._turn_start_count = 0

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method != "turn/start":
            return await super().request(method, params)
        self._turn_start_count += 1
        self.requests.append((method, params or {}))
        if self._turn_start_count >= 2:
            self.reply_turn_started.set()
            await self.release_reply_turn.wait()
        return {"turn": {"id": f"turn-{len(self.requests)}"}}


class FailingReplyClient(FakeClient):
    """replyのturn/startだけを失敗させる偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self._turn_start_count = 0

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "turn/start":
            self._turn_start_count += 1
            if self._turn_start_count >= 2:
                self.requests.append((method, params or {}))
                raise subject.JsonRpcResponseError("turn/start", -32000, "turn/start failed")
        return await super().request(method, params)


class FailingResumeClient(FakeClient):
    """replyのthread/resumeを最初の一度だけ失敗させる偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.fail_resume = True

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "thread/resume" and self.fail_resume:
            self.fail_resume = False
            self.requests.append((method, params or {}))
            raise subject.AppServerError("thread/resume failed")
        return await super().request(method, params)


class LostInitialTurnResponseClient(FakeClient):
    """初回turn/startの応答喪失を再現する偽クライアント。"""

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "turn/start":
            self.requests.append((method, params or {}))
            raise subject.AppServerError("turn/start response lost")
        return await super().request(method, params)


class InterruptResponseErrorClient(FakeClient):
    """turn/interruptだけ通常のJSON-RPC errorを返す偽クライアント。"""

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "turn/interrupt":
            self.requests.append((method, params or {}))
            raise subject.JsonRpcResponseError(method, -32000, "turn is already completing")
        return await super().request(method, params)


class FakeStderr:
    """stderrのreadlineを再現する非同期入力。"""

    def __init__(self, lines: list[bytes]) -> None:
        self.lines = lines

    async def readline(self) -> bytes:
        return self.lines.pop(0)


class FakeProcessWithStderr:
    """JsonRpcProcess._read_stderrが要求する最小の偽プロセス。"""

    def __init__(self, lines: list[bytes]) -> None:
        self.stderr = FakeStderr(lines)


def _seed_completed_reply_session(session: subject.SessionState) -> None:
    """前turn由来の値を設定し、失敗時の残留を検証できるようにする。"""
    session.status = "completed"
    session.turn_id = "turn-previous"
    session.plan = [{"id": "previous-plan"}]
    session.current_item = {"type": "commandExecution", "id": "previous-item"}
    session.commentary = "previous commentary"
    session.diff_changed = True
    session.agent_message = "previous result"
    session.protocol_warnings = ["previous warning"]
    session.result_retrieved = True


def test_tools_are_exactly_the_five_async_operations() -> None:
    assert set(subject.mcp._tool_manager._tools) == {  # noqa: SLF001  # pylint: disable=protected-access
        "codex_start",
        "codex_status",
        "codex_wait",
        "codex_result",
        "codex_start_reply",
    }
    start_schema = subject.mcp._tool_manager._tools["codex_start"].parameters  # noqa: SLF001
    assert start_schema["required"] == ["prompt", "cwd"]
    wait_schema = subject.mcp._tool_manager._tools["codex_wait"].parameters  # noqa: SLF001
    assert wait_schema["properties"]["timeout"]["default"] == subject.DEFAULT_WAIT_TIMEOUT


def test_validation_rejects_invalid_cwd_and_partial_model_effort(tmp_path: pathlib.Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        subject._validate_cwd("relative")  # noqa: SLF001
    with pytest.raises(ValueError, match="existing directory"):
        subject._validate_cwd(str(tmp_path / "missing"))  # noqa: SLF001
    with pytest.raises(ValueError, match="together"):
        subject._validate_model_effort("model", None)  # noqa: SLF001


@pytest.mark.asyncio
async def test_start_passes_fixed_noninteractive_policy_and_returns_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    manager = subject.AppServerManager()
    client = FakeClient()

    async def ensure_client() -> FakeClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    response = await manager.start("調査", str(tmp_path), "gpt-test", "high")

    assert response["session_id"] == "thread-1"
    assert response["status"] == "running"
    assert [method for method, _ in client.requests] == ["thread/start", "turn/start"]
    thread_params = client.requests[0][1]
    assert thread_params["approvalPolicy"] == "never"
    assert thread_params["sandbox"] == "danger-full-access"
    turn_params = client.requests[1][1]
    assert turn_params["sandboxPolicy"] == {"type": "dangerFullAccess"}
    assert turn_params["model"] == "gpt-test"
    assert turn_params["effort"] == "high"


@pytest.mark.asyncio
async def test_initial_turn_start_response_loss_keeps_thread_until_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """thread.id確定後の初回turn/start応答喪失をturn終端まで非終端で保持する。"""
    manager = subject.AppServerManager()
    client = LostInitialTurnResponseClient()

    async def ensure_client() -> LostInitialTurnResponseClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    response = await manager.start("開始", str(tmp_path))

    assert response["session_id"] == "thread-1"
    assert response["status"] == "running"
    assert manager.status("thread-1")["session_id"] == "thread-1"
    with pytest.raises(ValueError, match="not completed"):
        manager.result("thread-1")
    with pytest.raises(ValueError, match="still running"):
        await manager.start_reply("thread-1", "再試行")
    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-2", "status": "completed", "error": None},
            },
        }
    )
    result = manager.result("thread-1")
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_input_validation_failure_does_not_create_thread(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """thread開始前の入力検証失敗はsessionを作成しない。"""
    manager = subject.AppServerManager()
    client = FakeClient()

    async def ensure_client() -> FakeClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    with pytest.raises(ValueError, match="non-empty"):
        await manager.start("", str(tmp_path))

    assert not manager.sessions
    assert not client.requests


@pytest.mark.asyncio
async def test_notifications_complete_turn_and_result_then_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    manager = subject.AppServerManager()
    client = FakeClient()

    async def ensure_client() -> FakeClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "item/started",
            "params": {"threadId": "thread-1", "turnId": "turn-2", "item": {"type": "commandExecution"}},
        }
    )
    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-2",
                "item": {"type": "agentMessage", "text": "最終結果"},
            },
        }
    )
    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-2", "status": "completed", "error": None},
            },
        }
    )
    assert manager.status("thread-1")["status"] == "completed"
    result = manager.result("thread-1")
    assert result["agent_message"] == "最終結果"
    await manager.start_reply("thread-1", "続行")
    assert [method for method, _ in client.requests][-2:] == ["thread/resume", "turn/start"]
    assert client.requests[-2][1]["approvalPolicy"] == "never"


@pytest.mark.asyncio
async def test_start_reply_serializes_same_session(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """同一sessionの並行replyを直列化し、後続要求を二重開始しない。"""
    manager = subject.AppServerManager()
    client = BlockingReplyClient()

    async def ensure_client() -> BlockingReplyClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    session = manager.sessions["thread-1"]
    session.status = "completed"
    session.result_retrieved = True

    first = asyncio.create_task(manager.start_reply("thread-1", "続行1"))
    await client.reply_turn_started.wait()
    second = asyncio.create_task(manager.start_reply("thread-1", "続行2"))
    await asyncio.sleep(0)
    assert not second.done()

    client.release_reply_turn.set()
    assert (await first)["status"] == "running"
    with pytest.raises(ValueError, match="still running"):
        await second
    assert [method for method, _ in client.requests].count("thread/resume") == 1
    assert [method for method, _ in client.requests].count("turn/start") == 2


@pytest.mark.asyncio
async def test_start_reply_failure_marks_failed_and_wakes_waiters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """replyのturn/start失敗をfailedへ確定し、前turnの値を残さない。"""
    manager = subject.AppServerManager()
    client = FailingReplyClient()

    async def ensure_client() -> FailingReplyClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    session = manager.sessions["thread-1"]
    _seed_completed_reply_session(session)
    waiter = asyncio.create_task(manager.wait("thread-1", timeout=10))

    with pytest.raises(subject.AppServerError, match="turn/start failed"):
        await manager.start_reply("thread-1", "続行")

    waited = await waiter
    status = manager.status("thread-1")
    result = manager.result("thread-1")
    assert waited == status
    assert status["status"] == result["status"] == "failed"
    assert status["session_id"] == result["session_id"] == "thread-1"
    assert status["turn_id"] == result["turn_id"] == ""
    assert status["error"] == result["error"] == {"message": "turn/start: turn/start failed"}
    assert status["plan"] == []
    assert status["current_item"] is None
    assert status["commentary"] == ""
    assert status["diff_changed"] is False
    assert status["protocol_warnings"] == []
    assert result["agent_message"] == ""
    status_after_result = manager.status("thread-1")
    assert status_after_result["status"] == result["status"]
    assert status_after_result["turn_id"] == result["turn_id"]
    assert status_after_result["error"] == result["error"]
    with pytest.raises(ValueError, match="ambiguous"):
        await manager.start_reply("thread-1", "再試行")


@pytest.mark.asyncio
async def test_start_reply_resume_failure_clears_previous_state_and_allows_explicit_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """thread/resume失敗を最新failed状態へ反映し、結果回収後の安全な再試行を許可する。"""
    manager = subject.AppServerManager()
    client = FailingResumeClient()

    async def ensure_client() -> FailingResumeClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    _seed_completed_reply_session(manager.sessions["thread-1"])

    with pytest.raises(subject.AppServerError, match="thread/resume failed"):
        await manager.start_reply("thread-1", "続行")

    status = manager.status("thread-1")
    result = manager.result("thread-1")
    assert status["status"] == result["status"] == "failed"
    assert status["session_id"] == result["session_id"] == "thread-1"
    assert status["turn_id"] == result["turn_id"] == ""
    assert status["error"] == result["error"] == {"message": "thread/resume failed"}
    assert status["plan"] == []
    assert status["current_item"] is None
    assert status["commentary"] == ""
    assert status["diff_changed"] is False
    assert status["protocol_warnings"] == []
    assert result["agent_message"] == ""
    status_after_result = manager.status("thread-1")
    assert status_after_result["status"] == result["status"]
    assert status_after_result["turn_id"] == result["turn_id"]
    assert status_after_result["error"] == result["error"]

    retry = await manager.start_reply("thread-1", "再試行")
    assert retry["status"] == "running"
    assert [method for method, _ in client.requests].count("thread/resume") == 2


@pytest.mark.asyncio
async def test_app_server_stderr_is_forwarded_only_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """App Server stderrをMCP stdoutへ混ぜず、そのままstderrへ転送する。"""
    client = subject.JsonRpcProcess(lambda _: asyncio.sleep(0), lambda _: asyncio.sleep(0))
    client.process = cast(Any, FakeProcessWithStderr([b"diagnostic\n", b""]))

    await client._read_stderr()  # noqa: SLF001

    captured = capsys.readouterr()
    assert captured.err == "diagnostic\n"
    assert not captured.out


@pytest.mark.asyncio
async def test_mcp_lifespan_closes_app_server_on_current_event_loop() -> None:
    """MCP終了時の子接続回収を稼働中のイベントループへ結び付ける。"""
    manager = subject.AppServerManager()
    client = FakeClient()
    manager.client = cast(subject.JsonRpcProcess, client)
    original_manager = subject._MANAGER
    subject._MANAGER = manager
    try:
        loop = asyncio.get_running_loop()
        async with subject._mcp_lifespan(cast(subject.FastMCP[Any], None)):  # noqa: SLF001
            pass
    finally:
        subject._MANAGER = original_manager

    assert client.close_loop is loop
    assert manager.client is None


@pytest.mark.asyncio
async def test_all_server_requests_are_replied_and_noninteractive_requests_fail(
    tmp_path: pathlib.Path,
) -> None:
    manager = subject.AppServerManager()
    client = FakeClient()
    manager.client = cast(subject.JsonRpcProcess, client)
    manager.sessions["thread-1"] = subject.SessionState("thread-1", str(tmp_path), turn_id="turn-1")

    await manager._handle_server_request(  # noqa: SLF001
        {"id": "elicitation-1", "method": "mcpServer/elicitation/request", "params": {"threadId": "thread-1"}}
    )
    assert client.sent[-1] == {"id": "elicitation-1", "result": {"action": "cancel", "content": None, "_meta": None}}
    assert manager.status("thread-1")["status"] == "running"

    await manager._handle_server_request(  # noqa: SLF001
        {"id": 2, "method": "item/tool/requestUserInput", "params": {"threadId": "thread-1"}}
    )
    assert client.sent[-1]["id"] == 2
    assert "error" in client.sent[-1]
    assert manager.status("thread-1")["status"] == "running"
    with pytest.raises(ValueError, match="not completed"):
        manager.result("thread-1")
    await asyncio.sleep(0)
    assert client.requests[-1][0] == "turn/interrupt"
    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "interrupted", "error": None},
            },
        }
    )
    assert manager.result("thread-1")["status"] == "interrupted"


@pytest.mark.asyncio
async def test_interrupt_json_rpc_error_keeps_both_sessions_nonterminal_until_target_completes(
    tmp_path: pathlib.Path,
) -> None:
    """turn/interruptの通常エラーで対象外sessionをfailedにせず、対象turnの完了を待つ。"""
    manager = subject.AppServerManager()
    client = InterruptResponseErrorClient()
    manager.client = cast(subject.JsonRpcProcess, client)
    manager.sessions["thread-1"] = subject.SessionState("thread-1", str(tmp_path), turn_id="turn-1")
    manager.sessions["thread-2"] = subject.SessionState("thread-2", str(tmp_path), turn_id="turn-2")

    waiter = asyncio.create_task(manager.wait("thread-1", timeout=10))
    await manager._handle_server_request(  # noqa: SLF001
        {
            "id": 1,
            "method": "item/tool/requestUserInput",
            "params": {"threadId": "thread-1"},
        }
    )
    scheduled = tuple(manager._background_tasks)  # noqa: SLF001
    assert scheduled
    await asyncio.gather(*scheduled)

    target = manager.status("thread-1")
    unrelated = manager.status("thread-2")
    assert target["status"] == "running"
    assert unrelated["status"] == "running"
    assert target["error"] == {"message": "turn/interrupt: turn is already completing"}
    assert unrelated["error"] is None
    assert not waiter.done()

    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "interrupted", "error": None},
            },
        }
    )
    assert (await waiter)["status"] == "interrupted"
    assert manager.result("thread-1")["status"] == "interrupted"


@pytest.mark.asyncio
async def test_unknown_request_without_identifier_fails_all_active_sessions(tmp_path: pathlib.Path) -> None:
    manager = subject.AppServerManager()
    client = FakeClient()
    manager.client = cast(subject.JsonRpcProcess, client)
    manager.sessions["thread-1"] = subject.SessionState("thread-1", str(tmp_path), turn_id="turn-1")
    manager.sessions["thread-2"] = subject.SessionState("thread-2", str(tmp_path), turn_id="turn-2")

    await manager._fail_for_request({}, "unknown/server/request")  # noqa: SLF001

    assert {manager.status("thread-1")["status"], manager.status("thread-2")["status"]} == {"running"}
    await manager._handle_client_failure(subject.AppServerError("connection closed"))
    assert {manager.status("thread-1")["status"], manager.status("thread-2")["status"]} == {"failed"}


@pytest.mark.asyncio
async def test_wait_timeout_returns_current_state_without_failing(tmp_path: pathlib.Path) -> None:
    manager = subject.AppServerManager()
    manager.sessions["thread-1"] = subject.SessionState("thread-1", str(tmp_path), turn_id="turn-1")
    result = await manager.wait("thread-1", timeout=0)
    assert result["status"] == "running"
    with pytest.raises(ValueError, match="non-negative"):
        await manager.wait("thread-1", timeout=-1)
