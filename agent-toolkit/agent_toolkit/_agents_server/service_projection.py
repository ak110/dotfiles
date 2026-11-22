from __future__ import annotations

# StatusFileWriterの保存・回収状態を移譲する実装であり、同じpackageの内部契約を共有する。
# pylint: disable=protected-access
import asyncio
import dataclasses
import pathlib
from typing import Any

from agent_toolkit._agents_server import (
    retained_results,
    service_rpc,
    service_state,
    shared_layout,
    shared_roots,
    state,
    status_file,
    status_reader,
    turn_state,
)
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._agents_server.service_registry import restore_generation
from agent_toolkit._common.next_action import ActionableError

type SlotEnds = set[tuple[str, retained_results.ResultEnd]]


@dataclasses.dataclass
class LegacyObservation:
    projection: dict
    slots: SlotEnds


type FrozenSlots = dict[tuple[str, str], LegacyObservation]


def observe_legacy_slots(state_root: pathlib.Path, process: str) -> FrozenSlots:
    slots: FrozenSlots = {}
    pid = process.partition(":")[0]
    for root in shared_layout.list_root_session_ids(state_root):
        for path in shared_layout.list_status_files(root, state_root):
            try:
                status = status_reader.read_payload(path)
            except OSError, UnicodeError, ValueError:
                continue
            writer = status.get("writer_process")
            rows = status.get("sessions")
            if not isinstance(writer, dict) or str(writer.get("pid")) != pid or not isinstance(rows, list):
                continue
            for row in rows:
                if (
                    not isinstance(row, dict)
                    or "turn_seq" in row
                    or row.get("status") not in ("completed", "failed", "interrupted")
                ):
                    continue
                identifier = row.get("session_id")
                if not isinstance(identifier, str) or not shared_layout.valid_session_id(identifier):
                    continue
                original = shared_layout.results_directory(root, state_root) / f"{identifier}.json"
                try:
                    end = retained_results.legacy_publication(original)
                except OSError, UnicodeError, ValueError:
                    continue
                if end is not None:
                    slots.setdefault((root, path.name), LegacyObservation(status, set())).slots.add((identifier, end))
    return slots


class _RetryProjection(Exception):
    pass


class ProjectionHub:
    def __init__(self, generation: str, directory: pathlib.Path) -> None:
        self.generation = generation
        self.registry = GenerationRegistry(directory)
        self.state_root = directory.parent.parent
        self.loop = asyncio.get_running_loop()
        self.writers: dict[tuple[str, str], status_file.StatusFileWriter] = {}
        self.groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._legacy_slots: dict[tuple[str, str], SlotEnds] = {}
        self._codec = service_state._StateCodec()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._epochs = {name: entry["epoch"] for name, entry in self.registry.generations().items()}
        self._freezes: set[str] = set()
        self._available = asyncio.Event()
        self._available.set()
        self._update_lock = asyncio.Lock()

    async def request(self, identity: shared_roots.StatusFileIdentity, operation: str, arguments: dict) -> Any:
        payload = {
            "identity": dataclasses.asdict(identity),
            "source": self.generation,
            "operation": operation,
            "arguments": arguments,
        }
        return self._codec.decode(await self.apply(payload))

    async def close(self) -> None:
        current = self.registry.current()
        failures = []
        for writer in tuple(self.writers.values()):
            try:
                await writer.drain(cancel_scheduled=True)
                if current is None or current["generation"] == self.generation:
                    await writer.deactivate_async()
            except Exception as error:
                failures.append(error)
        if failures:
            raise ExceptionGroup("射影の保存を回収できませんでした", failures)

    async def freeze(self, token: str) -> None:
        async with self._update_lock:
            if token in self._freezes:
                return
            self._freezes.add(token)
            acquired = []
            try:
                for lock in tuple(self._locks.values()):
                    await lock.acquire()
                    acquired.append(lock)
                for writer in tuple(self.writers.values()):
                    await writer.drain(cancel_scheduled=True)
                    async with writer._write_lock:
                        for handle in (writer._flush_handle, writer._retention_handle):
                            if handle is not None:
                                handle.cancel()
                        writer._flush_handle = writer._retention_handle = None
                self._available.clear()
            finally:
                for lock in reversed(acquired):
                    lock.release()

    async def thaw(self, token: str) -> None:
        async with self._update_lock:
            self._freezes.discard(token)
            current = self.registry.current()
            if not self._freezes or current is not None and current["generation"] != self.generation:
                self._available.set()

    def inventory(self) -> dict:
        """環境・認証を含めず、既存結果の公開台帳と観測コピーだけを渡す。"""
        return {
            "writers": [
                {
                    "identity": dataclasses.asdict(writer._identity),
                    "groups": dict(self.groups[key]),
                    "turn_receipts": True,
                    "published": list(writer._published_results),
                }
                for key, writer in self.writers.items()
            ]
        }

    async def seed(self, inventory: dict, *, observed: FrozenSlots | None = None) -> None:
        for item in inventory["writers"]:
            identity = item["identity"]
            key = identity["root_session_id"], identity["file_name"]
            await self._apply_local({"identity": identity, "operation": "seed", "arguments": item})
            if not item.get("turn_receipts"):
                self._legacy_slots[key] = await run_file_operation(
                    self._legacy_handover, self.writers[key], item, (observed or {}).get(key)
                )

    async def activate(self) -> None:
        """受付を公開した後、書込主体ごとにロックを取った時点の観測から状態ファイルを書く。

        ロックの外で選んだ観測を書くと、ロックを待つ間に旧世代が保存した進捗やeffortを古い値へ戻す。
        """
        for key in tuple(self.writers):
            async with self._locks.setdefault(key, asyncio.Lock()):
                if self.groups.get(key):
                    await self._render(key)

    async def _render(self, key: tuple[str, str]) -> dict:
        """書込主体が持つ各世代の観測を世代順に重ねて状態ファイルへ書く。呼出元は書込主体のロックを持つ。"""
        writer = self.writers[key]
        if key in self._legacy_slots:
            for identifier, end in self._legacy_slots[key]:
                await run_file_operation(retained_results.mark_published, writer._result_path(identifier), end)
            del self._legacy_slots[key]
        previous = writer.sessions
        combined: dict[str, state.SessionState] = {}
        expired = {}
        ordered = sorted(self.groups[key].items(), key=lambda item: self._epochs.get(item[0], 0))
        for _source, group in ordered:
            for item in group["sessions"]:
                fields = group.get("source_results", {}).get(item["session_id"]["value"], {})
                session = service_state.restore({**item, **fields})
                prior = previous.get(session.session_id)
                if prior is not None and (prior.turn_seq, prior.finalized_at) == (
                    session.turn_seq,
                    session.finalized_at,
                ):
                    session.result_delivered |= prior.result_delivered

                if (
                    session.result_delivered
                    and not group.get("turn_receipts")
                    and await writer.result_state_async(session.session_id, session=session) == "published"
                ):
                    session.result_delivered = False

                session.publish_registry = False
                combined[session.session_id] = session
                if prior is None and session.session_id in group["published"]:
                    writer._published_results.add(session.session_id)
            expired.update(self._codec.decode(group["expired"]))
        writer._sessions = combined
        writer._expired_sessions = expired
        await writer.flush_async()
        return {
            "turn_receipts": True,
            "delivered": [
                [session.session_id, session.turn_seq, session.finalized_at]
                for session in combined.values()
                if session.result_delivered
            ],
            "published": list(writer._published_results),
        }

    def _legacy_handover(
        self, writer: status_file.StatusFileWriter, inventory: dict, observed: LegacyObservation | None
    ) -> SlotEnds:
        slots = set(observed.slots) if observed is not None else set()
        # 静止までに旧hubがもう一度保存した場合、以前見た原本とは別のturnも旧CLIへ届き得る。
        seen = set()
        if observed is not None:
            try:
                if status_reader.read_payload(writer.path) == observed.projection:
                    seen = {identifier for identifier, _ in slots}
            except OSError, UnicodeError, ValueError:
                pass
        listed = set(inventory["published"])
        latest = {}
        for source in sorted(inventory["groups"], key=lambda name: self._epochs.get(name, 0)):
            group = inventory["groups"][source]
            for item in group["sessions"]:
                identifier = item["session_id"]["value"]
                latest[identifier] = group, item
                if identifier in group["published"]:
                    listed.add(identifier)
        for identifier, (group, item) in latest.items():
            if group.get("turn_receipts"):
                continue
            session = service_state.restore(item)
            if session.finalized_at is None or not session.result_available:
                continue

            missing = (session.turn_seq, session.finalized_at) if identifier not in seen and identifier in listed else None
            if end := retained_results.legacy_publication(writer._result_path(identifier), missing=missing):
                slots.add((identifier, end))
        return slots

    async def apply(self, payload: dict) -> Any:
        while True:
            target = self.registry.current()
            if target is not None and target["generation"] != self.generation:
                try:
                    return await service_rpc.request(target, "projection", payload)
                except FileNotFoundError, ConnectionRefusedError:
                    # 受付先の世代は終了している。接続前の失敗なので配送は起きておらず、戻した受付先で保存し直せる。
                    restored = await restore_generation(self.registry)
                    if restored is None or restored["process"] == target["process"]:
                        raise
                    continue
            await self._available.wait()
            try:
                return await self._apply_local(payload)
            except _RetryProjection:
                continue

    async def _apply_local(self, payload: dict) -> Any:
        identity = shared_roots.StatusFileIdentity(**payload["identity"])
        if (
            not shared_roots.valid_session_id(identity.root_session_id)
            or not identity.file_name.endswith(".json")
            or not shared_roots.valid_session_id(identity.file_name[:-5])
            or identity.host_session_id is not None
            and not shared_roots.valid_session_id(identity.host_session_id)
        ):
            raise ValueError("射影する書込主体の識別が不正です")
        key = identity.root_session_id, identity.file_name
        async with self._locks.setdefault(key, asyncio.Lock()):
            target = self.registry.current()
            if payload["operation"] != "seed" and (
                not self._available.is_set() or target is not None and target["generation"] != self.generation
            ):
                raise _RetryProjection
            writer = self.writers.get(key)
            if writer is None:
                writer = status_file.StatusFileWriter({}, identity, state_root=self.state_root)
                if payload["operation"] != "seed" and await run_file_operation(
                    status_file._held_by_other_live_writer,
                    writer.path,
                ):
                    raise ActionableError(
                        "同じ会話の状態は旧MCP接続が所有しています。旧側の仕事と結果を保持しました",
                        next_action="初回移行では起動中の会話を旧接続のまま使う。会話終了後の通常起動で新版へ移る",
                    )

                writer._active = True
                self.writers[key] = writer
                self.groups[key] = {}
            operation, arguments = payload["operation"], payload["arguments"]
            if operation == "seed":
                self.groups[key].update(arguments["groups"])
                writer._published_results.update(arguments["published"])
                result = None
            elif operation == "snapshot":
                self.groups[key][payload["source"]] = arguments
                result = await self._render(key)
            elif operation == "deactivate":
                self.groups[key].pop(payload["source"], None)
                if not self.groups[key]:
                    await writer.drain(cancel_scheduled=True)
                    await writer.deactivate_async()
                    self.writers.pop(key)
                    self.groups.pop(key)
                    self._legacy_slots.pop(key, None)
                else:
                    # 残る世代の観測から、同じロックの中で書き直す。ロックを離してから書くと、その間の保存を古い値へ戻す。
                    await self._render(key)
                result = None
            elif operation in {
                "result_state_async",
                "delete_result_async",
                "take_result_async",
                "take_notices_async",
                "retain_result_async",
                "delete_wait_target",
            }:
                values = dict(arguments)
                if "session" in values:
                    item = values["session"]
                    values["session"] = (
                        service_state.restore(item["state"]) if "state" in item else self._codec.decode(item["resume"])
                    )
                if operation == "delete_wait_target":
                    result = await run_file_operation(writer.delete_wait_target, **values)
                else:
                    result = await getattr(writer, operation)(**values)
            else:
                raise ValueError("未対応の射影操作です")
            return self._codec.encode(result)


async def seed_from_previous(hub: ProjectionHub, directory: pathlib.Path, previous: dict, target: dict, token: str) -> None:
    observed = await run_file_operation(observe_legacy_slots, directory.parent.parent, previous["process"])
    inventory = await service_rpc.request(previous, "prepare_update", {"token": token, "target": target})
    await hub.seed(inventory, observed=observed)


class ServiceStatusWriter(status_file.StatusFileWriter):
    def __init__(self, sessions, identity, hub: ProjectionHub, **kwargs) -> None:
        super().__init__(sessions, identity, state_root=hub.state_root, **kwargs)
        self._hub = hub
        self._admitted = False

    async def activate_async(self) -> None:
        self._active = True
        await self.flush_async()

    async def flush_async(self) -> None:
        async with self._write_lock:
            if not self._active:
                return
            if self._flush_handle is not None:
                self._flush_handle.cancel()
                self._flush_handle = None
            waiting = dict(self._recovering)
            sessions = tuple(self.sessions.values())
            for session in sessions:
                await session.drain_registration()
                await session.publish_registration_async()
            snapshots = []
            source_results = {}
            ends = {}
            for session in sessions:
                item = await service_state.snapshot(session)
                if waiting.pop(session.session_id, None) is not None:
                    item["announced"] = self._hub._codec.encode(True)
                snapshots.append(item)
                if session.finalized_at is not None and (session.result_available or session.result_delivered):
                    identifier = session.session_id
                    ends[identifier] = session.turn_seq, session.finalized_at
                    source_results[identifier] = await self._prepare_result_view(item)
            for projection in waiting.values():
                snapshots.append(await service_state.snapshot(projection))
            if self._admitted:
                await run_file_operation(
                    retained_results.expose_legacy_slots,
                    shared_layout.results_directory(self.root_session_id, self._state_root),
                    self._identity.file_name,
                    sessions=set(self.sessions) | self.expired_sessions.keys(),
                )
            await self._hub.request(
                self._identity,
                "snapshot",
                {
                    "turn_receipts": True,
                    "source_results": source_results,
                    "sessions": snapshots,
                    "expired": self._hub._codec.encode(self.expired_sessions),
                    "published": [],
                },
            )
            self._admitted = True
            for identifier, end in ends.items():
                observed = await run_file_operation(retained_results.result_state, self._result_path(identifier), end)
                session = self.sessions.get(identifier)
                if observed == "consumed" and session is not None and (session.turn_seq, session.finalized_at) == end:
                    session.result_delivered = True

    async def _prepare_result_view(self, item: dict) -> dict:
        session = service_state.restore(item)
        await self._ensure_admitted(session.session_id)
        if session.result_delivered:
            await self._discard_delivered_async(session)
        else:
            await run_file_operation(
                retained_results.publish_result, self._result_path(session.session_id), self._result_payload(session)
            )
        fields = {key: item[key] for key in ("turn_end", "result_delivered", "retention_deadline")}

        item["turn_end"] = self._hub._codec.encode(turn_state.TurnEnd.IN_PROGRESS)
        item["result_delivered"] = self._hub._codec.encode(False)
        if await self.result_state_async(session.session_id, session=session) == "consumed":
            item["retention_deadline"] = self._hub._codec.encode(0.0)
        return fields

    async def _ensure_admitted(self, identifier: str) -> None:
        """初回の射影が既に終了結果を含む場合も、既存writerとの競合を確かめてから書く。"""
        if not self._admitted:
            await self._hub.request(self._identity, "result_state_async", {"session_id": identifier})
            self._admitted = True

    async def deactivate_async(self) -> None:
        self._active = False
        self._admitted = False
        for handle in (self._flush_handle, self._retention_handle):
            if handle is not None:
                handle.cancel()
        self._flush_handle = self._retention_handle = None
        await self._hub.request(self._identity, "deactivate", {})

    async def result_state_async(self, session_id: str, *, session=None) -> str:

        if session is None:
            local = self.sessions.get(session_id) or self.expired_sessions.get(session_id)
            if local is None or local.finalized_at is None:
                return await self._hub.request(self._identity, "result_state_async", {"session_id": session_id})
        return await super().result_state_async(session_id, session=session)

    async def read_notices_async(self, session_id: str):
        await self._ensure_admitted(session_id)
        return await super().read_notices_async(session_id)

    async def take_notices_async(self, session_id: str):
        return await self._hub.request(self._identity, "take_notices_async", {"session_id": session_id})

    async def retain_result_async(self, session: state.SessionState | state.SessionResumeState) -> None:
        if isinstance(session, state.SessionState):
            while session.registration_pending:
                await session.drain_registration()
        await self._ensure_admitted(session.session_id)
        await super().retain_result_async(session)

    def delete_wait_target(self, session_id: str) -> None:
        # Managerは同期ファイル操作をworkerで実行する。loop上では同期RPCを待たない。
        future = asyncio.run_coroutine_threadsafe(
            self._hub.request(self._identity, "delete_wait_target", {"session_id": session_id}), self._hub.loop
        )
        future.result(timeout=270)
