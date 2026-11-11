"""委譲先セッションの判定を検証する。"""

import pytest

from agent_toolkit._common.delegated_session import is_delegated, parse_prompt_handoff_path


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("作業", (False, None)),
        ("引き継ぎ記録先:\n/next.md", (True, None)),
        ("引き継ぎ記録先: relative.md\n引き継ぎ記録先: /valid.md", (True, None)),
        ("引き継ぎ記録先： /record.md（新規）", (True, "/record.md")),
        ("引き継ぎ記録先: /record.md(継続)", (True, "/record.md")),
    ],
)
def test_parse_prompt_handoff_path(prompt: str, expected: tuple[bool, str | None]) -> None:
    """空値や後続行を取り違えず最初の行だけを解釈する。"""
    assert parse_prompt_handoff_path(prompt) == expected


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"AGENT_TOOLKIT_DELEGATED_SESSION": "1"}, True),
        ({"AGENT_TOOLKIT_OWNER_SESSION": "owner"}, True),
        ({}, False),
        ({"AGENT_TOOLKIT_DELEGATED_SESSION": "0"}, False),
    ],
)
def test_is_delegated(environ: dict[str, str], expected: bool) -> None:
    """2つの環境変数の組合せから委譲先セッションを判定する。"""
    assert is_delegated(environ) is expected
