"""background_tasks_notificationsの契約テスト。"""

import pathlib

import pytest

from agent_toolkit._hooks.background_tasks import is_pending_async_work
from agent_toolkit._testing.background_tasks_support import (
    _assistant_entry,
    _attachment_task_notification_entry,
    _bash_no_bg,
    _user_async_launched_entry,
    _user_entry,
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

    @pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
    def test_notification_status_variants_count_as_completed(self, tmp_path: pathlib.Path, status: str):
        entries = [
            _user_entry("hello"),
            _user_async_launched_entry("toolu_a"),
            _user_task_notification_entry("toolu_a", status=status),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "", session_state=None) is False

    def test_sidechain_async_launched_is_ignored(self, tmp_path: pathlib.Path):
        entries = [
            _user_entry("hello"),
            _user_async_launched_entry("toolu_a", sidechain=True),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "", session_state=None) is False

    @pytest.mark.parametrize(
        ("pending_entries", "expected"),
        [
            (
                [
                    _user_async_launched_entry("toolu_a"),
                    _attachment_task_notification_entry("toolu_a"),
                ],
                False,
            ),
            (
                [
                    _user_async_launched_entry("toolu_old"),
                    _user_async_launched_entry("toolu_new"),
                    _user_task_notification_entry("toolu_old"),
                    _attachment_task_notification_entry("toolu_new"),
                ],
                False,
            ),
            (
                [
                    _user_async_launched_entry("toolu_a"),
                    _attachment_task_notification_entry("toolu_a", command_mode="prompt"),
                ],
                True,
            ),
            (
                [
                    _user_async_launched_entry("toolu_a"),
                    _attachment_task_notification_entry("toolu_a", sidechain=True),
                ],
                True,
            ),
            (
                [
                    _user_async_launched_entry("toolu_a"),
                    {"type": "attachment", "attachment": "not-a-dict"},
                ],
                True,
            ),
            (
                [
                    _user_async_launched_entry("toolu_a"),
                    {
                        "type": "attachment",
                        "attachment": {"commandMode": "task-notification", "prompt": None},
                    },
                ],
                True,
            ),
        ],
    )
    def test_attachment_task_notification(self, tmp_path: pathlib.Path, pending_entries: list[dict], expected: bool):
        """Claude Code 2.1系以降の新形式（`type=="attachment"`）完了通知から識別子を抽出する処理を検証する。

        旧形式との混在、`commandMode`非対象・`isSidechain`真・防御ガード（dictでない／strでない）を含めて
        境界値・同値分割で網羅する。
        """
        entries: list[dict] = [_user_entry("hello")]
        entries.extend(pending_entries)
        entries.append(_user_entry("続き"))
        entries.append(_assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]))
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "", session_state=None) is expected


def test_unresolved_completion_log_redacts_before_truncating(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_toolkit._hooks import background_tasks_notifications  # pylint: disable=import-outside-toplevel

    recorded: list[dict] = []
    monkeypatch.setattr(
        background_tasks_notifications, "append_stop_log", lambda _session, _decision, context: recorded.append(context)
    )
    detail = "z" * 480 + " https://" + "A" * 45 + "@example.org token=synthetic-secret-0123"
    background_tasks_notifications._log_unresolved_completion("S1", detail)  # pylint: disable=protected-access

    saved = recorded[0]["notification"]
    assert "A" * 10 not in saved
    assert len(saved) <= 500
