"""実IPCで元資源へ接続し、3制御世代のdecode・保存と元I/Oの寿命を分ける。"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import types
from collections.abc import Awaitable, Callable
from typing import Any, cast

import pytest

from agent_toolkit._agents_server import service, service_io, service_transport
from agent_toolkit._agents_server.codex_protocol import AppServerError
from agent_toolkit._agents_server.service_resources import ResourceBroker
from agent_toolkit._common import runtime_environment
from agent_toolkit._common.atomic_file import atomic_write

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


class _RawCodex:
    """外部App ServerのI/Oだけを代用し、broker・IPC・consumer・保存は実行する。"""

    def __init__(
        self,
        notification: Callable[[dict], Awaitable[None]],
        server_request: Callable[[dict], Awaitable[None]],
        failure: Callable[[BaseException], Awaitable[None]],
    ) -> None:
        self.process = types.SimpleNamespace(pid=os.getpid())
        self.parent_environment: dict[str, str] = {}
        self.closed = 0
        self.residual: list[str] = []
        self.calls: list[str] = []
        self.notification = notification
        self.server_request = server_request
        self.failure = failure

    async def start(self) -> None:
        pass

    async def request(self, method: str, _params: dict | None, *, on_sent_async: Callable[[], Awaitable[None]]) -> dict:
        self.calls.append(method)
        await on_sent_async()
        return {"method": method}

    async def send(self, _message: dict) -> None:
        pass

    async def notify(self, _method: str, _params: dict | None) -> None:
        pass

    async def close(self) -> list[str]:
        self.closed += 1
        return list(self.residual)


@pytest.mark.asyncio
async def test_origin_resource_survives_three_owners_without_old_decoder(tmp_path: pathlib.Path) -> None:
    caller = runtime_environment.CallerContext(dict(os.environ), tmp_path)
    directory = service_transport.namespace(caller)
    broker = ResourceBroker("G1")
    stop = asyncio.Event()
    serving = asyncio.create_task(service.run(directory, "G1", stop=stop, resources=broker))
    raw: _RawCodex | None = None
    clients: list[service_io.CodexResource] = []
    values: list[tuple[str, str]] = []
    saved: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
    destination = tmp_path / "result.json"

    async def unexpected(_value: Any) -> None:
        pytest.fail("未発生のイベントを受信した")

    def controller(generation: str, target: dict) -> tuple[service_io.Runtime, Callable[[dict], Awaitable[None]]]:
        async def notification(message: dict) -> None:
            values.append((generation, message["value"]))

        async def save() -> None:
            await asyncio.to_thread(atomic_write, destination, json.dumps(values))
            if values:
                saved.put_nowait(values[-1])

        runtime = service_io.Runtime(generation, target, broker if generation == "G1" else ResourceBroker(generation), save)
        return runtime, notification

    try:
        async with asyncio.timeout(10):
            ready = directory / "G1.json"
            while not ready.exists():
                if serving.done():
                    await serving
                await asyncio.sleep(0)
            origin = json.loads(ready.read_text(encoding="utf-8"))
            broker.owner_identity = origin["server_instance"]
            runtime, handler = controller("G1", origin)

            def factory(*args: Any) -> _RawCodex:
                nonlocal raw
                raw = _RawCodex(*args)
                return raw

            client = service_io.CodexResource.create(runtime, factory, handler, unexpected, unexpected)
            clients.append(client)
            await client.start()
            assert raw is not None
            sent: list[bool] = []
            assert await client.request("thread/read", {}, on_sent=lambda: sent.append(True)) == {"method": "thread/read"}
            assert sent == [True]
            for number, generation in enumerate(("G1", "G2", "G3"), 1):
                await raw.notification({"value": str(number)})
                while await saved.get() != (generation, str(number)):
                    pass
                assert json.loads(destination.read_text(encoding="utf-8")) == [list(value) for value in values]
                if generation == "G3":
                    break
                descriptor = await client.detach()
                assert descriptor["origin"]["generation"] == "G1"
                next_generation = f"G{number + 1}"
                assert await client.resource.operation(
                    "transfer",
                    expected_owner=client.resource.runtime.owner,
                    new_owner=next_generation,
                )
                # G2以降の制御先を、接続元のG1へ付け替えない。
                runtime, handler = controller(next_generation, {"generation": next_generation})
                client = service_io.CodexResource.attach(runtime, descriptor, handler, unexpected, unexpected)
                clients.append(client)
            assert values == [("G1", "1"), ("G2", "2"), ("G3", "3")]
            assert raw.calls == ["thread/read"]
            assert (raw.closed, tuple(raw.calls)) == (0, ("thread/read",))
            with pytest.raises(PermissionError):
                await clients[0].close()
            with pytest.raises(RuntimeError, match="PermissionError"):
                await clients[1].close()
            assert (raw.closed, tuple(raw.calls)) == (0, ("thread/read",))
            assert not await clients[2].close()
            assert raw.closed == 1
    finally:
        for client in clients:
            await client.detach()
        stop.set()
        await asyncio.wait_for(serving, 10)


@pytest.mark.parametrize("residual", [[], ["pid=7: codex app-server"]])
@pytest.mark.asyncio
async def test_consumer_failure_is_reported_as_reader_failure(tmp_path: pathlib.Path, residual: list[str]) -> None:
    """受信処理の停止は、元I/OのApp Serverを止めてから接続障害として報告し、停止を確認できない対象を伝える。"""
    caller = runtime_environment.CallerContext(dict(os.environ), tmp_path)
    directory = service_transport.namespace(caller)
    broker = ResourceBroker("G1")
    stop = asyncio.Event()
    serving = asyncio.create_task(service.run(directory, "G1", stop=stop, resources=broker))
    raw: _RawCodex | None = None
    client: service_io.CodexResource | None = None
    failures: asyncio.Queue[tuple[BaseException, int]] = asyncio.Queue()

    async def save() -> None:
        raise RuntimeError("保存の失敗")

    async def notification(_message: dict) -> None:
        pass

    async def unexpected(_value: Any) -> None:
        pytest.fail("未発生のイベントを受信した")

    async def failure(error: BaseException) -> None:
        assert raw is not None
        failures.put_nowait((error, raw.closed))

    def factory(*args: Any) -> _RawCodex:
        nonlocal raw
        raw = _RawCodex(*args)
        raw.residual = residual
        return raw

    try:
        async with asyncio.timeout(10):
            ready = directory / "G1.json"
            while not ready.exists():
                if serving.done():
                    await serving
                await asyncio.sleep(0)
            origin = json.loads(ready.read_text(encoding="utf-8"))
            broker.owner_identity = origin["server_instance"]
            runtime = service_io.Runtime("G1", origin, broker, save)
            client = service_io.CodexResource.create(runtime, factory, notification, unexpected, failure)
            await client.start()
            assert raw is not None
            await raw.notification({"value": "1"})
            error, closed = await failures.get()
            assert closed == 1
            assert isinstance(error, AppServerError)
            assert "保存の失敗" in str(error)
            assert all(item in str(error) for item in residual)
            assert client.reader_failure is error
    finally:
        if client is not None:
            await client.detach()
        stop.set()
        await asyncio.wait_for(serving, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("app_server_alive", [False, True])
async def test_close_after_owner_generation_exited(monkeypatch, app_server_alive):
    """資源を所有した世代が先に終了しても終了処理を失敗させず、App Serverが残る場合だけ所有を保つ。

    2026-11-17、移譲途中の資源の停止で現行世代の終了処理が失敗した。
    """

    class _Gone:
        origin = {"generation": "G1", "process": "999999:gone"}

        async def operation(self, *_args, **_kwargs):
            raise ConnectionRefusedError("所有世代は終了済み")

    async def unexpected(*_args):
        raise AssertionError("呼ばれない")

    client = service_io.CodexResource(cast(Any, _Gone()), unexpected, unexpected, unexpected)
    client.process = types.SimpleNamespace(pid=4242)
    monkeypatch.setattr(service_io.psutil, "pid_exists", lambda pid: app_server_alive and pid == 4242)

    residual = await client.close()

    assert bool(residual) is app_server_alive
    if app_server_alive:
        assert "pid=4242" in residual[0] and "G1" in residual[0]
