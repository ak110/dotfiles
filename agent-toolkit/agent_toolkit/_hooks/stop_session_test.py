"""agent-toolkit/agent_toolkit/_hooks/stop_session.py のテスト。"""

import pathlib

import pytest

from agent_toolkit._hooks.stop_session import append_stop_log


class TestAppendStopLog:
    """`append_stop_log`のログ追記挙動を検証する。"""

    def test_appends_one_line_with_decision_and_context(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """1行追記され、decisionとcontextのkey-valueが整形されて含まれる。"""
        monkeypatch.setattr("agent_toolkit._hooks.stop_session.tempfile.gettempdir", lambda: str(tmp_path))
        append_stop_log("session-x", "approve_pending_async", {"last_tool": "Agent", "pending": 0})
        path = tmp_path / "claude-agent-toolkit-stop-session-x.log"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert "decision=approve_pending_async" in lines[0]
        assert "last_tool=Agent" in lines[0]
        assert "pending=0" in lines[0]

    def test_skips_when_session_id_empty(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """session_idが空の場合はログファイルを作成しない。"""
        monkeypatch.setattr("agent_toolkit._hooks.stop_session.tempfile.gettempdir", lambda: str(tmp_path))
        append_stop_log("", "approve_pending_async", {})
        assert not list(tmp_path.iterdir())

    def test_multiple_calls_append(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """複数回の呼び出しが1行ずつ追記される。"""
        monkeypatch.setattr("agent_toolkit._hooks.stop_session.tempfile.gettempdir", lambda: str(tmp_path))
        append_stop_log("session-y", "approve_no_env", {})
        append_stop_log("session-y", "block_autonomous_exit", {})
        path = tmp_path / "claude-agent-toolkit-stop-session-y.log"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert "decision=approve_no_env" in lines[0]
        assert "decision=block_autonomous_exit" in lines[1]

    def test_rotates_when_max_bytes_exceeded(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`max_bytes`を小さくすると先行ログが`.log.1`へローテートされる。"""
        monkeypatch.setattr("agent_toolkit._hooks.stop_session.tempfile.gettempdir", lambda: str(tmp_path))
        append_stop_log("session-z", "first", {})
        # 先行ログが上限を超えた状態で追記するとローテーションが発生する。
        append_stop_log("session-z", "second", {}, max_bytes=10)
        path = tmp_path / "claude-agent-toolkit-stop-session-z.log"
        rotated = tmp_path / "claude-agent-toolkit-stop-session-z.log.1"
        assert rotated.exists()
        assert "decision=first" in rotated.read_text(encoding="utf-8")
        assert "decision=second" in path.read_text(encoding="utf-8")
