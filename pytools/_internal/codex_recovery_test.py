"""公開RPCの観測順序と、復旧時に維持する会話状態の契約を検証する。"""

from typing import Any

import pytest

from pytools._internal import codex_recovery


def _select(recovery: codex_recovery.ConversationRecovery, identifier: str = "thread") -> None:
    # codex_test_helpers.pyのpeerと同じthread/start応答形式を使う。
    recovery.observe_client({"id": 1, "method": "thread/start", "params": {}})
    recovery.observe_server(
        {
            "id": 1,
            "result": {
                "thread": {"id": identifier, "cwd": "/work"},
                "approvalPolicy": "never",
                "sandbox": {"type": "dangerFullAccess"},
            },
        }
    )


def _notify(recovery: codex_recovery.ConversationRecovery, method: str, turn: str = "turn") -> None:
    recovery.observe_server({"method": method, "params": {"threadId": "thread", "turn": {"id": turn}}})


def _assert_conversation(recovery: codex_recovery.ConversationRecovery, expected: codex_recovery.ConversationState) -> None:
    # 観測ごとに読み直し、所有者の更新をまたいだ型の絞込みをテスト側へ持ち越さない。
    assert recovery.conversation == expected


def test_observed_turn_states_preserve_selection_and_explicit_stop() -> None:
    """別turnの完了と中断後の遅延通知は、選択した仕事を実行中へ戻さない。"""
    recovery = codex_recovery.ConversationRecovery()
    initial = recovery.conversation
    assert isinstance(initial, codex_recovery.UnselectedConversation)
    _select(recovery)
    _assert_conversation(recovery, codex_recovery.SelectedConversation("thread", "/work"))
    _notify(recovery, "turn/started")
    original = recovery.conversation
    assert original == codex_recovery.RunningTurn("thread", "/work", "turn")
    _notify(recovery, "turn/completed", "other")
    _assert_conversation(recovery, original)
    _notify(recovery, "turn/completed")
    _assert_conversation(recovery, codex_recovery.CompletedTurn("thread", "/work", "turn"))
    _notify(recovery, "turn/started", "next")
    recovery.observe_client({"method": "turn/interrupt", "params": {"threadId": "thread"}})
    _notify(recovery, "turn/started", "late")
    _notify(recovery, "turn/completed", "next")
    _assert_conversation(recovery, codex_recovery.InterruptedConversation("thread", "/work", "next"))
    assert isinstance(original, codex_recovery.RunningTurn)
    recovery.restore_running_turn(original)
    interrupted = recovery.conversation
    assert interrupted == codex_recovery.InterruptedConversation("thread", "/work", "next")
    recovery.observe_client({"method": "turn/start", "params": {"threadId": "thread"}})
    _notify(recovery, "turn/started", "new")
    _assert_conversation(recovery, codex_recovery.RunningTurn("thread", "/work", "new"))


@pytest.mark.parametrize("stop", ["turn/interrupt", "thread/unsubscribe"])
def test_stop_before_first_turn_requires_new_start(stop: str) -> None:
    """turn通知前の中断も保持し、ユーザーの新しい開始で解除する。"""
    recovery = codex_recovery.ConversationRecovery()
    _select(recovery)
    recovery.observe_client({"method": stop, "params": {"threadId": "thread"}})
    _notify(recovery, "turn/started")
    _assert_conversation(recovery, codex_recovery.InterruptedConversation("thread", "/work", None))
    recovery.observe_client({"method": "turn/start", "params": {"threadId": "thread"}})
    assert isinstance(recovery.conversation, codex_recovery.SelectedConversation)
    _notify(recovery, "turn/started")
    assert isinstance(recovery.conversation, codex_recovery.RunningTurn)


@pytest.mark.parametrize(
    ("status", "latest", "writer", "continues"),
    [
        ("inProgress", "turn", "idle", True),
        ("inProgress", "other", "idle", False),
        ("completed", "turn", "idle", False),
        ("failed", "turn", "idle", False),
        ("inProgress", "turn", "active", False),
    ],
)
def test_continuation_checks_original_turn_and_writer(status: str, latest: str, writer: str, continues: bool) -> None:
    """元turnの未完了とwriter不在だけが自動続行を許す。"""
    recovery = codex_recovery.ConversationRecovery()
    _select(recovery)
    _notify(recovery, "turn/started")
    result = recovery.continuation_parameters(
        {"id": "thread", "cwd": "/work", "status": {"type": writer}}, [{"id": latest, "status": status}]
    )
    assert (result is not None) == continues
    if result is not None:
        assert result["threadId"] == "thread" and result["cwd"] == "/work"
        assert result["approvalPolicy"] == "never"
    if status in {"completed", "failed"}:
        assert isinstance(recovery.conversation, codex_recovery.CompletedTurn)


@pytest.mark.parametrize("thread", [{"id": "other", "cwd": "/work"}, {"id": "thread", "cwd": "/other"}])
def test_continuation_rejects_different_conversation(thread: dict[str, Any]) -> None:
    """復旧応答の会話とcwdの照合を維持する。"""
    recovery = codex_recovery.ConversationRecovery()
    _select(recovery)
    _notify(recovery, "turn/started")
    with pytest.raises(RuntimeError, match="一致しない"):
        recovery.continuation_parameters(thread, [{"id": "turn"}])
