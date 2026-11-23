"""所有を移す前に止めた生I/Oの復旧位置。"""

from __future__ import annotations

# 移譲元のbackendとmanagerが持つ実行資源を、同じsessionの境界で操作する。
# pylint: disable=protected-access
import asyncio
import dataclasses
import uuid
from typing import Any

from agent_toolkit._agents_server import service_io, service_state, service_turns


@dataclasses.dataclass
class MoveSource:
    client: Any
    backend: Any
    session: Any
    descriptor: dict | None = None
    auxiliary: dict = dataclasses.field(default_factory=dict)

    async def detach(self, gate: service_turns.TurnGate, *, source_is_current: bool) -> None:
        identifier = self.session.session_id
        if self.client is not None:
            if not source_is_current:
                self.client.pause()
                await gate.drain_session(identifier)
                if self.session.engine == "codex":
                    await self.backend._stop_auto_reset(identifier)
            self.descriptor = await self.client.detach()

    async def save_control_state(self, manager: Any) -> None:
        identifier = self.session.session_id
        if self.client is not None and self.session.engine == "claude":
            tasks = [task for task, name in self.backend._task_sessions.items() if name == identifier]
            await asyncio.gather(*tasks)
        await manager.save_control_state()

    async def packet(self, target: dict, parent: str, caller: dict) -> dict:
        return {
            "transfer_id": uuid.uuid4().hex,
            "source": target,
            "parent": parent,
            "caller": caller,
            "session": await service_state.snapshot(self.session),
            "resource": self.descriptor,
            "auxiliary": self.auxiliary,
            "writer_session_id": getattr(self.backend, "_writer_session_ids", {}).get(self.session.session_id),
        }

    async def confirm_owner(self, runtime: service_io.Runtime | None) -> None:
        if self.descriptor is not None:
            assert runtime is not None
            resource = service_io.Resource.attach(runtime, self.descriptor)
            if not await resource.operation("transfer", expected_owner=runtime.owner, new_owner=runtime.owner):
                raise PermissionError("旧生I/Oの制御所有を確認できません")
        elif self.session.engine == "codex" and self.client is not None and self.client.transferred:
            await self.client.resume()

    async def restore(self) -> None:
        if self.descriptor is not None:
            await self.backend.adopt_session(self.session, self.descriptor, self.auxiliary)

    def forget(self, manager: Any) -> None:
        identifier = self.session.session_id
        manager._cancel_retention_timer(identifier)
        manager.sessions.pop(identifier, None)
        if self.backend is not None:
            for name in (
                "_clients",
                "_sdk_clients",
                "_writer_session_ids",
            ):
                getattr(self.backend, name, {}).pop(identifier, None)
