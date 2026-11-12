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
        # 断片が文字列の途中（多バイト文字の途中を含む）や入れ子のobjectの直後で途切れ、次の記録が続く。
        (b'{"type":"assistant","message":"ab' + _NEXT_RECORD, 33),
        (b'{"type":"assistant","message":"\xe3\x81' + _NEXT_RECORD, 33),
        (_NESTED_HEAD + _NEXT_RECORD, len(_NESTED_HEAD)),
        # 末尾が欠けただけの1記録は、内側の`type`付きobjectが行末まで成立しても連結としない。
        (_NESTED_HEAD, None),
        # 断片が`:`の直後で途切れた連結行は、後半が記録の最上位のキーを持つ場合だけ分ける。
        (b'{"type":"x","status":{"clean":' + _RECORD_WITH_SESSION, 30),
        (b'{"type":"x","status":{"clean":' + _NEXT_RECORD, None),
        # 断片が単独で成立する2記録の連結は、完全な記録を捨てないよう連結としない。
        (b'{"type":"a"}' + _NEXT_RECORD, None),
        # 後半が`type`を持たないobjectだけの行は連結としない。
        (b'{"type":"assistant","message":"ab{"uuid":"u2"}', None),
    ],
)
def test_split_joined_line_separates_only_fragment_and_record(line: bytes, expected_start: int | None) -> None:
    """境界は後半の完全な記録の先頭とし、連結と区別できない行は分けない。"""
    result = split_joined_line(line)
    if expected_start is None:
        assert result is None
    else:
        assert result is not None
        assert result[0] == expected_start
        assert result[1] == json.loads(line[expected_start:])


def test_split_joined_line_handles_long_lines_without_quadratic_cost() -> None:
    """hookは全行を読むため、入れ子のobjectが多い長い行でも行の長さに比例する時間で判定する。"""
    # 候補ごとに行の残りを解析すると、この規模では数十秒以上かかる。
    fragment = b'{"type":"x","l":[' + b",".join(b'{"type":"t","v":%d}' % i for i in range(50000))
    started = time.monotonic()
    assert split_joined_line(fragment) is None
    assert split_joined_line(fragment + _NEXT_RECORD) == (len(fragment), {"type": "user", "uuid": "u2"})
    assert time.monotonic() - started < 10
