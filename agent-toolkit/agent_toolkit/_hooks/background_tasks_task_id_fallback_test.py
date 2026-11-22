"""agent-toolkit/agent_toolkit/_hooks/background_tasks.py のテスト（`<task-id>`フォールバック解決）。

`<task-notification>`要素に`<tool-use-id>`が含まれない通知形式に対する
`<task-id>`要素経由のフォールバック解決を、旧形式（userエントリ）・
新形式（`type=="attachment"`）の双方で検証する。不変条件
「起動として記録した全バックグラウンドタスクはいずれかの完了通知形式で完了集合へ解決できる」を担保する。
基幹テストは`background_tasks_*_test.py`に、共通ヘルパーは`_testing/background_tasks_support.py`に置く。
"""

import pathlib

import pytest

from agent_toolkit._hooks.background_tasks import is_pending_async_work
from agent_toolkit._testing.background_tasks_support import (  # noqa: E402  # pylint: disable=wrong-import-position
    _assistant_agent_entry,
    _assistant_entry,
    _attachment_task_notification_entry,
    _bash_no_bg,
    _host_mcp_notice,
    _queue_operation_task_notification_entry,
    _user_async_launched_entry,
    _user_task_notification_entry,
    _write_nested_subagent_fixture,
    _write_transcript,
)

_TEXT = "作業完了。"


def _mcp_background_timeout_entries(
    task_id: str,
    tool_use_id: str,
    *,
    sidechain: bool = False,
    tool_name: str = "mcp__agents_server__start",
) -> list[dict]:
    return [
        {
            "type": "assistant",
            "isSidechain": sidechain,
            "message": {
                "id": f"msg_{tool_use_id}",
                "role": "assistant",
                "content": [{"type": "tool_use", "id": tool_use_id, "name": tool_name, "input": {}}],
                "stop_reason": "tool_use",
            },
        },
        {
            "type": "user",
            "isSidechain": sidechain,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": [{"type": "text", "text": _host_mcp_notice(task_id, tool_name)}],
                    }
                ],
            },
        },
    ]


class TestTaskIdFallbackCompletion:
    def test_tool_use_id_only_completion(self, tmp_path: pathlib.Path) -> None:
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _user_task_notification_entry("toolu_a", task_id=None),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "", session_state=None) is False

    def test_task_id_only_completion(self, tmp_path: pathlib.Path) -> None:
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _user_task_notification_entry(None, task_id="agent-a"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "", session_state=None) is False

    def test_both_present_completion(self, tmp_path: pathlib.Path) -> None:
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _user_task_notification_entry("toolu_a", task_id="agent-a"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "", session_state=None) is False

    def test_both_missing_leaves_pending(self, tmp_path: pathlib.Path) -> None:
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _user_task_notification_entry(None, task_id=None),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "", session_state=None) is True


class TestAttachmentTaskIdFallbackCompletion:
    def test_tool_use_id_only_completion(self, tmp_path: pathlib.Path) -> None:
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _attachment_task_notification_entry("toolu_a", task_id=None),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "", session_state=None) is False

    def test_task_id_only_completion(self, tmp_path: pathlib.Path) -> None:
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _attachment_task_notification_entry(None, task_id="agent-a"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "", session_state=None) is False

    def test_both_present_completion(self, tmp_path: pathlib.Path) -> None:
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _attachment_task_notification_entry("toolu_a", task_id="agent-a"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "", session_state=None) is False

    def test_both_missing_leaves_pending_and_logs_unresolved(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("agent_toolkit._hooks.stop_session.tempfile.gettempdir", lambda: str(tmp_path))
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _attachment_task_notification_entry(None, task_id=None),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "session-fallback", session_state=None) is True
        log_path = tmp_path / "claude-agent-toolkit-stop-session-fallback.log"
        assert "task_notification_unresolved" in log_path.read_text(encoding="utf-8")


class TestNestedAgentTaskIdFallbackCompletion:
    @pytest.mark.parametrize(("operation", "pending"), [("enqueue", True), ("remove", False)])
    def test_task_id_only_queue_operation_completes_grandchild(
        self, tmp_path: pathlib.Path, operation: str, pending: bool
    ) -> None:
        entries = [
            _user_async_launched_entry("toolu_child", agent_id="child-id"),
            _user_task_notification_entry("toolu_child", task_id="child-id"),
            _queue_operation_task_notification_entry(operation, task_id="grandchild-id"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        child_entries = [
            _assistant_agent_entry("toolu_grandchild"),
            _user_async_launched_entry("toolu_grandchild", agent_id="grandchild-id"),
        ]
        transcript = _write_nested_subagent_fixture(tmp_path, entries, child_entries)
        assert is_pending_async_work(str(transcript), "", background_tasks=[], session_state=None) is pending


class TestMcpBackgroundTaskCompletion:
    def test_pending_mcp_task_is_completed_by_task_id_notification(self, tmp_path: pathlib.Path) -> None:
        entries = [
            *_mcp_background_timeout_entries("mcp-task-1", "toolu_mcp"),
            _user_task_notification_entry(None, task_id="mcp-task-1"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "", session_state=None) is False

    def test_pending_mcp_task_without_completion_remains_pending(self, tmp_path: pathlib.Path) -> None:
        entries = [
            *_mcp_background_timeout_entries("mcp-task-1", "toolu_mcp"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "", session_state=None) is True

    @pytest.mark.parametrize(
        "notification",
        [
            _user_task_notification_entry("toolu_mcp", task_id=None),
            _attachment_task_notification_entry("toolu_mcp", task_id=None),
        ],
    )
    def test_mcp_task_is_completed_by_tool_use_id_notification(self, tmp_path: pathlib.Path, notification: dict) -> None:
        entries = [
            *_mcp_background_timeout_entries("mcp-task-1", "toolu_mcp"),
            notification,
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "", session_state=None) is False

    def test_sidechain_mcp_timeout_is_ignored(self, tmp_path: pathlib.Path) -> None:
        entries = [
            *_mcp_background_timeout_entries("mcp-task-1", "toolu_mcp", sidechain=True),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "", session_state=None) is False

    def test_non_mcp_timeout_text_is_ignored(self, tmp_path: pathlib.Path) -> None:
        entries = [
            *_mcp_background_timeout_entries("mcp-task-1", "toolu_read", tool_name="Read"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "", session_state=None) is False

    def test_one_of_multiple_mcp_tasks_completed_leaves_other_pending(self, tmp_path: pathlib.Path) -> None:
        entries = [
            *_mcp_background_timeout_entries("mcp-task-1", "toolu_mcp_1"),
            *_mcp_background_timeout_entries("mcp-task-2", "toolu_mcp_2"),
            _attachment_task_notification_entry("toolu_mcp_1", task_id=None),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "", session_state=None) is True

    @pytest.mark.parametrize(
        ("completed_entries", "expected"),
        [
            ([_user_task_notification_entry(None, task_id="mcp-task-1")], True),
            (
                [
                    _user_task_notification_entry(None, task_id="mcp-task-1"),
                    _attachment_task_notification_entry("toolu_mcp_2", task_id=None),
                ],
                False,
            ),
        ],
    )
    def test_multiple_mcp_results_in_one_user_entry_are_matched_individually(
        self, tmp_path: pathlib.Path, completed_entries: list[dict], expected: bool
    ) -> None:
        entries = [
            _assistant_entry(
                [
                    {"type": "tool_use", "id": "toolu_read", "name": "Read", "input": {}},
                    {
                        "type": "tool_use",
                        "id": "toolu_mcp_1",
                        "name": "mcp__agents_server__start",
                        "input": {},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_mcp_2",
                        "name": "mcp__agents_server__start",
                        "input": {},
                    },
                ]
            ),
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_read", "content": "read completed"},
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_mcp_1",
                            "content": _host_mcp_notice("mcp-task-1"),
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_mcp_2",
                            "content": [{"type": "text", "text": _host_mcp_notice("mcp-task-2")}],
                        },
                    ],
                },
            },
            *completed_entries,
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "", session_state=None) is expected
