"""`_hook_notice`のblock通知整形契約を検証する。"""

import pytest

from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks.notice import (
    block_formatter,
    consume_warning_blocks,
    formatter,
    notice_cycle,
    set_warning_session_id,
    warning_formatter,
)
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


@pytest.mark.parametrize("dedicated", [False, True])
def test_actual_blocks_are_preserved_on_repeated_operations(monkeypatch: pytest.MonkeyPatch, tmp_path, dedicated: bool) -> None:
    """同じ実害を防ぐ遮断は通知の重複抑止でも毎回保持する。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    format_block = block_formatter("test/protection") if dedicated else formatter("test/protection", default_tag="block")
    messages = []
    for _ in range(2):
        with notice_cycle("protected-session", "pretooluse", "main"):
            messages.append(format_block("所有を確認できない終了操作", fix="自分が起動した対象を指定する"))
    assert messages[0] == messages[1]
    assert 'kind="block"' in messages[0]


def test_warning_formatter_never_escalates_repetition(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """同じ警告の反復は抑止し、回数による遮断へ変更しない。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    format_warning = warning_formatter("test/hook")

    first = format_warning(
        "warning", fix="retry", cause="same-cause", session_id="session-1", removable_cause=True, escalate_on_repeat=True
    )
    first_blocks = consume_warning_blocks()
    second = format_warning(
        "warning", fix="retry", cause="same-cause", session_id="session-1", removable_cause=True, escalate_on_repeat=True
    )
    second_blocks = consume_warning_blocks()

    assert not first_blocks
    assert not second_blocks
    assert "次の操作: retry" in first
    assert second == ""


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
        # 必須引数の欠落そのものを検証するため、静的解析が返す指摘を抑止する。
        format_warning(  # type: ignore[call-arg]  # pylint: disable=missing-kwoa
            "警告本文", cause="missing", session_id="session-1", removable_cause=False
        )


def test_repeated_warning_keeps_action_in_the_first_notice_only(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """初回に対処を届け、同じ条件の反復本文と件数通知を増やさない。"""
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

    assert "\n次の操作: 対象を直して再実行する。\n" in messages[0]
    assert messages[1:] == ["", ""]
    assert not blocks


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
    assert len(read_state("session-1")["notice_fingerprints"]) == 2
    assert len(read_state("session-2")["notice_fingerprints"]) == 1


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
    assert messages[1:] == ["", ""]
    assert "この通知は同一セッションで" not in removable_messages[0]
    assert removable_messages[1:] == ["", ""]
    assert not consume_warning_blocks()


def test_removable_warning_does_not_escalate_without_explicit_choice(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """是正可能な警告が反復しても、明示しなければ遮断しない。"""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    consume_warning_blocks()
    format_warning = warning_formatter("test/hook")

    messages = [
        format_warning("警告本文", fix="retry", cause="same", session_id="session-1", removable_cause=True) for _ in range(2)
    ]

    assert messages[1] == ""
    assert not consume_warning_blocks()


def test_formatter_rejects_warn_without_removability() -> None:
    """warn通知の除去可能性を暗黙に決めない。"""
    format_notice = formatter("test/hook")

    with pytest.raises(ValueError):
        format_notice("warning", tag="warn", fix="retry")


def test_notice_lifecycle_separates_subjects_and_releases_resolved_causes() -> None:
    """条件が継続する間の抑止、対象変更、解消後の再発、別主体を区別する。"""
    format_warning = warning_formatter("test/hook")

    def warn(body: str = "対象A") -> str:
        return format_warning(body, fix="retry", cause="same", session_id="lifecycle", removable_cause=True)

    with notice_cycle("lifecycle", "event", "main"):
        assert warn()
        assert not warn()
    with notice_cycle("lifecycle", "event", "main"):
        assert not warn()
        assert warn("対象B")
    with notice_cycle("lifecycle", "event", "child"):
        assert warn()
    with notice_cycle("lifecycle", "event", "main"):
        pass
    with notice_cycle("lifecycle", "event", "main"):
        assert warn()


def test_identical_warning_text_keeps_distinct_operation_subjects() -> None:
    """本文が共通の手順警告でも別の対象へ必要な通知を届ける。"""
    format_notice = formatter("test/operation")

    def warn(subject: str) -> str:
        return format_notice("診断が破棄される", tag="warn", fix="診断を保持する", removable_cause=True, subject=subject)

    with notice_cycle("operation-subjects", "pretooluse", "main"):
        assert warn("test -e /repo/a 2>/dev/null")
        assert not warn("test -e /repo/a 2>/dev/null")
        assert warn("test -e /repo/b 2>/dev/null")
