"""Codex App Server MCPの公開契約と状態集約を検証する。"""

# テストでは内部状態集約も直接検証する。
# pylint: disable=protected-access

import asyncio
import json
import pathlib
from collections.abc import Awaitable, Callable
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


class CompletionBeforeTurnResponseClient(FakeClient):
    """turn/start応答より先にturn/completed通知を配送する偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.before_turn_response: Callable[[str], Awaitable[None]] | None = None
        self._turn_start_count = 0

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method != "turn/start":
            return await super().request(method, params)
        self._turn_start_count += 1
        self.requests.append((method, params or {}))
        turn_id = f"turn-{self._turn_start_count}"
        callback = self.before_turn_response
        if callback is not None:
            self.before_turn_response = None
            await callback(turn_id)
        return {"turn": {"id": turn_id}}


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


class SteerClient(FakeClient):
    """turn/steerの応答と拒否を制御する偽クライアント。"""

    def __init__(self, *, response: dict[str, Any] | None = None, error: BaseException | None = None) -> None:
        super().__init__()
        self.steer_response = response
        self.steer_error = error
        self.steer_called = asyncio.Event()

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "turn/steer":
            self.requests.append((method, params or {}))
            self.steer_called.set()
            if self.steer_error is not None:
                raise self.steer_error
            return self.steer_response or {}
        return await super().request(method, params)


class ReplyResponseLossClient(FakeClient):
    """replyのturn/start応答だけを失わせる偽クライアント。"""

    def __init__(self) -> None:
        super().__init__()
        self.turn_start_count = 0

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "turn/start":
            self.turn_start_count += 1
            if self.turn_start_count >= 2:
                self.requests.append((method, params or {}))
                raise subject.AppServerError("reply turn/start response lost")
        return await super().request(method, params)


class MissingTurnIdResponseClient(FakeClient):
    """指定回のturn/start応答からturn IDを欠落させる偽クライアント。"""

    def __init__(self, missing_on_turn_start: int) -> None:
        super().__init__()
        self.missing_on_turn_start = missing_on_turn_start
        self.turn_start_count = 0

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "turn/start":
            self.turn_start_count += 1
            if self.turn_start_count == self.missing_on_turn_start:
                self.requests.append((method, params or {}))
                return {"turn": {}}
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


class FakeAppServerStdin:
    """App Server要求へ応答する非同期入力。"""

    def __init__(self, process: "FakeAppServerProcess") -> None:
        self.process = process

    def write(self, data: bytes) -> None:
        for raw_line in data.splitlines():
            request = json.loads(raw_line)
            if isinstance(request, dict) and "id" in request:
                self.process.emit(self.process.response_for(request))

    async def drain(self) -> None:
        """テスト用stdinの書込み完了を直ちに返す。"""


class FakeAppServerProcess:
    """JsonRpcProcessが利用するstdio subprocessの最小実装。"""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.stdout = asyncio.StreamReader(limit=limit)
        self.stderr = asyncio.StreamReader(limit=limit)
        self.stdin = FakeAppServerStdin(self)
        self.returncode: int | None = None
        self.requests: list[dict[str, Any]] = []

    def response_for(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        method = request.get("method")
        if method in {"thread/start", "thread/resume"}:
            result: dict[str, Any] = {"thread": {"id": "thread-1"}}
        elif method == "turn/start":
            result = {"turn": {"id": "turn-1"}}
        else:
            result = {}
        return {"id": request["id"], "result": result}

    def emit(self, message: dict[str, Any]) -> None:
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self.stdout.feed_data(encoded)

    def terminate(self) -> None:
        self.returncode = -15
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    def kill(self) -> None:
        self.returncode = -9
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


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


def _mark_result_available(session: subject.SessionState) -> None:
    """テスト用sessionをresult_availableへ進める。"""
    session.turn_completed = True


def test_tools_are_exactly_the_six_async_operations() -> None:
    assert set(subject.mcp._tool_manager._tools) == {  # noqa: SLF001  # pylint: disable=protected-access
        "codex_start",
        "codex_status",
        "codex_wait",
        "codex_result",
        "codex_start_reply",
        "codex_send_message",
    }
    start_schema = subject.mcp._tool_manager._tools["codex_start"].parameters  # noqa: SLF001
    assert start_schema["required"] == ["prompt", "cwd"]
    wait_schema = subject.mcp._tool_manager._tools["codex_wait"].parameters  # noqa: SLF001
    assert wait_schema["properties"]["timeout"]["default"] == subject.DEFAULT_WAIT_TIMEOUT
    send_schema = subject.mcp._tool_manager._tools["codex_send_message"].parameters  # noqa: SLF001
    assert send_schema["required"] == ["session_id", "prompt"]
    assert "expectedTurnId" not in send_schema["properties"]


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
    assert response["result_available"] is False
    assert [method for method, _ in client.requests] == ["thread/start", "turn/start"]
    thread_params = client.requests[0][1]
    assert thread_params["approvalPolicy"] == "never"
    assert thread_params["sandbox"] == "danger-full-access"
    turn_params = client.requests[1][1]
    assert turn_params["sandboxPolicy"] == {"type": "dangerFullAccess"}
    assert turn_params["model"] == "gpt-test"
    assert turn_params["effort"] == "high"


@pytest.mark.asyncio
async def test_large_jsonl_notifications_keep_connection_and_result_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """64KiBを超えるplan・diff通知後も後続通知を処理して結果を回収できる。"""
    processes: list[FakeAppServerProcess] = []

    async def create_subprocess(*args: Any, **kwargs: Any) -> FakeAppServerProcess:
        del args
        process = FakeAppServerProcess(kwargs["limit"])
        processes.append(process)
        return process

    monkeypatch.setattr(subject.asyncio, "create_subprocess_exec", create_subprocess)
    manager = subject.AppServerManager()
    try:
        response = await manager.start("開始", str(tmp_path))
        assert response["status"] == "running"
        process = processes[0]
        assert process.limit == subject.APP_SERVER_STREAM_LIMIT_BYTES

        large_plan = "p" * (80 * 1024)
        plan_notification = {
            "method": "turn/plan/updated",
            "params": {"threadId": "thread-1", "plan": [{"text": large_plan}]},
        }
        plan_line = json.dumps(plan_notification, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        assert len(plan_line) > 64 * 1024
        process.stdout.feed_data(plan_line + b"\n")

        large_diff = "d" * (80 * 1024)
        diff_notification = {
            "method": "turn/diff/updated",
            "params": {"threadId": "thread-1", "diff": large_diff},
        }
        diff_line = json.dumps(diff_notification, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        assert len(diff_line) > 64 * 1024
        process.stdout.feed_data(diff_line + b"\n")
        process.emit(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "thread-1",
                    "item": {"type": "agentMessage", "text": "最終結果"},
                },
            }
        )
        process.emit(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "completed", "error": None},
                },
            }
        )

        status = await manager.wait("thread-1", timeout=1)
        assert status["status"] == "completed"
        assert status["plan"] == [{"text": large_plan}]
        assert status["diff_changed"] is True
        assert manager.client is not None
        assert manager.client.reader_failure is None
        assert manager.client.closed is False
        result = manager.result("thread-1")
        assert result["agent_message"] == "最終結果"
        assert result["result_available"] is True
        assert (await manager.start_reply("thread-1", "継続"))["status"] == "running"
    finally:
        await manager.close()


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
    assert response["result_available"] is False
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
    assert result["result_available"] is True


@pytest.mark.asyncio
async def test_initial_turn_start_missing_id_is_ambiguous(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """初回turn/start応答のID欠落を非終端の曖昧状態へ保つ。"""
    manager = subject.AppServerManager()
    client = MissingTurnIdResponseClient(missing_on_turn_start=1)

    async def ensure_client() -> MissingTurnIdResponseClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    response = await manager.start("開始", str(tmp_path))

    session = manager.sessions["thread-1"]
    assert response["result_available"] is False
    assert session.turn_start_ambiguous is True
    assert response["error"] == {"message": "turn/start returned no turn.id"}


@pytest.mark.asyncio
async def test_initial_completion_before_turn_start_response_preserves_terminal_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """初回turn/completed通知がturn/start応答より先でも終端状態をrunningへ戻さない。"""
    manager = subject.AppServerManager()
    client = CompletionBeforeTurnResponseClient()

    async def ensure_client() -> CompletionBeforeTurnResponseClient:
        return client

    async def complete_before_response(turn_id: str) -> None:
        await manager._handle_notification(  # noqa: SLF001
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": turn_id, "status": "completed", "error": None},
                },
            }
        )

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    client.before_turn_response = complete_before_response
    response = await manager.start("開始", str(tmp_path))

    assert response["status"] == "completed"
    assert response["turn_id"] == "turn-1"
    assert response["result_available"] is True
    assert manager.result("thread-1")["status"] == "completed"


@pytest.mark.asyncio
async def test_reply_completion_before_turn_start_response_preserves_terminal_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """replyのturn/completed通知がturn/start応答より先でも終端状態をrunningへ戻さない。"""
    manager = subject.AppServerManager()
    client = CompletionBeforeTurnResponseClient()

    async def ensure_client() -> CompletionBeforeTurnResponseClient:
        return client

    async def complete_before_response(turn_id: str) -> None:
        await manager._handle_notification(  # noqa: SLF001
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-1",
                    "turn": {"id": turn_id, "status": "completed", "error": None},
                },
            }
        )

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "completed", "error": None},
            },
        }
    )
    manager.result("thread-1")
    client.before_turn_response = complete_before_response

    response = await manager.start_reply("thread-1", "続行")

    assert response["status"] == "completed"
    assert response["turn_id"] == "turn-2"
    assert response["result_available"] is True
    assert manager.result("thread-1")["status"] == "completed"


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
    status = manager.status("thread-1")
    assert status["status"] == "completed"
    assert status["result_available"] is True
    result = manager.result("thread-1")
    assert result["agent_message"] == "最終結果"
    assert result["result_available"] is True
    await manager.start_reply("thread-1", "続行")
    assert [method for method, _ in client.requests][-2:] == ["thread/resume", "turn/start"]
    assert client.requests[-2][1]["approvalPolicy"] == "never"


@pytest.mark.asyncio
async def test_send_message_steers_active_turn_without_resetting_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """実行中の追加指示は同じturnへ送り、既存状態を初期化しない。"""
    manager = subject.AppServerManager()
    client = SteerClient()

    async def ensure_client() -> SteerClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    manager.client = cast(subject.JsonRpcProcess, client)
    session = manager.sessions["thread-1"]
    expected_turn_id = session.turn_id
    session.plan = [{"id": "plan"}]
    session.commentary = "進行中"
    session.diff_changed = True
    client.steer_response = {"turnId": expected_turn_id}

    response = await manager.send_message("thread-1", "追加指示")

    assert response["delivery"] == "steered"
    assert response["previous_result"] is None
    assert response["status"] == "running"
    assert response["turn_id"] == expected_turn_id
    assert response["plan"] == [{"id": "plan"}]
    assert response["commentary"] == "進行中"
    assert response["diff_changed"] is True
    assert client.requests[-1] == (
        "turn/steer",
        {
            "threadId": "thread-1",
            "expectedTurnId": expected_turn_id,
            "input": [{"type": "text", "text": "追加指示"}],
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("response_kind", ["missing", "mismatched", "snake_case", "nested"])
async def test_send_message_rejects_missing_or_mismatched_steer_response_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    response_kind: str,
) -> None:
    """steer応答のturn ID欠落・不一致では別turnへ再試行しない。"""
    manager = subject.AppServerManager()
    client = SteerClient()

    async def ensure_client() -> SteerClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    manager.client = cast(subject.JsonRpcProcess, client)
    turn_id = manager.sessions["thread-1"].turn_id
    steer_responses: dict[str, dict[str, Any]] = {
        "missing": {},
        "mismatched": {"turnId": "turn-other"},
        "snake_case": {"turn_id": turn_id},
        "nested": {"turn": {"id": turn_id}},
    }
    client.steer_response = steer_responses[response_kind]

    with pytest.raises(subject.AppServerError, match="unexpected turn.id"):
        await manager.send_message("thread-1", "追加指示")

    assert [method for method, _ in client.requests].count("turn/steer") == 1
    assert [method for method, _ in client.requests].count("thread/resume") == 0


@pytest.mark.asyncio
async def test_send_message_replies_after_terminal_result_and_preserves_previous_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """終端turnでは直前結果を退避して同じlock内でreplyを1回開始する。"""
    manager = subject.AppServerManager()
    client = FakeClient()

    async def ensure_client() -> FakeClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    session = manager.sessions["thread-1"]
    previous_turn_id = session.turn_id
    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": previous_turn_id,
                "item": {"type": "agentMessage", "text": "直前結果"},
            },
        }
    )
    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": previous_turn_id, "status": "completed", "error": None},
            },
        }
    )

    response = await manager.send_message("thread-1", "続行")

    assert response["delivery"] == "reply_started"
    assert response["previous_result"]["agent_message"] == "直前結果"
    assert response["previous_result"]["turn_id"] == previous_turn_id
    assert response["previous_result"]["result_available"] is True
    assert response["status"] == "running"
    assert response["result_available"] is False
    assert [method for method, _ in client.requests].count("turn/steer") == 0
    assert [method for method, _ in client.requests].count("thread/resume") == 1
    assert [method for method, _ in client.requests].count("turn/start") == 2


@pytest.mark.asyncio
async def test_send_message_rejection_waits_for_completion_and_ignores_nonterminal_notifications(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """steer拒否後は同turnの非終端通知でreplyへ切り替えず、完了だけを待つ。"""
    manager = subject.AppServerManager()
    client = SteerClient(error=subject.JsonRpcResponseError("turn/steer", -32600, "turn is not active"))

    async def ensure_client() -> SteerClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    monkeypatch.setattr(subject, "DEFAULT_WAIT_TIMEOUT", 1.0)
    await manager.start("開始", str(tmp_path))
    manager.client = cast(subject.JsonRpcProcess, client)
    turn_id = manager.sessions["thread-1"].turn_id
    task = asyncio.create_task(manager.send_message("thread-1", "追加指示"))
    await client.steer_called.wait()

    for message in (
        {
            "method": "turn/plan/updated",
            "params": {"threadId": "thread-1", "turnId": turn_id, "plan": [{"id": "plan"}]},
        },
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-1", "turnId": turn_id, "delta": "途中"},
        },
        {
            "method": "turn/diff/updated",
            "params": {"threadId": "thread-1", "turnId": turn_id, "diff": "diff"},
        },
    ):
        await manager._handle_notification(message)  # noqa: SLF001
    await asyncio.sleep(0)
    assert not task.done()

    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": turn_id,
                "item": {"type": "agentMessage", "text": "完了結果"},
            },
        }
    )
    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": turn_id, "status": "completed", "error": None},
            },
        }
    )

    response = await task
    assert response["delivery"] == "reply_started"
    assert response["previous_result"]["agent_message"] == "完了結果"
    assert [method for method, _ in client.requests].count("turn/steer") == 1
    assert [method for method, _ in client.requests].count("thread/resume") == 1


@pytest.mark.asyncio
async def test_send_message_client_failure_takes_precedence_over_result_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """steer拒否待機中のclient failureでは、同時成立した結果をreplyへ使わない。"""
    manager = subject.AppServerManager()
    client = SteerClient(error=subject.JsonRpcResponseError("turn/steer", -32600, "turn is not active"))

    async def ensure_client() -> SteerClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    monkeypatch.setattr(subject, "DEFAULT_WAIT_TIMEOUT", 1.0)
    await manager.start("開始", str(tmp_path))
    manager.client = cast(subject.JsonRpcProcess, client)
    task = asyncio.create_task(manager.send_message("thread-1", "追加指示"))
    await client.steer_called.wait()
    client._closed = True  # noqa: SLF001
    await manager._handle_client_failure(subject.AppServerError("connection lost"))  # noqa: SLF001

    with pytest.raises(subject.JsonRpcResponseError, match="turn is not active"):
        await task
    assert [method for method, _ in client.requests].count("thread/resume") == 0


@pytest.mark.asyncio
async def test_send_message_reply_failures_are_structured_and_ambiguous_start_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """自動replyの確定失敗とturn/start応答喪失を配送種別で区別する。"""
    for client, expected_delivery in (
        (FailingReplyClient(), "reply_failed"),
        (ReplyResponseLossClient(), "reply_ambiguous"),
    ):
        manager = subject.AppServerManager()

        async def ensure_client(client: FakeClient = client) -> FakeClient:
            return client

        monkeypatch.setattr(manager, "_ensure_client", ensure_client)
        await manager.start("開始", str(tmp_path))
        session = manager.sessions["thread-1"]
        _seed_completed_reply_session(session)
        _mark_result_available(session)
        session.result_retrieved = False
        response = await manager.send_message("thread-1", "続行")

        assert response["delivery"] == expected_delivery
        assert response["previous_result"]["agent_message"] == "previous result"
        assert response["previous_result"]["status"] == "completed"
        if expected_delivery == "reply_ambiguous":
            assert response["status"] == "running"
            assert response["result_available"] is False
            assert session.turn_start_ambiguous is True
        else:
            assert response["status"] == "failed"
            assert response["result_available"] is True
        await manager.close()


@pytest.mark.asyncio
async def test_send_message_reply_turn_start_missing_id_is_ambiguous(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """自動replyのturn/start応答のID欠落を非終端の曖昧状態へ保つ。"""
    manager = subject.AppServerManager()
    client = MissingTurnIdResponseClient(missing_on_turn_start=2)

    async def ensure_client() -> MissingTurnIdResponseClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    session = manager.sessions["thread-1"]
    _seed_completed_reply_session(session)
    _mark_result_available(session)
    session.result_retrieved = False

    response = await manager.send_message("thread-1", "続行")

    assert response["delivery"] == "reply_ambiguous"
    assert response["result_available"] is False
    assert session.turn_start_ambiguous is True
    assert response["error"] == {"message": "turn/start returned no turn.id"}


@pytest.mark.asyncio
async def test_old_turn_notifications_do_not_overwrite_new_turn_state(tmp_path: pathlib.Path) -> None:
    """新turnが開始済みなら古いturnの完了・item通知を状態へ反映しない。"""
    manager = subject.AppServerManager()
    manager.sessions["thread-1"] = subject.SessionState("thread-1", str(tmp_path), turn_id="turn-new")

    for message in (
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-old",
                "item": {"type": "agentMessage", "text": "古い結果"},
            },
        },
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-old", "status": "completed", "error": None},
            },
        },
    ):
        await manager._handle_notification(message)  # noqa: SLF001

    status = manager.status("thread-1")
    assert status["status"] == "running"
    assert status["turn_id"] == "turn-new"
    assert status["result_available"] is False
    assert manager.sessions["thread-1"].agent_message == ""


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
async def test_send_message_and_start_reply_serialize_terminal_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """自動replyと明示replyの競合を同じsession lockで直列化する。"""
    manager = subject.AppServerManager()
    client = BlockingReplyClient()

    async def ensure_client() -> BlockingReplyClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    session = manager.sessions["thread-1"]
    _seed_completed_reply_session(session)
    _mark_result_available(session)
    first = asyncio.create_task(manager.send_message("thread-1", "自動継続"))
    await client.reply_turn_started.wait()
    second = asyncio.create_task(manager.start_reply("thread-1", "明示継続"))
    await asyncio.sleep(0)
    assert not second.done()

    client.release_reply_turn.set()
    assert (await first)["delivery"] == "reply_started"
    with pytest.raises(ValueError, match="still running"):
        await second
    assert [method for method, _ in client.requests].count("thread/resume") == 1
    assert [method for method, _ in client.requests].count("turn/start") == 2


@pytest.mark.asyncio
async def test_multiple_send_messages_preserve_order_and_current_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """同じactive turnへの複数追加指示をsession lockで順序付ける。"""
    manager = subject.AppServerManager()
    client = SteerClient()

    async def ensure_client() -> SteerClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    manager.client = cast(subject.JsonRpcProcess, client)
    turn_id = manager.sessions["thread-1"].turn_id
    client.steer_response = {"turnId": turn_id}

    responses = await asyncio.gather(
        manager.send_message("thread-1", "追加1"),
        manager.send_message("thread-1", "追加2"),
    )

    assert [response["delivery"] for response in responses] == ["steered", "steered"]
    assert [params["input"][0]["text"] for method, params in client.requests if method == "turn/steer"] == [
        "追加1",
        "追加2",
    ]
    assert all(response["turn_id"] == turn_id for response in responses)


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
async def test_start_reply_turn_start_missing_id_is_ambiguous(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """明示replyのturn/start応答のID欠落を非終端の曖昧状態へ保つ。"""
    manager = subject.AppServerManager()
    client = MissingTurnIdResponseClient(missing_on_turn_start=2)

    async def ensure_client() -> MissingTurnIdResponseClient:
        return client

    monkeypatch.setattr(manager, "_ensure_client", ensure_client)
    await manager.start("開始", str(tmp_path))
    session = manager.sessions["thread-1"]
    _seed_completed_reply_session(session)
    _mark_result_available(session)

    response = await manager.start_reply("thread-1", "続行")

    assert response["result_available"] is False
    assert session.turn_start_ambiguous is True
    assert response["error"] == {"message": "turn/start returned no turn.id"}


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

    waiter = asyncio.create_task(manager.wait("thread-1", timeout=10))
    await manager._handle_server_request(  # noqa: SLF001
        {"id": 2, "method": "item/tool/requestUserInput", "params": {"threadId": "thread-1"}}
    )
    assert client.sent[-1]["id"] == 2
    assert "error" in client.sent[-1]
    assert manager.status("thread-1")["status"] == "failed"
    assert manager.status("thread-1")["result_available"] is False
    waited = await waiter
    assert waited["status"] == "failed"
    assert waited["result_available"] is False
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
    assert (await waiter)["status"] == "failed"
    result = manager.result("thread-1")
    assert result["status"] == "failed"
    assert result["result_available"] is True
    assert result["error"] == {"message": "Codex requested interactive server input: item/tool/requestUserInput"}


@pytest.mark.asyncio
async def test_interrupt_json_rpc_error_releases_waiter_before_completion(
    tmp_path: pathlib.Path,
) -> None:
    """非対応requestはfailedを公開してwaiterを解放するが、turn/completedまで結果回収を許可しない。"""
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
    assert target["status"] == "failed"
    assert target["result_available"] is False
    assert unrelated["status"] == "running"
    assert target["error"] == {"message": "turn/interrupt: turn is already completing"}
    assert unrelated["error"] is None
    waited = await waiter
    assert waited["status"] == "failed"
    assert waited["result_available"] is False
    with pytest.raises(ValueError, match="not completed"):
        manager.result("thread-1")

    await manager._handle_notification(  # noqa: SLF001
        {
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "interrupted", "error": None},
            },
        }
    )
    assert (await waiter)["status"] == "failed"
    result = manager.result("thread-1")
    assert result["status"] == "failed"
    assert result["result_available"] is True
    assert result["error"] == {"message": "turn/interrupt: turn is already completing"}


@pytest.mark.asyncio
async def test_unknown_request_fails_all_active_sessions_and_releases_waiters(tmp_path: pathlib.Path) -> None:
    manager = subject.AppServerManager()
    client = FakeClient()
    manager.client = cast(subject.JsonRpcProcess, client)
    manager.sessions["thread-1"] = subject.SessionState("thread-1", str(tmp_path), turn_id="turn-1")
    manager.sessions["thread-2"] = subject.SessionState("thread-2", str(tmp_path), turn_id="turn-2")

    waiters = [asyncio.create_task(manager.wait(session_id, timeout=10)) for session_id in ("thread-1", "thread-2")]
    await manager._handle_server_request(  # noqa: SLF001
        {"id": "unknown-1", "method": "unknown/server/request", "params": {}}
    )

    statuses = [manager.status(session_id) for session_id in ("thread-1", "thread-2")]
    assert {status["status"] for status in statuses} == {"failed"}
    assert not any(status["result_available"] for status in statuses)
    waited = await asyncio.gather(*waiters)
    assert [status["status"] for status in waited] == ["failed", "failed"]
    assert not any(status["result_available"] for status in waited)
    with pytest.raises(ValueError, match="not completed"):
        manager.result("thread-1")
    with pytest.raises(ValueError, match="not completed"):
        manager.result("thread-2")

    await asyncio.gather(*tuple(manager._background_tasks))  # noqa: SLF001
    assert client.sent[-1]["error"]["message"] == "Unsupported non-interactive server request: unknown/server/request"
    for session_id, turn_id in (("thread-1", "turn-1"), ("thread-2", "turn-2")):
        await manager._handle_notification(  # noqa: SLF001
            {
                "method": "turn/completed",
                "params": {
                    "threadId": session_id,
                    "turn": {"id": turn_id, "status": "failed", "error": None},
                },
            }
        )
        result = manager.result(session_id)
        assert result["status"] == "failed"
        assert result["result_available"] is True


@pytest.mark.asyncio
async def test_wait_timeout_returns_current_state_without_failing(tmp_path: pathlib.Path) -> None:
    manager = subject.AppServerManager()
    manager.sessions["thread-1"] = subject.SessionState("thread-1", str(tmp_path), turn_id="turn-1")
    result = await manager.wait("thread-1", timeout=0)
    assert result["status"] == "running"
    assert result["result_available"] is False
    with pytest.raises(ValueError, match="non-negative"):
        await manager.wait("thread-1", timeout=-1)
