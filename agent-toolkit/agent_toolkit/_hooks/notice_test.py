"""`_hook_notice`のblock通知整形契約を検証する。"""

import pytest

from agent_toolkit._hooks.notice import (
    block_formatter,
    consume_warning_blocks,
    formatter,
    set_warning_session_id,
    warning_formatter,
)
from agent_toolkit._hooks.session_state import read_state
from agent_toolkit._testing.helpers import auto_message_opening_attributes


@pytest.mark.parametrize("fix", ["", " ", "\t"])
def test_block_formatter_rejects_empty_fix(fix: str) -> None:
    """空文字列または空白文字だけの解消手段を拒否する。"""
    format_block = block_formatter("test/hook")

    with pytest.raises(ValueError):
        format_block("blocked", fix=fix)


def test_block_formatter_adds_fix_tag_and_suffix() -> None:
    """blockタグ、次の操作の行、共通サフィックスを同時に付与する。"""
    format_block = block_formatter("test/hook")

    message = format_block("blocked", fix="retry")

    assert auto_message_opening_attributes(message) == {"source": "test/hook", "kind": "block"}
    assert "\nblocked\n次の操作: retry\n" in message
    assert message.endswith("</atk-auto>")


def test_warning_formatter_requests_block_from_second_notice(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """同じ原因の2件目以降を累積件数と解消手段付きのblockへ移す。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    format_warning = warning_formatter("test/hook")

    format_warning(
        "warning", fix="retry", cause="same-cause", session_id="session-1", removable_cause=True, escalate_on_repeat=True
    )
    first_blocks = consume_warning_blocks()
    format_warning(
        "warning", fix="retry", cause="same-cause", session_id="session-1", removable_cause=True, escalate_on_repeat=True
    )
    second_blocks = consume_warning_blocks()

    assert not first_blocks
    assert len(second_blocks) == 1
    assert "この通知は同一セッションで2件目である" in second_blocks[0]
    assert "次の操作: retry" in second_blocks[0]


@pytest.mark.parametrize("fix", ["", " ", "\t"])
def test_warning_formatters_reject_missing_or_empty_fix(fix: str) -> None:
    """warn区分の整形関数は解消手段を省いた呼び出しと空の解消手段を拒否する。"""
    format_warning = warning_formatter("test/hook")
    format_notice = formatter("test/hook")

    with pytest.raises(ValueError):
        format_warning("警告本文", fix=fix, cause="empty", session_id="session-1", removable_cause=False)
    with pytest.raises(ValueError):
        format_notice("警告本文", tag="warn", fix=fix, removable_cause=False)
    with pytest.raises(ValueError):
        format_notice("警告本文", tag="warn", removable_cause=False)
    with pytest.raises(TypeError):
        # 必須引数の欠落そのものを検証するため、静的検査の指摘を抑止する。
        format_warning(  # type: ignore[call-arg]  # pylint: disable=missing-kwoa
            "警告本文", cause="missing", session_id="session-1", removable_cause=False
        )


def test_repeated_warning_keeps_next_action_line(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """同じ原因の2件目以降と昇格したblockにも、本文の表記によらず次の操作の行が残る。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    set_warning_session_id("session-1")
    format_notice = formatter("test/hook")

    def _check_same() -> str:
        # 原因識別子は呼び出し元の関数名から決まるため、同じ関数から繰り返し呼ぶ。
        return format_notice(
            "警告本文\n対処は本文中の別の表記で書かれている。",
            tag="warn",
            fix="対象を直して再実行する。",
            removable_cause=True,
            escalate_on_repeat=True,
        )

    messages = [_check_same() for _ in range(3)]
    blocks = consume_warning_blocks()

    assert all("\n次の操作: 対象を直して再実行する。\n" in message for message in messages)
    assert "この通知は同一セッションで2件目である" in messages[1]
    assert "対処は本文中の別の表記" not in messages[1]
    assert len(blocks) == 2
    assert all("\n次の操作: 対象を直して再実行する。\n" in block for block in blocks)


def test_warning_formatter_separates_causes_and_sessions(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """原因識別子とセッションIDが異なる警告を別々に数える。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    format_warning = warning_formatter("test/hook")

    for _ in range(3):
        format_warning("warning", fix="retry", cause="first-cause", session_id="session-1", removable_cause=True)
        consume_warning_blocks()
    other_cause = format_warning("warning", fix="retry", cause="second-cause", session_id="session-1", removable_cause=True)
    other_session = format_warning("warning", fix="retry", cause="first-cause", session_id="session-2", removable_cause=True)

    assert "この通知は同一セッションで" not in other_cause
    assert "この通知は同一セッションで" not in other_session
    assert read_state("session-1")["warn_notice_counts"] == {
        "test/hook|first-cause": 3,
        "test/hook|first-cause|removable": 3,
        "test/hook|second-cause": 1,
        "test/hook|second-cause|removable": 1,
    }


def test_warning_formatter_omits_repeat_note_for_irremovable_cause(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """除去できない原因でも反復本文を短くし、遮断はしない。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    format_warning = warning_formatter("test/hook")

    messages = [
        format_warning("warning", fix="retry", cause="fixed-cause", session_id="session-1", removable_cause=False)
        for _ in range(3)
    ]
    removable_messages = [
        format_warning(
            "warning", fix="retry", cause="other-cause", session_id="session-1", removable_cause=True, escalate_on_repeat=True
        )
        for _ in range(3)
    ]

    assert "この通知は同一セッションで" not in messages[0]
    assert all("この通知は同一セッションで" in message for message in messages[1:])
    assert "この通知は同一セッションで" not in removable_messages[0]
    assert all("この通知は同一セッションで" in message for message in removable_messages[1:])
    assert len(consume_warning_blocks()) == 2
    assert read_state("session-1")["warn_notice_counts"]["test/hook|fixed-cause"] == 3


def test_removable_warning_does_not_escalate_without_explicit_choice(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """是正可能な警告でも反復遮断は既定で無効にする。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    consume_warning_blocks()
    format_warning = warning_formatter("test/hook")

    messages = [
        format_warning("警告本文", fix="retry", cause="same", session_id="session-1", removable_cause=True) for _ in range(2)
    ]

    assert "2件目" in messages[1]
    assert not consume_warning_blocks()


def test_formatter_rejects_warn_without_removability() -> None:
    """warn通知の除去可能性を暗黙に決めない。"""
    format_notice = formatter("test/hook")

    with pytest.raises(ValueError):
        format_notice("warning", tag="warn", fix="retry")
