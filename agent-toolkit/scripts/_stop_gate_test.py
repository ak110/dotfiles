"""agent-toolkit/scripts/_stop_gate.py のテスト。

各ヘルパー関数の単体テストと`is_real_session_end`の統合テスト。
transcript JSONLファイルを作成して関数を呼ぶ形式。
"""

import json
import pathlib
import threading
import time

import pytest
from _stop_gate import (
    _has_pending_subagents,
    _is_assistant_asking_question,
    _is_assistant_task_completed,
    _is_assistant_waiting,
    _last_tool_use_is_async_wait,
    _wait_for_end_turn,
    is_real_session_end,
)


def _write_transcript(tmp_path: pathlib.Path, lines: list[dict]) -> pathlib.Path:
    """dict のリストを JSONL 形式の transcript として書き込む。"""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8",
    )
    return transcript


def _assistant_entry(content: list[dict], *, msg_id: str = "msg_test", stop_reason: str = "end_turn") -> dict:
    """アシスタントエントリを生成する。

    `stop_reason`の既定は`end_turn`（最終ターン相当）。
    `_wait_for_end_turn`のポーリングを即時通過させるための設定。
    """
    return {
        "type": "assistant",
        "message": {"id": msg_id, "role": "assistant", "content": content, "stop_reason": stop_reason},
    }


def _user_entry(text: str) -> dict:
    """ユーザーエントリを生成する。"""
    return {"type": "user", "message": {"role": "user", "content": text}}


def _user_async_launched_entry(tool_use_id: str, *, sidechain: bool = False) -> dict:
    """background Agent起動を記録するuserエントリを生成する。

    実transcriptフォーマットに合わせ、`toolUseResult.status == "async_launched"`と
    `message.content`配列内の`tool_result`ブロックを持たせる。
    """
    return {
        "type": "user",
        "isSidechain": sidechain,
        "toolUseResult": {"isAsync": True, "status": "async_launched", "agentId": "agent-x"},
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": "Async agent launched successfully"}],
                }
            ],
        },
    }


def _user_task_notification_entry(tool_use_id: str, *, status: str = "completed") -> dict:
    """`<task-notification>`本文を持つuserエントリを生成する。"""
    notification = (
        "<task-notification>"
        "<task-id>task-x</task-id>"
        f"<tool-use-id>{tool_use_id}</tool-use-id>"
        f"<status>{status}</status>"
        "<summary>sub agent finished</summary>"
        "</task-notification>"
    )
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": notification}],
        },
    }


def _user_foreground_agent_entry(tool_use_id: str) -> dict:
    """foreground Agent完了を記録するuserエントリを生成する（参照用）。

    `toolUseResult.status`が`completed`の同期完了パスを再現する。
    """
    return {
        "type": "user",
        "isSidechain": False,
        "toolUseResult": {"status": "completed", "agentId": "agent-x"},
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": "Agent completed"}],
                }
            ],
        },
    }


# 共通 fixture: 基本的な transcript の末尾に置くアシスタントターン
_COMPLETION_TEXT = "作業が完了しました。"
_NO_COMPLETION_TEXT = "調査を続けます。"
_QUESTION_TEXT = "実装が完了しました。どうしますか？"
_WAITING_TEXT_JA = "バックグラウンドで実行中です。完了を待ちます。"
_WAITING_TEXT_EN = "Running in background. Waiting for completion."


class TestIsAssistantTaskCompleted:
    """完了文言検出の単体テスト。"""

    def test_detects_completion_keyword(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _COMPLETION_TEXT}])],
        )
        assert _is_assistant_task_completed(str(t)) is True

    def test_no_completion_keyword(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _NO_COMPLETION_TEXT}])],
        )
        assert _is_assistant_task_completed(str(t)) is False

    def test_missing_file_returns_false(self):
        assert _is_assistant_task_completed("/nonexistent/transcript.jsonl") is False


class TestIsAssistantAskingQuestion:
    """質問検出の単体テスト。"""

    def test_halfwidth_question_mark(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _QUESTION_TEXT}])],
        )
        assert _is_assistant_asking_question(str(t)) is True

    def test_ask_user_question_tool(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _COMPLETION_TEXT},
                        {"type": "tool_use", "id": "x", "name": "AskUserQuestion", "input": {}},
                    ]
                ),
            ],
        )
        assert _is_assistant_asking_question(str(t)) is True

    def test_no_question(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _COMPLETION_TEXT}])],
        )
        assert _is_assistant_asking_question(str(t)) is False


class TestIsAssistantWaiting:
    """待機語検出の単体テスト。"""

    @pytest.mark.parametrize(
        "waiting_text",
        [
            "バックグラウンドで実行中です。完了を待ちます。",
            "タスクが完了するまで待ちます。",
            "通知を待機中です。",
            "background process running",
            "待機します。",
        ],
    )
    def test_detects_waiting_keyword(self, tmp_path: pathlib.Path, waiting_text: str):
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": waiting_text}])],
        )
        assert _is_assistant_waiting(str(t)) is True

    def test_no_waiting_keyword(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _COMPLETION_TEXT}])],
        )
        assert _is_assistant_waiting(str(t)) is False

    def test_missing_file_returns_false(self):
        assert _is_assistant_waiting("/nonexistent/transcript.jsonl") is False


class TestLastToolUseIsAsyncWait:
    """非同期待機系ツール検出の単体テスト。"""

    @pytest.mark.parametrize("tool_name", ["Agent", "ScheduleWakeup", "Monitor"])
    def test_async_wait_tool_names(self, tmp_path: pathlib.Path, tool_name: str):
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _COMPLETION_TEXT},
                        {"type": "tool_use", "id": "x", "name": tool_name, "input": {}},
                    ]
                ),
            ],
        )
        assert _last_tool_use_is_async_wait(str(t)) is True

    def test_bash_run_in_background_true(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _COMPLETION_TEXT},
                        {
                            "type": "tool_use",
                            "id": "x",
                            "name": "Bash",
                            "input": {"command": "sleep 10", "run_in_background": True},
                        },
                    ]
                ),
            ],
        )
        assert _last_tool_use_is_async_wait(str(t)) is True

    def test_bash_run_in_background_false(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _COMPLETION_TEXT},
                        {
                            "type": "tool_use",
                            "id": "x",
                            "name": "Bash",
                            "input": {"command": "echo hello", "run_in_background": False},
                        },
                    ]
                ),
            ],
        )
        assert _last_tool_use_is_async_wait(str(t)) is False

    def test_bash_no_run_in_background_key(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _COMPLETION_TEXT},
                        {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "echo hello"}},
                    ]
                ),
            ],
        )
        assert _last_tool_use_is_async_wait(str(t)) is False

    def test_regular_tool_not_async(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _COMPLETION_TEXT},
                        {"type": "tool_use", "id": "x", "name": "Read", "input": {"file_path": "/tmp/x"}},
                    ]
                ),
            ],
        )
        assert _last_tool_use_is_async_wait(str(t)) is False

    def test_no_tool_use_returns_false(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _COMPLETION_TEXT}])],
        )
        assert _last_tool_use_is_async_wait(str(t)) is False

    def test_missing_file_returns_false(self):
        assert _last_tool_use_is_async_wait("/nonexistent/transcript.jsonl") is False


class TestWaitForEndTurn:
    """`_wait_for_end_turn` のポーリング挙動の単体テスト。"""

    def test_returns_immediately_when_end_turn_present(self, tmp_path: pathlib.Path):
        """末尾 assistant が end_turn → 即時戻る（poll 待ちなし）。"""
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _COMPLETION_TEXT}])],
        )
        start = time.monotonic()
        _wait_for_end_turn(str(t), timeout=0.3)
        assert time.monotonic() - start < 0.05

    def test_times_out_when_end_turn_never_arrives(self, tmp_path: pathlib.Path):
        """末尾 assistant が tool_use のみ → timeout まで待って戻る。"""
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [{"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "x"}}],
                    stop_reason="tool_use",
                ),
            ],
        )
        start = time.monotonic()
        _wait_for_end_turn(str(t), timeout=0.15)
        elapsed = time.monotonic() - start
        assert 0.1 <= elapsed < 0.5


class TestIsRealSessionEnd:
    """`is_real_session_end` の AND 条件を網羅するテスト。"""

    def test_all_conditions_pass(self, tmp_path: pathlib.Path):
        """完了文言あり・質問なし・待機語なし・非同期待機ツールなし → True。"""
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _COMPLETION_TEXT},
                        {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "echo done"}},
                    ]
                ),
            ],
        )
        assert is_real_session_end(str(t)) is True

    def test_no_completion_keyword_returns_false(self, tmp_path: pathlib.Path):
        """完了文言なし → False。"""
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _NO_COMPLETION_TEXT}])],
        )
        assert is_real_session_end(str(t)) is False

    def test_question_returns_false(self, tmp_path: pathlib.Path):
        """質問あり → False。"""
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _QUESTION_TEXT}])],
        )
        assert is_real_session_end(str(t)) is False

    def test_waiting_keyword_returns_false(self, tmp_path: pathlib.Path):
        """待機語あり → False（完了文言を含んでいても）。"""
        text = _COMPLETION_TEXT + " " + _WAITING_TEXT_JA
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": text}])],
        )
        assert is_real_session_end(str(t)) is False

    def test_async_tool_agent_returns_false(self, tmp_path: pathlib.Path):
        """最後の tool_use が Agent → False（完了文言を含んでいても）。"""
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _COMPLETION_TEXT},
                        {"type": "tool_use", "id": "x", "name": "Agent", "input": {}},
                    ]
                ),
            ],
        )
        assert is_real_session_end(str(t)) is False

    def test_bash_background_returns_false(self, tmp_path: pathlib.Path):
        """最後の tool_use が Bash+run_in_background=True → False。"""
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _COMPLETION_TEXT},
                        {
                            "type": "tool_use",
                            "id": "x",
                            "name": "Bash",
                            "input": {"command": "long_task.sh", "run_in_background": True},
                        },
                    ]
                ),
            ],
        )
        assert is_real_session_end(str(t)) is False

    def test_missing_transcript_returns_false(self):
        """transcript が存在しない → False。"""
        assert is_real_session_end("/nonexistent/transcript.jsonl") is False

    def test_completion_text_only_no_tool(self, tmp_path: pathlib.Path):
        """完了文言あり・ツールなし・待機語なし・質問なし → True。"""
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _COMPLETION_TEXT}])],
        )
        assert is_real_session_end(str(t)) is True

    def test_waiting_english_background_returns_false(self, tmp_path: pathlib.Path):
        """英語の待機語 'background' → False。"""
        text = _COMPLETION_TEXT + " " + _WAITING_TEXT_EN
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _assistant_entry([{"type": "text", "text": text}])],
        )
        assert is_real_session_end(str(t)) is False

    def test_schedule_wakeup_returns_false(self, tmp_path: pathlib.Path):
        """最後の tool_use が ScheduleWakeup → False。"""
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _COMPLETION_TEXT},
                        {"type": "tool_use", "id": "x", "name": "ScheduleWakeup", "input": {}},
                    ]
                ),
            ],
        )
        assert is_real_session_end(str(t)) is False

    def test_race_with_late_end_turn_flush(self, tmp_path: pathlib.Path):
        """assistant 最終 (end_turn) エントリが遅延 flush されるケースを吸収する。

        Stop hook 起動時点で transcript に未到着のレースを再現する。
        最初は tool_use のみが書かれた状態でファイル存在、別スレッドで遅延後に
        end_turn エントリを追記する。`is_real_session_end` がポーリングで吸収して True を返すこと。
        """
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [{"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "echo done"}}],
                    msg_id="msg_prev",
                    stop_reason="tool_use",
                ),
            ],
        )

        def append_end_turn() -> None:
            time.sleep(0.1)
            with t.open("a", encoding="utf-8") as f:
                f.write(json.dumps(_assistant_entry([{"type": "text", "text": _COMPLETION_TEXT}])) + "\n")

        thread = threading.Thread(target=append_end_turn)
        thread.start()
        try:
            assert is_real_session_end(str(t)) is True
        finally:
            thread.join()

    def test_monitor_returns_false(self, tmp_path: pathlib.Path):
        """最後の tool_use が Monitor → False。"""
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _COMPLETION_TEXT},
                        {"type": "tool_use", "id": "x", "name": "Monitor", "input": {}},
                    ]
                ),
            ],
        )
        assert is_real_session_end(str(t)) is False

    def test_pending_background_subagent_returns_false(self, tmp_path: pathlib.Path):
        """過去ターンに未完了 background Agent が残っている → False。

        完了文言あり・質問なし・待機語なし・最後の tool_use は Bash の通常実行で
        既存 4 条件は通過するが、background Agent が起動済みかつ完了通知未到着のため
        新条件で False となるケース。
        """
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [{"type": "tool_use", "id": "toolu_bg1", "name": "Agent", "input": {}}],
                    msg_id="msg_launch",
                    stop_reason="tool_use",
                ),
                _user_async_launched_entry("toolu_bg1"),
                _user_entry("続きをお願いします"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _COMPLETION_TEXT},
                        {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "echo done"}},
                    ]
                ),
            ],
        )
        assert is_real_session_end(str(t)) is False


class TestHasPendingSubagents:
    """`_has_pending_subagents` の単体テスト。"""

    def test_launched_without_notification_returns_true(self, tmp_path: pathlib.Path):
        """`async_launched` 1 件、対応する `<task-notification>` なし → True。"""
        t = _write_transcript(tmp_path, [_user_async_launched_entry("toolu_a")])
        assert _has_pending_subagents(str(t)) is True

    def test_launched_with_matching_notification_returns_false(self, tmp_path: pathlib.Path):
        """`async_launched` 1 件、対応する `<task-notification>` あり → False。"""
        t = _write_transcript(
            tmp_path,
            [
                _user_async_launched_entry("toolu_a"),
                _user_task_notification_entry("toolu_a"),
            ],
        )
        assert _has_pending_subagents(str(t)) is False

    def test_multiple_launched_partial_notification_returns_true(self, tmp_path: pathlib.Path):
        """複数の `async_launched` のうち 1 件のみ完了通知 → True。"""
        t = _write_transcript(
            tmp_path,
            [
                _user_async_launched_entry("toolu_a"),
                _user_async_launched_entry("toolu_b"),
                _user_task_notification_entry("toolu_a"),
            ],
        )
        assert _has_pending_subagents(str(t)) is True

    @pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
    def test_notification_status_variants_count_as_completed(self, tmp_path: pathlib.Path, status: str):
        """`<status>` の値が `completed` / `failed` / `cancelled` のいずれでも完了扱い → False。"""
        t = _write_transcript(
            tmp_path,
            [
                _user_async_launched_entry("toolu_a"),
                _user_task_notification_entry("toolu_a", status=status),
            ],
        )
        assert _has_pending_subagents(str(t)) is False

    def test_sidechain_launched_is_ignored(self, tmp_path: pathlib.Path):
        """sidechain 内の `async_launched` は無視 → False。"""
        t = _write_transcript(
            tmp_path,
            [_user_async_launched_entry("toolu_a", sidechain=True)],
        )
        assert _has_pending_subagents(str(t)) is False

    def test_foreground_agent_is_not_tracked(self, tmp_path: pathlib.Path):
        """foreground Agent（`toolUseResult.status == "completed"`）は対象外 → False。"""
        t = _write_transcript(tmp_path, [_user_foreground_agent_entry("toolu_a")])
        assert _has_pending_subagents(str(t)) is False

    def test_missing_file_returns_false(self):
        """transcript 未存在 → False。"""
        assert _has_pending_subagents("/nonexistent/transcript.jsonl") is False
