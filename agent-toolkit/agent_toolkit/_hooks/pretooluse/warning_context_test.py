"""PreToolUseの警告一覧整形を検証する。"""

import pytest

from agent_toolkit._hooks.pretooluse.warning_context import format_warning_context


@pytest.mark.parametrize(
    ("warnings", "expected"),
    [
        (["一件目"], "一件目"),
        (["一件目", "二件目"], "警告: 2件\n\n一件目\n\n二件目"),
        (["一件目", "二件目", "三件目"], "警告: 3件\n\n一件目\n\n二件目\n\n三件目"),
    ],
)
def test_format_warning_context(warnings: list[str], expected: str) -> None:
    """単一警告を維持し、複数警告へ件数見出しを付ける。"""
    assert format_warning_context(warnings) == expected


def test_preformatted_context_is_recounted_without_nested_header() -> None:
    """局所検査で結合済みの警告と入口の警告を総数で数え直す。"""
    first = "[auto-generated: hook][warn] first suffix"
    second = "[auto-generated: hook][warn] second suffix"
    third = "[auto-generated: hook][warn] third suffix"
    combined = format_warning_context([first, second])

    assert format_warning_context([third, combined]) == f"警告: 3件\n\n{third}\n\n{first}\n\n{second}"
