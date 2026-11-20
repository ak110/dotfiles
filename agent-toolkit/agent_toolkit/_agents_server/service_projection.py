"""実行中turnの制御状態を複製せず、状態表示・結果回収のwriterを現行世代へ集める。"""

from __future__ import annotations

# StatusFileWriterの保存・回収状態を移譲する実装であり、同じpackageの内部契約を共有する。
# pylint: disable=protected-access
import asyncio
import dataclasses
import pathlib
from typing import Any

from agent_toolkit._agents_server import service_rpc, service_state, shared_roots, state, status_file
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._common.next_action import ActionableError


class _RetryProjection(Exception):
    pass


class ProjectionHub:
    """会話ごとの観測用コピーだけで既存のStatusFileWriterを動かす。"""

    def __init__(self, generation: str, directory: pathlib.Path) -> None:
        self.generation = generation
        self.registry = GenerationRegistry(directory)
        self.state_root = directory.parent.parent
        self.loop = asyncio.get_running_loop()
        self.writers: dict[tuple[str, str], status_file.StatusFileWriter] = {}
        self.groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._codec = service_state._StateCodec()
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._epochs = {name: entry["epoch"] for name, entry in self.registry.generations().items()}
        self._freezes: set[str] = set()
        self._available = asyncio.Event()
        self._available.set()
        self._update_lock = asyncio.Lock()

    async def request(self, identity: shared_roots.StatusFileIdentity, operation: str, arguments: dict) -> Any:
        """受付先が進んだ場合も同じ観測元とwriter名を保って転送する。"""
        payload = {
            "identity": dataclasses.asdict(identity),
            "source": self.generation,
            "operation": operation,
            "arguments": arguments,
        }
        return self._codec.decode(await self.apply(payload))

    async def close(self) -> None:
        """予約した保存を回収し、現在もこの世代が持つ射影だけを終了する。"""
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
        """保存とtimerを回収してから静止し、移譲先の準備中は旧ファイルを書かない。"""
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
                    "published": list(writer._published_results),
                }
                for key, writer in self.writers.items()
            ]
        }

    async def seed(self, inventory: dict) -> None:
        """受付公開前はファイルを書かず、受け取った観測をメモリーへ保持する。"""
        for item in inventory["writers"]:
            await self._apply_local({"identity": item["identity"], "operation": "seed", "arguments": item})

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
        previous = writer.sessions
        combined: dict[str, state.SessionState] = {}
        expired = {}
        ordered = sorted(self.groups[key].items(), key=lambda item: self._epochs.get(item[0], 0))
        for _source, group in ordered:
            for item in group["sessions"]:
                session = service_state.restore(item)
                prior = previous.get(session.session_id)
                if prior is not None and (prior.turn_seq, prior.finalized_at) == (
                    session.turn_seq,
                    session.finalized_at,
                ):
                    session.result_delivered |= prior.result_delivered
                # 観測用の状態は登録簿の制御所有者を書き換えない。
                session.publish_registry = False
                combined[session.session_id] = session
                if prior is None and session.session_id in group["published"]:
                    writer._published_results.add(session.session_id)
            expired.update(self._codec.decode(group["expired"]))
        writer._sessions = combined
        writer._expired_sessions = expired
        await writer.flush_async()
        return {
            "delivered": [
                [session.session_id, session.turn_seq, session.finalized_at]
                for session in combined.values()
                if session.result_delivered
            ],
            "published": list(writer._published_results),
        }

    async def apply(self, payload: dict) -> Any:
        """現在もwriterを所有する世代だけが、許可した射影操作を保存する。"""
        while True:
            target = self.registry.current()
            if target is not None and target["generation"] != self.generation:
                try:
                    return await service_rpc.request(target, "projection", payload)
                except FileNotFoundError, ConnectionRefusedError:
                    # 受付先の世代は終了している。接続前の失敗なので配送は起きておらず、戻した受付先で保存し直せる。
                    restored = await run_file_operation(self.registry.restore)
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
                # 世代更新はsourceの保存停止後に同じファイルを引き継ぐ。起動時の別名writerは作らない。
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


class ServiceStatusWriter(status_file.StatusFileWriter):
    """制御辞書をManagerに残し、共有ファイルへの変更だけをhubへ渡す。"""

    def __init__(self, sessions, identity, hub: ProjectionHub, **kwargs) -> None:
        super().__init__(sessions, identity, state_root=hub.state_root, **kwargs)
        self._hub = hub

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
            sessions = tuple(self.sessions.values())
            for session in sessions:
                await session.drain_registration()
                await session.publish_registration_async()
            observed = {
                "sessions": [await service_state.snapshot(session) for session in sessions],
                "expired": self._hub._codec.encode(self.expired_sessions),
                "published": list(self._published_results),
            }
            saved = await self._hub.request(self._identity, "snapshot", observed)
            self._published_results = set(saved["published"])
            for identifier, turn, finalized in saved["delivered"]:
                session = self.sessions.get(identifier)
                if session is not None and (session.turn_seq, session.finalized_at) == (turn, finalized):
                    session.result_delivered = True

    async def deactivate_async(self) -> None:
        self._active = False
        for handle in (self._flush_handle, self._retention_handle):
            if handle is not None:
                handle.cancel()
        self._flush_handle = self._retention_handle = None
        await self._hub.request(self._identity, "deactivate", {})

    async def result_state_async(self, session_id: str) -> str:
        return await self._hub.request(self._identity, "result_state_async", {"session_id": session_id})

    async def delete_result_async(self, session_id: str, *, collector: str) -> None:
        await self._hub.request(self._identity, "delete_result_async", {"session_id": session_id, "collector": collector})
        self._published_results.discard(session_id)

    async def take_result_async(self, session_id: str, *, collector: str, expected_turn_seq: int | None = None):
        result = await self._hub.request(
            self._identity,
            "take_result_async",
            {
                "session_id": session_id,
                "collector": collector,
                "expected_turn_seq": expected_turn_seq,
            },
        )
        if result[0] is not None:
            self._published_results.discard(session_id)
        return result

    async def take_notices_async(self, session_id: str):
        return await self._hub.request(self._identity, "take_notices_async", {"session_id": session_id})

    async def retain_result_async(self, session: state.SessionState | state.SessionResumeState) -> None:
        if isinstance(session, state.SessionState):
            value = {"state": await service_state.snapshot(session)}
        else:
            value = {"resume": self._hub._codec.encode(session)}
        await self._hub.request(self._identity, "retain_result_async", {"session": value})

    def delete_wait_target(self, session_id: str) -> None:
        # Managerは同期ファイル操作をworkerで実行する。loop上では同期RPCを待たない。
        future = asyncio.run_coroutine_threadsafe(
            self._hub.request(self._identity, "delete_wait_target", {"session_id": session_id}), self._hub.loop
        )
        future.result(timeout=270)
