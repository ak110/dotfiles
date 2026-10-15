"""非同期のバイトストリームを、transportの上限と独立したLF区切りのrecordへ復元する。"""

import asyncio
from collections.abc import AsyncIterator


async def iter_lines(stream: asyncio.StreamReader) -> AsyncIterator[bytes]:
    """LFを含む行を返し、EOFの改行なしの残余も1回返す。デコードは呼び出し側が行う。"""
    buffer = bytearray()
    while True:
        scan_from = len(buffer)
        chunk = await stream.read(64 * 1024)
        if not chunk:
            if buffer:
                yield bytes(buffer)
            return
        buffer.extend(chunk)
        consumed = 0
        while (newline := buffer.find(b"\n", scan_from)) >= 0:
            yield bytes(buffer[consumed : newline + 1])
            consumed = newline + 1
            scan_from = consumed
        if consumed:
            del buffer[:consumed]
