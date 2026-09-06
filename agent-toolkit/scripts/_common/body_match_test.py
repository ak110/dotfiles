"""保存本文の共通一致判定を検証する。"""

from _common import body_match


def test_normalize_removes_only_one_trailing_newline() -> None:
    assert body_match.normalize("本文\n") == "本文"
    assert body_match.normalize("本文\n\n") == "本文\n"
    assert body_match.normalize("本文") == "本文"


def test_first_difference_returns_none_for_equal_and_trailing_newline_difference() -> None:
    assert body_match.first_difference("本文", "本文") is None
    assert body_match.first_difference("本文\n", "本文") is None


def test_first_difference_uses_one_based_position() -> None:
    assert body_match.first_difference("本文", "別文") == 1
    assert body_match.first_difference("本文", "本文追加") == 3


def test_verdict_reports_equal_and_first_difference() -> None:
    assert body_match.verdict("本文", "本文\n") == "一致"
    assert body_match.verdict("本文", "別文") == "不一致（最初の差異: 1文字目）"
