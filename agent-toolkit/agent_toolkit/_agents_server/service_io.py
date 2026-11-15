"""backendの生I/Oを所有元に残し、解釈と保存を制御所有世代へ結び付ける。"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import dataclasses
import types
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator
from typing import Any

from agent_toolkit._agents_server import service_rpc
from agent_toolkit._agents_server.codex_protocol import AppServerError, JsonRpcResponseError
from agent_toolkit._agents_server.service_resources import ResourceBroker


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
        """同じコード版の再起動でも、前の制御プロセスの要求を受理しない。"""
        return self.target.get("server_instance", self.generation)


_RUNTIME: contextvars.ContextVar[Runtime | None] = contextvars.ContextVar("agents_server_resources", default=None)
_REHOMING: contextvars.ContextVar[str | None] = contextvars.ContextVar("agents_server_rehoming", default=None)


def rehoming(identifier: str) -> bool:
    """次の本来入力のために旧接続を解放した呼出しだけを識別する。"""
    return _REHOMING.get() == identifier


@contextlib.asynccontextmanager
async def next_input(session: Any) -> AsyncGenerator[None]:
    """生存中の背景資源を保ち、次入力の境界で旧接続を回収する。"""
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
    """共有管理のI/O境界を返し、単体backendや従来CLIではNoneを返す。"""
    return _RUNTIME.get()


@contextlib.contextmanager
def bind(runtime: Runtime) -> Generator[None]:
    """新しいtaskも捕捉する範囲へ、資源所有と保存主体を束縛する。"""
    token = _RUNTIME.set(runtime)
    try:
        yield
    finally:
        _RUNTIME.reset(token)


@contextlib.contextmanager
def save_with(save: Callable[[], Awaitable[None]]) -> Generator[None]:
    """同じbrokerを使う会話の保存先だけを切り替える。"""
    runtime = current()
    with bind(dataclasses.replace(runtime, save=save)) if runtime else contextlib.nullcontext():
        yield


class Resource:
    """元世代のendpointを固定し、各操作で制御所有者の一致を確認する。"""

    def __init__(self, runtime: Runtime, identifier: str, origin: dict[str, Any]) -> None:
        self.runtime, self.identifier, self.origin = runtime, identifier, dict(origin)

    async def operation(self, name: str, **arguments: Any) -> Any:
        params = {"resource_id": self.identifier, **arguments}
        if name != "transfer":
            params["owner_generation"] = self.runtime.owner
        if self.origin["generation"] == self.runtime.generation and self.origin.get("process") == self.runtime.target.get(
            "process"
        ):
            return await getattr(self.runtime.broker, name)(**params)
        return await service_rpc.request(self.origin, name, params)

    def descriptor(self) -> dict[str, Any]:
        """環境や実行可能オブジェクトを含めず、元資源への参照だけを渡す。"""
        return {"identifier": self.identifier, "origin": dict(self.origin)}

    @classmethod
    def attach(cls, runtime: Runtime, descriptor: dict[str, Any]) -> Resource:
        """移譲済みの資源へ接続し、受付先の変更で所有元をたどり直さない。"""
        return cls(runtime, descriptor["identifier"], descriptor["origin"])


class CodexResource:
    """App Server通信をproxy化し、通知の解釈は現在のbackendだけで行う。"""

    def __init__(
        self,
        resource: Resource,
        notification: Callable[[dict], Awaitable[None]],
        server_request: Callable[[dict], Awaitable[None]],
        failure: Callable[[BaseException], Awaitable[None]],
    ) -> None:
        self.resource = resource
        self._notification, self._server_request, self._failure = notification, server_request, failure
        self._sent: dict[str, Callable[[], None]] = {}
        self._sent_done: dict[str, asyncio.Event] = {}
        self._requests = 0
        self._sequence = 0
        self._dispatch = asyncio.Lock()
        self._detached = False
        self._closed = False
        self._reader_failure: BaseException | None = None
        self._task: asyncio.Task[None] | None = None
        self.process: Any = None
        self.parent_environment: dict[str, str] | None = None

    @classmethod
    def create(
        cls,
        runtime: Runtime,
        factory: Callable[..., Any],
        notification: Callable[[dict], Awaitable[None]],
        server_request: Callable[[dict], Awaitable[None]],
        failure: Callable[[BaseException], Awaitable[None]],
    ) -> CodexResource:
        """状態処理をraw callbackから外し、保存確認付きの1受信列へ渡す。"""
        identifier = ""
        broker = runtime.broker

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

        async def request(method: str, params: dict | None, ticket: str) -> dict:
            async def publish_sent() -> None:
                await emit("sent", ticket)

            try:
                value = await raw.request(method, params, on_sent_async=publish_sent)
                return {"result": value}
            except JsonRpcResponseError as error:
                return {"response_error": {"method": method, "code": error.code, "message": str(error), "data": error.data}}

        async def send(message: dict) -> None:
            await raw.send(message)

        async def notify_io(method: str, params: dict | None) -> None:
            await raw.notify(method, params)

        released_io = False

        async def shutdown() -> list[str]:
            nonlocal released_io
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
        return cls(Resource(runtime, identifier, runtime.target), notification, server_request, failure)

    @property
    def closed(self) -> bool:
        """この制御側で解放を確定したかを返す。"""
        return self._closed

    @property
    def transferred(self) -> bool:
        """旧制御側のdecodeを停止したかを返す。元I/Oの解放とは区別する。"""
        return self._detached

    @property
    def reader_failure(self) -> BaseException | None:
        """元I/O側から通知された接続障害を返す。"""
        return self._reader_failure

    async def start(self) -> None:
        """通知consumerを先に有効化してから元I/Oを開始する。"""
        self._task = asyncio.create_task(self._consume())
        metadata = await self.resource.operation("call", operation="start", arguments={})
        self.process = types.SimpleNamespace(pid=metadata["pid"])
        self.parent_environment = metadata["environment"]

    async def request(self, method: str, params: dict | None = None, *, on_sent: Callable[[], None] | None = None) -> dict:
        """wire送信の観測と応答を分けて受信し、配送不確定の要求を保持する。"""
        ticket = str(uuid.uuid4())
        self._requests += 1
        if on_sent is not None:
            self._sent[ticket] = on_sent
            self._sent_done[ticket] = asyncio.Event()
        try:
            response = await self.resource.operation(
                "call",
                operation="request",
                arguments={
                    "method": method,
                    "params": params,
                    "ticket": ticket,
                },
            )
            # 応答はwire送信後にしか届かない。終了境界でconsumerが止まってもsent通知待ちにしない。
            callback = self._sent.pop(ticket, None)
            if callback is not None:
                callback()
            if error := response.get("response_error"):
                raise JsonRpcResponseError(error["method"], error["code"], error["message"], error["data"])
            return response["result"]
        finally:
            self._sent.pop(ticket, None)
            self._sent_done.pop(ticket, None)
            self._requests -= 1

    async def notify(self, method: str, params: dict | None = None) -> None:
        """元I/OへJSON-RPC notificationを渡す。"""
        await self.resource.operation("call", operation="notify", arguments={"method": method, "params": params})

    async def send(self, message: dict) -> None:
        """元I/Oへserver requestの応答を渡す。"""
        await self.resource.operation("call", operation="send", arguments={"message": message})

    async def _consume(self) -> None:
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
                        callback = self._sent.pop(value, None)
                        if callback is not None:
                            callback()
                            self._sent_done[value].set()
                    else:
                        raise ValueError("未対応のApp Server生イベント")
                    sequence = event["sequence"]
                # 1回の受信batchの解釈を保存してから、一括して既読へ進める。
                await self.resource.runtime.save()
                self._sequence = sequence
                await self.resource.operation("acknowledge", sequence=self._sequence)

    def pause(self) -> None:
        """適用中のイベントの保存だけを完走し、次のdecodeを止める。"""
        self._detached = True

    async def detach(self) -> dict[str, Any]:
        """解釈と保存を完走してからconsumerだけを解除し、App Serverをcloseしない。"""
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
        """元接続へresumeを送らず、新しいコードの通知処理だけを開始する。"""
        client = cls(Resource.attach(runtime, descriptor), notification, server_request, failure)
        client._sequence = descriptor["sequence"]
        client.parent_environment = descriptor["environment"]
        client.process = types.SimpleNamespace(pid=descriptor["pid"])
        client._task = asyncio.create_task(client._consume())
        return client

    async def close(self) -> list[str]:
        """制御所有者の一致を確認して元I/Oを回収し、所属が残れば所有を保つ。"""
        residual = await self.resource.operation("call", operation="shutdown", arguments={})
        if residual:
            return residual
        await self.resource.operation("release")
        self._closed = True
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        return []
