from __future__ import annotations

# pylint: disable=protected-access
import asyncio
import pathlib
import weakref
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolRequestParams

from agent_toolkit._agents_server import service_rpc, service_state, service_turns
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.service_move_source import MoveSource
from agent_toolkit._common import runtime_environment, session_registry
from agent_toolkit._common.next_action import ActionableError

if TYPE_CHECKING:
    from agent_toolkit._agents_server.service_context import Conversation, ServiceManagers
    from agent_toolkit._agents_server.service_io import Runtime


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


class ServiceSessionMove:
    if TYPE_CHECKING:
        target: dict[str, Any]
        generation: str
        managers: ServiceManagers
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
        _pending_adoptions: dict[str, dict]

        @abstractmethod
        def _current(self) -> dict: ...
        @abstractmethod
        def _conversation(self, identifier: str) -> tuple[str, Conversation] | None: ...  # pylint: disable=unused-argument
        def _ended_turn(self, session: Any) -> bool: ...  # pylint: disable=unused-argument
        def _schedule_move(self, identifier: str) -> None: ...  # pylint: disable=unused-argument
        @abstractmethod
        def _client(self, backend: Any, identifier: str) -> Any: ...  # pylint: disable=unused-argument
        async def _finish_adoption(self, identifier: str) -> None: ...  # pylint: disable=unused-argument

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
                # pyfltr: allow-comment 移譲先の予約前は旧側が所有するので、失敗時は制御を旧側へ戻す。
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
