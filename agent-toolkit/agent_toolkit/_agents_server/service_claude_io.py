from __future__ import annotations

import asyncio
import types
from typing import Any

from agent_toolkit._agents_server.service_io import Resource, Runtime
from agent_toolkit._agents_server.service_resources import sdk_event_codec
from agent_toolkit._agents_server.session_errors import DelegateBackendError


class SdkResource:
    def __init__(self, resource: Resource) -> None:
        self.resource = resource
        self.decode_lock = asyncio.Lock()
        self._detached = False
        self._sequence = 0
        self._query = self
        self._pending: int | None = None
        self._finish_lock = asyncio.Lock()
        self._transport: Any = None

    @property
    def transferred(self) -> bool:
        return self._detached

    def pause(self) -> None:
        self._detached = True

    @classmethod
    def create(cls, runtime: Runtime, factory: Any, options: Any, close_io: Any = None) -> SdkResource:
        commands: asyncio.Queue = asyncio.Queue()
        broker = runtime.broker
        codec = sdk_event_codec()
        identifier = ""
        owner_task = None

        async def owner() -> None:
            raw = factory(options)
            reader = None
            observations: set[asyncio.Task[None]] = set()
            closed = False

            async def observe(result: asyncio.Future, arguments: dict) -> None:
                try:
                    result.set_result(await raw.get_context_usage(**arguments))
                except asyncio.CancelledError:
                    result.cancel()
                    raise
                except Exception as error:
                    result.set_exception(error)

            async def stop_observations() -> None:
                for observation in observations:
                    observation.cancel()
                await asyncio.gather(*observations, return_exceptions=True)

            async def read() -> None:
                try:
                    async for message in raw.receive_messages():
                        await broker.emit(identifier, {"message": codec.encode(message)})
                    await broker.emit(identifier, {"end": True})
                except Exception as error:
                    await broker.emit(
                        identifier,
                        {
                            "error": str(error),
                            "backend_error": type(error).__module__.startswith("claude_agent_sdk"),
                        },
                    )

            try:
                while True:
                    name, arguments, result = await commands.get()
                    try:
                        if name == "shutdown":
                            await stop_observations()
                            if reader is not None:
                                reader.cancel()
                                await asyncio.gather(reader, return_exceptions=True)
                            if not closed:
                                await raw.disconnect()
                                closed = True
                            if close_io is not None:
                                await close_io()
                            result.set_result(None)
                            return
                        if name == "get_context_usage":
                            observation = asyncio.create_task(observe(result, arguments))
                            observations.add(observation)
                            observation.add_done_callback(observations.discard)
                            continue
                        # SDKの既存control request接続を、同じ所有taskで使う。
                        target = raw._query if name == "_send_control_request" else raw  # pylint: disable=protected-access
                        value = await getattr(target, name)(**arguments)
                        if name == "connect":
                            reader = asyncio.create_task(read())
                            process = getattr(getattr(raw, "_transport", None), "_process", None)
                            value = {"pid": getattr(process, "pid", None)}
                        result.set_result(value)
                    except Exception as error:
                        result.set_exception(error)
            finally:
                if not closed:
                    await stop_observations()
                    if reader is not None:
                        reader.cancel()
                        await asyncio.gather(reader, return_exceptions=True)
                    await raw.disconnect()

        async def invoke(name: str, arguments: dict) -> Any:
            nonlocal owner_task
            if name not in {
                "connect",
                "query",
                "interrupt",
                "get_context_usage",
                "reconnect_mcp_server",
                "_send_control_request",
                "shutdown",
            }:
                raise ValueError("未対応のSDK操作です")
            if owner_task is None:
                owner_task = asyncio.create_task(owner())
            if owner_task.done():
                await owner_task
                raise RuntimeError("SDK接続は終了しています")
            result = asyncio.get_running_loop().create_future()
            await commands.put((name, arguments, result))
            await asyncio.wait((result, owner_task), return_when=asyncio.FIRST_COMPLETED)
            if not result.done():
                await owner_task
                raise RuntimeError("SDK操作の完了前に接続taskが終了しました")
            return await asyncio.shield(result)

        async def close() -> None:
            await invoke("shutdown", {})
            assert owner_task is not None
            await owner_task

        identifier = runtime.broker.register({"invoke": invoke, "close": close})
        return cls(Resource(runtime, identifier, runtime.target))

    async def _call(self, name: str, **arguments: Any) -> Any:
        return await self.resource.operation("call", operation="invoke", arguments={"name": name, "arguments": arguments})

    async def connect(self, prompt: Any = None) -> None:
        metadata = await self._call("connect", prompt=prompt)
        self._transport = types.SimpleNamespace(_process=types.SimpleNamespace(pid=metadata["pid"]))

    async def query(self, prompt: Any, session_id: str = "default") -> None:
        await self._call("query", prompt=prompt, session_id=session_id)

    async def interrupt(self) -> None:
        await self._call("interrupt")

    async def get_context_usage(self) -> Any:
        return await self._call("get_context_usage")

    async def reconnect_mcp_server(self, server_name: str) -> None:
        await self._call("reconnect_mcp_server", server_name=server_name)

    async def _send_control_request(self, request: dict, timeout: float = 60) -> Any:
        return await self._call("_send_control_request", request=request, timeout=timeout)

    async def finish_message(self) -> None:
        async with self._finish_lock:
            if self._pending is None:
                return
            try:
                await self.resource.runtime.save()
                await self.resource.operation("acknowledge", sequence=self._pending)
                self._sequence = self._pending
            finally:
                self._pending = None
                self.decode_lock.release()

    async def receive_messages(self):
        codec = sdk_event_codec()
        while not self._detached:
            events = await self.resource.operation("events", after_sequence=self._sequence, wait=30)
            for event in events:
                await self.decode_lock.acquire()
                try:
                    if self._detached:
                        return
                    payload = event["payload"]
                    if "error" in payload or "end" in payload:
                        await self.resource.runtime.save()
                        await self.resource.operation("acknowledge", sequence=event["sequence"])
                        self._sequence = event["sequence"]
                    if "error" in payload:
                        if payload.get("backend_error"):
                            raise DelegateBackendError(payload["error"])
                        raise RuntimeError(payload["error"])
                    if "end" in payload:
                        return
                    message = codec.decode(payload["message"])
                    self._pending = event["sequence"]
                finally:
                    if self._pending is None:
                        self.decode_lock.release()
                try:
                    yield message
                    await self.finish_message()
                finally:
                    async with self._finish_lock:
                        if self._pending is not None:
                            self._pending = None
                            self.decode_lock.release()

    async def detach(self) -> dict:
        self.pause()
        async with self.decode_lock:
            self._detached = True
            process = getattr(self._transport, "_process", None)
            return {**self.resource.descriptor(), "sequence": self._sequence, "pid": getattr(process, "pid", None)}

    @classmethod
    def attach(cls, runtime: Runtime, descriptor: dict) -> SdkResource:
        client = cls(Resource.attach(runtime, descriptor))
        client._sequence = descriptor["sequence"]
        client._transport = types.SimpleNamespace(_process=types.SimpleNamespace(pid=descriptor.get("pid")))
        return client

    async def disconnect(self) -> None:
        await self.resource.operation("release")
        await self.resource.operation("discard")
