"""文脈使用量のbackend観測から共有状態と公開CLIまでの契約を検証する。"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import pathlib
import sys
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import antigravity, claude, commands, context_usage, state, status_file
from agent_toolkit._common import state_paths
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    QueueClaudeClient,
    ResultMessage,
    SystemMessage,
)

_CONTRACT = pathlib.Path(__file__).resolve().parents[3] / "rust/claude-statusline/testdata/agents_server_state.json"


@pytest.mark.asyncio
async def test_public_cli_connects_shared_fixture_parent_ids_and_formats_usage(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """共有fixtureの親IDを生成側からCLIへ渡し、書込主体名と異なる多段の枝を表示する。"""
    contract = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(commands, "is_agent_environment", lambda _env: False)
    monkeypatch.setattr(commands.shutil, "get_terminal_size", lambda *_args, **_kwargs: os.terminal_size((240, 30)))
    writers = []
    sessions = []
    for file in contract["tree_files"]:
        owned = {}
        for session_id in file["session_ids"]:
            session = state.SessionState(
                session_id, str(tmp_path), announced=True, label=session_id, engine="codex", model="model"
            )
            session.started_at = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=65)).isoformat()
            context_usage.record_context_usage(session, 53500, 1000000)
            owned[session_id] = session
            sessions.append(session)
        writer = status_file.StatusFileWriter(
            owned,
            status_file.StatusFileIdentity("tree-root", file["file_name"], file["host_session_id"]),
            state_root=tmp_path,
        )
        writer.activate()
        writers.append(writer)
    try:
        with pytest.raises(SystemExit, match="0"):
            atk.main(["agents", "list"])
        lines = capsys.readouterr().out.splitlines()[1:]
        assert len(lines) == len(contract["tree_order"])
        assert [line.split()[1] for line in lines] == contract["tree_order"]
        assert lines[1].startswith("   └─ child")
        assert lines[2].startswith("      └─ deep")
        assert lines[-1].startswith("   └─ orphan-deep")
        assert all("1m5s · 53.5k/5% · running" in line for line in lines)
        for error_type, diagnostic in [("rate_limit_error", "HTTP 429"), ("usage_limit", "解除時刻確認中")]:
            sessions[1].api_error = {"type": error_type, "http_status": 429, "first_at": sessions[1].started_at, "count": 2}
            writers[1].flush()
            with pytest.raises(SystemExit, match="0"):
                atk.main(["agents", "list"])
            output = capsys.readouterr().out
            assert diagnostic in output
            assert "53.5k/5%" in output
        sessions[1].api_error = None
        # 同じ公開一覧が終端と再描画後の新観測値を扱う。
        sessions[0].status = "completed"
        sessions[0].turn_completed = True
        sessions[0].touch()
        context_usage.record_context_usage(sessions[1], 0, None)
        for writer in writers:
            writer.flush()
        with pytest.raises(SystemExit, match="0"):
            atk.main(["agents", "list", "--include-terminated"])
        output = capsys.readouterr().out
        assert "53.5k/5% · completed" in output
        assert "0.0k · running" in output
        polls = 0

        def update_or_interrupt(_seconds: float) -> None:
            nonlocal polls
            polls += 1
            if polls == 2:
                raise KeyboardInterrupt
            context_usage.record_context_usage(sessions[1], 1000, 100000)
            writers[1].flush()

        monkeypatch.setattr(commands.sys.stdout, "isatty", lambda: True)
        monkeypatch.setattr(commands.time, "sleep", update_or_interrupt)
        with pytest.raises(SystemExit, match="0"):
            atk.main(["agents", "list", "--watch", "--include-terminated"])
        output = capsys.readouterr().out
        assert "0.0k · running" in output
        assert "1.0k/1% · running" in output
        monkeypatch.setattr(commands.shutil, "get_terminal_size", lambda *_args, **_kwargs: os.terminal_size((80, 30)))
        sessions[1].label = "長いラベル" * 40
        writers[1].flush()
        with pytest.raises(SystemExit, match="0"):
            atk.main(["agents", "list", "--include-terminated"])
        output = capsys.readouterr().out
        assert "1.0k/1%" in output
        for line in output.splitlines()[1:]:
            assert sum(2 if ord(char) >= 0x3000 else 1 for char in line) <= 80
    finally:
        for writer in writers:
            writer.deactivate()


def test_shared_usage_examples_match_python_format() -> None:
    """Rustも読む独立した数値例で丸め、0、未知、不正な分母と分子をそろえる。"""
    contract = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    for case in contract["context_usage_cases"]:
        assert context_usage.format_tokens(case["tokens"], case["window"]) == case["formatted"]


@pytest.mark.asyncio
async def test_antigravity_uses_latest_response_step_and_retains_terminal_usage(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, host_environ: Callable[[], dict[str, str]]
) -> None:
    """公式headlessのstep形を実プロセスから渡し、checkpoint・会話累計を分子へ混ぜない。"""
    # 2026-10-13取得のhttps://antigravity.google/docs/cli/headless/のstream-json形。
    events: list[dict[str, Any]] = [
        {"event": "init", "conversation_id": "agy-context", "init": {}},
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "agy-context",
                "step_index": 3,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "usage": {"total_tokens": 53500, "cache_read_tokens": 0},
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "agy-context",
                "step_index": 3,
                "state": "DONE",
                "step_type": "agent_response",
                "text_delta": "完了",
                "usage": {"total_tokens": 1000, "cache_read_tokens": 2500},
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "step_type": "checkpoint",
                "usage": {"total_tokens": 99999, "cache_read_tokens": 0},
            },
        },
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "response": "完了",
                "usage": {"total_tokens": 999999, "cache_read_tokens": 0},
            },
        },
    ]
    script = tmp_path / "headless.py"
    script.write_text(
        "import json\n"
        + f"events = json.loads({json.dumps(json.dumps(events))})\n"
        + "for event in events:\n    print(json.dumps(event), flush=True)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(antigravity, "build_command", lambda *_args: [sys.executable, str(script)])
    monkeypatch.setattr(antigravity, "_child_env", lambda _root: host_environ())
    backend = antigravity.AntigravityManager()
    terminal = asyncio.Event()

    def on_terminal(_session: state.SessionState) -> None:
        terminal.set()

    state.add_terminal_listener(on_terminal)
    try:
        async with asyncio.timeout(3):
            session = await backend.start("調査", str(tmp_path))
            await terminal.wait()
        assert session.context_tokens == 3500
        assert session.context_window is None
        assert session.agent_message == "完了"
    finally:
        await backend.close()
        state.remove_terminal_listener(on_terminal)


@pytest.mark.asyncio
@pytest.mark.parametrize("tokens", [0, 53500, -1, True, "53500", None, float("inf"), float("nan")])
async def test_context_usage_reaches_public_cli_without_activity(
    tokens: object, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """数値の未知・0と不正値を公開list/showで区別し、表示更新が活動を進めない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.setenv("AI_AGENT", "1")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    session = state.SessionState("session-1", str(tmp_path), announced=True)
    activity = session.updated_at
    output_activity = session.output_updated_at
    writer = status_file.StatusFileWriter(
        {session.session_id: session}, status_file.StatusFileIdentity("root-session", "root.json", None), state_root=tmp_path
    )
    state.add_touch_listener(writer.schedule)
    writer.activate()
    try:
        context_usage.record_context_usage(session, tokens, 1000000)
        writer.flush()
        assert session.updated_at == activity
        assert session.output_updated_at == output_activity
        for argv in (["agents", "list"], ["agents", "show", session.session_id]):
            with pytest.raises(SystemExit, match="0"):
                atk.main(argv)
            payload = json.loads(capsys.readouterr().out)
            row = payload["sessions"][0] if "sessions" in payload else payload
            if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
                assert row["context_tokens"] == tokens
            else:
                assert "context_tokens" not in row
            assert row["context_window"] == 1000000
        context_usage.record_context_usage(session, 1000, None)
        session.status = "completed"
        session.turn_completed = True
        session.touch()
        retained = state.SessionResumeState.from_session(session)
        assert retained.context_tokens == 1000
        writer.flush()
        # 状態ファイル消失後も、終端結果に保存した詳細へ同じ観測値を届ける。
        writer.path.unlink()
        with pytest.raises(SystemExit, match="0"):
            atk.main(["agents", "show", session.session_id])
        row = json.loads(capsys.readouterr().out)
        assert row["context_tokens"] == 1000
        assert "context_window" not in row
    finally:
        state.remove_touch_listener(writer.schedule)
        writer.deactivate()


@pytest.mark.asyncio
async def test_claude_context_usage_updates_init_response_compaction_and_terminal(tmp_path: pathlib.Path) -> None:
    """SDK 0.2.165の文脈応答を使い、各更新の直近値とrawMaxTokensを分子と分母にする。"""

    class Client(QueueClaudeClient):
        usage: object = {"totalTokens": 53500, "rawMaxTokens": 1000000, "maxTokens": 800000}
        observed = asyncio.Event()

        async def get_context_usage(self) -> object:
            self.observed.set()
            if isinstance(self.usage, Exception):
                raise self.usage
            return self.usage

    client = Client([[SystemMessage("context-session")], [SystemMessage("context-session")]])
    backend = claude.ClaudeServerManager(client_factory=lambda _options: client)
    changed = asyncio.Event()
    state.add_touch_listener(changed.set)

    async def await_value(session: state.SessionState, expected: int | None, message: str | None = None) -> None:
        async with asyncio.timeout(2):
            while (
                session.context_tokens != expected
                or not client.observed.is_set()
                or (message is not None and session.agent_message != message)
            ):
                changed.clear()
                await changed.wait()

    try:
        session = await backend.start("調査", str(tmp_path), "model", "high")
        await await_value(session, 53500)
        assert session.context_window == 1000000
        client.usage = {"totalTokens": 54000, "rawMaxTokens": 1000000}
        client.messages.put_nowait(AssistantMessage("応答"))
        await await_value(session, 54000, "応答")
        compact = SystemMessage(session.session_id)
        compact.subtype = "compact_boundary"
        client.usage = {"totalTokens": 1250, "rawMaxTokens": 1000000}
        client.messages.put_nowait(compact)
        await await_value(session, 1250)
        activity = session.updated_at
        assert session.output_updated_at is not None
        client.usage = {"totalTokens": 0, "rawMaxTokens": 1000000}
        client.messages.put_nowait(ResultMessage("完了"))
        await await_value(session, 0, "完了")
        assert session.result_available
        assert state.SessionResumeState.from_session(session).context_tokens == 0
        await backend.send_message(session, "続行")
        client.usage = {"totalTokens": 2500, "rawMaxTokens": 100000}
        client.messages.put_nowait(AssistantMessage("再開応答"))
        await await_value(session, 2500, "再開応答")
        assert session.updated_at != activity
        client.usage = RuntimeError("unsupported context request")
        client.messages.put_nowait(AssistantMessage("照会失敗後も応答"))
        await await_value(session, None, "照会失敗後も応答")
        assert session.agent_message == "照会失敗後も応答"
        assert session.status == "running"
    finally:
        await backend.close()
        state.remove_touch_listener(changed.set)
    assert client.disconnected


@pytest.mark.asyncio
async def test_claude_slow_context_request_keeps_receiver_and_closes_owned_task(tmp_path: pathlib.Path) -> None:
    """照会が未完了でも起動・応答が進み、closeで照会taskを回収する。"""

    class Client(QueueClaudeClient):
        started = asyncio.Event()
        finished = asyncio.Event()

        async def get_context_usage(self) -> None:
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.finished.set()

    client = Client([[SystemMessage("slow-context"), AssistantMessage("応答"), ResultMessage("完了")]])
    backend = claude.ClaudeServerManager(client_factory=lambda _options: client)
    changed = asyncio.Event()
    state.add_touch_listener(changed.set)
    try:
        async with asyncio.timeout(2):
            session = await backend.start("調査", str(tmp_path))
            await client.started.wait()
            while not session.result_available:
                changed.clear()
                await changed.wait()
        assert session.agent_message == "完了"
        assert session.context_tokens is None
    finally:
        await backend.close()
        state.remove_touch_listener(changed.set)
    assert client.finished.is_set()
    assert client.disconnected
