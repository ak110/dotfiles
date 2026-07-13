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
    _assistant_entry,
    _attachment_task_notification_entry,
    _bash_no_bg,
    _user_async_launched_entry,
    _user_task_notification_entry,
    _write_transcript,
)

_TEXT = "作業完了。"


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
