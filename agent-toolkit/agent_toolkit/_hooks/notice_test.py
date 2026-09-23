"""`_hook_notice`のblock通知整形契約を検証する。"""

import pytest

from agent_toolkit._hooks.notice import block_formatter, consume_warning_blocks, formatter, warning_formatter
from agent_toolkit._hooks.session_state import read_state


@pytest.mark.parametrize("fix", ["", " ", "\t"])
def test_block_formatter_rejects_empty_fix(fix: str) -> None:
    """空文字列または空白文字だけの解消手段を拒否する。"""
    format_block = block_formatter("test/hook")

    with pytest.raises(ValueError):
        format_block("blocked", fix=fix)


def test_block_formatter_adds_fix_tag_and_suffix() -> None:
    """blockタグ、Fix行、共通サフィックスを同時に付与する。"""
    format_block = block_formatter("test/hook")

    message = format_block("blocked", fix="retry")

    assert message.startswith('<agent-toolkit-auto-inserted source="test/hook" kind="block" nonce="')
    assert "\nblocked\nFix: retry\n" in message
    assert message.endswith("</agent-toolkit-auto-inserted>")


def test_warning_formatter_requests_block_from_second_notice(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """同じ原因の2件目以降を累積件数と解消手段付きのblockへ移す。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    format_warning = warning_formatter("test/hook")

    format_warning(
        "warning\n対処: retry", cause="same-cause", session_id="session-1", removable_cause=True, escalate_on_repeat=True
    )
    first_blocks = consume_warning_blocks()
    format_warning(
        "warning\n対処: retry", cause="same-cause", session_id="session-1", removable_cause=True, escalate_on_repeat=True
    )
    second_blocks = consume_warning_blocks()

    assert not first_blocks
    assert len(second_blocks) == 1
    assert "この通知は同一セッションで2件目である" in second_blocks[0]
    assert "Fix: retry" in second_blocks[0]


def test_warning_formatter_block_fix_without_marker(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """解消手段のマーカーが無い警告では、Fix欄へ本文を複製せず汎用の手段を書く。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    format_warning = warning_formatter("test/hook")

    format_warning("警告本文", cause="no-marker", session_id="session-1", removable_cause=True, escalate_on_repeat=True)
    consume_warning_blocks()
    format_warning("警告本文", cause="no-marker", session_id="session-1", removable_cause=True, escalate_on_repeat=True)
    blocks = consume_warning_blocks()

    assert len(blocks) == 1
    fix_line = blocks[0].split("\nFix: ", maxsplit=1)[1]
    assert "警告本文" not in fix_line
    assert "原因を除去してから同じ操作を実行する" in fix_line


def test_warning_formatter_separates_causes_and_sessions(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """原因識別子とセッションIDが異なる警告を別々に数える。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    format_warning = warning_formatter("test/hook")

    for _ in range(3):
        format_warning("warning", cause="first-cause", session_id="session-1", removable_cause=True)
        consume_warning_blocks()
    other_cause = format_warning("warning", cause="second-cause", session_id="session-1", removable_cause=True)
    other_session = format_warning("warning", cause="first-cause", session_id="session-2", removable_cause=True)

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

    messages = [format_warning("warning", cause="fixed-cause", session_id="session-1", removable_cause=False) for _ in range(3)]
    removable_messages = [
        format_warning("warning", cause="other-cause", session_id="session-1", removable_cause=True, escalate_on_repeat=True)
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

    messages = [format_warning("警告本文", cause="same", session_id="session-1", removable_cause=True) for _ in range(2)]

    assert "2件目" in messages[1]
    assert not consume_warning_blocks()


def test_formatter_rejects_warn_without_removability() -> None:
    """warn通知の除去可能性を暗黙に決めない。"""
    format_notice = formatter("test/hook")

    with pytest.raises(ValueError):
        format_notice("warning", tag="warn")
