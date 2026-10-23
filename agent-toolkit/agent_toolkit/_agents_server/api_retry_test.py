"""turn内の再試行通知を受け取り、MCPとCLIの公開応答に同じエラーが出ることを確かめる。"""

# 状態ファイルと通知処理を同じ受信列から検証する。
# pylint: disable=protected-access
import argparse
import asyncio
import datetime
import json
from types import SimpleNamespace
from typing import Any

import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import (
    agents_wait,
    claude,
    codex,
    commands,
    manager,
    mcp_tools,
    shared_layout,
    state,
    status_file,
)
from agent_toolkit._atk import config
from agent_toolkit._common import state_paths
from agent_toolkit._testing.agents_server_support import FakeClaudeClient, SystemMessage, _rate_limit_event, install_backend

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


class _RetryClient(FakeClaudeClient):
    """通知を受信し終えた時点で同期し、streamの終端を検証側が所有する。"""

    def __init__(self, messages: list[Any]):
        super().__init__([messages])
        self.received = asyncio.Event()

    def receive_messages(self):
        messages = self.streams.pop(0)

        async def stream():
            for message in messages:
                yield message
            self.received.set()
            await asyncio.Event().wait()

        return stream()


def _retry(status: int, attempt: int = 2):
    data = {
        "attempt": attempt,
        "max_retries": 301,
        "retry_delay_ms": 1000,
        "error_status": status,
        "error": "rate_limit_error" if status == 429 else "authentication_error",
    }
    return claude_agent_sdk.SystemMessage(subtype="api_retry", data=data)


async def _assert_public_error(owner, session, tmp_path, monkeypatch, capsys):
    """同じ状態をMCPのlist/showと公開CLIのlist/waitへ通す。"""
    monkeypatch.setattr(mcp_tools, "_MANAGER", owner)
    shown = await mcp_tools.show_session(session.session_id)
    listed = (await mcp_tools.list_sessions())["sessions"][0]
    expected = shown["api_error"]
    assert listed["api_error"] == expected
    directory = shared_layout.status_directory("root-session", tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "root.json").write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": [status_file._serialize_session(session)],
            }
        )
    )
    capsys.readouterr()
    parser = argparse.ArgumentParser()
    commands.build_parser(parser)
    environment = {"CLAUDE_CODE_SESSION_ID": "root-session", "AI_AGENT": "1"}
    assert commands.dispatch(parser.parse_args(["list"]), environment=environment) == 0
    cli_list = json.loads(capsys.readouterr().out)["sessions"][0]
    assert cli_list["api_error"] == expected
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0)
    monkeypatch.setattr(agents_wait, "is_human_terminal", lambda: False)
    assert agents_wait.wait_for_result(environment=environment, state_root=tmp_path) == 3
    rows = [json.loads(row) for row in capsys.readouterr().out.splitlines()]
    running = next(row for row in rows if row.get("status") == "running")
    assert running["api_error"] == expected
    return expected


@pytest.mark.parametrize("scenario", ["limit", "bare", "auth-after-limit", "no-reset"])
@pytest.mark.asyncio
async def test_claude_retry_receive_and_public_projection(tmp_path, monkeypatch, capsys, scenario):
    """利用枠報告の有無とエラー遷移をMCPとCLIの各操作へ同じ形で返し、通知数を試行数へ数えない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    monkeypatch.setattr(config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [("claude", "model", "high")])
    messages: list[SystemMessage | claude_agent_sdk.SystemMessage] = [SystemMessage("retry-session")]
    if scenario != "bare":
        messages.append(_rate_limit_event("rejected", "five_hour", None if scenario == "no-reset" else 2000000000))
    # 通知件数3とattempt7を分け、同じ通知の反復を試行回数へ加算する退行を検出する。
    messages.extend(_retry(429, 7) for _ in range(3))
    if scenario == "auth-after-limit":
        messages.append(_retry(401, 11))
    client = _RetryClient(messages)
    before = datetime.datetime.now(datetime.UTC)
    async with (
        manager.AgentsServerManager(None) as owner,
        claude.ClaudeServerManager(
            owner.sessions,
            owner._condition,
            client_factory=lambda _options: client,
        ) as backend,
    ):
        install_backend(owner, "claude", backend)
        started = await owner.start("high_tier", "作業", str(tmp_path))
        await asyncio.wait_for(client.received.wait(), timeout=2)
        session = owner.sessions[started["session_id"]]
        last_activity = session.updated_at
        error = await _assert_public_error(owner, session, tmp_path, monkeypatch, capsys)
        after = datetime.datetime.now(datetime.UTC)
        retry_at = datetime.datetime.fromisoformat(error["retry_at"])
        assert before + datetime.timedelta(seconds=1) <= retry_at <= after + datetime.timedelta(seconds=1)
        assert session.updated_at == last_activity
        serialized = status_file._serialize_session(session)
        assert serialized["api_error"]["count"] == (11 if scenario == "auth-after-limit" else 7)
        saved = json.loads((shared_layout.status_directory("root-session", tmp_path) / "root.json").read_text())
        assert saved["sessions"][0]["api_error"]["count"] == (11 if scenario == "auth-after-limit" else 7)
        if scenario in {"limit", "no-reset"}:
            assert error["type"] == "usage_limit" and error["limit_type"] == "five_hour"
            assert error["resets_at"] == (
                datetime.datetime.fromtimestamp(2000000000, datetime.UTC).isoformat()
                if scenario == "limit"
                else error["retry_at"]
            )
        else:
            assert error["type"] == ("authentication_error" if scenario == "auth-after-limit" else "rate_limit_error")
            assert error["http_status"] == (401 if scenario == "auth-after-limit" else 429)
            assert "resets_at" not in error
        claude.consume_assistant_message(session, SimpleNamespace(content=[]))
        assert session.api_error is None


@pytest.mark.parametrize("activity", ["item/agentMessage/delta", "item/reasoning/textDelta", "item/reasoning/summaryTextDelta"])
@pytest.mark.parametrize("info,status", [("serverOverloaded", None), ({"httpConnectionFailed": {"httpStatusCode": 503}}, 503)])
@pytest.mark.asyncio
async def test_codex_retry_turn_and_activity_projection(tmp_path, monkeypatch, capsys, info, status, activity):
    """Codexの再試行を対応turnへ記録し、モデル活動とturn終端で解除する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session = state.SessionState("retry-session", str(tmp_path), engine="codex", turn_id="turn-1", status="running")
    async with manager.AgentsServerManager(None) as owner, codex.AppServerManager(owner.sessions, owner._condition) as backend:
        install_backend(owner, "codex", backend)
        owner.sessions[session.session_id] = session
        last_activity = session.updated_at
        params = {"threadId": session.session_id, "turnId": "old-turn", "willRetry": True, "error": {"codexErrorInfo": info}}
        await backend._handle_notification({"method": "error", "params": params})
        assert session.api_error is None
        params["turnId"] = session.turn_id
        await backend._handle_notification({"method": "error", "params": params})
        assert session.updated_at == last_activity
        await backend._handle_notification(
            {
                "method": "item/completed",
                "params": {
                    "threadId": session.session_id,
                    "turnId": session.turn_id,
                    "item": {"id": "child-end", "type": "subAgentActivity", "kind": "completed", "agentThreadId": "child"},
                },
            }
        )
        assert session.api_error is not None
        error = await _assert_public_error(owner, session, tmp_path, monkeypatch, capsys)
        assert error["type"] == (info if isinstance(info, str) else "httpConnectionFailed")
        assert error["http_status"] == status
        await backend._handle_notification(
            {
                "method": activity,
                "params": {
                    "threadId": session.session_id,
                    "turnId": session.turn_id,
                    "delta": "再開後の出力",
                },
            }
        )
        assert session.api_error is None
        await backend._handle_notification({"method": "error", "params": params})
        await backend._handle_notification(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": session.session_id,
                    "turn": {"id": session.turn_id, "status": "completed"},
                },
            }
        )
        assert session.api_error is None
