"""Codexの公開control proxyを、通常TUIと同じWebSocket形式で中継する。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from collections import deque
from collections.abc import AsyncGenerator, Mapping
from typing import Any

import psutil
from agent_toolkit._common import process_identity, process_tree
from pydantic import BaseModel, ConfigDict
from wsproto import ConnectionType, WSConnection
from wsproto.events import AcceptConnection, CloseConnection, Ping, RejectConnection, Request, TextMessage


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)

    id: str | int | None = None
    method: str | None = None
    params: dict[str, Any] | None = None
    result: Any = None
    error: Any = None


class RpcChannel:
    """取得済みstreamのWebSocket framingと公開RPCのJSON境界を扱う。

    streamとprocessの寿命は、接続を取得するcontext managerが所有する。
    RPC本文は転送中のメモリーにだけ保持し、ログへ記録しない。
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, client: bool) -> None:
        self.reader, self.writer = reader, writer
        self.codec = WSConnection(ConnectionType.CLIENT if client else ConnectionType.SERVER)
        self._messages: deque[dict[str, Any]] = deque()
        self._fragments: list[str] = []

    async def send(self, value: dict[str, Any]) -> None:
        """標準JSONとして送信する。コマンド文字列への評価は行わない。"""
        self.writer.write(self.codec.send(TextMessage(data=json.dumps(value, ensure_ascii=False))))
        await self.writer.drain()

    async def receive(self) -> dict[str, Any]:
        """1個の完全な公開RPCを返す。通知とサーバー側の承認要求も同じ形で扱う。"""
        while not self._messages:
            data = await self.reader.read(65536)
            if not data:
                raise EOFError("CodexのIPC接続が終了した")
            self.codec.receive_data(data)
            for event in self.codec.events():
                if isinstance(event, CloseConnection):
                    self.writer.write(self.codec.send(event.response()))
                    await self.writer.drain()
                    raise EOFError("CodexのWebSocket接続が終了した")
                if isinstance(event, Ping):
                    self.writer.write(self.codec.send(event.response()))
                    await self.writer.drain()
                if isinstance(event, TextMessage):
                    self._fragments.append(event.data)
                    if event.message_finished:
                        self._messages.append(
                            _Envelope.model_validate_json("".join(self._fragments)).model_dump(exclude_unset=True)
                        )
                        self._fragments.clear()
        return self._messages.popleft()

    async def handshake(self, *, token: str | None = None) -> None:
        """公開HTTP Upgradeを行い、ローカル待受では起動ごとの認証を要求する。"""
        if self.codec.client:
            self.writer.write(self.codec.send(Request(host="localhost", target="/")))
            await self.writer.drain()
        while True:
            data = await self.reader.read(65536)
            if not data:
                raise EOFError("CodexのIPC handshakeが終了した")
            self.codec.receive_data(data)
            for event in self.codec.events():
                if isinstance(event, RejectConnection):
                    raise ConnectionError(f"CodexがIPC接続を拒否した: HTTP {event.status_code}")
                if isinstance(event, AcceptConnection):
                    return
                if isinstance(event, Request):
                    values = [value for name, value in event.extra_headers if name.lower() == b"authorization"]
                    expected = f"Bearer {token}".encode("ascii") if token is not None else None
                    if expected is None or len(values) != 1 or not secrets.compare_digest(values[0], expected):
                        self.writer.write(self.codec.send(RejectConnection(status_code=401)))
                        await self.writer.drain()
                        raise PermissionError("Codex中継接続の起動トークンが一致しない")
                    self.writer.write(self.codec.send(AcceptConnection()))
                    await self.writer.drain()
                    return


@contextlib.asynccontextmanager
async def accept_tui(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, token: str, *, timeout: float
) -> AsyncGenerator[RpcChannel]:
    """loopbackに接続した通常TUIのstreamを所有する。"""
    try:
        channel = RpcChannel(reader, writer, client=False)
        async with asyncio.timeout(timeout):
            await channel.handshake(token=token)
        yield channel
    finally:
        writer.close()
        await writer.wait_closed()


@contextlib.asynccontextmanager
async def native_proxy(binary: str, environment: Mapping[str, str], *, timeout: float) -> AsyncGenerator[RpcChannel]:
    """OSごとのcontrol socketへの接続を、Codex自身の公開proxyへ委ねる。"""
    process = await asyncio.create_subprocess_exec(
        binary,
        "app-server",
        "proxy",
        env=dict(environment),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    identity = None
    stderr = None
    try:
        try:
            identity = psutil.Process(process.pid)
            process_identity.start_id(identity)
        except psutil.NoSuchProcess:
            pass
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        stderr = asyncio.create_task(process.stderr.read())
        channel = RpcChannel(process.stdout, process.stdin, client=True)
        try:
            async with asyncio.timeout(timeout):
                await channel.handshake()
        except EOFError as error:
            await process.wait()
            diagnostic = (await stderr).decode("utf-8", errors="replace").strip()
            raise ConnectionError(f"Codexのcontrol proxyへ接続できない: {diagnostic or process.returncode}") from error
        yield channel
    finally:
        try:
            if process.stdin is not None:
                process.stdin.close()
            if process.returncode is None:
                try:
                    await asyncio.wait_for(process.wait(), process_tree.RECLAIM_TIMEOUT_SECONDS)
                except TimeoutError:
                    result = await asyncio.to_thread(process_tree.stop_tree, identity)
                    process_tree.log_residual(result.descriptions(), context="Codex control proxy")
                    await asyncio.wait_for(process.wait(), process_tree.RECLAIM_TIMEOUT_SECONDS)
        finally:
            if stderr is not None:
                stderr.cancel()
                await asyncio.gather(stderr, return_exceptions=True)
