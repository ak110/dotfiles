from __future__ import annotations

# 分割前と同じサービス内の所有状態を、このmixinと実体の間で共有する。
# pylint: disable=protected-access
import asyncio
import contextlib
import dataclasses
import os
import pathlib
import weakref
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolRequestParams, CallToolResult, InputRequiredResult, TextContent
from pydantic import TypeAdapter

from agent_toolkit._agents_server import mcp_tools, service_rpc, service_state, service_transport, service_turns
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.service_move_source import MoveSource
from agent_toolkit._common import runtime_environment, session_registry
from agent_toolkit._common.next_action import ActionableError

if TYPE_CHECKING:
    from agent_toolkit._agents_server.service_context import Conversation, ServiceManagers
    from agent_toolkit._agents_server.service_generations import GenerationRegistry
    from agent_toolkit._agents_server.service_io import Runtime
    from agent_toolkit._agents_server.service_projection import ProjectionHub

_RESULT = TypeAdapter(CallToolResult | InputRequiredResult)
_OWNER_PROBE_SECONDS = 10.0


def _caller_payload(caller: runtime_environment.CallerContext) -> dict:
    return {
        "environment": dict(caller.environment),
        "cwd": str(caller.cwd),
        "connection_id": caller.connection_id,
        "parent_settings": caller.parent_settings,
        "credit_shortages": dict(caller.credit_shortages),
        "host_process": caller.host_process,
    }


async def _forward_call(target: dict, operation: str, params: CallToolRequestParams, arguments: dict) -> Any:
    try:
        return await service_rpc.request(
            target,
            operation,
            {**arguments, "params": params.model_dump(by_alias=True, exclude_none=True)},
            mark_unsent=params.name == "send_message",
        )
    except service_rpc.RequestNotSentError as error:
        if params.name != "send_message":
            raise
        raise ActionableError(
            f"管理サービスに接続できず、追送は未配送です: {error}",
            next_action="接続先の状態を確認し、通信が復旧してから同じ指示を送り直す",
        ) from error
    except (OSError, EOFError, RuntimeError) as error:
        if params.name != "send_message":
            raise
        raise ActionableError(
            f"追送の配送成否は未確定です: {type(error).__name__}: {error}",
            next_action="同じ指示を直ちに再送せず、showや受信側の記録で配送を確かめる",
        ) from error


def _restore_caller(value: dict) -> runtime_environment.CallerContext:
    return runtime_environment.CallerContext(
        value["environment"],
        pathlib.Path(value["cwd"]),
        dict(value["credit_shortages"]),
        value["connection_id"],
        value["parent_settings"],
        value.get("host_process"),
    )


class ServiceSessionControl:
    if TYPE_CHECKING:  # pylint: disable=unused-argument
        target: dict[str, Any]
        generation: str
        directory: pathlib.Path
        managers: ServiceManagers
        projections: ProjectionHub
        registry: GenerationRegistry
        gate: service_turns.TurnGate
        _ended: dict[str, tuple[int, str | None]]
        _ended_sdk_clients: dict[str, Any]
        _moving: dict[str, asyncio.Task[dict]]
        _locks: weakref.WeakValueDictionary[str, asyncio.Lock]
        _updates: dict[str, Any]
        _runtime: Runtime | None
        _incoming: dict[str, dict]
        _received: dict[str, tuple[str, dict]]
        _uncertain: dict[str, str]
        _pending_restores: dict[str, MoveSource]
        _contexts: set[str]
        _pending_adoptions: dict[str, dict]

        @abstractmethod
        def _current(self) -> dict: ...

        @abstractmethod
        def _conversation(self, identifier: str) -> tuple[str, Conversation] | None: ...
        def _ended_turn(self, session: Any) -> bool: ...  # pylint: disable=unused-argument
        def _schedule_move(self, identifier: str) -> None: ...  # pylint: disable=unused-argument
        async def _finish_adoption(self, identifier: str) -> None: ...  # pylint: disable=unused-argument

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
            or self._ended_sdk_clients.get(identifier)
        )

    async def parent_sessions(self, parent: str, include_terminated: bool) -> list:
        conversation = self.managers.conversations.get(parent)
        if conversation is None:
            return []
        with self.managers._bound(conversation):
            response = await conversation.manager.list_sessions(include_terminated=include_terminated)
        return list(response["sessions"])

    async def _foreign_sessions(self, parent: str, include_terminated: bool) -> dict[str, list]:
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
        conversation = self.managers.conversations.get(parent)
        identifier = (params.arguments or {}).get("session_id")
        if conversation is None or identifier not in conversation.manager.sessions:
            return {"move_required": True}
        session = conversation.manager.sessions[identifier]
        await self._finish_adoption(identifier)
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
            try:
                return await self._call_tool(context, params)
            except service_turns.MoveRequired as moved:
                caller = runtime_environment.current()
                assert caller is not None
                value = await _forward_call(moved.target, "public_call", params, {"caller": _caller_payload(caller)})
                return _RESULT.validate_python(value)
        except ActionableError as error:
            return CallToolResult(content=[TextContent(type="text", text=error.message)], is_error=True)

    async def _call_tool(self, context: Any, params: CallToolRequestParams) -> Any:
        caller = runtime_environment.current()
        if caller is None:
            raise RuntimeError("呼出元contextがありません")
        current = self._current()
        if current["generation"] != self.generation:
            value = await _forward_call(current, "public_call", params, {"caller": _caller_payload(caller)})
            return _RESULT.validate_python(value)
        async with self.managers.scope(params) as manager, contextlib.AsyncExitStack() as scopes:
            parent = next(key for key, item in self.managers.conversations.items() if item.manager is manager)
            arguments = params.arguments or {}
            identifier = arguments.get("session_id")
            predecessor = arguments.get("handoff_from") if params.name == "start" else None
            inherited_owner = (
                self.managers.handoff_child_owner(parent, identifier)
                if params.name in {"show", "send_message"} and isinstance(identifier, str)
                else None
            )
            if inherited_owner is not None and isinstance(identifier, str):
                await scopes.enter_async_context(self.managers.handoff_scope(inherited_owner))
                manager = inherited_owner.manager
                if params.name == "send_message":
                    child = (
                        manager.sessions.get(identifier)
                        or manager.expired_sessions.get(identifier)
                        or manager.stopped_sessions.get(identifier)
                    )
                    if child is None or not pathlib.Path(child.cwd).is_absolute():
                        raise ActionableError(
                            "所有managerの子sessionに絶対cwdの記録がなく、追送は未配送です",
                            next_action="所有managerの起動記録でcwdを確認し、確認できるまで追送しない",
                        )
            if inherited_owner is None and isinstance(identifier, str) and identifier not in manager.sessions:
                owner = await self._owner(identifier)
                if owner is not None and owner["generation"] != self.generation and await self._owns(owner, parent, identifier):
                    value = await _forward_call(
                        owner,
                        "owned_call",
                        params,
                        {"parent": parent, "caller": _caller_payload(caller)},
                    )
                    if not value.get("move_required"):
                        return mcp_tools.relay_result(params.name, _RESULT.validate_python(value["result"]), owner, self.target)
                    try:
                        moved = await service_rpc.request(owner, "move", {"session_id": identifier})
                    except (RuntimeError, OSError, EOFError, TimeoutError) as error:
                        raise ActionableError(
                            f"session {identifier}の所有世代からの移譲を確認できず、新しい入力は未配送: "
                            f"owner_generation={owner['generation']}; error={type(error).__name__}: {error}",
                            next_action=(
                                "showで状態と所有世代を確認してから同じ入力を送り直す。"
                                "同じ失敗が続く場合は、errorが示す所有世代での失敗の原因を解消してから送り直す"
                            ),
                        ) from error
                    if not moved.get("moved"):
                        raise ActionableError(
                            "移譲可能な終了境界を待っています。新しい入力は未配送です",
                            next_action="showで状態を確認し、現在の処理の終了後に同じ入力を送る",
                        )
                    return await self.call_tool(context, params)
            if isinstance(identifier, str) and identifier in self._pending_restores and (task := self._moving.get(identifier)):
                # 巡回が予約した所有確認に合流する。照会自身は再試行を増やさない。
                await asyncio.wait((task,))
            lock_id = predecessor if isinstance(predecessor, str) else identifier
            lock = self._locks.setdefault(lock_id, asyncio.Lock()) if isinstance(lock_id, str) else None
            show_local = (
                params.name == "show"
                and isinstance(identifier, str)
                and identifier in manager.sessions
                and identifier not in self._pending_restores
                and identifier not in self._uncertain
                and lock is not None
                and lock.locked()
            )
            if lock is not None and lock.locked() and params.name in {"kill", "stop"}:
                # 先行の追送が終わるまで待つと、停止の上限が受付前の待機だけで尽きる。
                raise ActionableError(
                    "同じsessionへの先行要求が受付中のため停止要求は未配送です",
                    next_action="showで状態を確認し、先行要求の受付が終わった後に停止を再試行する",
                )
            if show_local:
                selected = mcp_tools._SELECTED_MANAGER.set(manager)
                try:
                    return await mcp_tools.mcp._call_registered_tool(context, params)
                finally:
                    mcp_tools._SELECTED_MANAGER.reset(selected)
            lock_scope = lock if lock is not None else contextlib.nullcontext()
            async with lock_scope:
                if isinstance(identifier, str):
                    if identifier in self._pending_restores or identifier in self._uncertain:
                        raise ActionableError(
                            f"session {identifier}の所有を確認できず、要求は未配送です",
                            next_action="管理サービスの登録簿で所有世代を確認し、通信の復旧後に再照会する",
                        )
                    await self._finish_adoption(identifier)
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
                        result = await mcp_tools.mcp._call_registered_tool(context, params)
                    finally:
                        mcp_tools._SELECTED_MANAGER.reset(selected)
                    if params.name == "start" and isinstance(result, CallToolResult) and not result.is_error:
                        details = result.structured_content or {}
                        successor = details.get("session_id")
                        if isinstance(successor, str) and successor in manager.sessions:
                            self.managers.conversations[parent].started_children.add(successor)
                            if isinstance(predecessor, str) and details.get("status") in {"running", "completed"}:
                                self.managers.handoff_children(manager, predecessor, successor)
                    return result

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
                f"session {identifier}の所有照会に失敗し、要求は未配送です: owner_generation={owner['generation']}; "
                f"owner_server_instance={owner.get('server_instance')}; owner_process={owner['process']}; "
                f"queried_generation={self.generation}; error={type(error).__name__}: {error}",
                next_action=(
                    "同じ前提で待機や再発行を繰り返さない。"
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
        if os.name == "nt" and resolution.state is session_registry.Resolution.RUNNING:
            raise ActionableError(
                f"session {identifier}の所有世代は現在の管理サービスの名前空間にありません",
                next_action=(
                    "元のsessionを起動した時と同じ昇格状態のホストでshowを実行する。"
                    "所有状況を確認するまでは同じ送信や停止を繰り返さない"
                ),
            )
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
            backend: Any = manager._backends.get(session.engine)
            current = self._current()
            source_is_current = current["generation"] == self.generation
            if source_is_current and self._updates:
                return {"moved": False}
            with self.managers._bound(conversation):
                if pending := self._pending_restores.get(identifier):
                    await self._restore_source(manager, pending)
                    return {"moved": False}
                if not source_is_current and not self._ended_turn(session):
                    return {"moved": False}
                client = self._client(backend, identifier)
                if source_is_current and (client is None or not client.transferred):
                    return {"moved": False}
                source = MoveSource(client, backend, session)
                try:
                    if not source_is_current:
                        await self.gate.pause_session(identifier)
                        source.client = self._client(backend, identifier)
                    elif session.engine == "codex":
                        await self.gate.pause_session(identifier, drain=False)
                    await source.detach(self.gate, source_is_current=source_is_current)
                except Exception, asyncio.CancelledError:
                    if session.engine == "codex":
                        await self._restore_source(manager, source)
                    raise
                if source_is_current:
                    await self._restore_source(manager, source)
                    return {"moved": False}
                # 切り離した後、移譲先が受入を予約するまでに失敗したら所有を移さず、実行資源と制御の受付を旧側へ戻す。
                try:
                    await source.save_control_state(manager)
                    packet = await source.packet(self.target, parent, _caller_payload(conversation.caller))
                    prepared = await service_rpc.request(current, "prepare_move", packet)
                except Exception, asyncio.CancelledError:
                    await self._restore_source(manager, source)
                    raise
                if not prepared.get("prepared"):
                    await self._restore_source(manager, source)
                    return {"moved": False}
                if (client := source.client) is not None and not await client.resource.operation(
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
                                await self._restore_source(manager, source)
                        if not accepted.get("owned"):
                            raise
                if not accepted.get("owned"):
                    raise RuntimeError("移譲先が制御所有を確定しませんでした")
                source.forget(manager)
                self._ended.pop(identifier, None)
                self._ended_sdk_clients.pop(identifier, None)
                await manager.save_control_state()
            return {"moved": True}

    async def _restore_source(self, manager: Any, source: MoveSource) -> None:
        identifier = source.session.session_id
        if source.session.engine == "codex":
            manager._service_paused.add(identifier)
        try:
            await source.confirm_owner(self._runtime)
        except (PermissionError, RuntimeError, KeyError) as error:
            self._pending_restores.pop(identifier, None)
            self._uncertain[identifier] = str(error)
            raise
        except OSError, EOFError, asyncio.CancelledError:
            if source.session.engine == "codex":
                self._pending_restores[identifier] = source
            raise
        self._pending_restores.pop(identifier, None)
        await source.restore()
        await self.gate.resume_session(identifier)
        if source.session.engine == "codex":
            manager._service_paused.discard(identifier)

    def prepare_move(self, packet: dict) -> dict:
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

        async with self._locks.setdefault(identifier, asyncio.Lock()):
            received = self._received.get(identifier)
            if received is not None and received[0] == transfer:
                return {"owned": True}
            incoming = self._incoming.get(identifier)
            if incoming is not None and incoming["transfer_id"] == transfer:
                self._incoming.pop(identifier, None)
            return {"owned": False}

    async def accept(self, packet: dict) -> dict:
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
                    await self._finish_adoption(identifier)
                    return {"owned": True}
