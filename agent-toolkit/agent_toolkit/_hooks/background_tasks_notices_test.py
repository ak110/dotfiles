"""background_tasks_noticesの契約テスト。"""

import pathlib

import pytest

from agent_toolkit._hooks import background_tasks as _background_tasks
from agent_toolkit._testing.background_tasks_support import (
    _describe_pending_background_tasks,
    _host_mcp_notice,
    _monitor_launch_entries,
    _user_non_monitor_task_id_entry,
    _user_task_notification_entry,
)
from agent_toolkit._testing.helpers import _write_transcript

_QUOTED_NOTICE_PHRASES = (
    "moved to the background as task {task_id} and keeps running",
    "Command timed out and is now running in the background. ID: {task_id}",
    "Command did not complete within its 15s timeout and was moved to the background (ID: {task_id}).",
)


class TestMonitorTaskNotificationSuppression:
    """Monitor由来の`task_notification_unresolved`過検出が抑止されることを検証する。"""

    def test_monitor_notification_does_not_log_unresolved(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monitorのtool_useと突合できる通知はtask_notification_unresolvedをログしない。"""
        monkeypatch.setattr("agent_toolkit._hooks.stop_session.tempfile.gettempdir", lambda: str(tmp_path))
        entries = [
            *_monitor_launch_entries("toolu_monitor1", "monitor-a"),
            _user_task_notification_entry(None, task_id="monitor-a"),
        ]
        t = _write_transcript(tmp_path, entries)
        _describe_pending_background_tasks(str(t), "sess-monitor")
        log_path = tmp_path / "claude-agent-toolkit-stop-sess-monitor.log"
        if log_path.exists():
            assert "task_notification_unresolved" not in log_path.read_text(encoding="utf-8")

    def test_non_monitor_task_id_collision_still_logs_unresolved(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monitor以外の`taskId`値では未解決通知をログする。"""
        monkeypatch.setattr("agent_toolkit._hooks.stop_session.tempfile.gettempdir", lambda: str(tmp_path))
        entries = [
            _user_non_monitor_task_id_entry("toolu_taskupdate1", "1"),
            _user_task_notification_entry(None, task_id="1"),
        ]
        t = _write_transcript(tmp_path, entries)
        _describe_pending_background_tasks(str(t), "sess-collision")
        log_path = tmp_path / "claude-agent-toolkit-stop-sess-collision.log"
        assert "task_notification_unresolved" in log_path.read_text(encoding="utf-8")

    def test_same_value_shared_by_monitor_and_non_monitor_still_logs_unresolved(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monitor由来か一意に決まらない`taskId`値では未解決通知をログする。"""
        monkeypatch.setattr("agent_toolkit._hooks.stop_session.tempfile.gettempdir", lambda: str(tmp_path))
        entries = [
            *_monitor_launch_entries("toolu_monitor1", "shared-id"),
            _user_non_monitor_task_id_entry("toolu_taskupdate1", "shared-id"),
            _user_task_notification_entry(None, task_id="shared-id"),
        ]
        t = _write_transcript(tmp_path, entries)
        _describe_pending_background_tasks(str(t), "sess-shared-collision")
        log_path = tmp_path / "claude-agent-toolkit-stop-sess-shared-collision.log"
        assert "task_notification_unresolved" in log_path.read_text(encoding="utf-8")

    def test_unknown_notification_still_logs_unresolved(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Monitor由来と識別できない未知の通知は引き続きtask_notification_unresolvedをログする。"""
        monkeypatch.setattr("agent_toolkit._hooks.stop_session.tempfile.gettempdir", lambda: str(tmp_path))
        entries = [_user_task_notification_entry(None, task_id="unknown-task")]
        t = _write_transcript(tmp_path, entries)
        _describe_pending_background_tasks(str(t), "sess-unknown")
        log_path = tmp_path / "claude-agent-toolkit-stop-sess-unknown.log"
        assert "task_notification_unresolved" in log_path.read_text(encoding="utf-8")


class TestBackgroundTaskIdFromNotice:
    """ホストのMCP移行通知から抽出する識別子の境界を固定する。"""

    _PREFIX = 'MCP tool "mcp__agents_server__wait" is still running after 120s. It was moved to the background as task '

    @pytest.mark.parametrize(
        ("suffix", "expected"),
        [
            ("task-1", "task-1"),
            ("task-1 and keeps running; wait for it", "task-1"),
            ("mcp_task_1\n", "mcp_task_1"),
            (" spaced-1 ", "spaced-1"),
            ("bg-task-notice.", "bg-task-notice"),
            ("bg-1, and the result is pending.", "bg-1"),
            ("bg-2; observe it later", "bg-2"),
            ("bash:12/34", "bash:12/34"),
        ],
        ids=[
            "end-of-text",
            "followed-by-word",
            "trailing-newline",
            "extra-space",
            "period",
            "comma",
            "semicolon",
            "non-word-characters",
        ],
    )
    def test_extracts_identifier_from_host_notice(self, suffix: str, expected: str) -> None:
        """通知の形で始まる本文から、文末の句読点を除いた識別子を途中で打ち切らずに返す。"""
        assert _background_tasks.background_task_id_from_notice(self._PREFIX + suffix) == expected
        assert (
            _background_tasks.background_task_id_from_notice({"content": [{"type": "text", "text": self._PREFIX + suffix}]})
            == expected
        )

    @pytest.mark.parametrize(
        "text",
        [
            *(f"rg output: {phrase.format(task_id='task-1')}" for phrase in _QUOTED_NOTICE_PHRASES),
            *(phrase.format(task_id="task-1") for phrase in _QUOTED_NOTICE_PHRASES),
            "log: " + _host_mcp_notice("task-1"),
            'MCP tool "mcp__agents_server__wait" is still running after 120s. It was moved to the background as task .',
            "no notice here",
        ],
    )
    def test_returns_none_for_text_not_starting_with_host_notice(self, text: str) -> None:
        """通知の形で始まらない本文と識別子を伴わない通知では`None`を返す。"""
        assert _background_tasks.background_task_id_from_notice(text) is None
