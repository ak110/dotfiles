"""serveのcliに関するsynchronizationの試験。"""

# pylint: disable=protected-access

import asyncio
import contextlib
import pathlib
import signal
import typing

import pytest
import watchdog.events

from agent_toolkit._atk.serve import cli as serve
from agent_toolkit._atk.serve import config, state
from agent_toolkit._testing.serve_cli_support import _unused_local_port
from agent_toolkit._testing.serve_support import _FakeTimer


@pytest.mark.asyncio
async def test_state_ignores_pending_timer_cancelled_by_deadline(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取り消し済みタイマーが発火しても、再設定後の保留通知を重複発行しない。"""
    current = state.ServeState(
        tmp_path,
        debounce_seconds=10.0,
        timer_factory=_FakeTimer,
    )
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))
    event = watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md"))

    current.on_modified(event)
    stale = current._pending_notification
    assert stale is not None
    current.on_modified(event)
    # 取り消しと同時に起動した旧タイマーが発火した状況を再現する。
    stale.function(*stale.args)
    await asyncio.sleep(0)

    assert not published


@pytest.mark.parametrize("path", ["/api/events", "/api/plans/events", "/api/sessions/events"])
@pytest.mark.asyncio
async def test_serve_stops_promptly_while_sse_client_is_connected(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """SSEの購読接続を開いたまま停止要求を受けても、実際のhypercornが短時間で停止する。

    hypercornは停止時に全接続の切断を待つため、停止要求を参照しないSSE応答があると
    クライアントが切断するまで`_serve`が戻らない（systemdの停止タイムアウトでSIGKILLされる）。
    """
    handlers: dict[int, tuple[typing.Callable[[str], None], str]] = {}
    loop = asyncio.get_running_loop()

    def add_signal_handler(sig: int, callback: typing.Callable[[str], None], signal_name: str, **kwargs: object) -> None:
        del kwargs
        handlers[sig] = (callback, signal_name)

    # 実プロセスのシグナル処理を変えず、受信時の処理だけを取り出す。
    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    port = _unused_local_port()
    server = asyncio.create_task(serve._serve(tmp_path, config.ServeConfig("127.0.0.1", port)))
    reader: asyncio.StreamReader | None = None
    writer: asyncio.StreamWriter | None = None
    try:
        for _ in range(200):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        assert reader is not None and writer is not None
        writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: text/event-stream\r\n\r\n".encode())
        await writer.drain()
        status_line = await asyncio.wait_for(reader.readline(), timeout=10)
        assert status_line.startswith(b"HTTP/1.1 200")

        callback, signal_name = handlers[signal.SIGTERM]
        callback(signal_name)
        started = loop.time()
        await asyncio.wait_for(asyncio.shield(server), timeout=5)

        assert loop.time() - started < 5
    finally:
        if writer is not None:
            writer.close()
        if not server.done():
            server.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await server
