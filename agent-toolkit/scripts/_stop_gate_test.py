"""agent-toolkit/scripts/_stop_gate.py のテスト。

公開関数`is_pending_async_work`と`has_session_review_skill_invoked`の
振る舞いを境界値・同値分割で網羅する。
"""

import json
import pathlib
import threading
import time

import pytest
from _stop_gate import has_session_review_skill_invoked, is_pending_async_work


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
    """foreground Agent完了を記録するuserエントリを生成する。

    `toolUseResult.status`が`completed`の同期完了パスを再現する。
    `is_pending_async_work`がforeground Agentを未完了扱いしないことの確認に使う。
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


def _skill_entry(skill_name: str, *, sidechain: bool = False) -> dict:
    """Skill tool_use を含む assistant エントリを生成する。"""
    entry: dict = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "x", "name": "Skill", "input": {"skill": skill_name}},
            ],
        },
    }
    if sidechain:
        entry["isSidechain"] = True
    return entry


_TEXT = "作業途中です。"
_TARGET_SKILL = "agent-toolkit:session-review"
_OTHER_SKILL = "agent-toolkit:coding-standards"
_EXTENSION_SKILL = "extension-skill-example"


def _bash_no_bg() -> dict:
    """非同期でないBash tool_useブロックを返す（最終ターンの末尾を構造的にtool_useで終端するため）。"""
    return {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "echo done"}}


class TestIsPendingAsyncWork:
    """`is_pending_async_work` の判定を網羅するテスト。

    tool_use 種別 × {Agent / ScheduleWakeup / Monitor / Bash背景 / Bash前景 / その他 / なし}
    と未完了backgroundAgent × {なし / 起動のみ / 起動と通知ペア} の同値分割で組み合わせを検証する。
    """

    @pytest.mark.parametrize(
        ("tool_block", "expected"),
        [
            ({"type": "tool_use", "id": "x", "name": "Agent", "input": {}}, True),
            ({"type": "tool_use", "id": "x", "name": "ScheduleWakeup", "input": {}}, True),
            ({"type": "tool_use", "id": "x", "name": "Monitor", "input": {}}, True),
            (
                {
                    "type": "tool_use",
                    "id": "x",
                    "name": "Bash",
                    "input": {"command": "x", "run_in_background": True},
                },
                True,
            ),
            (
                {
                    "type": "tool_use",
                    "id": "x",
                    "name": "Bash",
                    "input": {"command": "x", "run_in_background": False},
                },
                False,
            ),
            (
                {"type": "tool_use", "id": "x", "name": "Read", "input": {"file_path": "/tmp/x"}},
                False,
            ),
            (None, False),
        ],
    )
    def test_tool_use_kinds(self, tmp_path: pathlib.Path, tool_block: dict | None, expected: bool):
        content: list[dict] = [{"type": "text", "text": _TEXT}]
        if tool_block is not None:
            content.append(tool_block)
        t = _write_transcript(tmp_path, [_user_entry("hello"), _assistant_entry(content)])
        assert is_pending_async_work(str(t)) is expected

    @pytest.mark.parametrize(
        ("pending_entries", "expected"),
        [
            ([], False),
            ([_user_async_launched_entry("toolu_bg1")], True),
            (
                [
                    _user_async_launched_entry("toolu_bg1"),
                    _user_task_notification_entry("toolu_bg1"),
                ],
                False,
            ),
            (
                [
                    _user_async_launched_entry("toolu_bg1"),
                    _user_async_launched_entry("toolu_bg2"),
                    _user_task_notification_entry("toolu_bg1"),
                ],
                True,
            ),
        ],
    )
    def test_pending_background_agent(self, tmp_path: pathlib.Path, pending_entries: list[dict], expected: bool):
        """直前ターンの最後のtool_useはRead（非同期でない）。

        background Agentの起動・通知の有無のみで結果が決まることを検証する。
        """
        entries: list[dict] = [_user_entry("hello")]
        entries.extend(pending_entries)
        entries.append(_user_entry("続きをお願いします"))
        entries.append(
            _assistant_entry(
                [
                    {"type": "text", "text": _TEXT},
                    {"type": "tool_use", "id": "x", "name": "Read", "input": {"file_path": "/tmp/x"}},
                ]
            )
        )
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t)) is expected

    @pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
    def test_notification_status_variants_count_as_completed(self, tmp_path: pathlib.Path, status: str):
        """`<status>`の値が`completed`／`failed`／`cancelled`のいずれでも完了扱い。"""
        entries = [
            _user_entry("hello"),
            _user_async_launched_entry("toolu_a"),
            _user_task_notification_entry("toolu_a", status=status),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t)) is False

    def test_sidechain_async_launched_is_ignored(self, tmp_path: pathlib.Path):
        """sidechain内の`async_launched`は未完了扱いしない。"""
        entries = [
            _user_entry("hello"),
            _user_async_launched_entry("toolu_a", sidechain=True),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t)) is False

    def test_foreground_agent_is_not_tracked(self, tmp_path: pathlib.Path):
        """foreground Agent（`toolUseResult.status == "completed"`）は未完了扱いしない。"""
        entries = [
            _user_entry("hello"),
            _user_foreground_agent_entry("toolu_a"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t)) is False

    def test_missing_transcript_returns_false(self):
        """transcript が存在しない → False（Stop抑止しない）。"""
        assert is_pending_async_work("/nonexistent/transcript.jsonl") is False

    def test_race_with_late_end_turn_flush(self, tmp_path: pathlib.Path):
        """assistant 最終 (end_turn) エントリが遅延 flush されるケースに対処する。

        Stop hook 起動時点で transcript に未到着のレースを再現する。
        最初は tool_use のみが書かれた状態でファイル存在、別スレッドで遅延後に
        end_turn エントリを追記する。`is_pending_async_work` がポーリングで末尾の到着を待ち、
        最終的に False を返すこと。
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
                f.write(json.dumps(_assistant_entry([{"type": "text", "text": _TEXT}])) + "\n")

        thread = threading.Thread(target=append_end_turn)
        thread.start()
        try:
            # end_turn到着後の最終ターンは text のみ → tool_useなし → 非同期待機なし → False
            assert is_pending_async_work(str(t)) is False
        finally:
            thread.join()


class TestHasSessionReviewSkillInvoked:
    """`has_session_review_skill_invoked` の境界値テスト。"""

    def test_no_skill_invocation_returns_false(self, tmp_path: pathlib.Path):
        t = _write_transcript(tmp_path, [_user_entry("hello")])
        assert has_session_review_skill_invoked(str(t), _TARGET_SKILL) is False

    def test_target_skill_invocation_returns_true(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _skill_entry(_TARGET_SKILL)],
        )
        assert has_session_review_skill_invoked(str(t), _TARGET_SKILL) is True

    def test_other_skill_only_returns_false(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _skill_entry(_OTHER_SKILL)],
        )
        assert has_session_review_skill_invoked(str(t), _TARGET_SKILL) is False

    def test_sidechain_invocation_ignored(self, tmp_path: pathlib.Path):
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _skill_entry(_TARGET_SKILL, sidechain=True)],
        )
        assert has_session_review_skill_invoked(str(t), _TARGET_SKILL) is False

    def test_distinct_skill_name_lookup(self, tmp_path: pathlib.Path):
        """引数で渡したスキル名のみを検出し、別名は検出しない。"""
        t = _write_transcript(
            tmp_path,
            [_user_entry("hello"), _skill_entry(_EXTENSION_SKILL)],
        )
        assert has_session_review_skill_invoked(str(t), _EXTENSION_SKILL) is True
        assert has_session_review_skill_invoked(str(t), _TARGET_SKILL) is False

    def test_missing_file_returns_false(self):
        assert has_session_review_skill_invoked("/nonexistent/transcript.jsonl", "x") is False
