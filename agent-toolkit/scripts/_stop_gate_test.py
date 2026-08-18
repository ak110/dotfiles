"""agent-toolkit/scripts/_stop_gate.py のテスト。

公開関数`is_pending_async_work`の振る舞いを境界値・同値分割で網羅する。
常時ログ関数（`append_stop_log`）およびコマンド起動検出関数（`has_command_invocation`）も対象とする。
`<task-id>`要素フォールバック解決の網羅テストは責務分離のため
`_stop_gate_task_id_fallback_test.py`へ分割し、共通ヘルパーは本ファイルから再利用する。
"""

import json
import pathlib
import re
import threading
import time
from typing import Literal

import pytest
from _stop_gate import (
    _describe_pending_background_tasks,
    append_stop_log,
    has_command_invocation,
    has_pending_agent_launches,
    is_pending_async_work,
)
from conftest import _write_transcript


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


def _user_async_launched_entry(
    tool_use_id: str,
    *,
    sidechain: bool = False,
    agent_id: str = "agent-x",
) -> dict:
    """background Agent起動を記録するuserエントリを生成する。

    実transcriptフォーマットに合わせ、`toolUseResult.status == "async_launched"`と
    `message.content`配列内の`tool_result`ブロックを持たせる。
    """
    return {
        "type": "user",
        "isSidechain": sidechain,
        "toolUseResult": {"isAsync": True, "status": "async_launched", "agentId": agent_id},
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


def _assistant_agent_entry(tool_use_id: str, *, sidechain: bool = True) -> dict:
    """Agent呼び出しを記録するassistantエントリを生成する。"""
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": "Agent", "input": {}}],
            "stop_reason": "tool_use",
        },
    }


def _user_agent_launch_marker_entry(tool_use_id: str, *, status: str | None = None) -> dict:
    """起動成功本文を持つsidechainのAgent結果を実記録形状で生成する。"""
    tool_use_result: dict[str, object] = {"isAsync": True, "agentId": "agent-real-shape"}
    if status is not None:
        tool_use_result["status"] = status
    return {
        "type": "user",
        "isSidechain": True,
        "toolUseResult": tool_use_result,
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


def _user_background_bash_entry(tool_use_id: str, *, sidechain: bool = False) -> dict:
    """background Bash起動を記録するuserエントリを生成する。

    実transcriptフォーマットに合わせ、`toolUseResult.backgroundTaskId`を持たせ、
    `message.content`配列内の`tool_result`ブロックに対応`tool_use_id`を含める。
    """
    return {
        "type": "user",
        "isSidechain": sidechain,
        "toolUseResult": {
            "stdout": "",
            "stderr": "",
            "backgroundTaskId": "bash-task-x",
        },
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": "Background command launched"}],
                }
            ],
        },
    }


def _user_task_stop_success_entry(stopped_task_id: str, *, tool_use_id: str = "toolu_stop") -> dict:
    """`TaskStop`の停止成功結果を実記録形状で生成する。"""
    return {
        "type": "user",
        "isSidechain": False,
        "toolUseResult": {
            "message": f"Successfully stopped task: {stopped_task_id} (sleep 60)",
            "task_id": stopped_task_id,
            "task_type": "local_bash",
            "command": "sleep 60",
        },
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": f"Successfully stopped task: {stopped_task_id}",
                    "is_error": False,
                }
            ],
        },
    }


def _assistant_sendmessage_entry(tool_use_id: str, *, sidechain: bool = False) -> dict:
    """SendMessage呼び出しを記録するassistantエントリを生成する。

    `_collect_sendmessage_tool_use_ids`がSendMessage tool_use idを収集するための
    非sidechain assistantエントリを再現する。
    """
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "id": "msg_sm",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": "SendMessage",
                    "input": {"message": "続きを再開してください"},
                }
            ],
            "stop_reason": "tool_use",
        },
    }


def _user_sendmessage_bg_resume_entry(
    tool_use_id: str, *, sidechain: bool = False, text_format: Literal["list", "str"] = "list"
) -> dict:
    """SendMessage背景再開を記録するuserエントリを生成する。

    `text_format="list"`のとき`content`はリスト形式のtextブロック、
    `text_format="str"`のとき`content`は文字列形式で返す。
    `_extract_sendmessage_bg_resume_id`が両形式を正しく処理することを検証するために用いる。
    """
    resume_text = "Agent aecb02dc74cf84e99 resumed from transcript in the background with your message."
    if text_format == "str":
        content: str | list[dict] = resume_text
    else:
        content = [{"type": "text", "text": resume_text}]
    return {
        "type": "user",
        "isSidechain": sidechain,
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                }
            ],
        },
    }


def _user_sendmessage_bg_resume_current_entry(
    tool_use_id: str,
    *,
    sidechain: bool = False,
    resumed_agent_id: object = "aecb02dc74cf84e99",
    resume_text: str | None = None,
) -> dict:
    """現行形式のSendMessage背景再開を記録するuserエントリを生成する。

    `toolUseResult`が`resumedAgentId`と`Resuming agent <id>`形式の`message`を持ち、
    tool_result本文が旧マーカーを含まない実記録の形式を再現する。
    `resumed_agent_id`へ非文字列を渡すと構造化フィールド判定が成立しない検体になり、
    `resume_text`を渡すとtool_result本文を差し替えられる。
    """
    body = resume_text if resume_text is not None else f"Resuming agent {resumed_agent_id}"
    return {
        "type": "user",
        "isSidechain": sidechain,
        "toolUseResult": {
            "success": True,
            "message": f"Resuming agent {resumed_agent_id}",
            "resumedAgentId": resumed_agent_id,
        },
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": body}],
                }
            ],
        },
    }


def _task_notification_body(tool_use_id: str | None, *, task_id: str | None = "task-x", status: str = "completed") -> str:
    """`<task-notification>`要素の本文を組み立てる。

    `tool_use_id`または`task_id`に`None`を渡すと該当要素を省略する。
    `<task-id>`要素経由のフォールバック解決・両者欠落ケースの検証に用いる。
    """
    parts = ["<task-notification>"]
    if task_id is not None:
        parts.append(f"<task-id>{task_id}</task-id>")
    if tool_use_id is not None:
        parts.append(f"<tool-use-id>{tool_use_id}</tool-use-id>")
    parts.append(f"<status>{status}</status><summary>sub agent finished</summary></task-notification>")
    return "".join(parts)


def _user_task_notification_entry(
    tool_use_id: str | None, *, task_id: str | None = "task-x", status: str = "completed"
) -> dict:
    """`<task-notification>`本文を持つuserエントリを生成する（旧形式）。

    `tool_use_id`または`task_id`に`None`を渡すと該当要素を省略できる（`_task_notification_body`参照）。
    """
    notification = _task_notification_body(tool_use_id, task_id=task_id, status=status)
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": notification}],
        },
    }


def _monitor_launch_entries(tool_use_id: str, task_id: str) -> list[dict]:
    """Monitorツール起動と対応結果のエントリを生成する。"""
    return [
        {
            "type": "assistant",
            "isSidechain": False,
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": tool_use_id, "name": "Monitor", "input": {}}],
            },
        },
        {
            "type": "user",
            "isSidechain": False,
            "toolUseResult": {"taskId": task_id, "timeoutMs": 3600000, "persistent": False},
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "Monitor started"}],
            },
        },
    ]


def _user_non_monitor_task_id_entry(tool_use_id: str, task_id: str) -> dict:
    """Monitor以外のツールが`taskId`キーを返すuserエントリを生成する（実transcript調査で確認した衝突ケース）。

    `success`・`updatedFields`・`statusChange`キーを伴う形で観測された、
    Monitor以外の`toolUseResult.taskId`使用例を再現する。対応するassistant tool_useを
    `name == "Monitor"`以外にすることで、キーの存在だけに依存する誤判定が
    再発した場合にテストが失敗するようにする。
    """
    return {
        "type": "user",
        "isSidechain": False,
        "toolUseResult": {"success": True, "taskId": task_id, "updatedFields": [], "statusChange": None},
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "updated"}],
        },
    }


def _attachment_task_notification_entry(
    tool_use_id: str | None,
    *,
    task_id: str | None = "task-x",
    status: str = "completed",
    command_mode: str = "task-notification",
    sidechain: bool = False,
) -> dict:
    """`<task-notification>`本文を持つattachmentエントリを生成する（Claude Code 2.1系以降の新形式）。

    `command_mode`を`task-notification`以外に上書きすると、走査対象から外れる否定ケースを再現できる。
    `tool_use_id`または`task_id`に`None`を渡すと該当要素を省略できる（`_task_notification_body`参照）。
    """
    notification = _task_notification_body(tool_use_id, task_id=task_id, status=status)
    return {
        "type": "attachment",
        "isSidechain": sidechain,
        "attachment": {
            "type": "queued_command",
            "prompt": notification,
            "commandMode": command_mode,
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


_TEXT = "作業途中です。"


def _bash_no_bg() -> dict:
    """非同期でないBash tool_useブロックを返す（最終ターンの末尾を構造的にtool_useで終端するため）。"""
    return {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "echo done"}}


class TestIsPendingAsyncWork:
    """`is_pending_async_work` の判定を網羅するテスト。

    tool_use 種別 × {Agent / ScheduleWakeup / Monitor / Bash背景 / Bash前景 / その他 / なし}
    と未完了background task（Agent・Bash双方）× {なし / 起動のみ / 起動と通知ペア} の
    同値分割で組み合わせを検証する。

    SendMessage背景再開テスト群は以下の観点を網羅する。

    - SendMessage呼び出しと背景再開tool_resultが存在する場合に`True`を返す（誤発動防止のコア）
    - 現行形式（`toolUseResult.resumedAgentId`を持ち本文に旧マーカーを含まない）でも`True`を返し、
      同`tool_use_id`の完了通知で`False`へ相殺される
    - `resumedAgentId`を持たない旧形式は本文マーカー照合へフォールバックして`True`を返す（後方互換）
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
        assert is_pending_async_work(str(t), "") is expected

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
        assert is_pending_async_work(str(t), "") is expected

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
        assert is_pending_async_work(str(t), "") is False

    def test_sidechain_async_launched_is_ignored(self, tmp_path: pathlib.Path):
        """sidechain内の`async_launched`は未完了扱いしない。"""
        entries = [
            _user_entry("hello"),
            _user_async_launched_entry("toolu_a", sidechain=True),
            _user_entry("続き"),
            _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
        ]
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is False

    @pytest.mark.parametrize(
        ("pending_entries", "expected"),
        [
            # 新形式単独で完了通知を受信し、起動と完了が紐付く
            (
                [
                    _user_async_launched_entry("toolu_a"),
                    _attachment_task_notification_entry("toolu_a"),
                ],
                False,
            ),
            # 旧user形式と新attachment形式の混在で双方の完了が完了集合へ追加される
            (
                [
                    _user_async_launched_entry("toolu_old"),
                    _user_async_launched_entry("toolu_new"),
                    _user_task_notification_entry("toolu_old"),
                    _attachment_task_notification_entry("toolu_new"),
                ],
                False,
            ),
            # `commandMode`が`task-notification`以外のattachmentエントリは完了集合へ寄与しない
            (
                [
                    _user_async_launched_entry("toolu_a"),
                    _attachment_task_notification_entry("toolu_a", command_mode="prompt"),
                ],
                True,
            ),
            # `isSidechain`が真のattachmentエントリは完了集合へ寄与しない
            (
                [
                    _user_async_launched_entry("toolu_a"),
                    _attachment_task_notification_entry("toolu_a", sidechain=True),
                ],
                True,
            ),
            # `attachment`がdictでないエントリは防御ガードで無視され、起動が残るため未完了扱い
            (
                [
                    _user_async_launched_entry("toolu_a"),
                    {"type": "attachment", "attachment": "not-a-dict"},
                ],
                True,
            ),
            # `attachment.prompt`が文字列でないエントリは防御ガードで無視される
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
        """Claude Code 2.1系以降の新形式（`type=="attachment"`）完了通知の抽出経路を検証する。

        旧形式との混在、`commandMode`非対象・`isSidechain`真・防御ガード（dictでない／strでない）を含めて
        境界値・同値分割で網羅する。
        """
        entries: list[dict] = [_user_entry("hello")]
        entries.extend(pending_entries)
        entries.append(_user_entry("続き"))
        entries.append(_assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]))
        t = _write_transcript(tmp_path, entries)
        assert is_pending_async_work(str(t), "") is expected

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
        monkeypatch.setattr("_stop_gate.tempfile.gettempdir", lambda: str(tmp_path))
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

    def test_missing_transcript_returns_false(self):
        """transcript が存在しない → False（Stop抑止しない）。"""
        assert is_pending_async_work("/nonexistent/transcript.jsonl", "") is False

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
            assert is_pending_async_work(str(t), "") is False
        finally:
            thread.join()

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
        """`resumedAgentId`を持たない`toolUseResult`では旧マーカー照合へフォールバックする。

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


class TestHasPendingAgentLaunches:
    """`has_pending_agent_launches`の抽出種別を検証する。"""

    def test_returns_true_for_pending_agent(self, tmp_path: pathlib.Path) -> None:
        """背景Agent起動のみが未消化の場合に真を返す。"""
        t = _write_transcript(tmp_path, [_user_async_launched_entry("toolu_agent_pending")])
        assert has_pending_agent_launches(str(t), "sess-agent") is True

    def test_returns_false_for_pending_bash(self, tmp_path: pathlib.Path) -> None:
        """背景Bash起動のみが未消化の場合に偽を返す。"""
        t = _write_transcript(tmp_path, [_user_background_bash_entry("toolu_bash_pending")])
        assert has_pending_agent_launches(str(t), "sess-bash-agent-filter") is False

    def test_returns_false_for_completed_agent(self, tmp_path: pathlib.Path) -> None:
        """背景Agent起動が完了消化済みの場合に偽を返す。"""
        t = _write_transcript(
            tmp_path,
            [
                _user_async_launched_entry("toolu_agent_completed"),
                _user_task_notification_entry("toolu_agent_completed"),
            ],
        )
        assert has_pending_agent_launches(str(t), "sess-agent-completed") is False

    def test_returns_true_for_sendmessage_resume(self, tmp_path: pathlib.Path) -> None:
        """SendMessageによる背景再開が未消化の場合に真を返す。"""
        t = _write_transcript(
            tmp_path,
            [
                _assistant_sendmessage_entry("toolu_sendmessage_pending"),
                _user_sendmessage_bg_resume_entry("toolu_sendmessage_pending"),
            ],
        )
        assert has_pending_agent_launches(str(t), "sess-sendmessage") is True

    def test_returns_true_for_sidechain_launch_marker_without_status(self, tmp_path: pathlib.Path) -> None:
        """sidechainの起動成功本文から未消化の子エージェントを検出する。"""
        t = _write_transcript(
            tmp_path,
            [
                _assistant_agent_entry("toolu_sidechain_pending"),
                _user_agent_launch_marker_entry("toolu_sidechain_pending"),
            ],
        )
        assert has_pending_agent_launches(str(t), "sess-sidechain-pending") is True

    def test_sync_completed_sidechain_launch_marker_is_not_pending(self, tmp_path: pathlib.Path) -> None:
        """同期完了statusを持つ起動成功本文は未消化へ計上しない。"""
        t = _write_transcript(
            tmp_path,
            [
                _assistant_agent_entry("toolu_sidechain_completed"),
                _user_agent_launch_marker_entry("toolu_sidechain_completed", status="completed"),
            ],
        )
        assert has_pending_agent_launches(str(t), "sess-sidechain-completed") is False


class TestDebugOutput:
    """`AGENT_TOOLKIT_STOP_GATE_DEBUG`環境変数によるstderrデバッグ出力の検証。

    Stop hookの誤判定時の原因切り分け手段として、判定根拠を1行出力する機能を確認する。
    同値分割: 環境変数値 × 残差有無 で代表ケースを抽出する。
    """

    @pytest.fixture(autouse=True)
    def _clear_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """環境変数の事前削除でテスト間の状態混入を防ぐ。"""
        monkeypatch.delenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", raising=False)

    def test_no_output_when_env_unset(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """環境変数が未設定の場合はstderr出力なし。"""
        t = _write_transcript(tmp_path, [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _TEXT}])])
        is_pending_async_work(str(t), "")
        captured = capsys.readouterr()
        assert captured.err == ""

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "True"])
    def test_output_when_env_truthy(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """環境変数が真値（小文字一致）の場合はstderrへ1行出力する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", value)
        t = _write_transcript(tmp_path, [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _TEXT}])])
        is_pending_async_work(str(t), "")
        captured = capsys.readouterr()
        assert "_stop_gate result=False" in captured.err
        assert "last_tool=-" in captured.err
        assert "launched=0" in captured.err
        assert "pending=0" in captured.err
        assert "pending_ids=-" in captured.err

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
    def test_no_output_when_env_falsy(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """環境変数が偽値の場合はstderr出力なし。"""
        monkeypatch.setenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", value)
        t = _write_transcript(tmp_path, [_user_entry("hello"), _assistant_entry([{"type": "text", "text": _TEXT}])])
        is_pending_async_work(str(t), "")
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_output_with_pending_remainder(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """残差がある場合は`result=True`・残差件数・残差IDを出力する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", "1")
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _user_async_launched_entry("toolu_bg1"),
                _user_async_launched_entry("toolu_bg2"),
                _user_task_notification_entry("toolu_bg1"),
                _user_entry("続き"),
                _assistant_entry([{"type": "text", "text": _TEXT}, _bash_no_bg()]),
            ],
        )
        is_pending_async_work(str(t), "")
        captured = capsys.readouterr()
        assert "_stop_gate result=True" in captured.err
        assert "last_tool=Bash(bg=False)" in captured.err
        assert "launched=2" in captured.err
        assert "pending=1" in captured.err
        assert "pending_ids=toolu_bg2" in captured.err

    def test_output_with_background_bash(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """直前tool_useが背景Bash（`run_in_background=true`）の場合は`Bash(bg=True)`を出力する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", "1")
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _TEXT},
                        {
                            "type": "tool_use",
                            "id": "x",
                            "name": "Bash",
                            "input": {"command": "echo bg", "run_in_background": True},
                        },
                    ]
                ),
            ],
        )
        is_pending_async_work(str(t), "")
        captured = capsys.readouterr()
        assert "_stop_gate result=True" in captured.err
        assert "last_tool=Bash(bg=True)" in captured.err

    def test_output_with_async_wait_tool(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """直前tool_useが非同期待機系の場合は当該tool名を出力する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_STOP_GATE_DEBUG", "1")
        t = _write_transcript(
            tmp_path,
            [
                _user_entry("hello"),
                _assistant_entry(
                    [
                        {"type": "text", "text": _TEXT},
                        {"type": "tool_use", "id": "x", "name": "Agent", "input": {}},
                    ]
                ),
            ],
        )
        is_pending_async_work(str(t), "")
        captured = capsys.readouterr()
        assert "_stop_gate result=True" in captured.err
        assert "last_tool=Agent" in captured.err


class TestMonitorTaskNotificationSuppression:
    """Monitor由来の`task_notification_unresolved`過検出が抑止されることを検証する。"""

    def test_monitor_notification_does_not_log_unresolved(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monitorのtool_useと突合できる通知はtask_notification_unresolvedをログしない。"""
        monkeypatch.setattr("_stop_gate.tempfile.gettempdir", lambda: str(tmp_path))
        entries = [
            *_monitor_launch_entries("toolu_monitor1", "monitor-a"),
            _user_task_notification_entry(None, task_id="monitor-a"),
        ]
        t = _write_transcript(tmp_path, entries)
        _describe_pending_background_tasks(str(t), "sess-monitor")
        log_path = tmp_path / "claude-agent-toolkit-stop-sess-monitor.log"
        if log_path.exists():
            assert "task_notification_unresolved" not in log_path.read_text(encoding="utf-8")

    def test_non_monitor_task_id_collision_still_logs_unresolved(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monitor以外の`taskId`値では未解決通知をログする。"""
        monkeypatch.setattr("_stop_gate.tempfile.gettempdir", lambda: str(tmp_path))
        entries = [
            _user_non_monitor_task_id_entry("toolu_taskupdate1", "1"),
            _user_task_notification_entry(None, task_id="1"),
        ]
        t = _write_transcript(tmp_path, entries)
        _describe_pending_background_tasks(str(t), "sess-collision")
        log_path = tmp_path / "claude-agent-toolkit-stop-sess-collision.log"
        assert "task_notification_unresolved" in log_path.read_text(encoding="utf-8")

    def test_same_value_shared_by_monitor_and_non_monitor_still_logs_unresolved(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monitor由来か一意に決まらない`taskId`値では未解決通知をログする。"""
        monkeypatch.setattr("_stop_gate.tempfile.gettempdir", lambda: str(tmp_path))
        entries = [
            *_monitor_launch_entries("toolu_monitor1", "shared-id"),
            _user_non_monitor_task_id_entry("toolu_taskupdate1", "shared-id"),
            _user_task_notification_entry(None, task_id="shared-id"),
        ]
        t = _write_transcript(tmp_path, entries)
        _describe_pending_background_tasks(str(t), "sess-shared-collision")
        log_path = tmp_path / "claude-agent-toolkit-stop-sess-shared-collision.log"
        assert "task_notification_unresolved" in log_path.read_text(encoding="utf-8")

    def test_unknown_notification_still_logs_unresolved(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Monitor由来と識別できない未知の通知は引き続きtask_notification_unresolvedをログする。"""
        monkeypatch.setattr("_stop_gate.tempfile.gettempdir", lambda: str(tmp_path))
        entries = [_user_task_notification_entry(None, task_id="unknown-task")]
        t = _write_transcript(tmp_path, entries)
        _describe_pending_background_tasks(str(t), "sess-unknown")
        log_path = tmp_path / "claude-agent-toolkit-stop-sess-unknown.log"
        assert "task_notification_unresolved" in log_path.read_text(encoding="utf-8")


class TestAppendStopLog:
    """`append_stop_log`のログ追記挙動を検証する。"""

    def test_appends_one_line_with_decision_and_context(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """1行追記され、decisionとcontextのkey-valueが整形されて含まれる。"""
        monkeypatch.setattr("_stop_gate.tempfile.gettempdir", lambda: str(tmp_path))
        append_stop_log("session-x", "approve_pending_async", {"last_tool": "Agent", "pending": 0})
        path = tmp_path / "claude-agent-toolkit-stop-session-x.log"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert "decision=approve_pending_async" in lines[0]
        assert "last_tool=Agent" in lines[0]
        assert "pending=0" in lines[0]

    def test_skips_when_session_id_empty(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """session_idが空の場合はログファイルを作成しない。"""
        monkeypatch.setattr("_stop_gate.tempfile.gettempdir", lambda: str(tmp_path))
        append_stop_log("", "approve_pending_async", {})
        assert not list(tmp_path.iterdir())

    def test_multiple_calls_append(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """複数回の呼び出しが1行ずつ追記される。"""
        monkeypatch.setattr("_stop_gate.tempfile.gettempdir", lambda: str(tmp_path))
        append_stop_log("session-y", "approve_no_pyfltr", {})
        append_stop_log("session-y", "block_session_review", {})
        path = tmp_path / "claude-agent-toolkit-stop-session-y.log"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert "decision=approve_no_pyfltr" in lines[0]
        assert "decision=block_session_review" in lines[1]

    def test_rotates_when_max_bytes_exceeded(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`max_bytes`を小さくすると先行ログが`.log.1`へローテートされる。"""
        monkeypatch.setattr("_stop_gate.tempfile.gettempdir", lambda: str(tmp_path))
        append_stop_log("session-z", "first", {})
        # 先行ログが上限を超えた状態で追記するとローテーションが発生する。
        append_stop_log("session-z", "second", {}, max_bytes=10)
        path = tmp_path / "claude-agent-toolkit-stop-session-z.log"
        rotated = tmp_path / "claude-agent-toolkit-stop-session-z.log.1"
        assert rotated.exists()
        assert "decision=first" in rotated.read_text(encoding="utf-8")
        assert "decision=second" in path.read_text(encoding="utf-8")


class TestHasCommandInvocation:
    """`has_command_invocation`のtranscript走査を検証する。"""

    def test_matches_user_command(self, tmp_path: pathlib.Path) -> None:
        """ユーザーターンに指定パターンがあれば真を返す。"""
        transcript = tmp_path / "t.jsonl"
        entry = {
            "type": "user",
            "message": {"content": "<command-name>/foo</command-name>"},
        }
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        assert has_command_invocation(str(transcript), re.compile(r"<command-name>/foo</command-name>"))

    def test_no_match_returns_false(self, tmp_path: pathlib.Path) -> None:
        """パターンに一致しなければ偽。"""
        transcript = tmp_path / "t.jsonl"
        entry = {"type": "user", "message": {"content": "no marker"}}
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        assert not has_command_invocation(str(transcript), re.compile(r"<command-name>/foo</command-name>"))

    def test_tool_result_content_ignored(self, tmp_path: pathlib.Path) -> None:
        """ツール結果ブロック内にパターンのリテラルがあっても偽。

        検出パターンのリテラルを含むソースコードを閲覧した場合、その内容がツール結果として
        transcriptへ記録される。これを利用者のコマンド起動と判定してはならない。
        """
        transcript = tmp_path / "t.jsonl"
        entry = {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "<command-name>/foo</command-name>",
                    }
                ]
            },
        }
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        assert not has_command_invocation(str(transcript), re.compile(r"<command-name>/foo</command-name>"))

    def test_system_notification_text_block_ignored(self, tmp_path: pathlib.Path) -> None:
        """システム生成通知のtextブロック内にパターンのリテラルがあっても偽。"""
        transcript = tmp_path / "t.jsonl"
        entry = {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "<task-notification><command-name>/foo</command-name></task-notification>",
                    }
                ]
            },
        }
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        assert not has_command_invocation(str(transcript), re.compile(r"<command-name>/foo</command-name>"))

    def test_string_content_task_notification_ignored(self, tmp_path: pathlib.Path) -> None:
        """文字列contentのシステム生成通知内にパターンのリテラルがあっても偽。

        task-notificationはcontentが文字列のエントリとしても記録されるため、
        文字列content限定だけでは通知本文への誤一致を防げない。
        """
        transcript = tmp_path / "t.jsonl"
        entry = {
            "type": "user",
            "message": {
                "content": (
                    "<task-notification>\n<task-id>abc</task-id>\n"
                    "<summary><command-name>/foo</command-name></summary>\n</task-notification>"
                )
            },
        }
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        assert not has_command_invocation(str(transcript), re.compile(r"<command-name>/foo</command-name>"))

    def test_string_content_teammate_message_ignored(self, tmp_path: pathlib.Path) -> None:
        """他セッションからの受信メッセージ内にパターンのリテラルがあっても偽。"""
        transcript = tmp_path / "t.jsonl"
        entry = {
            "type": "user",
            "message": {
                "content": (
                    "Another Claude session sent a message:\n"
                    '<teammate-message teammate_id="x" summary="調査結果">\n'
                    "検出源は`<command-name>/foo</command-name>`の走査\n</teammate-message>"
                )
            },
        }
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        assert not has_command_invocation(str(transcript), re.compile(r"<command-name>/foo</command-name>"))

    def test_command_invocation_after_task_notification_detected(self, tmp_path: pathlib.Path) -> None:
        """システム生成通知を除いた残りに起動痕跡があれば真を返す。"""
        transcript = tmp_path / "t.jsonl"
        entry = {
            "type": "user",
            "message": {
                "content": ("<task-notification><task-id>abc</task-id></task-notification>\n<command-name>/foo</command-name>")
            },
        }
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        assert has_command_invocation(str(transcript), re.compile(r"<command-name>/foo</command-name>"))

    def test_sidechain_ignored(self, tmp_path: pathlib.Path) -> None:
        """sidechainのユーザーエントリは対象外。"""
        transcript = tmp_path / "t.jsonl"
        entry = {
            "type": "user",
            "isSidechain": True,
            "message": {"content": "<command-name>/foo</command-name>"},
        }
        transcript.write_text(json.dumps(entry) + "\n", encoding="utf-8")
        assert not has_command_invocation(str(transcript), re.compile(r"<command-name>/foo</command-name>"))
