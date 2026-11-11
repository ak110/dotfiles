"""claudeのdiagnosticsの契約を検証する。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import os
import pathlib
import types

import pytest

from agent_toolkit._agents_server import claude, resume_waits, session_errors, state
from agent_toolkit._common import state_paths
from agent_toolkit._testing import late_descendant
from agent_toolkit._testing.agents_server_claude_support import _SilentClient


def test_debug_file_name_carries_session_id_after_initialization(tmp_path: pathlib.Path) -> None:
    """診断ログの名前にsession識別子を含め、時刻を比較せずに対応するsessionを判別できる状態にする。"""
    debug_file = tmp_path / "20260916T000000000000-delegate.log"
    debug_file.write_text("diagnostic", encoding="utf-8")

    renamed = claude.rename_debug_file_for_session(debug_file, "session-1", "delegate")

    assert renamed.name == "20260916T000000000000-session-1-delegate.log"
    assert renamed.read_text(encoding="utf-8") == "diagnostic"
    assert not debug_file.exists()


def test_debug_file_keeps_its_name_when_renaming_is_rejected(tmp_path: pathlib.Path) -> None:
    """改名できない実行環境では元の名前を保ち、後続の記録先を失わない。"""
    debug_file = tmp_path / "missing" / "20260916T000000000000-delegate.log"

    renamed = claude.rename_debug_file_for_session(debug_file, "session-1", "delegate")

    assert renamed == debug_file


def test_prepare_debug_file_drops_records_beyond_retention(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """保持世代を超えた古い診断ログを自動的に削除する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    directory = tmp_path / claude._DEBUG_LOG_DIR_NAME  # pylint: disable=protected-access
    directory.mkdir(parents=True)
    retention = claude._DEBUG_LOG_RETENTION  # pylint: disable=protected-access
    existing = []
    for index in range(retention + 5):
        path = directory / f"{index:04d}.log"
        path.write_text("記録", encoding="utf-8")
        os.utime(path, (index, index))
        existing.append(path)

    created = claude._prepare_debug_file("delegate")  # pylint: disable=protected-access
    created.write_text("記録", encoding="utf-8")

    assert created.parent == directory
    assert len(sorted(directory.glob("*.log"))) == retention
    assert not existing[0].exists()
    assert existing[-1].exists()


def test_assistant_message_without_text_advances_activity_time() -> None:
    """ツール呼び出しだけのassistantメッセージでも活動時刻を進め、テキスト出力時刻は進めない。"""
    session = state.SessionState("session-1", "/tmp")
    session.updated_at = "2000-01-01T00:00:00+00:00"
    message = types.SimpleNamespace(
        content=[types.SimpleNamespace(id="toolu_1", name="Bash", input={"command": "ls"})],
    )

    claude.consume_assistant_message(session, message)

    assert session.updated_at != "2000-01-01T00:00:00+00:00"
    assert session.output_updated_at is None
    assert session.active_tool_uses()[0]["name"] == "Bash"
    assert session.active_tool_uses()[0]["detail"] == "command=ls"
    assert session.last_action == "Bash: command=ls"


def test_assistant_message_with_text_updates_both_activity_and_output() -> None:
    """テキストを持つassistantメッセージは活動時刻とテキスト出力時刻の双方を進める。"""
    session = state.SessionState("session-1", "/tmp")
    session.updated_at = "2000-01-01T00:00:00+00:00"
    message = types.SimpleNamespace(content=[types.SimpleNamespace(text="調査を続ける")])

    claude.consume_assistant_message(session, message)

    assert session.updated_at != "2000-01-01T00:00:00+00:00"
    assert session.output_updated_at is not None
    assert session.agent_message == "調査を続ける"
    assert session.last_action == "調査を続ける"


def test_only_assistant_messages_without_api_error_count_as_model_output() -> None:
    """API失敗の合成メッセージではモデル出力を観測済みにせず、通常のassistantメッセージで観測済みにする。"""
    session = state.SessionState("session-1", "/tmp")
    api_error = types.SimpleNamespace(
        content=[types.SimpleNamespace(text="API Error: 429 rate limit")],
        error="rate_limit",
    )

    claude.consume_assistant_message(session, api_error)
    after_error = session.model_output_observed
    claude.consume_assistant_message(session, types.SimpleNamespace(content=[], error=None))

    assert not after_error
    assert session.model_output_observed


def test_api_error_does_not_advance_activity() -> None:
    """API失敗の再試行はモデル活動に数えず、正常メッセージで失敗記録が消える。"""
    session = state.SessionState("session-1", "/tmp")
    session.updated_at = "2000-01-01T00:00:00+00:00"
    failure = types.SimpleNamespace(
        content=[types.SimpleNamespace(text="API Error: 429 rate limit")],
        error="rate_limit",
    )

    claude.consume_assistant_message(session, failure)
    claude.consume_assistant_message(session, failure)

    assert session.updated_at == "2000-01-01T00:00:00+00:00"
    assert session.output_updated_at is None
    assert session.api_error is not None
    assert session.api_error["count"] == 2
    assert session.api_error["type"] == "rate_limit_error"
    assert session.api_error["http_status"] == 429

    claude.consume_assistant_message(session, types.SimpleNamespace(content=[], error="rate_limit"))
    assert session.api_error["http_status"] == 429
    assert session.api_error["count"] == 3

    claude.consume_assistant_message(session, types.SimpleNamespace(content=[], error=None))

    assert session.api_error is None
    assert session.updated_at != "2000-01-01T00:00:00+00:00"


def test_initialization_diagnostic_identifies_received_messages() -> None:
    """初期化診断は受信メッセージを種別と内容で識別できる形で保持する。"""
    diagnostic = claude._InitializationDiagnostic()  # pylint: disable=protected-access

    diagnostic.record_message(types.SimpleNamespace(subtype="SessionStart"))
    diagnostic.record_message(types.SimpleNamespace(subtype="PreToolUse"))

    public = diagnostic.public()
    assert public["received_messages"] == ["SimpleNamespace: SessionStart", "SimpleNamespace: PreToolUse"]
    assert public["received_message_types"] == {"SimpleNamespace": 2}
    assert "received_message_count" not in public
    assert not public["child_processes"]


def test_initialization_diagnostic_lists_child_processes(tmp_path: pathlib.Path) -> None:
    """初期化診断は委譲先プロセスが起動した子プロセスを列挙する。"""
    diagnostic = claude._InitializationDiagnostic()  # pylint: disable=protected-access
    with late_descendant.held_process(tmp_path) as child:
        try:
            diagnostic.child_pid = os.getpid()
            children = diagnostic.public()["child_processes"]
        finally:
            child.terminate()
    assert any(entry.startswith(f"{child.pid}: ") for entry in children), children


@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_start_aborts_when_init_message_never_arrives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """initへ到達しないsessionを上限で打ち切り、子プロセスを終了させて例外で返す。"""
    monkeypatch.setattr(state, "SESSION_INITIALIZATION_TIMEOUT", 0.05)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    client = _SilentClient()
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as manager:
        with pytest.raises(session_errors.SessionInitializationTimeoutError):
            await manager.start("調査する", str(tmp_path))

        assert not manager.sessions
        await manager.close()
        assert client.terminated is True


@pytest.mark.asyncio
async def test_auto_resume_deadline_is_independent_from_result_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    """自動再開の待機上限を終了時の結果の保持期限から独立して算出する。"""
    monkeypatch.setattr(resume_waits, "AUTO_RESUME_DEADLINE_SECONDS", 5.0)
    monkeypatch.setattr(state, "RESULT_RETENTION_SECONDS", 0.0)
    session = state.SessionState("claude-session", "/tmp", engine="claude")
    before = asyncio.get_running_loop().time()

    deadline = resume_waits.begin_auto_resume_wait(
        session,
        {"status": "completed", "agent_message": "完了", "error": None},
    )

    assert deadline is not None
    assert before + 5.0 <= deadline <= asyncio.get_running_loop().time() + 5.0
    assert session.auto_resume_deadline == deadline
    assert session.awaiting_auto_resume is True
