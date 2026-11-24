from __future__ import annotations

# 同じservice内のManager・writer・backendを、所有移譲の単一境界で協調させる。
# pylint: disable=protected-access
import asyncio
import contextlib
import dataclasses
import logging
import pathlib
import uuid
import weakref
from collections.abc import AsyncGenerator
from typing import Any

from agent_toolkit._agents_server import (
    resume_waits,
    service_io,
    service_rpc,
    service_transport,
    service_turns,
)
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.service_context import Conversation, ServiceManagers
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._agents_server.service_move_source import MoveSource
from agent_toolkit._agents_server.service_projection import ProjectionHub, seed_from_previous
from agent_toolkit._agents_server.service_registry import RegistryForwarder, restore_generation
from agent_toolkit._agents_server.service_session_control import ServiceSessionControl, _caller_payload
from agent_toolkit._common import file_lock

_LOG = logging.getLogger("agent-toolkit.agents-server.service")


_PUBLISHER_PROBE_SECONDS = 1.0

# 初回の確定からの期限と、確定が届かなかった旧世代への再送間隔。
# 確認ごとに世代索引のロックを取るため、退役を記録する旧世代とロックを取り合わない程度に間隔を空ける。
_COMMIT_WAIT_SECONDS = 10.0
_COMMIT_RETRY_SECONDS = 0.25


@dataclasses.dataclass
class _Update:
    publisher: dict
    frozen: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)
    committed: asyncio.Future[dict] = dataclasses.field(default_factory=lambda: asyncio.get_running_loop().create_future())
    released: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)


class ServiceCoordinator(ServiceSessionControl):
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
        self._ended_sdk_clients: dict[str, Any] = {}
        self._moving: dict[str, asyncio.Task[dict]] = {}
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._force_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._updates: dict[str, _Update] = {}
        self._runtime: service_io.Runtime | None = None
        self._publish_lock = asyncio.Lock()
        self._incoming: dict[str, dict] = {}
        self._received: dict[str, tuple[str, dict]] = {}
        self._uncertain: dict[str, str] = {}
        self._pending_restores: dict[str, MoveSource] = {}
        self._contexts: set[str] = set()
        self._pending_adoptions: dict[str, dict] = {}
        self._supervisors: set[asyncio.Task[None]] = set()
        self.managers.before_controls = self.tick
        self.managers.foreign_sessions = self._foreign_sessions

    async def publish(self) -> dict:
        async with self._publish_lock:
            with (self.directory / "publish.lock").open("a+", encoding="utf-8") as handle:
                await run_file_operation(file_lock.acquire_lock, handle)
                previous = None
                token = uuid.uuid4().hex
                try:
                    current = await restore_generation(self.registry)

                    entry = self.registry.generations().get(self.generation)
                    if entry is not None:
                        self.target["epoch"] = entry["epoch"]
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
                        await seed_from_previous(self.projections, self.directory, current, self.target, token)
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
                            await self._commit(previous, token)
                    finally:
                        file_lock.release_lock(handle)

    async def _commit(self, previous: dict, token: str) -> None:
        """確定を再送し、応答を失った場合は今回の移転の確定記録で判断する。"""
        arguments = {"token": token, "current": self._current()}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _COMMIT_WAIT_SECONDS
        first_error: OSError | EOFError | None = None
        try:
            async with asyncio.timeout_at(deadline):
                while True:
                    try:
                        await service_rpc.request(previous, "commit_update", arguments, timeout=deadline - loop.time())
                        return
                    except (OSError, EOFError) as error:
                        first_error = first_error or error

                    alive = service_transport.process_alive(previous["process"])
                    if await run_file_operation(self.registry.handover_confirmed, self.target, previous):
                        return
                    if not alive:
                        raise RuntimeError(
                            "旧世代が終了し、会話移転の確定記録がありません。"
                            "旧世代の会話は新世代へ移っていないことがあります: "
                            f"generation={previous['generation']} process={previous['process']} "
                            f"error={type(first_error).__name__}: {first_error}"
                        ) from first_error
                    await asyncio.sleep(_COMMIT_RETRY_SECONDS)
        except TimeoutError as timeout:
            raise first_error or timeout from None

    def bind_runtime(self, runtime: service_io.Runtime) -> service_io.Runtime:
        self._runtime = dataclasses.replace(
            runtime,
            boundary=self.boundary,
            started=self.started,
            gate=self.gate,
            input_scope=self.input_scope,
        )
        return self._runtime

    def started(self, session: Any) -> None:
        self._ended.pop(session.session_id, None)
        self._ended_sdk_clients.pop(session.session_id, None)

    @contextlib.asynccontextmanager
    async def input_scope(self, session: Any) -> AsyncGenerator[bool]:
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
        current = self.registry.current()
        if current is not None and current["process"] == self.target["process"]:
            self.target["epoch"] = current["epoch"]
        return current or self.target

    def _superseded(self) -> bool:
        return self._current()["epoch"] > self.target["epoch"]

    def holds_handover(self) -> bool:
        return bool(
            self.managers.conversations
            or self._runtime is None
            or self._runtime.broker._resources
            or self._incoming
            or self._pending_adoptions
        )

    def can_retire(self) -> bool:
        return self._superseded() and not self._updates and not self.holds_handover()

    def _conversation(self, identifier: str) -> tuple[str, Conversation] | None:
        return next(
            ((key, item) for key, item in self.managers.conversations.items() if identifier in item.manager.sessions), None
        )

    def boundary(self, session: Any, client: Any) -> None:
        self._ended[session.session_id] = session.turn_seq, session.turn_id
        # SDKは所有taskの終了時にbackendの参照が消える。Codexは解放までbackend自身が所有する。
        if session.engine == "claude" and client is not None:
            self._ended_sdk_clients[session.session_id] = client
        current = self._current()
        if current["generation"] == self.generation and not self._updates:
            return
        found = self._conversation(session.session_id)
        if found is None:
            return
        if client is not None:
            client.pause()
        if current["generation"] != self.generation:
            self._schedule_move(session.session_id)

    def _ended_turn(self, session: Any) -> bool:
        return session.turn_completed or self._ended.get(session.session_id) == (session.turn_seq, session.turn_id)

    def _schedule_move(self, identifier: str) -> None:
        task = self._moving.get(identifier)
        if task is not None and not task.done():
            return
        if task is not None and not task.cancelled():
            task.exception()
        self._moving[identifier] = asyncio.create_task(self._move(identifier))

    async def prepare(self, token: str, target: dict) -> dict:
        update = _Update(target)
        self._updates[token] = update
        supervisor = asyncio.create_task(self._supervise(token, update))
        self._supervisors.add(supervisor)
        supervisor.add_done_callback(self._supervised)
        try:
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
        finally:
            update.frozen.set()
        return self.projections.inventory()

    async def commit_update(self, token: str, current: dict) -> None:
        update = self._updates.get(token)
        if update is None:
            return
        if not update.committed.done():
            update.committed.set_result(current)
        await update.released.wait()

    async def _supervise(self, token: str, update: _Update) -> None:
        """公開元の確定、受付先の前進、公開元の終了のうち最初に起きたものを受けて、準備が掛けた静止を1回だけ解く。

        公開元は受付を公開した後で必ず確定を送るため、受付先が自世代より新しい世代へ進んだことは確定と同じ意味を持つ。
        確定が一時的な通信の失敗で届かなくても、常駐する公開元の終了を待たずに静止を解き、退役できる。
        静止中は共有監視の周期処理も保存の待機で止まり得るため、周期処理に頼らず公開元の生存と受付先を確かめる。
        生存する公開元が確定を通知せず受付先も進まない間は、準備中の引継ぎを失わないよう時間では解かない。
        """
        try:
            while (
                not update.committed.done()
                and service_transport.process_alive(update.publisher["process"])
                and not self._superseded()
            ):
                await asyncio.wait((update.committed,), timeout=_PUBLISHER_PROBE_SECONDS)
            await update.frozen.wait()
            if update.committed.done():
                current = update.committed.result()
            else:
                await restore_generation(self.registry)
                current = self._current()
            await self.gate.observe_current(current)
        finally:
            # どの段が失敗しても後続の解除を続け、待機側を解くreleasedは全段の解除の後に立てる。
            # 例外は連鎖して_supervisedが記録する。
            try:
                await self.projections.thaw(token)
            finally:
                try:
                    self.registrations.thaw(token)
                finally:
                    self._updates.pop(token, None)
                    try:
                        await self.gate.thaw(token)
                    finally:
                        update.released.set()
                        # 準備中に終了境界を迎えたclientの取り込み直しの契機はここだけのため、段の失敗でも必ず実行する。
                        for identifier in tuple(self._ended):
                            self._schedule_move(identifier)

    def _supervised(self, supervisor: asyncio.Task[None]) -> None:
        self._supervisors.discard(supervisor)
        if not supervisor.cancelled() and (error := supervisor.exception()) is not None:
            _LOG.error("準備の静止の解除で失敗しました", exc_info=error)

    async def tick(self) -> bool:
        """未確定の旧所有者の復旧を再試行し、受付が先に進んでいれば終了した会話を移す。"""
        await self._retry_adoptions()
        retained = {identifier for item in self.managers.conversations.values() for identifier in item.manager.sessions} | set(
            self._pending_adoptions
        )
        for identifier in set(self._ended) - retained:
            self._ended.pop(identifier, None)
            self._ended_sdk_clients.pop(identifier, None)
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
        if self._current()["generation"] != self.generation:
            await restore_generation(self.registry)
        current = self._current()
        await self.gate.observe_current(current)
        for identifier in tuple(self._pending_restores):
            if self._conversation(identifier) is None:
                del self._pending_restores[identifier]
            elif not self._updates:
                self._schedule_move(identifier)
        if current["generation"] == self.generation:
            return not self._updates
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
        for identifier in tuple(self._pending_adoptions):
            lock = self._locks.setdefault(identifier, asyncio.Lock())
            if not lock.locked():
                async with lock:
                    await self._finish_adoption(identifier)

    async def _reclaim_unadopted(self, descriptor: dict) -> bool:
        assert self._runtime is not None
        resource = service_io.Resource.attach(self._runtime, descriptor)
        if not descriptor.get("closed"):
            try:
                await resource.operation("release")
            except KeyError, OSError, EOFError, RuntimeError:
                # 前回の応答だけを失った場合、releaseは解放済みの資源を拒む。
                # discardは未解放や所有者違いなら失敗し、解放済みか登録が無ければ成功する。
                _LOG.debug("未取り込み資源の解放結果を確認します: %s", resource.identifier, exc_info=True)
            else:
                descriptor["closed"] = True
        try:
            await resource.operation("discard")
        except OSError, EOFError, RuntimeError:
            if not descriptor.get("closed") or service_transport.process_alive(resource.origin["process"]):
                _LOG.warning("未取り込み資源の回収を再試行します: %s", resource.identifier, exc_info=True)
                return False
        return True

    async def _finish_adoption(self, identifier: str) -> None:
        """移譲で受け取ったsessionのraw資源を取り込み、状態を保存してから制御を受け付ける。

        保存の失敗などで残った取り込み待ちは、本体が保持期限や会話の回収で解放された後も残る。
        取り込み済みのraw資源は本体の解放が回収し、再開情報は会話の文脈に残るため、失うものは無く待ちを終える。
        未取り込みのraw資源は元のbrokerで回収できるまで待ちを残す。
        """
        packet = self._pending_adoptions.get(identifier)
        if packet is None:
            return
        found = self._conversation(identifier)
        if found is None:
            if packet["resource"] is not None and not await self._reclaim_unadopted(packet["resource"]):
                return
            conversation = self.managers.conversations.get(packet["parent"])
            if conversation is not None:
                conversation.manager._service_paused.discard(identifier)
            self._pending_adoptions.pop(identifier, None)
            return
        manager = found[1].manager
        with self.managers._bound(found[1]):
            session = manager.sessions[identifier]
            backend: Any = manager._backend(session.engine)
            client = self._client(backend, identifier)
            if packet["resource"] is not None and (client is None or client.transferred):
                await backend.adopt_session(session, packet["resource"], packet["auxiliary"])
                if packet["writer_session_id"] is not None:
                    backend._writer_session_ids[identifier] = packet["writer_session_id"]

            packet["resource"] = None
            await manager.save_control_state()
            manager._service_paused.discard(identifier)
            self._pending_adoptions.pop(identifier, None)
