"""background_tasks_sendmessageの契約テスト。"""

import json
import pathlib
import time
import types

import pytest

from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._hooks.background_tasks import is_pending_async_work
from agent_toolkit._testing.background_tasks_support import (
    _assistant_entry,
    _assistant_sendmessage_entry,
    _attachment_task_notification_entry,
    _bash_no_bg,
    _user_entry,
    _user_sendmessage_bg_resume_current_entry,
    _user_sendmessage_bg_resume_entry,
    _user_task_notification_entry,
)
from agent_toolkit._testing.helpers import _write_transcript

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

    def test_missing_transcript_returns_false(self):
        """transcript が存在しない → False（Stop抑止しない）。"""
        assert is_pending_async_work("/nonexistent/transcript.jsonl", "") is False

    @pytest.mark.usefixtures("real_end_turn_wait")
    def test_race_with_late_end_turn_flush(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """Stop hook起動時点で未到着の最終（end_turn）エントリを、待機が取り込んでから判定する。

        遅着前のtranscriptは最後のtool_useが背景Bashで終わり、この状態で判定すると非同期待機中として`True`になる。
        `_wait_for_end_turn`の最初のポーリング待ちの時点でend_turnエントリを追記し、待機がそれを取り込めば
        最終ターンはtextだけになって`False`になる。待機が後着を取り込まないと`True`のまま失敗する。
        """
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [{"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "x", "run_in_background": True}}],
                    msg_id="msg_prev",
                    stop_reason="tool_use",
                ),
            ],
        )
        appended = False

        def append_end_turn_on_first_poll(_seconds: float) -> None:
            # 待機が最初のポーリング待ちに入った時点を、transcriptへ最終エントリが書き込まれる時点とする。
            nonlocal appended
            if not appended:
                appended = True
                with t.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(_assistant_entry([{"type": "text", "text": _TEXT}])) + "\n")

        monkeypatch.setattr(
            _transcript_scan,
            "time",
            types.SimpleNamespace(monotonic=time.monotonic, sleep=append_end_turn_on_first_poll),
        )

        assert is_pending_async_work(str(t), "") is False
        assert appended

    def test_sendmessage_bg_resume_detected(self, tmp_path: pathlib.Path):
        """SendMessage呼び出しと対応する背景再開tool_resultが存在する場合に`True`を返す。

        誤発動防止のコア観点。SendMessage起動直後のStop hookで継続中と判定されることを確認する。
        """
        entries = [
            _user_entry("hello"),
            _assistant_sendmessage_entry("toolu_sm1"),
            _user_sendmessage_bg_resume_entry("toolu_sm1"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is True

    def test_sendmessage_bg_resume_completed_by_notification(self, tmp_path: pathlib.Path):
        """SendMessage背景再開後に同`tool_use_id`の完了通知を受信した場合は`False`を返す。

        完了済み相殺の観点。`launched - completed`が空になり抑止しないことを確認する。
        """
        entries = [
            _user_entry("hello"),
            _assistant_sendmessage_entry("toolu_sm1"),
            _user_sendmessage_bg_resume_entry("toolu_sm1"),
            _user_task_notification_entry("toolu_sm1"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_sendmessage_bg_resume_completed_by_attachment_notification(self, tmp_path: pathlib.Path):
        """SendMessage背景再開後に同`tool_use_id`の新形式完了通知（attachment形式）を受信した場合は`False`を返す。

        完了済み相殺（attachment形式）の観点。`type=="attachment"`・`commandMode=="task-notification"`の
        完了通知でも`launched - completed`から相殺されることを確認する。
        """
        entries = [
            _user_entry("hello"),
            _assistant_sendmessage_entry("toolu_sm1"),
            _user_sendmessage_bg_resume_entry("toolu_sm1"),
            _attachment_task_notification_entry("toolu_sm1"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_sendmessage_bg_resume_str_content_detected(self, tmp_path: pathlib.Path):
        """tool_result contentが文字列形式の背景再開エントリも`True`を返す。

        content形式バリエーション（文字列形式）の観点。
        `_extract_sendmessage_bg_resume_id`が両形式を処理することを確認する。
        """
        entries = [
            _user_entry("hello"),
            _assistant_sendmessage_entry("toolu_sm1"),
            _user_sendmessage_bg_resume_entry("toolu_sm1", text_format="str"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is True

    def test_sendmessage_bg_resume_current_format_detected(self, tmp_path: pathlib.Path):
        """現行形式（`toolUseResult.resumedAgentId`）の背景再開を`True`と判定する。

        構造化フィールド優先の観点。tool_result本文が旧マーカーを含まなくても
        起動集合へ加算されることを確認する。
        """
        entries = [
            _user_entry("hello"),
            _assistant_sendmessage_entry("toolu_sm1"),
            _user_sendmessage_bg_resume_current_entry("toolu_sm1"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is True

    def test_sendmessage_bg_resume_current_format_completed_by_notification(self, tmp_path: pathlib.Path):
        """現行形式の背景再開も同`tool_use_id`の完了通知で相殺され`False`を返す。

        永続pending防止の観点。起動集合と完了集合が同じ`tool_use_id`名前空間で突合することを確認する。
        """
        entries = [
            _user_entry("hello"),
            _assistant_sendmessage_entry("toolu_sm1"),
            _user_sendmessage_bg_resume_current_entry("toolu_sm1"),
            _user_task_notification_entry("toolu_sm1"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_sendmessage_bg_resume_legacy_marker_with_tool_use_result_detected(self, tmp_path: pathlib.Path):
        """`resumedAgentId`を持たない`toolUseResult`では旧マーカーとの一致判定へフォールバックする。

        後方互換の観点。構造化フィールドを欠く結果でも旧形式の検出が維持されることを確認する。
        """
        entry = _user_sendmessage_bg_resume_entry("toolu_sm1")
        entry["toolUseResult"] = {"success": True, "message": "Agent received your message."}
        entries = [
            _user_entry("hello"),
            _assistant_sendmessage_entry("toolu_sm1"),
            entry,
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is True

    @pytest.mark.parametrize(
        ("resume_text", "expected"),
        [
            ("Agent aecb02dc74cf84e99 resumed from transcript in the background with your message.", True),
            ("Resuming agent aecb02dc74cf84e99", False),
        ],
    )
    def test_sendmessage_bg_resume_non_string_resumed_agent_id(self, tmp_path: pathlib.Path, resume_text: str, expected: bool):
        """`resumedAgentId`が非文字列の場合は本文マーカーの有無だけで判定する。

        異常系の観点。構造化フィールドが型契約を満たさない結果を起動の根拠にしないことを確認する。
        """
        entries = [
            _user_entry("hello"),
            _assistant_sendmessage_entry("toolu_sm1"),
            _user_sendmessage_bg_resume_current_entry("toolu_sm1", resumed_agent_id=None, resume_text=resume_text),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is expected

    def test_sendmessage_bg_resume_current_format_without_sendmessage_call_returns_false(self, tmp_path: pathlib.Path):
        """SendMessage呼び出し由来でない`tool_use_id`は現行形式でも加算しない。

        境界の観点。`sendmessage_ids`に含まれない`tool_use_id`は構造化フィールドがあっても対象外とする。
        """
        entries = [
            _user_entry("hello"),
            _assistant_entry(
                [{"type": "tool_use", "id": "toolu_read1", "name": "Read", "input": {"file_path": "/tmp/x"}}],
                stop_reason="tool_use",
            ),
            _user_sendmessage_bg_resume_current_entry("toolu_read1"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_sendmessage_sync_only_returns_false(self, tmp_path: pathlib.Path):
        """マーカーを含まない同期SendMessage tool_resultのみの場合は`False`を返す。

        過剰抑止防止の観点。同期応答はマーカー文字列を持たないため起動集合へ加算されない。
        """
        sync_result_entry = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_sm1",
                        "content": [{"type": "text", "text": "Agent received your message."}],
                    }
                ],
            },
        }
        entries = [
            _user_entry("hello"),
            _assistant_sendmessage_entry("toolu_sm1"),
            sync_result_entry,
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_marker_in_other_tool_result_without_sendmessage_call_returns_false(self, tmp_path: pathlib.Path):
        """SendMessage呼び出しなしでマーカー文字列が他ツールtool_resultに含まれる場合は`False`を返す。

        誤検知防止の観点。SendMessage tool_use id集合が空のため、マーカーが他ツール出力に
        あっても起動集合へ加算されないことを確認する。
        """
        marker_in_read_result = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_read1",
                        "content": [
                            {
                                "type": "text",
                                "text": "File content: resumed from transcript in the background with your message.",
                            }
                        ],
                    }
                ],
            },
        }
        entries = [
            _user_entry("hello"),
            _assistant_entry(
                [{"type": "tool_use", "id": "toolu_read1", "name": "Read", "input": {"file_path": "/tmp/x"}}],
                stop_reason="tool_use",
            ),
            marker_in_read_result,
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_sidechain_sendmessage_bg_resume_is_ignored(self, tmp_path: pathlib.Path):
        """sidechain内のSendMessage呼び出し・背景再開tool_resultは対象外で`False`を返す。

        既存仕様（sidechain除外）の観点。sidechainエントリは起動集合へ加算されないことを確認する。
        """
        entries = [
            _user_entry("hello"),
            _assistant_sendmessage_entry("toolu_sm1", sidechain=True),
            _user_sendmessage_bg_resume_entry("toolu_sm1", sidechain=True),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False
