"""旧制御主体の登録更新を、同じ管理領域の現行サービスだけに保存させる。"""

from __future__ import annotations

import asyncio
import pathlib
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent_toolkit._agents_server import service_rpc, shared_roots
from agent_toolkit._agents_server.file_operations import finish_task, run_file_operation
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._agents_server.service_state import _StateCodec
from agent_toolkit._common import registry_forwarding, session_registry

_RECOVERY_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agents-server-recovery")


async def restore_generation(registry: GenerationRegistry) -> dict[str, Any] | None:
    """静止の解除に必要な復旧は、登録保存が解除を待つexecutorへ並べない。"""
    return await finish_task(asyncio.get_running_loop().run_in_executor(_RECOVERY_EXECUTOR, registry.restore))


class RegistryForwarder:
    """元のPID・起動単位・effort受理先を保持し、移譲済み所有者への遅延更新を防ぐ。"""

    def __init__(self, generation: str, directory: pathlib.Path) -> None:
        self.generation = generation
        self.registry = GenerationRegistry(directory)
        self.state_root = directory.parent.parent
        self._codec = _StateCodec()
        self._condition = threading.Condition()
        self._freezes: set[str] = set()
        self._operations: dict[str, Callable[..., Any]] = {
            "publish": session_registry.publish,
            "release": session_registry.release,
            "claim_resume": session_registry.claim_resume,
            "release_resume_claim": session_registry.release_resume_claim,
            "record_interrupted_turn": session_registry.record_interrupted_turn,
            "transfer_owner": session_registry.transfer_owner,
            "write_root_alias": shared_roots.write_root_alias,
            "write_host_alias": shared_roots.write_host_alias,
        }

    def forward(self, operation: str, arguments: dict[str, Any]) -> tuple[bool, Any]:
        """モデルや認証値を送らず、既存登録操作の引数と所有者だけを転送する。"""
        if operation not in self._operations:
            raise ValueError("未対応の登録簿更新操作")
        identity = registry_forwarding.PublicationIdentity(
            session_registry.owner_process(), session_registry.owner_server_instance(), session_registry.effort_receiver()
        )
        normalized = {
            key: str(value) if key == "state_root" and value is not None else value for key, value in arguments.items()
        }
        payload = {
            "operation": operation,
            "arguments": self._codec.encode(normalized),
            "identity": {
                "owner": self._codec.encode(identity.owner),
                "server_instance": identity.server_instance,
                "effort_receiver": self._codec.encode(identity.effort_receiver),
            },
        }
        return True, self._apply_sync(payload)

    async def freeze(self, token: str) -> None:
        await run_file_operation(self._freeze, token)

    def _freeze(self, token: str) -> None:
        with self._condition:
            self._freezes.add(token)

    def thaw(self, token: str) -> None:
        with self._condition:
            self._freezes.discard(token)
            self._condition.notify_all()

    async def apply(self, payload: dict[str, Any]) -> Any:
        """OS認証済みRPCの許可された登録操作だけを、元の所有者識別で実行する。"""
        return await run_file_operation(self._apply_sync, payload)

    def _apply_sync(self, payload: dict[str, Any]) -> Any:
        # 同期APIのworkerをloop経由で同じexecutorへ戻すと、多接続で空き枠がなくなり保存が進まない。
        # 保存と世代の静止を同じ同期区間へ集め、通常の保存は元のworkerで完走させる。
        while True:
            with self._condition:
                target = self.registry.current()
                if target is None or target["generation"] == self.generation:
                    if self._freezes:
                        self._condition.wait()
                        continue
                    return self._save(payload)
            try:
                return service_rpc.request_sync(target, "registry_mutation", payload)
            except FileNotFoundError, ConnectionRefusedError:
                # 受付先の世代は終了している。接続前の失敗なので配送は起きておらず、戻した受付先で保存し直せる。
                restored = _RECOVERY_EXECUTOR.submit(self.registry.restore).result()
                if restored is None or restored["process"] == target["process"]:
                    raise

    def _save(self, payload: dict[str, Any]) -> Any:
        operation = payload.get("operation")
        if operation not in self._operations:
            raise ValueError("未対応の登録簿更新操作")
        arguments = self._codec.decode(payload["arguments"])
        if not isinstance(arguments, dict):
            raise ValueError("登録操作の引数がobjectではありません")
        arguments["state_root"] = self.state_root
        raw_identity = payload["identity"]
        identity = registry_forwarding.PublicationIdentity(
            self._codec.decode(raw_identity["owner"]),
            raw_identity["server_instance"],
            self._codec.decode(raw_identity["effort_receiver"]),
        )
        if not isinstance(identity.owner, session_registry.ProcessIdentity):
            raise ValueError("登録を所有するプロセスの識別が不正です")

        with registry_forwarding.bind(None, owner=identity):
            return self._operations[operation](**arguments)
