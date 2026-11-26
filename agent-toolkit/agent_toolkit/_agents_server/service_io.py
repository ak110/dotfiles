from __future__ import annotations

import asyncio
import contextlib
import contextvars
import dataclasses
import logging
import types
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from typing import Any

import psutil

from agent_toolkit._agents_server import service_rpc, service_transport
from agent_toolkit._agents_server.codex_protocol import AppServerError, JsonRpcResponseError
from agent_toolkit._agents_server.service_resources import ResourceBroker
from agent_toolkit._common import codex_fast_mode, process_tree

_LOG = logging.getLogger(__name__)


async def _no_save() -> None:
    pass


@dataclasses.dataclass(frozen=True)
class Runtime:
    """同じサービスのI/O所有と、会話の保存先。環境値は保持しない。"""

    generation: str
    target: dict[str, Any]
    broker: ResourceBroker
    save: Callable[[], Awaitable[None]] = _no_save
    boundary: Callable[[Any, Any], None] | None = None
    started: Callable[[Any], None] | None = None
    gate: Any = None
    input_scope: Callable[[Any], contextlib.AbstractAsyncContextManager[bool]] | None = None

    @property
    def owner(self) -> str:
        return self.target.get("server_instance", self.generation)


_RUNTIME: contextvars.ContextVar[Runtime | None] = contextvars.ContextVar("agents_server_resources", default=None)
_REHOMING: contextvars.ContextVar[str | None] = contextvars.ContextVar("agents_server_rehoming", default=None)


def rehoming(identifier: str) -> bool:
    return _REHOMING.get() == identifier


@contextlib.asynccontextmanager
async def next_input(session: Any) -> AsyncGenerator[None]:
    runtime = current()
    if runtime is None or runtime.input_scope is None:
        yield
        return
    async with runtime.input_scope(session) as released:
        token = _REHOMING.set(session.session_id if released else None)
        try:
            yield
        finally:
            _REHOMING.reset(token)


def current() -> Runtime | None:
    return _RUNTIME.get()


@contextlib.contextmanager
def bind(runtime: Runtime) -> Generator[None]:
    token = _RUNTIME.set(runtime)
    try:
        yield
    finally:
        _RUNTIME.reset(token)


@contextlib.contextmanager
def save_with(save: Callable[[], Awaitable[None]]) -> Generator[None]:
    runtime = current()
    with bind(dataclasses.replace(runtime, save=save)) if runtime else contextlib.nullcontext():
        yield


class Resource:
    def __init__(self, runtime: Runtime, identifier: str, origin: dict[str, Any]) -> None:
        self.runtime, self.identifier, self.origin = runtime, identifier, dict(origin)

    async def operation(self, name: str, **arguments: Any) -> Any:
        params = {"resource_id": self.identifier, **arguments}
        if name != "transfer":
            params["owner_generation"] = self.runtime.owner
        if self.local:
            return await getattr(self.runtime.broker, name)(**params)
        return await service_rpc.request(self.origin, name, params)

    @property
    def local(self) -> bool:
        return self.origin["generation"] == self.runtime.generation and self.origin.get("process") == self.runtime.target.get(
            "process"
        )

    def descriptor(self) -> dict[str, Any]:
        return {"identifier": self.identifier, "origin": dict(self.origin)}

    @classmethod
    def attach(cls, runtime: Runtime, descriptor: dict[str, Any]) -> Resource:
        return cls(runtime, descriptor["identifier"], descriptor["origin"])


class CodexResource:
    def __init__(
        self,
        resource: Resource,
        notification: Callable[[dict], Awaitable[None]],
        server_request: Callable[[dict], Awaitable[None]],
        failure: Callable[[BaseException], Awaitable[None]],
    ) -> None:
        self.resource = resource
        self._notification, self._server_request, self._failure = notification, server_request, failure
        self._sent: dict[str, Callable[[str | None], None]] = {}
        self._requests = 0
        self._sequence = 0
        self._dispatch = asyncio.Lock()
        self._detached = False
        self._closed = False
        self._reader_failure: BaseException | None = None
        self._failure_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[list[str]] | None = None
        self._task: asyncio.Task[None] | None = None
        self.process: Any = None
        self.parent_environment: dict[str, str] | None = None
        self._supports_outgoing_speed = False

    @classmethod
    def create(
        cls,
        runtime: Runtime,
        factory: Callable[..., Any],
        notification: Callable[[dict], Awaitable[None]],
        server_request: Callable[[dict], Awaitable[None]],
        failure: Callable[[BaseException], Awaitable[None]],
    ) -> CodexResource:
        identifier = ""
        broker = runtime.broker
        controller: CodexResource | None = None

        async def emit(kind: str, value: Any) -> None:
            await broker.emit(identifier, {"kind": kind, "value": value})

        async def notify(message: dict) -> None:
            await emit("notification", message)

        async def requested(message: dict) -> None:
            await emit("server_request", message)

        async def failed(error: BaseException) -> None:
            await emit("failure", str(error))

        raw = factory(notify, requested, failed)

        async def start() -> dict:
            await raw.start()
            return {"pid": raw.process.pid, "environment": raw.parent_environment}

        async def request(method: str, params: dict | None, ticket: str | None, outgoing_speed: dict | None = None) -> dict:
            outgoing_params = params if params is not None else {}

            def sent_tier() -> str | None:
                return outgoing_params.get("serviceTier") if outgoing_speed is not None else None

            async def publish_sent() -> None:
                if ticket is None:
                    return
                if controller is not None and not controller.transferred:
                    controller.record_sent(ticket, sent_tier())

            try:
                if outgoing_speed is None:
                    value = await raw.request(method, params, on_sent_async=publish_sent)
                else:
                    value = await raw.request(
                        method,
                        outgoing_params,
                        on_sent_async=publish_sent,
                        before_send=lambda: codex_fast_mode.apply_outgoing_metadata(outgoing_params, outgoing_speed),
                    )
                return {"result": value, "service_tier": sent_tier()}
            except JsonRpcResponseError as error:
                return {
                    "response_error": {"method": method, "code": error.code, "message": str(error), "data": error.data},
                    "service_tier": sent_tier(),
                }

        async def send(message: dict) -> None:
            await raw.send(message)

        async def notify_io(method: str, params: dict | None) -> None:
            await raw.notify(method, params)

        released_io = False

        async def shutdown(force: bool = False) -> list[str]:
            nonlocal released_io
            with process_tree.FORCE_STOP.set(force):
                residual = await raw.close()
            released_io = not residual
            return residual

        async def close() -> None:
            if not released_io and (residual := await raw.close()):
                raise RuntimeError(f"App Serverの資源を解放できません: {residual}")

        identifier = runtime.broker.register(
            {
                "start": start,
                "request": request,
                "send": send,
                "notify": notify_io,
                "shutdown": shutdown,
                "close": close,
            }
        )
        controller = cls(Resource(runtime, identifier, runtime.target), notification, server_request, failure)
        controller._supports_outgoing_speed = True
        return controller

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def transferred(self) -> bool:
        return self._detached

    @property
    def reader_failure(self) -> BaseException | None:
        return self._reader_failure

    def diagnostic(self) -> dict[str, Any]:
        if not self.resource.local:
            backlog = "別プロセス"
        elif (counts := self.resource.runtime.broker.backlog(self.resource.identifier)) is None:
            backlog = "解放済み"
        else:
            backlog = f"{counts[0]}/{counts[1]}"
        return {
            "consumer": "稼働中" if self._task is not None and not self._task.done() else "停止",
            "acknowledged_sequence": self._sequence,
            "transferred": self._detached,
            "reader_failure": None if self._reader_failure is None else str(self._reader_failure),
            "backlog": backlog,
        }

    async def start(self) -> None:
        self._task = asyncio.create_task(self._consume())
        metadata = await self.resource.operation("call", operation="start", arguments={})
        self.process = types.SimpleNamespace(pid=metadata["pid"])
        self.parent_environment = metadata["environment"]

    async def request(
        self,
        method: str,
        params: dict | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
        outgoing_speed: dict | None = None,
    ) -> dict:
        if outgoing_speed is not None and not self._supports_outgoing_speed:
            if params is None:
                params = {}
            with codex_fast_mode.apply_legacy_outgoing_metadata(params, outgoing_speed):
                pass
            outgoing_speed = None
        ticket = str(uuid.uuid4()) if on_sent is not None else None
        self._requests += 1
        if ticket is not None and on_sent is not None:

            def sent(tier: str | None) -> None:
                if tier is not None and params is not None:
                    params["serviceTier"] = tier
                on_sent()

            self._sent[ticket] = sent
        arguments = {"method": method, "params": params, "ticket": ticket}
        if outgoing_speed is not None:
            arguments["outgoing_speed"] = outgoing_speed
        try:
            response = await self.resource.operation("call", operation="request", arguments=arguments)
            if ticket is not None:
                self.record_sent(ticket, response.get("service_tier"))
            if error := response.get("response_error"):
                raise JsonRpcResponseError(error["method"], error["code"], error["message"], error["data"])
            return response["result"]
        finally:
            if ticket is not None:
                self._sent.pop(ticket, None)
            self._requests -= 1

    def record_sent(self, ticket: str, tier: str | None = None) -> None:
        callback = self._sent.pop(ticket, None)
        if callback is not None:
            callback(tier)

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self.resource.operation("call", operation="notify", arguments={"method": method, "params": params})

    async def send(self, message: dict) -> None:
        await self.resource.operation("call", operation="send", arguments={"message": message})

    async def _consume(self) -> None:

        try:
            await self._consume_events()
        except Exception as error:
            _LOG.exception("App Serverイベントの受信処理が停止しました: sequence=%d", self._sequence)
            if self._closed or self._detached:
                return
            cause = f"App Serverイベントの受信処理が停止しました: {error!r}。"
            # 先に失敗を記録し、停止中の要求を接続へ送らせない。
            self._reader_failure = AppServerError(
                f"{cause}次の操作: 管理サービスのログで停止の原因を確認し、同じ作業を新しい委譲先で起動し直してください。"
            )
            self._failure_task = asyncio.create_task(self._finish_receive_failure(cause))

    async def _finish_receive_failure(self, cause: str) -> None:
        try:
            residual = await self.close()
        except Exception as stop_error:
            _LOG.exception("受信処理が停止したApp Serverを停止できません: sequence=%d", self._sequence)
            residual = [f"停止要求の失敗: {stop_error!r}"]
        if residual:
            self._reader_failure = AppServerError(
                f"{cause}App Serverの停止を確認できない対象: {'; '.join(residual)}。"
                "次の操作: この対象の終了を確かめてから、同じ作業を新しい委譲先で起動し直してください。"
            )
        assert self._reader_failure is not None
        await self._failure(self._reader_failure)

    async def _consume_events(self) -> None:
        while not self._closed and not self._detached:
            events = await self.resource.operation("events", after_sequence=self._sequence, wait=30)
            if not events:
                continue
            async with self._dispatch:
                if self._detached:
                    return
                sequence = self._sequence
                for event in events:
                    if self._detached:
                        break
                    payload = event["payload"]
                    kind, value = payload["kind"], payload["value"]
                    if kind == "notification":
                        await self._notification(value)
                    elif kind == "server_request":
                        await self._server_request(value)
                    elif kind == "failure":
                        self._reader_failure = AppServerError(value)
                        await self._failure(self._reader_failure)
                    elif kind == "sent":
                        self.record_sent(value)
                    else:
                        raise ValueError("未対応のApp Server生イベント")
                    sequence = event["sequence"]

                await self.resource.runtime.save()
                self._sequence = sequence
                await self.resource.operation("acknowledge", sequence=self._sequence)

    def pause(self) -> None:
        self._detached = True

    async def resume(self) -> None:
        async with self._dispatch:
            # 保存済みの位置の再確認は所有が違えば失敗し、transferと異なり配送中の要求の完了を待たない。
            await self.resource.operation("acknowledge", sequence=self._sequence)
            self._detached = False
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._consume())

    async def detach(self) -> dict[str, Any]:
        async with self._dispatch:
            if self._requests:
                raise RuntimeError("App Serverへ配送中の要求があるため移譲を待ちます")
            self._detached = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        return {
            **self.resource.descriptor(),
            "sequence": self._sequence,
            "supports_outgoing_speed": self._supports_outgoing_speed,
            "environment": self.parent_environment,
            "pid": self.process.pid if self.process is not None else None,
        }

    @classmethod
    def attach(
        cls,
        runtime: Runtime,
        descriptor: dict[str, Any],
        notification: Callable[[dict], Awaitable[None]],
        server_request: Callable[[dict], Awaitable[None]],
        failure: Callable[[BaseException], Awaitable[None]],
    ) -> CodexResource:
        client = cls(Resource.attach(runtime, descriptor), notification, server_request, failure)
        client._supports_outgoing_speed = descriptor.get("supports_outgoing_speed") is True
        client._sequence = descriptor["sequence"]
        client.parent_environment = descriptor["environment"]
        client.process = types.SimpleNamespace(pid=descriptor["pid"])
        client._task = asyncio.create_task(client._consume())
        return client

    async def close(self) -> list[str]:
        task = self._close_task
        if task is None or task.done() and (task.cancelled() or task.exception() is not None or task.result()):
            task = self._close_task = asyncio.create_task(self._close(asyncio.current_task()))
        return await asyncio.shield(task)

    async def _close(self, caller: asyncio.Task | None) -> list[str]:
        arguments = {"force": True} if process_tree.FORCE_STOP.get() else {}
        released = False
        try:
            residual = await self.resource.operation("call", operation="shutdown", arguments=arguments)
        except ConnectionRefusedError, FileNotFoundError:
            if service_transport.process_alive(self.resource.origin.get("process")):
                raise
            # 資源を所有した世代が先に終了した（2026-11-17、移譲途中の資源の停止で現行世代の終了が失敗した）。
            # App Serverは元世代の子でstdinの終了とともに終わる。残っていれば停止を確認できないため所有を保つ。
            pid = self.process.pid if self.process is not None else None
            if pid is not None and psutil.pid_exists(pid):
                origin = self.resource.origin.get("generation")
                return [f"pid={pid}: 資源を所有した世代{origin}が終了し、App Serverの停止を確認できない"]
        else:
            if residual:
                return residual
            released = True
        reader = self._task if self._task is not caller else None
        if reader is not None:
            # broker登録を除いた後に、起こされた受信側が同じIDを読み直さない。  # pyfltr: allow-comment
            reader.cancel()
        try:
            if released:
                await self.resource.operation("release")
        finally:
            if reader is not None:
                await asyncio.gather(reader, return_exceptions=True)
        self._closed = True
        if released:
            await self.resource.operation("discard")
        return []
