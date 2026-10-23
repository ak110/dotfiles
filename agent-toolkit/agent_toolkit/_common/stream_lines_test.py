"""transportのbufferとrecord境界を分離したバイト行の復元を検証する。"""

import asyncio

import pytest

from agent_toolkit._common import stream_lines


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", []),
        (b"\n\n", [b"\n", b"\n"]),
        (b"a\nb\nc", [b"a\n", b"b\n", b"c"]),
        (("あ" * 50000 + "\n末尾").encode(), [("あ" * 50000 + "\n").encode(), "末尾".encode()]),
    ],
    ids=["empty", "empty-lines", "multiple-lines", "long-utf8-line"],
)
@pytest.mark.asyncio
async def test_iter_lines_preserves_records_beyond_transport_limit(data: bytes, expected: list[bytes]) -> None:
    """長い行、連続行、空行、UTF-8の分割とEOFの残余を欠損・重複なく返す。"""
    stream = asyncio.StreamReader(limit=8)
    stream.feed_data(data)
    stream.feed_eof()
    assert [line async for line in stream_lines.iter_lines(stream)] == expected
