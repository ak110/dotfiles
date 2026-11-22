from __future__ import annotations

# service内の協調役として、既存MCP定義とManagerの内部状態を一体で管理する。
# pylint: disable=protected-access
import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import pathlib
import signal
import sys
from collections.abc import Awaitable, Callable
from multiprocessing import connection
from typing import Any

import anyio
import psutil
from anyio.abc import ObjectReceiveStream, ObjectSendStream
from mcp.server.lowlevel import Server
from mcp.shared.message import SessionMessage
from mcp.types import CallToolRequestParams, JSONRPCMessage
from pydantic import TypeAdapter

from agent_toolkit._agents_server import logging_config, mcp_tools, mcp_transport, service_io, service_transport
from agent_toolkit._agents_server.service_context import ServiceManagers
from agent_toolkit._agents_server.service_coordinator import ServiceCoordinator
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._agents_server.service_projection import ProjectionHub
from agent_toolkit._agents_server.service_registry import RegistryForwarder
from agent_toolkit._agents_server.service_resources import ResourceBroker
from agent_toolkit._agents_server.session_errors import DelegateBackendError
from agent_toolkit._common import (
    inherited_venv,
    plugin_root_leases,
    private_directory,
    process_identity,
    registry_forwarding,
    runtime_environment,
    session_registry,
)
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.diagnostic_redaction import diagnostic_excerpt

_LOG = logging.getLogger("agent-toolkit.agents-server.service")
_MESSAGE = TypeAdapter(JSONRPCMessage)


@dataclasses.dataclass
class _PeerState:
    pending: set = dataclasses.field(default_factory=lambda: {"handshake"})
    sender: _SendStream | None = None
    redirect_supported: bool = False
    redirect_requested: bool = False
    redirect_acknowledged: bool = False


class _ReceiveStream(ObjectReceiveStream[SessionMessage | Exception]):
    def __init__(
        self,
        peer: service_transport.AsyncPeer,
        state: _PeerState,
        acknowledge: Callable[[], Awaitable[None]],
    ) -> None:
        self._peer = peer
        self._state = state
        self._acknowledge = acknowledge

    async def receive(self) -> SessionMessage | Exception:
        while True:
            try:
                payload = await self._peer.call(service_transport.receive)
            except EOFError:
                raise anyio.EndOfStream from None
            if payload.get("kind") != "redirect_ack":
                break
            if not self._state.redirect_requested:
                raise ValueError("未要求の接続切替ACKです")
            await self._acknowledge()
            self._state.redirect_acknowledged = True
        if "method" in payload and "id" in payload:
            self._state.pending.add(payload["id"])
        return SessionMessage(_MESSAGE.validate_python(payload))

    async def aclose(self) -> None:
        await self._peer.close()


class _SendStream(ObjectSendStream[SessionMessage]):
    def __init__(self, peer: service_transport.AsyncPeer, state: _PeerState) -> None:
        self._peer = peer
        self._lock = asyncio.Lock()
        self._state = state

    async def send(self, item: SessionMessage) -> None:
        async with self._lock:
            payload = item.message.model_dump(by_alias=True, exclude_none=True)
            await self._peer.call(service_transport.send, payload)
            if "method" not in payload and "id" in payload:
                self._state.pending.discard(payload["id"])

    async def redirect(self, target: dict) -> None:
        async with self._lock:
            await self._peer.call(service_transport.send, {"kind": "redirect", "target": target})

    async def aclose(self) -> None:
        await self._peer.close()


def _caller(payload: dict[str, Any]) -> runtime_environment.CallerContext:
    env = payload.get("environment")
    cwd = payload.get("cwd")
    if not isinstance(env, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in env.items()):
        raise ValueError("管理サービスへ渡す環境は文字列のmapで指定する")
    if not isinstance(cwd, str):
        raise ValueError("管理サービスへ渡すcwdが不正")
    connection_id = payload.get("connection_id")
    if not isinstance(connection_id, str):
        raise ValueError("管理サービスの接続識別子が不正")
    settings = payload.get("parent_settings")
    if settings is not None and not isinstance(settings, str):
        raise ValueError("呼出元のsettingsが不正")
    host = payload.get("host_process")
    if host is not None:
        process_identity.ProcessIdentity.parse(host)
    return runtime_environment.CallerContext(
        env,
        pathlib.Path(cwd),
        connection_id=connection_id,
        parent_settings=settings,
        host_process=host,
    )


def _host(peer_pid: int) -> tuple[dict | None, str | None]:
    """uv等の中間親を越え、認証した転送側の祖先CLIだけを所有主体として観測する。"""
    try:
        for process in process_identity.parents(psutil.Process(peer_pid)):
            executable = pathlib.Path(process.exe()).name.lower().removesuffix(".exe")
            if executable not in {"codex", "claude", "agy", "antigravity", "opencode", "gemini"}:
                continue
            arguments = process.cmdline()
            settings = None
            for index, value in enumerate(arguments):
                if value.startswith("--settings="):
                    settings = value.removeprefix("--settings=") or None
                elif value == "--settings" and index + 1 < len(arguments):
                    settings = arguments[index + 1]
            if settings and not settings.lstrip().startswith("{"):
                settings = str((pathlib.Path(process.cwd()) / settings).resolve())
            return process_identity.ProcessIdentity.capture(process).payload(), settings
    except psutil.Error:
        return None, None
    return None, None


async def serve_peer(
    peer: connection.Connection,
    directory: pathlib.Path,
    resources: ResourceBroker,
    registry: RegistryForwarder,
    projections: ProjectionHub,
    coordinator: ServiceCoordinator,
    peer_state: _PeerState,
) -> None:
    owned = service_transport.AsyncPeer(peer)
    connection_id = None
    try:
        peer_pid = service_transport.authenticate(peer, server=True)
        payload = await owned.call(service_transport.receive)
        if payload.get("kind") == "rpc":
            peer_state.pending.discard("handshake")
            peer_state.pending.add("rpc")
            try:
                await _rpc(owned, payload, resources, registry, projections, coordinator)
            finally:
                peer_state.pending.discard("rpc")
            return
        caller = _caller(payload)
        host, settings = await asyncio.to_thread(_host, peer_pid)
        caller = dataclasses.replace(caller, host_process=host, parent_settings=settings or caller.parent_settings)
        connection_id = caller.connection_id
        peer_state.redirect_supported = payload.get("forwarder_protocol") == 1
        if service_transport.namespace(caller).resolve() != directory.resolve():
            raise PermissionError("異なる状態領域の要求を同じ管理サービスへ渡せません")
        with runtime_environment.bind(caller):
            await owned.call(service_transport.send, {"ready": True, "protocol": 1})
            peer_state.pending.discard("handshake")
            peer_state.sender = _SendStream(owned, peer_state)

            async def acknowledge() -> None:
                if connection_id is not None:
                    await coordinator.disconnect(connection_id)

            mcp = mcp_tools.mcp
            server = Server(
                "agents_server",
                instructions=mcp._agents_instructions,
                lifespan=lambda _server: mcp_tools._mcp_lifespan(mcp),
                on_list_tools=mcp._list_registered_tools,
                on_call_tool=coordinator.call_tool,
            )
            tracker = mcp_transport._InitializationLogTracker()
            try:
                await server.run(
                    mcp_transport._InitializationLoggingReceiveStream(_ReceiveStream(owned, peer_state, acknowledge), tracker),
                    mcp_transport._InitializationLoggingSendStream(peer_state.sender, tracker),
                    server.create_initialization_options(),
                )
            finally:
                tracker.transport_closed()
    except Exception:
        _LOG.exception("管理サービスの接続が失敗しました")
        raise
    finally:
        try:
            if connection_id is not None and not peer_state.redirect_acknowledged:
                await coordinator.disconnect(connection_id)
        except Exception:
            _LOG.exception("管理サービスの会話切断処理が失敗しました")
            raise
        finally:
            try:
                await owned.close()
                peer_state.pending.clear()
            except Exception:
                _LOG.exception("管理サービスの接続回収が失敗しました")
                raise


async def _rpc(
    peer: service_transport.AsyncPeer,
    payload: dict[str, Any],
    resources: ResourceBroker,
    registry: RegistryForwarder,
    projections: ProjectionHub,
    coordinator: ServiceCoordinator,
) -> None:
    operations: dict[str, Callable[..., Awaitable[Any]]] = {
        "call": resources.call,
        "transfer": resources.transfer,
        "events": resources.events,
        "acknowledge": resources.acknowledge,
        "release": resources.release,
        "discard": resources.discard,
    }
    try:
        operation = payload.get("operation")
        arguments = payload.get("arguments")
        if operation == "ping":
            result = {"generation": resources.origin_generation}
        elif operation == "publish":
            result = await coordinator.publish()
        elif operation == "prepare_update" and isinstance(arguments, dict):
            result = await coordinator.prepare(arguments["token"], arguments["target"])
        elif operation == "commit_update" and isinstance(arguments, dict):
            await coordinator.commit_update(arguments["token"], arguments["current"])
            result = None
        elif operation == "move" and isinstance(arguments, dict):
            result = await coordinator.move(arguments["session_id"])
        elif operation == "prepare_move" and isinstance(arguments, dict):
            result = coordinator.prepare_move(arguments)
        elif operation == "move_status" and isinstance(arguments, dict):
            result = coordinator.move_status(arguments["session_id"], arguments["transfer_id"])
        elif operation == "abort_move" and isinstance(arguments, dict):
            result = await coordinator.abort_move(arguments["session_id"], arguments["transfer_id"])
        elif operation == "move_context" and isinstance(arguments, dict):
            await coordinator.accept_context(arguments)
            result = None
        elif operation == "disconnect" and isinstance(arguments, dict):
            await coordinator.disconnect(arguments["connection_id"])
            result = None
        elif operation == "accept" and isinstance(arguments, dict):
            result = await coordinator.accept(arguments)
        elif operation == "parent_sessions" and isinstance(arguments, dict):
            result = await coordinator.parent_sessions(arguments["parent"], bool(arguments["include_terminated"]))
        elif operation == "ping":
            result = {"generation": coordinator.generation}
        elif operation == "owns" and isinstance(arguments, dict):
            result = coordinator.owns(arguments["parent"], arguments["session_id"])
        elif operation in {"public_call", "owned_call"} and isinstance(arguments, dict):
            caller = _caller(arguments["caller"])
            if service_transport.namespace(caller).resolve() != coordinator.directory.resolve():
                raise PermissionError("異なる管理領域への転送はできません")
            params = CallToolRequestParams.model_validate(arguments["params"])
            with runtime_environment.bind(caller):
                if operation == "owned_call":
                    result = await coordinator.owned_call(arguments["parent"], caller, params)
                else:
                    result = (await coordinator.call_tool(None, params)).model_dump(by_alias=True, exclude_none=True)
        elif operation == "projection" and isinstance(arguments, dict):
            result = await projections.apply(arguments)
        elif operation == "registry_mutation" and isinstance(arguments, dict):
            result = await registry.apply(arguments)
        elif operation not in operations or not isinstance(arguments, dict):
            raise ValueError("未対応の管理サービス内部操作")
        else:
            result = await operations[operation](**arguments)
        response = {"result": result}
    except Exception as error:
        response = {"error": f"{type(error).__name__}: {error}"}
        if isinstance(error, DelegateBackendError) or type(error).__module__.startswith("claude_agent_sdk"):
            response["category"] = "backend"
    # 移譲でevents待機を取り消したconsumerは既に切断している。未ACKのイベントはbrokerが保持する。
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        await peer.call(service_transport.send, response)


async def run(
    directory: pathlib.Path, generation: str, *, stop: asyncio.Event | None = None, resources: ResourceBroker | None = None
) -> None:
    address = service_transport.endpoint(directory, generation)
    directory.mkdir(parents=True, exist_ok=True, mode=private_directory.MODE)
    # 共有起動の診断をsupervisorへ結び付ける。会話や認証の環境は診断へ渡さない。
    environment = {name: os.environ[name] for name in (logging_config.LAUNCH_RECORD_ENV,) if name in os.environ}
    instance = logging_config.start_server_instance(environment=environment, state_root=directory.parent.parent)
    session_registry.set_owner_server_instance(instance.instance_id)
    tasks: set[asyncio.Task[None]] = set()
    peers: set[connection.Connection] = set()
    peer_states: dict[connection.Connection, _PeerState] = {}
    stop = stop or asyncio.Event()
    resources = resources or ResourceBroker(generation, owner_identity=instance.instance_id)
    registry = RegistryForwarder(generation, directory)
    projections = ProjectionHub(generation, directory)

    handed_over = True
    try:
        with registry_forwarding.bind(registry.forward):
            async with ServiceManagers(state_root=directory.parent.parent, projections=projections) as managers:
                mcp_tools._SERVICE_MANAGERS = managers
                coordinator: ServiceCoordinator | None = None
                try:
                    target = {
                        "generation": generation,
                        "endpoint": address,
                        "process": service_transport.process_identity(os.getpid()),
                        "server_instance": instance.instance_id,
                        "root": str(pathlib.Path(__file__).resolve().parents[2]),
                        "epoch": GenerationRegistry(directory).generations().get(generation, {}).get("epoch", 0),
                    }
                    coordinator = ServiceCoordinator(target, directory, managers, projections, registry)
                    runtime = coordinator.bind_runtime(service_io.Runtime(generation, target, resources))
                    with service_transport.listen(address) as listener, service_io.bind(runtime):
                        atomic_write(
                            directory / f"{generation}.json",
                            json.dumps(target),
                        )
                        while not stop.is_set():
                            if coordinator.can_retire():
                                for peer_state in tuple(peer_states.values()):
                                    if (
                                        peer_state.redirect_supported
                                        and peer_state.sender is not None
                                        and not peer_state.redirect_requested
                                    ):
                                        peer_state.redirect_requested = True
                                        await peer_state.sender.redirect(coordinator._current())
                                if (
                                    not any(state.pending for state in peer_states.values())
                                    and all(
                                        state.sender is None or state.redirect_acknowledged for state in peer_states.values()
                                    )
                                    and not any(not task.done() for task in coordinator._moving.values())
                                ):
                                    stop.set()
                                    break
                            accepting = asyncio.create_task(asyncio.to_thread(listener.accept))
                            stopping = asyncio.create_task(stop.wait())
                            try:
                                done, _ = await asyncio.wait({accepting, stopping}, return_when=asyncio.FIRST_COMPLETED)
                                if stopping in done:
                                    listener.close()
                                    await asyncio.gather(accepting, return_exceptions=True)
                                    break
                                peer = accepting.result()
                            except TimeoutError:
                                continue
                            except BaseException:
                                listener.close()
                                await asyncio.gather(accepting, return_exceptions=True)
                                raise
                            finally:
                                stopping.cancel()
                                await asyncio.gather(stopping, return_exceptions=True)
                            peers.add(peer)
                            peer_states[peer] = _PeerState()
                            task = asyncio.create_task(
                                serve_peer(peer, directory, resources, registry, projections, coordinator, peer_states[peer])
                            )
                            tasks.add(task)

                            def completed(finished: asyncio.Task[None], owned: connection.Connection = peer) -> None:
                                peers.discard(owned)
                                peer_states.pop(owned, None)
                                _finished(finished, tasks)

                            task.add_done_callback(completed)
                finally:
                    handed_over = coordinator is None or not coordinator.holds_handover()
                    mcp_tools._SERVICE_MANAGERS = None
                    for peer in peers:
                        service_transport.disconnect(peer)
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        try:
            await projections.close()
        finally:
            try:
                if handed_over:
                    GenerationRegistry(directory).retire(generation, service_transport.process_identity(os.getpid()))
            finally:
                error = sys.exception()
                if error is None:
                    _LOG.info("manager closeが完了しました: scope=service")
                logging_config.record_instance_event(
                    "exited",
                    outcome="normal" if error is None else "exception",
                    exception_type=type(error).__name__ if error is not None else None,
                    message=(
                        diagnostic_excerpt(str(error), prefix_chars=logging_config.MESSAGE_LIMIT) if error is not None else None
                    ),
                )
                session_registry.set_owner_server_instance(None)
                logging_config.end_server_instance()


def _finished(task: asyncio.Task[None], tasks: set[asyncio.Task[None]]) -> None:
    tasks.discard(task)
    if not task.cancelled():
        task.exception()


def main() -> int:
    """内部起動の状態領域・世代だけを受け取り、設定や認証値を保存しない。"""
    if len(sys.argv) != 3:
        raise ValueError("管理サービスの内部起動には状態領域と世代が必要")
    root = pathlib.Path(__file__).resolve().parents[2]
    if (root.parent / ".agent-toolkit-plugin-root.json").is_file():
        with plugin_root_leases.plugin_root_lock(root.parent):
            plugin_root_leases.register_plugin_root_use(root.parent)
    logging_config.configure_logging()
    # 委譲先のSDKとApp Serverは本プロセスの環境を継承し、キーの削除を表現できない。
    # 起動層が設定した共有依存環境やコード配置の値を、委譲先へ渡る前に呼出元の値へ戻す。
    inherited_venv.strip_inherited_venv(os.environ)

    async def execute() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        previous = signal.signal(signal.SIGTERM, lambda *_args: loop.call_soon_threadsafe(stop.set))
        restore_recording = logging_config.install_signal_recording()
        try:
            await run(pathlib.Path(sys.argv[1]), sys.argv[2], stop=stop)
        finally:
            restore_recording()
            signal.signal(signal.SIGTERM, previous)

    asyncio.run(execute())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
