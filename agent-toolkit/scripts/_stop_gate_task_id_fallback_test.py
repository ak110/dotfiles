"""agent-toolkit/scripts/_stop_gate.py のテスト（`<task-id>`フォールバック解決）。

`<task-notification>`要素に`<tool-use-id>`が含まれない通知形式に対する
`<task-id>`要素経由のフォールバック解決を、旧形式（userエントリ）・
新形式（`type=="attachment"`）の双方で検証する。不変条件
「起動として記録した全背景タスクはいずれかの完了通知形式で完了集合へ解決できる」を担保する。
基幹テストは`_stop_gate_test.py`に、共通ヘルパーは同ファイルから再利用する。
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _stop_gate import is_pending_async_work  # noqa: E402  # pylint: disable=wrong-import-position
from _stop_gate_test import (  # noqa: E402  # pylint: disable=wrong-import-position
    _assistant_agent_entry,
    _assistant_entry,
    _attachment_task_notification_entry,
    _bash_no_bg,
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
    """MCP timeoutによる背景化を記録したassistant・userエントリを生成する。"""
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
                        "content": [{"type": "text", "text": f"moved to the background as task {task_id}"}],
                    }
                ],
            },
        },
    ]


class TestTaskIdFallbackCompletion:
    """旧形式（userエントリの`<task-notification>`）における`<task-id>`フォールバック解決の検証。

    通知形式のバリエーション: `<tool-use-id>`のみ・`<task-id>`のみ・両方あり・両方欠落。
    """

    def test_tool_use_id_only_completion(self, tmp_path: pathlib.Path) -> None:
        """`<tool-use-id>`のみを持つ通知で完了解決される（既存挙動）。"""
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _user_task_notification_entry("toolu_a", task_id=None),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_task_id_only_completion(self, tmp_path: pathlib.Path) -> None:
        """`<task-id>`のみを持つ通知でも起動時agentId経由で完了解決される。"""
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _user_task_notification_entry(None, task_id="agent-a"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_both_present_completion(self, tmp_path: pathlib.Path) -> None:
        """`<tool-use-id>`と`<task-id>`の両方があり、双方とも同一起動へ対応する通知でも完了解決される。"""
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _user_task_notification_entry("toolu_a", task_id="agent-a"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_both_missing_leaves_pending(self, tmp_path: pathlib.Path) -> None:
        """`<tool-use-id>`と`<task-id>`の両方が欠落した通知では完了解決されずpendingが残る。"""
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _user_task_notification_entry(None, task_id=None),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is True


class TestAttachmentTaskIdFallbackCompletion:
    """新形式（`type=="attachment"`の`<task-notification>`）における`<task-id>`フォールバック解決の検証。

    項番1のリファクタリング（`_resolve_task_notification_ids`共通ヘルパー抽出）後の
    attachment分岐が旧形式と同一の解決経路を通ることを確認する。
    通知形式のバリエーション: `<tool-use-id>`のみ・`<task-id>`のみ・両方あり・両方欠落。
    両方欠落時は`task_notification_unresolved`ログが出力されることも検証する。
    """

    def test_tool_use_id_only_completion(self, tmp_path: pathlib.Path) -> None:
        """`<tool-use-id>`のみを持つattachment通知で完了解決される。"""
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _attachment_task_notification_entry("toolu_a", task_id=None),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_task_id_only_completion(self, tmp_path: pathlib.Path) -> None:
        """`<task-id>`のみを持つattachment通知でも起動時agentId経由で完了解決される。"""
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _attachment_task_notification_entry(None, task_id="agent-a"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_both_present_completion(self, tmp_path: pathlib.Path) -> None:
        """`<tool-use-id>`と`<task-id>`の両方があり、双方とも同一起動へ対応するattachment通知でも完了解決される。"""
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _attachment_task_notification_entry("toolu_a", task_id="agent-a"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_both_missing_leaves_pending_and_logs_unresolved(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`<tool-use-id>`と`<task-id>`の両方が欠落したattachment通知は解決されずpendingが残る。

        あわせて`task_notification_unresolved`が常時ログへ記録されることを検証する。
        """
        monkeypatch.setattr("_stop_gate.tempfile.gettempdir", lambda: str(tmp_path))
        entries = [
            _user_async_launched_entry("toolu_a", agent_id="agent-a"),
            _attachment_task_notification_entry(None, task_id=None),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "session-fallback") is True
        log_path = tmp_path / "claude-agent-toolkit-stop-session-fallback.log"
        assert "task_notification_unresolved" in log_path.read_text(encoding="utf-8")


class TestNestedAgentTaskIdFallbackCompletion:
    """孫Agentの`queue-operation`完了通知を`<task-id>`だけで突合する。"""

    @pytest.mark.parametrize("operation", ["enqueue", "remove"])
    def test_task_id_only_queue_operation_completes_grandchild(self, tmp_path: pathlib.Path, operation: str) -> None:
        """子記録から収集した孫のagent ID対応表でtask-id単独通知を解決する。"""
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
        assert is_pending_async_work(str(transcript), "") is False


class TestMcpBackgroundTaskCompletion:
    """MCPタイムアウト通知とタスクIDだけの完了通知を突合する。"""

    def test_pending_mcp_task_is_completed_by_task_id_notification(self, tmp_path: pathlib.Path) -> None:
        entries = [
            _assistant_entry(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_mcp",
                        "name": "mcp__agents_server__start",
                        "input": {},
                    }
                ]
            ),
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_mcp",
                            "content": [{"type": "text", "text": "moved to the background as task mcp-task-1"}],
                        }
                    ],
                },
            },
            _user_task_notification_entry(None, task_id="mcp-task-1"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "") is False

    def test_pending_mcp_task_without_completion_remains_pending(self, tmp_path: pathlib.Path) -> None:
        entries = [
            _assistant_entry(
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_mcp",
                        "name": "mcp__agents_server__start",
                        "input": {},
                    }
                ]
            ),
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_mcp",
                            "content": [{"type": "text", "text": "moved to the background as task mcp-task-1"}],
                        }
                    ],
                },
            },
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "") is True

    @pytest.mark.parametrize(
        "notification",
        [
            _user_task_notification_entry("toolu_mcp", task_id=None),
            _attachment_task_notification_entry("toolu_mcp", task_id=None),
        ],
    )
    def test_mcp_task_is_completed_by_tool_use_id_notification(self, tmp_path: pathlib.Path, notification: dict) -> None:
        """MCP背景化は既存のtool-use-idだけの完了通知でも相殺される。"""
        entries = [
            *_mcp_background_timeout_entries("mcp-task-1", "toolu_mcp"),
            notification,
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "") is False

    def test_sidechain_mcp_timeout_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """sidechain内のMCP背景化はメイン側の未完了タスクに含めない。"""
        entries = [
            *_mcp_background_timeout_entries("mcp-task-1", "toolu_mcp", sidechain=True),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "") is False

    def test_non_mcp_timeout_text_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """MCP以外のtool_resultに同じ文言があっても背景化として誤検出しない。"""
        entries = [
            *_mcp_background_timeout_entries("mcp-task-1", "toolu_read", tool_name="Read"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "") is False

    def test_one_of_multiple_mcp_tasks_completed_leaves_other_pending(self, tmp_path: pathlib.Path) -> None:
        """複数MCP背景化の一部だけが完了すると、未完了のタスクだけが残る。"""
        entries = [
            *_mcp_background_timeout_entries("mcp-task-1", "toolu_mcp_1"),
            *_mcp_background_timeout_entries("mcp-task-2", "toolu_mcp_2"),
            _attachment_task_notification_entry("toolu_mcp_1", task_id=None),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "") is True

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
        """複数MCP結果を同じuserエントリで受領しても、各結果と完了通知を対応付ける。"""
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
                            "content": "moved to the background as task mcp-task-1",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_mcp_2",
                            "content": [{"type": "text", "text": "moved to the background as task mcp-task-2"}],
                        },
                    ],
                },
            },
            *completed_entries,
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "") is expected
