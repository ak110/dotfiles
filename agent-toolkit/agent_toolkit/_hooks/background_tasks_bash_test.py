"""background_tasks_bashの契約テスト。"""

import pathlib

import pytest

from agent_toolkit._hooks.background_tasks import is_pending_async_work
from agent_toolkit._testing.background_tasks_support import (
    _assistant_entry,
    _bash_no_bg,
    _user_async_launched_entry,
    _user_background_bash_entry,
    _user_entry,
    _user_foreground_agent_entry,
    _user_task_notification_entry,
    _user_task_stop_success_entry,
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

    @pytest.mark.parametrize(
        ("pending_entries", "expected"),
        [
            ([_user_background_bash_entry("toolu_bash1")], True),
            (
                [
                    _user_background_bash_entry("toolu_bash1"),
                    _user_task_notification_entry("toolu_bash1"),
                ],
                False,
            ),
            (
                [
                    _user_async_launched_entry("toolu_ag1"),
                    _user_background_bash_entry("toolu_bash1"),
                    _user_task_notification_entry("toolu_ag1"),
                ],
                True,
            ),
        ],
    )
    def test_pending_background_bash(self, tmp_path: pathlib.Path, pending_entries: list[dict], expected: bool):
        """background Bashの起動・完了通知の有無で判定が決まることを検証する。

        3ケース目はAgent完了とBash未完了の混在で、Bash側のみ残ることを確認する。
        """
        entries: list[dict] = [_user_entry("hello")]
        entries.extend(pending_entries)
        entries.append(_user_entry("続き"))
        entries.append(_assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]))
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is expected

    def test_sidechain_background_bash_is_ignored(self, tmp_path: pathlib.Path):
        """sidechain内の背景Bash起動は未完了扱いしない。"""
        entries = [
            _user_entry("hello"),
            _user_background_bash_entry("toolu_bash1", sidechain=True),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_task_stop_success_completes_background_bash(self, tmp_path: pathlib.Path) -> None:
        """停止成功結果だけが残る場合は背景Bashを完了済みとして扱う。"""
        entries = [
            _user_background_bash_entry("toolu_bash1"),
            _user_task_stop_success_entry("bash-task-x"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_task_stop_for_other_id_keeps_background_bash_pending(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """別識別子の停止結果では対象の背景Bashを未完了のまま保つ。"""
        monkeypatch.setattr("agent_toolkit._hooks.stop_session.tempfile.gettempdir", lambda: str(tmp_path))
        entries = [
            _user_background_bash_entry("toolu_bash1"),
            _user_task_stop_success_entry("bash-task-other"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "sess-stop-unresolved") is True
        log_path = tmp_path / "claude-agent-toolkit-stop-sess-stop-unresolved.log"
        assert "decision=task_notification_unresolved" in log_path.read_text(encoding="utf-8")

    def test_foreground_agent_is_not_tracked(self, tmp_path: pathlib.Path):
        """foreground Agent（`toolUseResult.status == "completed"`）は未完了扱いしない。"""
        entries = [
            _user_entry("hello"),
            _user_foreground_agent_entry("toolu_a"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False
