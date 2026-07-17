"""agent-toolkit/scripts/_stop_gate.py のテスト（`name`付きteammate完了突合）。

`name`付きteammate並列起動と`<teammate-message>`要素経由の完了通知（idle_notification）の
突合ロジックを検証する。基幹テストは`_stop_gate_test.py`に、共通ヘルパーは同ファイルから再利用する。
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _stop_gate import is_pending_async_work  # noqa: E402  # pylint: disable=wrong-import-position
from _stop_gate_test import (  # noqa: E402  # pylint: disable=wrong-import-position
    _TEXT,
    _assistant_entry,
    _bash_no_bg,
    _user_async_launched_entry,
    _user_entry,
    _write_transcript,
)


def _assistant_named_agent_entry(tool_use_id: str, name: str, *, sidechain: bool = False) -> dict:
    """`name`付きAgent tool_useを記録するassistantエントリを生成する。"""
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "id": "msg_named",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": "Agent",
                    "input": {"subagent_type": "Explore", "name": name, "run_in_background": True, "prompt": "x"},
                }
            ],
            "stop_reason": "tool_use",
        },
    }


def _user_teammate_spawned_entry(tool_use_id: str, name: str, *, sidechain: bool = False) -> dict:
    """`name`付きteammate並列起動を記録するuserエントリ（`toolUseResult.status == "teammate_spawned"`）。"""
    return {
        "type": "user",
        "isSidechain": sidechain,
        "toolUseResult": {"status": "teammate_spawned", "teammate_id": name},
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "Spawned successfully."}],
        },
    }


def _user_teammate_idle_notification_entry(name: str, *, idle_reason: str = "available", sidechain: bool = False) -> dict:
    """teammateからのidle_notificationを含むuserエントリ（`content`は文字列形式の`<teammate-message>`要素）。"""
    body = f'{{"type":"idle_notification","from":"{name}","idleReason":"{idle_reason}"}}'
    text = f'<teammate-message teammate_id="{name}" color="blue">\n{body}\n</teammate-message>\n'
    return {"type": "user", "isSidechain": sidechain, "message": {"role": "user", "content": text}}


def _assistant_sendmessage_to_entry(tool_use_id: str, teammate_name: str) -> dict:
    """teammate名を`input.to`に持つSendMessage tool_useエントリを生成する。"""
    return {
        "type": "assistant",
        "isSidechain": False,
        "message": {
            "id": "msg_sm",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": "SendMessage",
                    "input": {"to": teammate_name, "message": "続きを再開してください"},
                }
            ],
            "stop_reason": "tool_use",
        },
    }


class TestNamedTeammate:
    """`name`付きteammate並列起動と`<teammate-message>`経由の完了通知を検証する。

    観点:
    - teammate_spawned起動と対応するidle_notification(available)完了で相殺され`False`
    - 起動のみで完了通知未到達なら`True`（擬似pending抑止確認）
    - `idleReason`が`available`以外（`waiting_for_input`等）は完了扱いしない
    - name→tool_use_idマップが未登録teammateからのidle_notificationは完了集合へ寄与しない
    - sidechain assistantのname付きAgent tool_useはマップへ寄与しない
    - 複数teammateの並列起動と部分完了で残差が正しく判定される
    """

    def test_named_teammate_launched_and_completed(self, tmp_path: pathlib.Path):
        """teammate_spawned起動と`available`のidle_notificationが揃えば相殺されて`False`。"""
        entries = [
            _user_entry("hello"),
            _assistant_named_agent_entry("toolu_tm1", "explore-boot"),
            _user_teammate_spawned_entry("toolu_tm1", "explore-boot"),
            _user_teammate_idle_notification_entry("explore-boot"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_named_teammate_launched_without_completion(self, tmp_path: pathlib.Path):
        """teammate_spawned起動があり完了通知が届いていなければ`True`。"""
        entries = [
            _user_entry("hello"),
            _assistant_named_agent_entry("toolu_tm1", "explore-boot"),
            _user_teammate_spawned_entry("toolu_tm1", "explore-boot"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is True

    def test_named_teammate_idle_reason_not_available(self, tmp_path: pathlib.Path):
        """`idleReason`が`available`以外のidle_notificationは完了扱いしない。"""
        entries = [
            _user_entry("hello"),
            _assistant_named_agent_entry("toolu_tm1", "explore-boot"),
            _user_teammate_spawned_entry("toolu_tm1", "explore-boot"),
            _user_teammate_idle_notification_entry("explore-boot", idle_reason="waiting_for_input"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is True

    def test_teammate_idle_notification_without_matching_launch(self, tmp_path: pathlib.Path):
        """name→tool_use_idマップに登録されていないteammate名のidle_notificationは無視される。

        誤検知防止の観点。マップ解決が空集合ならcompletedへは何も追加されない。
        `_bash_no_bg`は最終ターン末尾を`Bash(bg=False)`で終端しつつ起動集合を空に保つ。
        """
        entries = [
            _user_entry("hello"),
            # 別teammate（マップ未登録）からのidle_notificationのみ
            _user_teammate_idle_notification_entry("unknown-teammate"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        # 起動集合が空・完了集合が空なので残差なし → 非同期待機なし → False
        assert is_pending_async_work(str(t), "") is False

    def test_sidechain_named_agent_launch_ignored(self, tmp_path: pathlib.Path):
        """sidechain assistantの`name`付きAgent tool_useはname→tool_use_idマップへ寄与しない。

        sidechain内launchのteammate_spawnedも起動集合へ寄与しないため、
        idle_notification到着でも完了集合は空のままとなり、他起動があれば残差として残る。
        """
        entries = [
            _user_entry("hello"),
            # sidechain assistantのname付きAgent（マップ未登録扱い）
            _assistant_named_agent_entry("toolu_tm1", "explore-boot", sidechain=True),
            _user_teammate_spawned_entry("toolu_tm1", "explore-boot", sidechain=True),
            # 本流でtaskを起動
            _user_async_launched_entry("toolu_bg1"),
            _user_teammate_idle_notification_entry("explore-boot"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        # 本流async_launchedが完了せず残る
        assert is_pending_async_work(str(t), "") is True

    def test_multiple_named_teammates_partial_completion(self, tmp_path: pathlib.Path):
        """複数teammateを並列起動し1件だけ完了なら残差1件で`True`を返す。"""
        entries = [
            _user_entry("hello"),
            _assistant_named_agent_entry("toolu_tm1", "explore-a"),
            _user_teammate_spawned_entry("toolu_tm1", "explore-a"),
            _assistant_named_agent_entry("toolu_tm2", "explore-b"),
            _user_teammate_spawned_entry("toolu_tm2", "explore-b"),
            _user_teammate_idle_notification_entry("explore-a"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is True

    def test_idle_then_sendmessage_reactivates_pending(self, tmp_path: pathlib.Path):
        """idle到達後の同名teammate宛SendMessageでpendingへ復帰する。"""
        entries = [
            _user_entry("hello"),
            _assistant_named_agent_entry("toolu_tm1", "explore-boot"),
            _user_teammate_spawned_entry("toolu_tm1", "explore-boot"),
            _user_teammate_idle_notification_entry("explore-boot"),
            _assistant_sendmessage_to_entry("toolu_sm1", "explore-boot"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is True

    def test_idle_then_sendmessage_then_idle_completes_again(self, tmp_path: pathlib.Path):
        """SendMessageによる再委譲後の再度のidle到達でcompletedへ戻る。"""
        entries = [
            _user_entry("hello"),
            _assistant_named_agent_entry("toolu_tm1", "explore-boot"),
            _user_teammate_spawned_entry("toolu_tm1", "explore-boot"),
            _user_teammate_idle_notification_entry("explore-boot"),
            _assistant_sendmessage_to_entry("toolu_sm1", "explore-boot"),
            _user_teammate_idle_notification_entry("explore-boot"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    def test_sendmessage_to_other_teammate_does_not_reactivate(self, tmp_path: pathlib.Path):
        """idle到達後の別teammate宛SendMessageはcompleted状態へ影響しない。"""
        entries = [
            _user_entry("hello"),
            _assistant_named_agent_entry("toolu_tm1", "explore-boot"),
            _user_teammate_spawned_entry("toolu_tm1", "explore-boot"),
            _user_teammate_idle_notification_entry("explore-boot"),
            _assistant_sendmessage_to_entry("toolu_sm1", "explore-other"),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False
