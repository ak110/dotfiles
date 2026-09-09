"""`_hook_notice`のblock通知整形契約を検証する。"""

import pytest

from agent_toolkit._hooks.notice import block_formatter, formatter, warning_formatter
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

    assert message.startswith("[auto-generated: test/hook][block] blocked\nFix: retry ")
    assert message.endswith("（自動生成のhook通知。行動する前に会話コンテキストとの関連性を評価すること。）")


def test_warning_formatter_adds_repeat_note_from_third_notice(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """同じ原因の3件目以降だけ反復注記と累積件数を付与する。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    format_warning = warning_formatter("test/hook")

    first = format_warning("warning", cause="same-cause", session_id="session-1", removable_cause=True)
    second = format_warning("warning", cause="same-cause", session_id="session-1", removable_cause=True)
    third = format_warning("warning", cause="same-cause", session_id="session-1", removable_cause=True)
    fourth = format_warning("warning", cause="same-cause", session_id="session-1", removable_cause=True)

    assert "この通知は同一セッションで" not in first
    assert "この通知は同一セッションで" not in second
    assert "この通知は同一セッションで3件目である" in third
    assert "この通知は同一セッションで4件目である" in fourth


def test_warning_formatter_separates_causes_and_sessions(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """原因識別子とセッションIDが異なる警告を別々に数える。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    format_warning = warning_formatter("test/hook")

    for _ in range(3):
        format_warning("warning", cause="first-cause", session_id="session-1", removable_cause=True)
    other_cause = format_warning("warning", cause="second-cause", session_id="session-1", removable_cause=True)
    other_session = format_warning("warning", cause="first-cause", session_id="session-2", removable_cause=True)

    assert "この通知は同一セッションで" not in other_cause
    assert "この通知は同一セッションで" not in other_session
    assert read_state("session-1")["warn_notice_counts"] == {
        "test/hook|first-cause": 3,
        "test/hook|second-cause": 1,
    }


def test_warning_formatter_omits_repeat_note_for_irremovable_cause(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """除去できない原因では件数を集計しても反復注記を付けない。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    format_warning = warning_formatter("test/hook")

    messages = [format_warning("warning", cause="fixed-cause", session_id="session-1", removable_cause=False) for _ in range(3)]
    removable_messages = [
        format_warning("warning", cause="other-cause", session_id="session-1", removable_cause=True) for _ in range(3)
    ]

    assert all("この通知は同一セッションで" not in message for message in messages)
    assert "この通知は同一セッションで3件目である" in removable_messages[-1]
    assert read_state("session-1")["warn_notice_counts"]["test/hook|fixed-cause"] == 3


def test_formatter_rejects_warn_without_removability() -> None:
    """warn通知の除去可能性を暗黙に決めない。"""
    format_notice = formatter("test/hook")

    with pytest.raises(ValueError):
        format_notice("warning", tag="warn")
