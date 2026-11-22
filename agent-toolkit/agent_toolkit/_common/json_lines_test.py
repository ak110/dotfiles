"""JSON Linesの文字列・バイト列の物理行契約と、連結行の分割を検証する。"""

import json
import time

import pytest

from agent_toolkit._common.json_lines import split_joined_line, split_lines


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


_NEXT_RECORD = b'{"type":"user","uuid":"u2"}'
_RECORD_WITH_SESSION = b'{"type":"user","uuid":"u2","sessionId":"s"}'
_NESTED_HEAD = b'{"type":"assistant","message":{"content":[{"type":"text","text":"x"}'


@pytest.mark.parametrize(
    ("line", "expected_start"),
    [
        (b'{"type":"assistant","message":"ab' + _NEXT_RECORD, 33),
        (b'{"type":"assistant","message":"\xe3\x81' + _NEXT_RECORD, 33),
        (_NESTED_HEAD + _NEXT_RECORD, len(_NESTED_HEAD)),
        (_NESTED_HEAD, None),
        (b'{"type":"x","status":{"clean":' + _RECORD_WITH_SESSION, 30),
        (b'{"type":"x","status":{"clean":' + _NEXT_RECORD, None),
        (b'{"type":"a"}' + _NEXT_RECORD, None),
        (b'{"type":"assistant","message":"ab{"uuid":"u2"}', None),
    ],
)
def test_split_joined_line_separates_only_fragment_and_record(line: bytes, expected_start: int | None) -> None:
    result = split_joined_line(line)
    if expected_start is None:
        assert result is None
    else:
        assert result is not None
        assert result[0] == expected_start
        assert result[1] == json.loads(line[expected_start:])


@pytest.mark.cpu_limit(
    2.75,
    reason=(
        "5万objectと深さ2万のbytesで線形処理と分割結果を確認し、小入力では検出できない二次処理を識別する（Linux CI C"
        "PU合計最大1.226秒）。"
    ),
)
def test_split_joined_line_handles_long_lines_without_quadratic_cost() -> None:

    fragment = b'{"type":"x","l":[' + b",".join(b'{"type":"t","v":%d}' % i for i in range(50000))
    started = time.monotonic()
    assert split_joined_line(fragment) is None
    assert split_joined_line(fragment + _NEXT_RECORD) == (len(fragment), {"type": "user", "uuid": "u2"})

    deep = b'{"a":' * 20000
    assert split_joined_line(deep) is None
    assert split_joined_line(deep + _RECORD_WITH_SESSION) is None
    assert time.monotonic() - started < 10
