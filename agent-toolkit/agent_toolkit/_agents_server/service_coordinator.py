"""現在の受付と実行中turnの制御所有を分け、実終了後だけ状態と生I/Oを移す。"""

from __future__ import annotations

# 同じservice内のManager・writer・backendを、所有移譲の単一境界で協調させる。
# pylint: disable=protected-access
import asyncio
import contextlib
import dataclasses
import pathlib
import uuid
import weakref
from collections.abc import AsyncGenerator
from typing import Any

from mcp.types import CallToolRequestParams, CallToolResult, InputRequiredResult, TextContent
from pydantic import TypeAdapter

from agent_toolkit._agents_server import (
    mcp_tools,
    resume_waits,
    service_io,
    service_rpc,
    service_state,
    service_transport,
    service_turns,
)
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.service_context import Conversation, ServiceManagers
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._agents_server.service_projection import ProjectionHub
from agent_toolkit._agents_server.service_registry import RegistryForwarder
from agent_toolkit._common import file_lock, runtime_environment, session_registry
from agent_toolkit._common.next_action import ActionableError

_RESULT = TypeAdapter(CallToolResult | InputRequiredResult)


def _caller_payload(caller: runtime_environment.CallerContext) -> dict:
    return {
        "environment": dict(caller.environment),
        "cwd": str(caller.cwd),
        "connection_id": caller.connection_id,
        "parent_settings": caller.parent_settings,
        "credit_shortages": dict(caller.credit_shortages),
        "host_process": caller.host_process,
    }


def _restore_caller(value: dict) -> runtime_environment.CallerContext:
    return runtime_environment.CallerContext(
        value["environment"],
        pathlib.Path(value["cwd"]),
        dict(value["credit_shortages"]),
        value["connection_id"],
        value["parent_settings"],
        value.get("host_process"),
    )


# 所有世代への所有照会の待機上限。生存する世代は即時に応答するため、停止した世代だけを短時間で判別する。
_OWNER_PROBE_SECONDS = 10.0


class ServiceCoordinator:
    """一度だけの移譲と未配送要求の振り分けを、同じsession単位で直列化する。"""

    def __init__(
        self,
        target: dict,
        directory: pathlib.Path,
        managers: ServiceManagers,
        projections: ProjectionHub,
        registrations: RegistryForwarder,
    ) -> None:
        self.target = target
        self.generation = target["generation"]
        self.directory = directory
        self.managers = managers
        self.projections = projections
        self.registrations = registrations
        self.registry = GenerationRegistry(directory)
        self.gate = service_turns.TurnGate(self.generation, target)
        self._ended: dict[str, tuple[int, str | None]] = {}
        self._ended_clients: dict[str, Any] = {}
        self._moving: dict[str, asyncio.Task[dict]] = {}
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._intents: dict[str, dict] = {}
        self._runtime: service_io.Runtime | None = None
        self._publish_lock = asyncio.Lock()
        self._incoming: dict[str, dict] = {}
        self._received: dict[str, tuple[str, dict]] = {}
        self._uncertain: dict[str, str] = {}
        self._contexts: set[str] = set()
        self._pending_adoptions: dict[str, dict] = {}
        self.managers.before_controls = self.tick
        self.managers.foreign_sessions = self._foreign_sessions

    async def publish(self) -> dict:
        """起動の重複は合流し、旧保存を静止してから現行受付を公開する。"""
        async with self._publish_lock:
            with (self.directory / "publish.lock").open("a+", encoding="utf-8") as handle:
                await run_file_operation(file_lock.acquire_lock, handle)
                previous = None
                token = uuid.uuid4().hex
                try:
                    current = self.registry.current()
                    if (
                        current is not None
                        and current["epoch"] >= self.target["epoch"]
                        and (current["generation"] != self.generation or current["process"] == self.target["process"])
                    ):
                        return current
                    if (
                        current is not None
                        and service_transport.process_alive(current["process"])
                        and current["generation"] != self.generation
                    ):
                        previous = current
                        inventory = await service_rpc.request(
                            current,
                            "prepare_update",
                            {
                                "token": token,
                                "target": self.target,
                            },
                        )
                        await self.projections.seed(inventory)
                    accepted = self.registry.publish(
                        self.generation,
                        endpoint=self.target["endpoint"],
                        process=self.target["process"],
                        server_instance=self.target["server_instance"],
                        root=pathlib.Path(self.target["root"]) if self.target.get("root") else None,
                    )
                    await self.projections.activate()
                    return accepted
                finally:
                    try:
                        if previous is not None:
                            await service_rpc.request(
                                previous,
                                "commit_update",
                                {
                                    "token": token,
                                    "current": self._current(),
                                },
                            )
                    finally:
                        file_lock.release_lock(handle)

    def bind_runtime(self, runtime: service_io.Runtime) -> service_io.Runtime:
        """現在のbackendにだけ終了境界を知らせ、資源のoriginは変えない。"""
        self._runtime = dataclasses.replace(
            runtime,
            boundary=self.boundary,
            started=self.started,
            gate=self.gate,
            input_scope=self.input_scope,
        )
        return self._runtime

    def started(self, session: Any) -> None:
        """背景完了で自律開始したturnへ、前turnの終了境界を持ち越さない。"""
        self._ended.pop(session.session_id, None)
        self._ended_clients.pop(session.session_id, None)

    @contextlib.asynccontextmanager
    async def input_scope(self, session: Any) -> AsyncGenerator[bool]:
        """子孫の終了後の本来入力で、旧世代の生I/Oを通常の解放処理へ返す。"""
        found = self._conversation(session.session_id)
        backend: Any = None if found is None else found[1].manager._backends.get(session.engine)
        client = self._client(backend, session.session_id)
        resource = getattr(client, "resource", None)
        if (
            resource is None
            or resource.origin.get("process") == self.target["process"]
            or not self._ended_turn(session)
            or resume_waits.has_pending_auto_resume_targets(session)
        ):
            yield False
            return
        child = self.managers.conversations.get(session.session_id)
        if child is not None:
            # 通常のreleaseが旧CLIを閉じる間も、その子会話の保存と未回収結果を保持する。
            child.active += 1
        try:
            await backend.release_session(session)
            self.started(session)
            yield True
        finally:
            if child is not None:
                identity = session.executor_process
                if identity is not None and identity.exists():
                    child.hosts = {identity.pid: identity}
                    child.caller = dataclasses.replace(child.caller, host_process=dataclasses.asdict(identity))
                    child.manager._caller_context = child.caller
                child.active -= 1

    def _current(self) -> dict:
        return self.registry.current() or self.target

    def can_retire(self) -> bool:
        """接続数や経過時間で強制終了せず、元資源と担当状態がなくなったことだけで退出する。"""
        return (
            self._current()["epoch"] > self.target["epoch"]
            and not self.managers.conversations
            and self._runtime is not None
            and not self._runtime.broker._resources
            and not self._incoming
            and not self._pending_adoptions
        )

    def _conversation(self, identifier: str) -> tuple[str, Conversation] | None:
        return next(
            ((key, item) for key, item in self.managers.conversations.items() if identifier in item.manager.sessions), None
        )

    def boundary(self, session: Any, client: Any) -> None:
        """backendの実終了だけを記録し、準備中または新受付公開後は次のdecodeを止める。"""
        self._ended[session.session_id] = session.turn_seq, session.turn_id
        if client is not None:
            self._ended_clients[session.session_id] = client
        current = self._current()
        if current["generation"] == self.generation and not self._intents:
            return
        found = self._conversation(session.session_id)
        if found is None:
            return
        backend: Any = found[1].manager._backends.get(session.engine)
        if session.session_id in getattr(backend, "_wait_compaction_turns", {}):
            return
        if client is not None:
            client.pause()
        if current["generation"] != self.generation:
            self._schedule_move(session.session_id)

    def _ended_turn(self, session: Any) -> bool:
        return session.turn_completed or self._ended.get(session.session_id) == (session.turn_seq, session.turn_id)

    def _schedule_move(self, identifier: str) -> None:
        task = self._moving.get(identifier)
        if task is None or task.done() and (task.cancelled() or task.exception() is not None):
            task = asyncio.create_task(self._move(identifier))
            self._moving[identifier] = task

    async def prepare(self, token: str, target: dict) -> dict:
        """新しいmodel実行だけを静止し、進行中turnや背景taskの終了は待たない。"""
        self._intents[token] = target
        await self.gate.freeze(token)
        await self._retry_adoptions()
        await self.projections.freeze(token)
        await self.registrations.freeze(token)
        for conversation in tuple(self.managers.conversations.values()):
            with self.managers._bound(conversation):
                for session in tuple(conversation.manager.sessions.values()):
                    if session.turn_completed:
                        backend: Any = conversation.manager._backends.get(session.engine)
                        client = self._client(backend, session.session_id)
                        self.boundary(session, client)
        return self.projections.inventory()

    async def commit_update(self, token: str, current: dict) -> None:
        """遅れた準備で受付を戻さず、保存先を開いてから終了済み会話を移す。"""
        await self.gate.observe_current(current)
        await self.projections.thaw(token)
        self.registrations.thaw(token)
        self._intents.pop(token, None)
        await self.gate.thaw(token)
        for identifier in tuple(self._ended):
            if self._conversation(identifier) is not None and current["generation"] != self.generation:
                self._schedule_move(identifier)
            elif current["generation"] == self.generation:
                found = self._conversation(identifier)
                if found is None:
                    continue
                conversation = found[1]
                with self.managers._bound(conversation):
                    session = conversation.manager.sessions[identifier]
                    backend: Any = conversation.manager._backends.get(session.engine)
                    client = self._client(backend, identifier)
                    if client is not None and client.transferred:
                        auxiliary = await backend.export_service_state(identifier)
                        descriptor = await client.detach()
                        await self._restore_source(backend, session, descriptor, auxiliary)

    async def tick(self) -> bool:
        """受付が先に進んだ場合だけ、未終了の移譲を再確認する。"""
        await self._retry_adoptions()
        retained = {identifier for item in self.managers.conversations.values() for identifier in item.manager.sessions} | set(
            self._pending_adoptions
        )
        for identifier in set(self._ended) - retained:
            self._ended.pop(identifier, None)
            self._ended_clients.pop(identifier, None)
        for identifier, task in tuple(self._moving.items()):
            if task.done():
                if not task.cancelled():
                    task.exception()
                self._moving.pop(identifier, None)
        for identifier, (_transfer, source) in tuple(self._received.items()):
            if identifier not in retained and not service_transport.process_alive(source["process"]):
                self._received.pop(identifier, None)
        self._contexts = {key for key in self._contexts if key.split(":", 1)[1] in self.managers.conversations}
        for identifier, packet in tuple(self._incoming.items()):
            if not service_transport.process_alive(packet["source"]["process"]):
                self._incoming.pop(identifier, None)
        current = self._current()
        await self.gate.observe_current(current)
        if current["generation"] == self.generation:
            return not self._intents
        for conversation in tuple(self.managers.conversations.values()):
            for session in tuple(conversation.manager.sessions.values()):
                if self._ended_turn(session) and session.session_id not in self._uncertain:
                    backend: Any = conversation.manager._backends.get(session.engine)
                    self.boundary(session, self._client(backend, session.session_id))
        for key, conversation in tuple(self.managers.conversations.items()):
            if conversation.manager.sessions or conversation.active:
                continue
            with self.managers._bound(conversation):
                await service_rpc.request(
                    current,
                    "move_context",
                    {
                        "source": self.generation,
                        "parent": key,
                        "caller": _caller_payload(conversation.caller),
                        "expired": self.projections._codec.encode(conversation.manager.expired_sessions),
                        "stopped": self.projections._codec.encode(conversation.manager.stopped_sessions),
                    },
                )
                await self.managers.close_conversation(key)
        return False

    async def _retry_adoptions(self) -> None:
        for identifier, packet in tuple(self._pending_adoptions.items()):
            if self._locks.setdefault(identifier, asyncio.Lock()).locked():
                continue
            found = self._conversation(identifier)
            if found is None:
                continue
            with self.managers._bound(found[1]):
                await self._finish_adoption(found[1].manager, packet)

    async def _finish_adoption(self, manager: Any, packet: dict) -> None:
        session = service_state.restore(packet["session"])
        identifier = session.session_id
        session = manager.sessions[identifier]
        backend = manager._backend(session.engine)
        client = self._client(backend, identifier)
        if packet["resource"] is not None and (client is None or client.transferred):
            await backend.adopt_session(session, packet["resource"], packet["auxiliary"])
            if packet["writer_session_id"] is not None:
                backend._writer_session_ids[identifier] = packet["writer_session_id"]
        await manager.save_control_state()
        manager._service_paused.discard(identifier)
        self._pending_adoptions.pop(identifier, None)

    async def accept_context(self, packet: dict) -> None:
        current = self._current()
        if current["generation"] != self.generation:
            await service_rpc.request(current, "move_context", packet)
            return
        key = packet["source"] + ":" + packet["parent"]
        if key in self._contexts:
            return
        with runtime_environment.bind(_restore_caller(packet["caller"])):
            async with self.managers.scope(
                CallToolRequestParams(name="list", arguments={}),
                identifier_override=packet["parent"],
            ) as manager:
                for attribute, field in (("expired_sessions", "expired"), ("stopped_sessions", "stopped")):
                    for identifier, value in self.projections._codec.decode(packet[field]).items():
                        getattr(manager, attribute).setdefault(identifier, value)
                await manager.save_control_state()
                self._contexts.add(key)

    async def disconnect(self, identifier: str) -> None:
        self.managers.disconnect(identifier)
        current = self._current()
        if current["generation"] != self.generation:
            await service_rpc.request(current, "disconnect", {"connection_id": identifier})

    def _client(self, backend: Any, identifier: str) -> Any:
        if backend is None:
            return None
        return (
            getattr(backend, "_sdk_clients", {}).get(identifier)
            or getattr(backend, "_clients", {}).get(identifier)
            or self._ended_clients.get(identifier)
        )

    async def parent_sessions(self, parent: str, include_terminated: bool) -> list:
        """この世代が保持する、指定した親会話のsessionの一覧項目を返す（別世代からの照会用）。"""
        conversation = self.managers.conversations.get(parent)
        if conversation is None:
            return []
        with self.managers._bound(conversation):
            response = await conversation.manager.list_sessions(include_terminated=include_terminated)
        return list(response["sessions"])

    async def _foreign_sessions(self, parent: str, include_terminated: bool) -> dict[str, list]:
        """同じ親会話のsessionを別の生存世代から集める。ターンの途中の会話は終了境界まで旧世代に残るため。"""
        sessions: list = []
        unreachable: list = []
        for generation, entry in self.registry.generations().items():
            if generation == self.generation or not entry.get("ready") or entry.get("retired"):
                continue
            try:
                target = service_rpc.discovery(self.directory, generation)
            except OSError, ValueError:
                continue
            if not service_transport.process_alive(target["process"]):
                continue
            try:
                sessions.extend(
                    await service_rpc.request(
                        target,
                        "parent_sessions",
                        {"parent": parent, "include_terminated": include_terminated},
                        timeout=_OWNER_PROBE_SECONDS,
                    )
                )
            except (OSError, EOFError, TimeoutError, RuntimeError) as error:
                unreachable.append(
                    {
                        "generation": generation,
                        "server_instance": target.get("server_instance"),
                        "error": f"{type(error).__name__}: {error}",
                        "next_action": (
                            "この世代が所有するsessionは一覧に含まれていない。agents_serverの`list`が返す`server_generations`で"
                            "当該世代のプロセスの生存を確かめ、応答が再開した後にlistを再実行する。"
                            "応答しないままなら当該プロセスを処置する（処置前に自身のMCP接続先でないことを確かめる）"
                        ),
                    }
                )
        return {"sessions": sessions, "unreachable": unreachable}

    def owns(self, parent: str, identifier: str) -> bool:
        conversation = self.managers.conversations.get(parent)
        return conversation is not None and identifier in conversation.manager.sessions

    async def owned_call(self, parent: str, caller: runtime_environment.CallerContext, params: CallToolRequestParams) -> dict:
        """元の親会話の制御を受理し、未配送の次turnは現行側へ返す。"""
        conversation = self.managers.conversations.get(parent)
        identifier = (params.arguments or {}).get("session_id")
        if conversation is None or identifier not in conversation.manager.sessions:
            return {"move_required": True}
        session = conversation.manager.sessions[identifier]
        if identifier in self._pending_adoptions:
            with self.managers._bound(conversation):
                await self._finish_adoption(conversation.manager, self._pending_adoptions[identifier])
        next_turn = params.name == "send_message" and (session.terminal or self._ended_turn(session))
        try:
            async with self.gate.admit(new_turn=next_turn, session_id=identifier):
                conversation.caller = dataclasses.replace(caller, credit_shortages=conversation.caller.credit_shortages)
                conversation.manager._caller_context = conversation.caller
                with self.managers._bound(conversation):
                    selected = mcp_tools._SELECTED_MANAGER.set(conversation.manager)
                    try:
                        result = await mcp_tools.mcp._call_registered_tool(None, params)
                    finally:
                        mcp_tools._SELECTED_MANAGER.reset(selected)
                return {"result": result.model_dump(by_alias=True, exclude_none=True)}
        except service_turns.MoveRequired:
            return {"move_required": True}

    async def call_tool(self, context: Any, params: CallToolRequestParams) -> Any:
        try:
            return await self._call_tool(context, params)
        except ActionableError as error:
            # 世代間の振り分けで生じた診断は登録ツールの外で送出されるため、ツールの例外と同じく
            # 理由と次の操作の2行をツールのエラーとして返す。素通しするとMCPの汎用エラーとなり次の操作が落ちる。
            return CallToolResult(content=[TextContent(type="text", text=error.message)], is_error=True)
        except service_turns.MoveRequired as moved:
            caller = runtime_environment.current()
            assert caller is not None
            value = await service_rpc.request(
                moved.target,
                "public_call",
                {
                    "caller": _caller_payload(caller),
                    "params": params.model_dump(by_alias=True, exclude_none=True),
                },
            )
            return _RESULT.validate_python(value)

    async def _call_tool(self, context: Any, params: CallToolRequestParams) -> Any:
        """schema・説明・結果生成は既存MCP登録を使い、受付と所有の振り分けだけを行う。"""
        caller = runtime_environment.current()
        if caller is None:
            raise RuntimeError("呼出元contextがありません")
        current = self._current()
        if current["generation"] != self.generation:
            value = await service_rpc.request(
                current,
                "public_call",
                {
                    "caller": _caller_payload(caller),
                    "params": params.model_dump(by_alias=True, exclude_none=True),
                },
            )
            return _RESULT.validate_python(value)
        async with self.managers.scope(params) as manager:
            parent = next(key for key, item in self.managers.conversations.items() if item.manager is manager)
            identifier = (params.arguments or {}).get("session_id")
            if isinstance(identifier, str) and identifier not in manager.sessions:
                owner = await self._owner(identifier)
                if owner is not None and owner["generation"] != self.generation and await self._owns(owner, parent, identifier):
                    value = await service_rpc.request(
                        owner,
                        "owned_call",
                        {
                            "parent": parent,
                            "caller": _caller_payload(caller),
                            "params": params.model_dump(by_alias=True, exclude_none=True),
                        },
                    )
                    if not value.get("move_required"):
                        return _RESULT.validate_python(value["result"])
                    moved = await service_rpc.request(owner, "move", {"session_id": identifier})
                    if not moved.get("moved"):
                        raise ActionableError(
                            "移譲可能な終了境界を待っています。新しい入力は未配送です",
                            next_action="showで状態を確認し、現在の処理の終了後に同じ入力を送る",
                        )
                    return await self.call_tool(context, params)
            lock = (
                self._locks.setdefault(identifier, asyncio.Lock()) if isinstance(identifier, str) else contextlib.nullcontext()
            )
            async with lock:
                if isinstance(identifier, str) and identifier in self._pending_adoptions:
                    await self._finish_adoption(manager, self._pending_adoptions[identifier])
                next_turn = (
                    params.name == "start"
                    or params.name == "send_message"
                    and (
                        identifier not in manager.sessions
                        or manager.sessions[identifier].turn_completed
                        or manager.sessions[identifier].terminal
                    )
                )
                async with self.gate.admit(new_turn=next_turn, session_id=identifier):
                    selected = mcp_tools._SELECTED_MANAGER.set(manager)
                    try:
                        return await mcp_tools.mcp._call_registered_tool(context, params)
                    finally:
                        mcp_tools._SELECTED_MANAGER.reset(selected)

    async def _owns(self, owner: dict, parent: str, identifier: str) -> bool:
        """所有世代へ所有を照会する。応答しない世代を待ち続けず、終了済みの世代は登録簿による判定へ委ねる。

        2026-11-17、所有世代が停止・終了した後の照会と停止が、内部の待機上限（270秒）まで戻らないか生の接続エラーで終わった。
        """
        try:
            return bool(
                await service_rpc.request(
                    owner, "owns", {"parent": parent, "session_id": identifier}, timeout=_OWNER_PROBE_SECONDS
                )
            )
        except (OSError, EOFError, TimeoutError) as error:
            if not service_transport.process_alive(owner["process"]):
                # 所有世代が終了している。登録簿の所有者喪失の判定（中断の公開・委譲先CLIの停止）を使う。
                return False
            raise ActionableError(
                f"session {identifier}を所有する世代へ配送できない: owner_generation={owner['generation']}; "
                f"owner_server_instance={owner.get('server_instance')}; owner_process={owner['process']}; "
                f"queried_generation={self.generation}; error={type(error).__name__}: {error}",
                next_action=(
                    "配送は未確定であり、同じ前提で待機や再発行を繰り返さない。"
                    "所有世代のプロセスが応答を再開するかをagents_serverの`list`が返す`server_generations`と`show`で確かめ、"
                    "応答しないままなら所有世代のプロセスを処置する。処置の前に、そのプロセスが自身のMCP接続先"
                    "（`queried_server_instance`）ではないことを確かめる"
                ),
            ) from error

    async def _owner(self, identifier: str) -> dict | None:
        resolution = await asyncio.to_thread(session_registry.resolve, identifier, state_root=self.directory.parent.parent)
        if resolution.server_instance is None:
            return None
        for generation, entry in self.registry.generations().items():
            if not entry.get("ready"):
                continue
            with contextlib.suppress(OSError, ValueError):
                target = service_rpc.discovery(self.directory, generation)
                if target.get("server_instance") == resolution.server_instance:
                    return target
        return None

    async def move(self, identifier: str) -> dict:
        self._schedule_move(identifier)
        return await asyncio.shield(self._moving[identifier])

    async def _move(self, identifier: str) -> dict:
        async with self._locks.setdefault(identifier, asyncio.Lock()):
            if identifier in self._uncertain:
                raise ActionableError(
                    "移譲先の所有を確定できないため、旧decoderとqueryを再開しません",
                    next_action="showと既存登録簿・物理記録で現在の所有を確認して再開する",
                )
            found = self._conversation(identifier)
            if found is None:
                return {"moved": True}
            parent, conversation = found
            manager = conversation.manager
            session = manager.sessions[identifier]
            if not self._ended_turn(session):
                return {"moved": False}
            current = self._current()
            if current["generation"] == self.generation:
                return {"moved": False}
            await self.gate.pause_session(identifier)
            with self.managers._bound(conversation):
                backend: Any = manager._backends.get(session.engine)
                client = self._client(backend, identifier)
                descriptor = None
                auxiliary = {}
                if client is not None:
                    auxiliary = await backend.export_service_state(identifier)
                    client.pause()
                    await self.gate.drain_session(identifier)
                    if session.engine == "codex":
                        await backend._stop_auto_reset(identifier)
                    descriptor = await client.detach()
                    if session.engine == "claude":
                        tasks = [task for task, name in backend._task_sessions.items() if name == identifier]
                        await asyncio.gather(*tasks)
                await manager.save_control_state()
                packet = {
                    "transfer_id": uuid.uuid4().hex,
                    "source": self.target,
                    "parent": parent,
                    "caller": _caller_payload(conversation.caller),
                    "session": service_state.snapshot(session),
                    "resource": descriptor,
                    "auxiliary": auxiliary,
                    "writer_session_id": getattr(backend, "_writer_session_ids", {}).get(identifier),
                }
                try:
                    prepared = await service_rpc.request(current, "prepare_move", packet)
                except Exception:
                    await self._restore_source(backend, session, descriptor, auxiliary)
                    raise
                if not prepared.get("prepared"):
                    await self._restore_source(backend, session, descriptor, auxiliary)
                    return {"moved": False}
                if client is not None and not await client.resource.operation(
                    "transfer",
                    expected_owner=client.resource.runtime.owner,
                    new_owner=current.get("server_instance", current["generation"]),
                ):
                    raise ActionableError("生I/Oの所有が既に変更された", next_action="showで現在の所有を確認する")
                try:
                    accepted = await service_rpc.request(current, "accept", packet)
                except Exception as error:
                    try:
                        accepted = await service_rpc.request(
                            current,
                            "move_status",
                            {
                                "session_id": identifier,
                                "transfer_id": packet["transfer_id"],
                            },
                        )
                    except Exception:
                        self._uncertain[identifier] = str(error)
                        raise ActionableError(
                            f"会話の移譲結果を確定できません: {error}",
                            next_action="queryは再送せず、showと既存登録簿・物理記録で状態を確認して再開する",
                        ) from error
                    if not accepted.get("owned"):
                        if accepted.get("prepared"):
                            accepted = await service_rpc.request(
                                current,
                                "abort_move",
                                {
                                    "session_id": identifier,
                                    "transfer_id": packet["transfer_id"],
                                },
                            )
                            if not accepted.get("owned"):
                                if client is not None:
                                    await client.resource.operation(
                                        "transfer",
                                        expected_owner=current.get("server_instance", current["generation"]),
                                        new_owner=client.resource.runtime.owner,
                                    )
                                await self._restore_source(backend, session, descriptor, auxiliary)
                        if not accepted.get("owned"):
                            raise
                if not accepted.get("owned"):
                    raise RuntimeError("移譲先が制御所有を確定しませんでした")
                manager._cancel_retention_timer(identifier)
                manager.sessions.pop(identifier, None)
                if backend is not None:
                    for name in (
                        "_clients",
                        "_sdk_clients",
                        "_wait_compactors",
                        "_service_compaction_checkpoints",
                        "_writer_session_ids",
                    ):
                        getattr(backend, name, {}).pop(identifier, None)
                self._ended.pop(identifier, None)
                self._ended_clients.pop(identifier, None)
                await manager.save_control_state()
            return {"moved": True}

    async def _restore_source(self, backend: Any, session: Any, descriptor: dict | None, auxiliary: dict) -> None:
        if descriptor is not None:
            assert self._runtime is not None
            resource = service_io.Resource.attach(self._runtime, descriptor)
            if not await resource.operation("transfer", expected_owner=self._runtime.owner, new_owner=self._runtime.owner):
                self._uncertain[session.session_id] = "旧生I/Oの制御所有を確認できません"
                raise RuntimeError(self._uncertain[session.session_id])
            await backend.adopt_session(session, descriptor, auxiliary)
        await self.gate.resume_session(session.session_id)

    def prepare_move(self, packet: dict) -> dict:
        """所有変更前の入力を検証し、モデルを実行しない非永続の受入予約を作る。"""
        _restore_caller(packet["caller"])
        session = service_state.restore(packet["session"])
        transfer = packet["transfer_id"]
        if not isinstance(transfer, str) or len(transfer) != 32:
            raise ValueError("移譲の識別子が不正です")
        received = self._received.get(session.session_id)
        if self._conversation(session.session_id) is not None and (received is None or received[0] != transfer):
            raise ValueError("移譲先が既に同じsessionを所有しています")
        incoming = self._incoming.get(session.session_id)
        lock = self._locks.get(session.session_id)
        if incoming is not None and incoming["transfer_id"] != transfer and lock is not None and lock.locked():
            raise ValueError("先行する所有移譲が処理中です")
        self._incoming[session.session_id] = packet
        return {"prepared": True, "session_id": session.session_id}

    def move_status(self, identifier: str, transfer: str) -> dict:
        incoming = self._incoming.get(identifier)
        received = self._received.get(identifier)
        return {
            "owned": received is not None and received[0] == transfer,
            "prepared": incoming is not None and incoming["transfer_id"] == transfer,
        }

    async def abort_move(self, identifier: str, transfer: str) -> dict:
        # acceptの所有CASが未完了の間は、旧側へ巻き戻し可能と応答しない。
        async with self._locks.setdefault(identifier, asyncio.Lock()):
            received = self._received.get(identifier)
            if received is not None and received[0] == transfer:
                return {"owned": True}
            incoming = self._incoming.get(identifier)
            if incoming is not None and incoming["transfer_id"] == transfer:
                self._incoming.pop(identifier, None)
            return {"owned": False}

    async def accept(self, packet: dict) -> dict:
        """新世代で状態を保存し、次の本来入力を待つ。移譲時のqueryは送信対象から除く。"""
        transfer = packet["transfer_id"]
        identifier = service_state.restore(packet["session"]).session_id
        received = self._received.get(identifier)
        if received is not None and received[0] == transfer:
            return {"owned": True}
        if self._incoming.get(identifier) != packet:
            raise ValueError("移譲の受入予約がありません")
        async with self.gate.admit(new_turn=True, session_id=identifier):
            return await self._accept(packet)

    async def _accept(self, packet: dict) -> dict:
        caller = _restore_caller(packet["caller"])
        params = CallToolRequestParams(name="list", arguments={})
        with runtime_environment.bind(caller):
            async with self.managers.scope(params, identifier_override=packet["parent"]) as manager:
                session = service_state.restore(packet["session"])
                identifier = session.session_id
                async with self._locks.setdefault(identifier, asyncio.Lock()):
                    if self._incoming.get(identifier) != packet:
                        raise ValueError("移譲の受入予約は解除されています")
                    if identifier in manager.sessions:
                        return {"owned": True}
                    owner = session_registry.owner_process()
                    if owner is None:
                        raise RuntimeError("移譲先のプロセス識別を取得できません")
                    transferred = await run_file_operation(
                        session_registry.transfer_owner,
                        identifier,
                        (packet["source"]["server_instance"], session.turn_seq),
                        owner,
                        self.target["server_instance"],
                    )
                    if not transferred:
                        raise RuntimeError("登録簿の所有が移譲元と一致しません。旧結果を保持します")
                    manager.sessions[identifier] = session
                    manager._service_paused.add(identifier)
                    session.publish_registry = True
                    self._received[identifier] = packet["transfer_id"], packet["source"]
                    self._pending_adoptions[identifier] = packet
                    self._incoming.pop(identifier, None)
                    manager._schedule_session_expiry(session)
                    await self._finish_adoption(manager, packet)
                    return {"owned": True}
