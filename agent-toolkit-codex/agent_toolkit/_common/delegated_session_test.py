"""委譲先セッションの判定を検証する。"""

import pytest

from agent_toolkit._common.delegated_session import is_delegated


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
