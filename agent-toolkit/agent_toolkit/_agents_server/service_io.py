"""backendの生I/Oを所有元に残し、解釈と保存を制御所有世代へ結び付ける。"""

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
        if self.local:
            return await getattr(self.runtime.broker, name)(**params)
        return await service_rpc.request(self.origin, name, params)

    @property
    def local(self) -> bool:
        """元I/Oが同じプロセスのbrokerにあるかを返す。"""
        return self.origin["generation"] == self.runtime.generation and self.origin.get("process") == self.runtime.target.get(
            "process"
        )

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

    def diagnostic(self) -> dict[str, Any]:
        """要求が進まない時に、受信処理の生存と元I/O側の未確認イベント数を返す。

        未確認イベントが上限に達すると、元I/Oは送出通知と後続の受信を止める。
        元I/Oが別プロセスにある場合、未確認数はこのプロセスから読めない。
        """
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
        # 例外で止まると既読が進まず、元I/O側のイベント上限で要求の送信まで止まる。
        # 停止を記録し、接続障害として所有sessionへ反映して、終了しない「実行中」を残さない。
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
            # 失敗の報告を受けたsessionは終了として公開される。turnを続けさせないよう、App Serverを先に止める。
            # brokerの登録解除は`close`が行う。送出通知の空き待ちで止まった要求が残ると解除が終わらないため、ここでは待たない。
            try:
                residual = await self.resource.operation("call", operation="shutdown", arguments={})
            except Exception as stop_error:
                _LOG.exception("受信処理が停止したApp Serverを停止できません: sequence=%d", self._sequence)
                residual = [f"停止要求の失敗: {stop_error!r}"]
            if residual:
                self._reader_failure = AppServerError(
                    f"{cause}App Serverの停止を確認できない対象: {'; '.join(residual)}。"
                    "次の操作: この対象の終了を確かめてから、同じ作業を新しい委譲先で起動し直してください。"
                )
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

    async def resume(self) -> None:
        """所有を保つ旧clientで保存済みの位置からdecodeを続け、配送中の要求のcallbackを保持する。"""
        async with self._dispatch:
            # 保存済みの位置の再確認は所有が違えば失敗し、transferと異なり配送中の要求の完了を待たない。
            await self.resource.operation("acknowledge", sequence=self._sequence)
            self._detached = False
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._consume())

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
        released = False
        try:
            residual = await self.resource.operation("call", operation="shutdown", arguments={})
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
            await self.resource.operation("release")
            released = True
        self._closed = True
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if released:
            # 受信処理を止めた後の未確認イベントを読む主体は無い。残すとbrokerが資源を保持し続け、元世代が退出できない。
            await self.resource.operation("discard")
        return []
