"""JSON Linesの文字列・バイト列の物理行契約を検証する。"""

import pytest

from agent_toolkit._common.json_lines import split_lines


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", []),
        ("\n", [""]),
        ("\n\n", ["", ""]),
        ('{"text":"前\u2028中\u2029後\u0085末"}\n{}', ['{"text":"前\u2028中\u2029後\u0085末"}', "{}"]),
        ("{}\r\n\r\n{}\r\n", ["{}", "", "{}"]),
        ("{}\r{}", ["{}\r{}"]),
        ("broken\n{}\n", ["broken", "{}"]),
    ],
)
@pytest.mark.parametrize("binary", [False, True])
def test_split_lines_preserves_physical_rows(text: str, expected: list[str], binary: bool) -> None:
    """空入力・内部空行・最後のLF・CRLF・単独CR・Unicode区切りを区別する。"""
    if binary:
        assert split_lines(text.encode("utf-8")) == [line.encode("utf-8") for line in expected]
    else:
        assert split_lines(text) == expected
