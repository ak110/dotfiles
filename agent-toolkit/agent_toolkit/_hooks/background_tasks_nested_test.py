"""background_tasks_nestedの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import background_tasks as _background_tasks
from agent_toolkit._hooks.background_tasks import is_pending_async_work
from agent_toolkit._testing.background_tasks_support import (
    _assistant_agent_entry,
    _assistant_entry,
    _bash_no_bg,
    _queue_operation_task_notification_entry,
    _task_notification_body,
    _user_async_launched_entry,
    _user_entry,
    _user_task_notification_entry,
    _write_nested_subagent_fixture,
)
from agent_toolkit._testing.helpers import _write_transcript

_NOTICE_A = _task_notification_body("toolu_a", task_id="task-a")


_NOTICE_B = _task_notification_body("toolu_b", task_id="task-b")


_AGENT_MESSAGE = "<agent-message>委譲先からの途中報告</agent-message>"


_TEXT = "作業途中です。"


class TestIsPendingAsyncWork:
    """`is_pending_async_work` の判定を網羅するテスト。

    tool_use 種別 × {Agent / ScheduleWakeup / CronCreate / Monitor / Bash背景 / Bash前景 / その他 / なし}
    と未完了background task（Agent・Bash双方）× {なし / 起動のみ / 起動と通知ペア} の
    同値分割で組み合わせを検証する。

    SendMessage背景再開テスト群は以下の観点を網羅する。

    - SendMessage呼び出しと背景再開tool_resultが存在する場合に`True`を返す（誤発動防止のコア）
    - 現行形式（`toolUseResult.resumedAgentId`を持ち本文に旧マーカーを含まない）でも`True`を返し、
      同`tool_use_id`の完了通知で`False`へ相殺される
    - `resumedAgentId`を持たない旧形式は本文マーカーとの一致判定へフォールバックして`True`を返す（後方互換）
    - `resumedAgentId`が非文字列の場合は構造化フィールド判定が成立せず本文マーカーの有無で決まる
    - 現行形式でもSendMessage呼び出し由来でない`tool_use_id`は加算しない（誤検知防止）
    - 同`tool_use_id`の旧形式完了通知（user textブロック内`<task-notification>`）で`False`へ相殺される
    - 同`tool_use_id`の新形式完了通知（`type=="attachment"`・`commandMode=="task-notification"`）でも`False`へ相殺される
    - tool_result contentが文字列形式でも`True`を返す（content形式バリエーション）
    - マーカーを含まない同期SendMessage tool_resultのみの場合は`False`を返す（過剰抑止防止）
    - SendMessage呼び出しなしでマーカー文字列が他ツール出力に含まれる場合は`False`を返す（誤検知防止）
    - sidechain内のSendMessage呼び出し・背景再開tool_resultは対象外で`False`を返す
    """

    def test_pending_grandchild_from_direct_child_transcript(self, tmp_path: pathlib.Path) -> None:
        """直接の子が完了済みでも、子transcriptの未完了な孫Agentを待機中と判定する。"""
        entries = [
            _user_entry("hello"),
            _user_async_launched_entry("toolu_child", agent_id="child-id"),
            _user_task_notification_entry("toolu_child", task_id="child-id"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        child_entries = [
            _assistant_agent_entry("toolu_grandchild"),
            _user_async_launched_entry("toolu_grandchild", agent_id="grandchild-id"),
        ]
        transcript = _write_nested_subagent_fixture(tmp_path, entries, child_entries)
        assert is_pending_async_work(str(transcript), "") is True

    @pytest.mark.parametrize(("operation", "pending"), [("enqueue", True), ("remove", False)])
    def test_queue_operation_completion_completes_grandchild(
        self, tmp_path: pathlib.Path, operation: str, pending: bool
    ) -> None:
        """完了突合後も投入だけの通知は配送待ちとして保つ。"""
        entries = [
            _user_entry("hello"),
            _user_async_launched_entry("toolu_child", agent_id="child-id"),
            _user_task_notification_entry("toolu_child", task_id="child-id"),
            _queue_operation_task_notification_entry(
                operation,
                tool_use_id="toolu_grandchild",
                task_id="grandchild-id",
            ),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        child_entries = [
            _assistant_agent_entry("toolu_grandchild"),
            _user_async_launched_entry("toolu_grandchild", agent_id="grandchild-id"),
        ]
        transcript = _write_nested_subagent_fixture(tmp_path, entries, child_entries)
        assert is_pending_async_work(str(transcript), "", background_tasks=[]) is pending

    @pytest.mark.parametrize(
        ("operations", "pending"),
        [
            (("enqueue",), True),
            (("enqueue", "dequeue"), False),
            (("enqueue", "remove"), False),
            (("enqueue", "enqueue", "dequeue"), True),
            (("dequeue", "enqueue"), True),
        ],
    )
    def test_queued_notification_tracks_delivery_order(
        self,
        tmp_path: pathlib.Path,
        operations: tuple[str, ...],
        pending: bool,
    ) -> None:
        """投入・先頭配送・同じ本文の除去から未配送の通知を判定する。"""
        entries = [_user_entry("続き")]
        for operation in operations:
            if operation == "dequeue":
                entries.append({"type": "queue-operation", "operation": "dequeue"})
            else:
                entries.append(_queue_operation_task_notification_entry(operation, tool_use_id="toolu_done"))
        entries.append(_assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]))
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "queued-order", background_tasks=[]) is pending

    @pytest.mark.parametrize("operation", ["popAll", "popOne"])
    def test_editable_pop_before_notification_delivery(self, tmp_path: pathlib.Path, operation: str) -> None:
        """編集用に取り出した入力を1件ずつ除き、後続の先頭配送で通知を配送済みにする。"""
        queue = [
            {"type": "queue-operation", "operation": "enqueue", "content": "編集1"},
            {"type": "queue-operation", "operation": "enqueue", "content": "編集1"},
            {"type": "queue-operation", "operation": "enqueue", "content": "編集2"},
            _queue_operation_task_notification_entry("enqueue", tool_use_id="toolu_done"),
            {"type": "queue-operation", "operation": operation, "content": "登録されていない本文"},
            {"type": "queue-operation", "operation": operation, "content": "編集1"},
            {"type": "queue-operation", "operation": operation, "content": "編集2"},
        ]
        dequeue = {"type": "queue-operation", "operation": "dequeue"}
        tail = [_assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()])]
        # 取り出し直後と1回目の配送後は、同じ本文の残り1件が通知より先にあるため未配送として残る。
        for name, dequeues, pending in (("popped", 0, True), ("first", 1, True), ("delivered", 2, False)):
            directory = tmp_path / name
            directory.mkdir()
            transcript = _write_transcript(directory, [*queue, *([dequeue] * dequeues), *tail])
            assert is_pending_async_work(str(transcript), "queued-pop", background_tasks=[]) is pending

    def test_non_notification_enqueue_still_occupies_fifo(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """通知以外の先行項目を配送しても、後ろの完了通知はキューへ残る。"""
        monkeypatch.setattr("agent_toolkit._hooks.stop_session.tempfile.gettempdir", lambda: str(tmp_path))
        entries = [
            {"type": "queue-operation", "operation": "enqueue", "content": {"kind": "other"}},
            _queue_operation_task_notification_entry("enqueue", tool_use_id="toolu_done"),
            {"type": "queue-operation", "operation": "dequeue"},
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "queued-source", background_tasks=[]) is True
        log_path = tmp_path / "claude-agent-toolkit-stop-queued-source.log"
        assert "source=queued_notification" in log_path.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("contents", "delivered", "expected"),
        [
            pytest.param([], [_user_entry("続き")], [], id="empty"),
            pytest.param([_NOTICE_A], [_user_entry(_NOTICE_A)], [], id="single"),
            pytest.param([_NOTICE_A, _NOTICE_B], [_user_entry(_NOTICE_B)], [_NOTICE_A], id="non-head-notification"),
            pytest.param([_NOTICE_A, _AGENT_MESSAGE], [_user_entry(_AGENT_MESSAGE)], [_NOTICE_A], id="non-head-agent-message"),
            pytest.param(
                [_NOTICE_A, _NOTICE_B],
                [
                    {
                        "type": "user",
                        "message": {"content": [{"type": "text", "text": "前置き\n"}, {"type": "text", "text": _NOTICE_B}]},
                    }
                ],
                [_NOTICE_A],
                id="contained-in-text-blocks",
            ),
            pytest.param([_NOTICE_A, _NOTICE_B], [_user_entry("どちらでもない")], [_NOTICE_B], id="mismatch-removes-head"),
            pytest.param([_NOTICE_A, _NOTICE_B], [], [_NOTICE_B], id="no-user-removes-head"),
            pytest.param(
                [_NOTICE_A, _NOTICE_B],
                [
                    {
                        "type": "user",
                        "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": _NOTICE_A}]},
                    },
                    {"type": "user", "isSidechain": True, "message": {"content": _NOTICE_A}},
                    {"type": "attachment", "attachment": {"prompt": _NOTICE_A}},
                    _user_entry(_NOTICE_B),
                ],
                [_NOTICE_A],
                id="skips-tool-result-sidechain-attachment",
            ),
        ],
    )
    def test_dequeue_removes_element_matching_delivered_user_message(
        self, contents: list[str], delivered: list[dict], expected: list[str]
    ) -> None:
        """`dequeue`はその後に配送されたユーザーメッセージの本文に該当する1件を除き、該当が無ければ先頭を除く。"""
        entries = [{"type": "queue-operation", "operation": "enqueue", "content": content} for content in contents]
        entries.append({"type": "queue-operation", "operation": "dequeue"})
        entries.extend(delivered)
        assert _background_tasks.queued_task_notification_contents(entries) == expected

    def test_missing_child_transcript_preserves_top_level_decision(self, tmp_path: pathlib.Path) -> None:
        """サブエージェント記録ディレクトリが無い場合も、最上位の起動・完了判定を維持する。"""
        entries = [
            _user_async_launched_entry("toolu_child", agent_id="child-id"),
            _user_task_notification_entry("toolu_child", task_id="child-id"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(transcript), "") is False

    def test_malformed_or_unrelated_child_transcript_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """破損記録と親子関係の無い孫記録を無視し、最上位判定を例外化しない。"""
        entries = [
            _user_async_launched_entry("toolu_child", agent_id="child-id"),
            _user_task_notification_entry("toolu_child", task_id="child-id"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        transcript = _write_transcript(tmp_path, entries)
        subagents_dir = transcript.with_suffix("") / "subagents"
        subagents_dir.mkdir(parents=True)
        malformed = subagents_dir / "agent-malformed.jsonl"
        malformed.write_text("{not-json\n", encoding="utf-8")
        malformed.with_name("agent-malformed.meta.json").write_text(json.dumps({"spawnDepth": 1}), encoding="utf-8")
        unrelated = subagents_dir / "agent-unrelated.jsonl"
        unrelated.write_text(
            "\n".join(
                json.dumps(entry, ensure_ascii=False)
                for entry in [
                    _assistant_agent_entry("toolu_unrelated"),
                    _user_async_launched_entry("toolu_unrelated", agent_id="unrelated-id"),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        unrelated.with_name("agent-unrelated.meta.json").write_text(
            json.dumps({"parentAgentId": "other-parent", "spawnDepth": 2}), encoding="utf-8"
        )
        assert is_pending_async_work(str(transcript), "") is False

    def test_empty_child_metadata_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """直接の子を示すmetadataが空の場合はサブエージェント記録を走査しない。"""
        entries = [
            _user_async_launched_entry("toolu_child", agent_id="child-id"),
            _user_task_notification_entry("toolu_child", task_id="child-id"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        child_entries = [
            _assistant_agent_entry("toolu_grandchild"),
            _user_async_launched_entry("toolu_grandchild", agent_id="grandchild-id"),
        ]
        transcript = _write_nested_subagent_fixture(tmp_path, entries, child_entries, metadata={})
        assert is_pending_async_work(str(transcript), "") is False

    def test_partially_corrupt_child_transcript_is_ignored(self, tmp_path: pathlib.Path) -> None:
        """子transcriptに破損行が1行でもあれば有効行を部分採用しない。"""
        entries = [
            _user_async_launched_entry("toolu_child", agent_id="child-id"),
            _user_task_notification_entry("toolu_child", task_id="child-id"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        child_entries = [
            _assistant_agent_entry("toolu_grandchild"),
            _user_async_launched_entry("toolu_grandchild", agent_id="grandchild-id"),
        ]
        transcript = _write_nested_subagent_fixture(tmp_path, entries, child_entries)
        child_path = transcript.with_suffix("") / "subagents" / "agent-child-id.jsonl"
        with child_path.open("a", encoding="utf-8") as child_file:
            child_file.write("{not-json\n")
        assert is_pending_async_work(str(transcript), "") is False

    def test_multiple_children_and_mixed_grandchildren_are_completed(self, tmp_path: pathlib.Path) -> None:
        """複数の直接の子から収集したAgent・Taskの孫を個別識別子で完了扱いする。"""
        entries = [
            _user_async_launched_entry("toolu_child_a", agent_id="child-a"),
            _user_task_notification_entry("toolu_child_a", task_id="child-a"),
            _user_async_launched_entry("toolu_child_b", agent_id="child-b"),
            _user_task_notification_entry("toolu_child_b", task_id="child-b"),
            _queue_operation_task_notification_entry(
                "enqueue",
                tool_use_id="toolu_grandchild_agent",
                task_id="local-agent-id",
            ),
            _queue_operation_task_notification_entry("remove", task_id="local-task-id"),
            _queue_operation_task_notification_entry(
                "enqueue",
                tool_use_id="toolu_grandchild_other",
                task_id="local-other-id",
            ),
            {"type": "queue-operation", "operation": "dequeue"},
            {"type": "queue-operation", "operation": "dequeue"},
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        child_entries = [
            _assistant_agent_entry("toolu_grandchild_agent"),
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "toolu_grandchild_task", "name": "Task", "input": {}}],
                    "stop_reason": "tool_use",
                },
            },
            _user_async_launched_entry("toolu_grandchild_agent", agent_id="local-agent-id"),
            _user_async_launched_entry("toolu_grandchild_task", agent_id="local-task-id"),
        ]
        transcript = _write_nested_subagent_fixture(tmp_path, entries, child_entries, child_id="child-a")
        subagents_dir = transcript.with_suffix("") / "subagents"
        child_b_path = subagents_dir / "agent-child-b.jsonl"
        child_b_path.write_text(
            "\n".join(
                json.dumps(entry, ensure_ascii=False)
                for entry in [
                    _assistant_agent_entry("toolu_grandchild_other"),
                    _user_async_launched_entry("toolu_grandchild_other", agent_id="local-other-id"),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        child_b_path.with_name("agent-child-b.meta.json").write_text(
            json.dumps({"spawnDepth": 1}),
            encoding="utf-8",
        )
        assert is_pending_async_work(str(transcript), "") is False
