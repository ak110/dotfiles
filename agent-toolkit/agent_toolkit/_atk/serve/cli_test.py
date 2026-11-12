"""serveのcliに関するruntimeの試験。"""

# pylint: disable=protected-access

import asyncio
import binascii
import contextlib
import pathlib
import signal
import struct
import subprocess
import typing
import zlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config as toolkit_config
from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import assets, config, state
from agent_toolkit._atk.serve import cli as serve
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve.plans import views as serve_plan_views
from agent_toolkit._testing.serve_cli_support import _unused_local_port


def test_config_precedence_and_platform_ports(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """`atk config`の保存値をSERVE環境変数とCLIが順に上書きし、未保存の待受ポートはOS別の値になる。"""
    for name in ("AGENT_TOOLKIT_SERVE_HOST", "AGENT_TOOLKIT_SERVE_PORT", "AGENT_TOOLKIT_SERVE_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    for key in ("SERVE_HOST", "SERVE_PORT"):
        monkeypatch.delenv(f"AGENT_TOOLKIT_CONFIG_{key}", raising=False)
    assert config.resolve_config() == config.ServeConfig("127.0.0.1", toolkit_config.serve_default_port())
    assert toolkit_config.serve_default_port("linux") == 28766
    assert toolkit_config.serve_default_port("win32") == 28876
    for key, value in (("serve_host", "saved-host"), ("serve_port", "3000")):
        with pytest.raises(SystemExit) as exited:
            atk.main(["config", "set", key, value])
        assert exited.value.code == 0
    capsys.readouterr()
    assert config.resolve_config() == config.ServeConfig("saved-host", 3000)
    monkeypatch.setenv("AGENT_TOOLKIT_SERVE_HOST", "0.0.0.0")
    monkeypatch.setenv("AGENT_TOOLKIT_SERVE_PORT", "4000")
    assert config.resolve_config() == config.ServeConfig("0.0.0.0", 4000)
    assert config.resolve_config(host="cli-host", port=5000) == config.ServeConfig("cli-host", 5000)


@pytest.mark.asyncio
async def test_manifest_declares_svg_icon(tmp_path: pathlib.Path) -> None:
    """manifestのiconsがSVG1件を宣言し、従来のPNGも引き続き配信する。"""
    app = serve_app.create_app(tmp_path, config.ServeConfig("127.0.0.1", 28766), state.ServeState(tmp_path))
    client = app.test_client()
    manifest_response = await client.get("/manifest.webmanifest")
    assert manifest_response.content_type == "application/manifest+json"
    assert manifest_response.headers["Cache-Control"] == "no-cache"
    manifest = await manifest_response.get_json()
    assert manifest == {
        "name": "atk serve",
        "short_name": "atk serve",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": assets.THEME_COLOR,
        "background_color": assets.THEME_COLOR,
        "icons": [
            {
                "src": "/favicon.svg",
                "sizes": "192x192 512x512 any",
                "type": "image/svg+xml",
                "purpose": "any",
            }
        ],
    }

    for size in (192, 512):
        response = await client.get(f"/static/icon-{size}.png")
        body = await response.get_data()
        assert isinstance(body, bytes)
        assert response.content_type == "image/png"
        assert body.startswith(b"\x89PNG\r\n\x1a\n")
        assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"

        offset = 8
        chunks: list[tuple[bytes, bytes]] = []
        while offset < len(body):
            length = struct.unpack_from(">I", body, offset)[0]
            chunk_type = body[offset + 4 : offset + 8]
            chunk_data = body[offset + 8 : offset + 8 + length]
            stored_crc = struct.unpack_from(">I", body, offset + 8 + length)[0]
            assert binascii.crc32(chunk_type + chunk_data) & 0xFFFFFFFF == stored_crc
            chunks.append((chunk_type, chunk_data))
            offset += 12 + length
        assert offset == len(body)
        assert [chunk_type for chunk_type, _ in chunks] == [b"IHDR", b"IDAT", b"IEND"]

        width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
            ">IIBBBBB",
            chunks[0][1],
        )
        assert (width, height) == (size, size)
        assert (bit_depth, color_type, compression, filtering, interlace) == (8, 6, 0, 0, 0)
        raw = zlib.decompress(b"".join(data for chunk_type, data in chunks if chunk_type == b"IDAT"))
        row_size = 1 + size * 4
        assert len(raw) == size * row_size
        for row_start in range(0, len(raw), row_size):
            assert raw[row_start] in range(5)


@pytest.mark.parametrize("follow", [False, True])
def test_serve_logs_reads_atk_serve_journal(monkeypatch: pytest.MonkeyPatch, follow: bool) -> None:
    """atk serveのuser serviceのjournalを読み、追従は指定時だけ行い、journalctlの終了状態を返す。"""
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: typing.Any) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(subprocess, "run", run)

    assert serve.show_logs(follow=follow) == 7
    assert len(commands) == 1
    command = commands[0]
    assert command[:2] == ["journalctl", "--user"]
    assert "_SYSTEMD_USER_UNIT=atk-serve.service" in command
    assert ("-f" in command) is follow


@pytest.mark.parametrize("path", ["/api/sessions/list", "/api/plans/search?q=x"])
@pytest.mark.asyncio
async def test_serve_stops_promptly_while_slow_request_is_in_progress(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    """処理に時間のかかる一覧・検索の要求の処理中に停止要求を受けても、実際のhypercornが短時間で停止する。

    hypercornは停止時に全接続の終了を待つため、要求の処理を停止要求で打ち切らないと、一覧の走査や
    リモート取得が終わるまで`_serve`が戻らない（systemdの停止タイムアウトでSIGKILLされる）。
    打ち切った要求は応答を開始していないため503で完了する。
    """

    request_started = asyncio.Event()
    request_release = asyncio.Event()

    async def slow_list_sessions(*args: object, **kwargs: object) -> typing.NoReturn:
        del args, kwargs
        request_started.set()
        await asyncio.wait_for(request_release.wait(), timeout=30)
        raise AssertionError("停止要求で打ち切られなかった")

    monkeypatch.setattr(serve_sessions, "list_sessions", slow_list_sessions)
    monkeypatch.setattr(serve_plan_views, "all_entries", slow_list_sessions)
    handlers: dict[int, tuple[typing.Callable[[str], None], str]] = {}
    loop = asyncio.get_running_loop()

    def add_signal_handler(sig: int, callback: typing.Callable[[str], None], signal_name: str, **kwargs: object) -> None:
        del kwargs
        handlers[sig] = (callback, signal_name)

    monkeypatch.setattr(loop, "add_signal_handler", add_signal_handler)
    port = _unused_local_port()
    server = asyncio.create_task(serve._serve(tmp_path, config.ServeConfig("127.0.0.1", port)))
    writer: asyncio.StreamWriter | None = None
    try:
        reader: asyncio.StreamReader | None = None
        for _ in range(200):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                break
            except OSError:
                await asyncio.sleep(0.05)
        assert reader is not None and writer is not None
        writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode())
        await writer.drain()
        await asyncio.wait_for(request_started.wait(), timeout=10)

        callback, signal_name = handlers[signal.SIGTERM]
        callback(signal_name)
        started = loop.time()
        status_line = await asyncio.wait_for(reader.readline(), timeout=5)
        await asyncio.wait_for(asyncio.shield(server), timeout=5)

        assert status_line.startswith(b"HTTP/1.1 503")
        assert loop.time() - started < 3
    finally:
        request_release.set()
        if writer is not None:
            writer.close()
        if not server.done():
            server.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await server
