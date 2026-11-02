"""引用状態ごとのエスケープと実行位置の回帰試験。"""

import pytest

from agent_toolkit._common.shell_segments import extract_execution_segments, split_bash_segments


@pytest.mark.parametrize(
    "prefix",
    [r"echo \"", r"echo \'", r'echo "a\";b"', r"echo 'a\';", r"echo a\;b", r"echo a\&b", r"echo a\|b"],
)
def test_escaped_symbols_preserve_following_execution(prefix: str) -> None:
    command = prefix + "; atk wi adopt 20261030-120000-001.md"
    assert split_bash_segments(command)[-1] == "atk wi adopt 20261030-120000-001.md"
    assert extract_execution_segments(command)[-1].tokens == ("atk", "wi", "adopt", "20261030-120000-001.md")


def test_escaped_separator_is_data() -> None:
    assert [segment.tokens for segment in extract_execution_segments(r"echo a\;atk wi adopt file.md")] == [
        ("echo", "a;atk", "wi", "adopt", "file.md")
    ]
