from __future__ import annotations

# pylint: disable=protected-access
import asyncio
import contextlib
import dataclasses
import json
import os
import pathlib
import uuid
import weakref
from collections.abc import AsyncGenerator, Awaitable
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolRequestParams, CallToolResult

from agent_toolkit._agents_server import mcp_tools, service_rpc
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._common import reclaim_records, session_launchers, session_registry
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.next_action import ActionableError

if TYPE_CHECKING:
    from agent_toolkit._agents_server.service_context import ServiceManagers


def _launch_fence_path(state_root: pathlib.Path, identifier: str) -> pathlib.Path:
    return session_registry.registry_directory(state_root) / ".force" / identifier


def _launch_fence(state_root: pathlib.Path, launcher: str) -> dict[str, str]:
    directory = session_registry.registry_directory(state_root)
    epochs: dict[str, str] = {}
    while launcher not in epochs and session_launchers.valid_launcher(launcher):
        try:
            epochs[launcher] = _launch_fence_path(state_root, launcher).read_text(encoding="utf-8")
        except FileNotFoundError:
            epochs[launcher] = ""
        parent = session_launchers.read_launcher(directory / f"{launcher}.json")
        if parent is None:
            break
        launcher = parent
    return epochs


def _active_launch_fence(value: str) -> bool:
    if not value.startswith("active:"):
        return False
    owner = session_registry.ProcessIdentity.parse(json.loads(value.split(":", 2)[2]))
    return owner.exists()


def _change_launch_fence(state_root: pathlib.Path, identifier: str, token: str, *, begin: bool) -> None:
    path = _launch_fence_path(state_root, identifier)
    with session_registry.registry_lock(identifier, state_root=state_root):
        if begin:
            try:
                current = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                current = ""
            if _active_launch_fence(current):
                raise ActionableError("強制停止の処理中です", next_action="先行した停止の結果を確認する")
            path.parent.mkdir(parents=True, exist_ok=True)
        if begin:
            owner = session_registry.ProcessIdentity.of(os.getpid())
            if owner is None:
                raise RuntimeError("強制停止を所有するプロセスの識別を確定できません")
            active = json.dumps(session_registry.process_payload(owner))
            atomic_write(path, f"active:{token}:{active}")
        else:
            atomic_write(path, token)


@contextlib.asynccontextmanager
async def _force_launch_fence(state_root: pathlib.Path, identifier: str) -> AsyncGenerator[None]:
    token = uuid.uuid4().hex
    acquired = False

    def mark_acquired(_unused: None) -> None:
        nonlocal acquired
        acquired = True

    try:
        await run_file_operation(_change_launch_fence, state_root, identifier, token, begin=True, on_success=mark_acquired)
        yield
    finally:
        if acquired:
            await run_file_operation(_change_launch_fence, state_root, identifier, token, begin=False)


def _pending_launch_dir(state_root: pathlib.Path) -> pathlib.Path:
    return session_registry.registry_directory(state_root) / ".force" / "pending-starts"


def _store_pending_launch(path: pathlib.Path, launcher: str, ancestors: tuple[str, ...]) -> None:
    owner = session_registry.ProcessIdentity.of(os.getpid())
    if owner is None:
        raise RuntimeError("起動を所有するプロセスの識別を確定できません")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps({"launcher": launcher, "ancestors": ancestors, "owner": owner.payload()}))


@contextlib.asynccontextmanager
async def _pending_launch(state_root: pathlib.Path, launcher: str) -> AsyncGenerator[tuple[pathlib.Path, dict[str, str]]]:
    path = _pending_launch_dir(state_root) / f"{uuid.uuid4().hex}.json"
    epoch = await run_file_operation(_launch_fence, state_root, launcher)
    try:
        await run_file_operation(_store_pending_launch, path, launcher, tuple(epoch))
        yield path, epoch
    finally:
        if not path.with_suffix(".failed").exists():
            await run_file_operation(path.unlink, missing_ok=True)


@dataclasses.dataclass(frozen=True)
class _LaunchFailure:
    ticket: pathlib.Path
    launcher: str
    child: str
    attempts: frozenset[str]


def _launch_failure_attempts(state_root: pathlib.Path, launcher: str) -> frozenset[str]:
    return frozenset(
        fence.split(":", 2)[1] for fence in _launch_fence(state_root, launcher).values() if _active_launch_fence(fence)
    )


def _record_launch_failure(state_root: pathlib.Path, ticket: pathlib.Path, launcher: str, child: str) -> None:
    attempts = sorted(_launch_failure_attempts(state_root, launcher))
    atomic_write(ticket.with_suffix(".failed"), json.dumps({"child": child, "attempts": attempts}))


def _read_launch_failure(ticket: pathlib.Path, launcher: str) -> _LaunchFailure | None:
    try:
        raw = ticket.with_suffix(".failed").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    if not raw.startswith("{"):
        return _LaunchFailure(ticket, launcher, raw, frozenset())
    data = json.loads(raw)
    return _LaunchFailure(ticket, launcher, data["child"], frozenset(data["attempts"]))


def _retire_launch_failure(state_root: pathlib.Path, failure: _LaunchFailure) -> None:
    if failure.attempts & _launch_failure_attempts(state_root, failure.launcher):
        return
    failure.ticket.unlink(missing_ok=True)
    failure.ticket.with_suffix(".failed").unlink(missing_ok=True)


def _pending_launches(state_root: pathlib.Path, identifier: str) -> tuple[bool, list[_LaunchFailure], list[str]]:
    pending = False
    failures: list[_LaunchFailure] = []
    lost: list[str] = []
    for path in _pending_launch_dir(state_root).glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        launcher = record["launcher"]
        if identifier not in record["ancestors"] and identifier not in _launch_fence(state_root, launcher):
            continue
        if failure := _read_launch_failure(path, launcher):
            failures.append(failure)
        elif session_registry.ProcessIdentity.parse(record["owner"]).exists():
            pending = True
        elif path.exists():
            lost.append(launcher)
    return pending, failures, lost


def _descendant_launchers(children: dict[str, list[str]], identifier: str) -> dict[str, str]:
    result: dict[str, str] = {}
    pending = [(identifier, child) for child in children.get(identifier, ())]
    while pending:
        launcher, child = pending.pop()
        if child == identifier or child in result:
            continue
        result[child] = launcher
        pending.extend((child, item) for item in children.get(child, ()))
    return result


type _TurnStamp = tuple[
    int | None,
    str | None,
    str | None,
    session_registry.Resolution,
    session_registry.ProcessIdentity | None,
    tuple[session_registry.ProcessIdentity, ...],
    bool,
]


def _turn_stamp(resolution: session_registry.SessionResolution) -> _TurnStamp:
    return (
        resolution.recorded_turn_seq,
        resolution.resume_info.turn_id if resolution.resume_info is not None else None,
        resolution.server_instance,
        resolution.state,
        resolution.owner_process,
        resolution.executor_processes,
        resolution.resume_pending,
    )


def _force_target(name: str, arguments: dict[str, Any]) -> str | None:
    if name != "kill" or arguments.get("force", False) is False:
        return None
    identifier = arguments.get("session_id")
    timeout = arguments.get("timeout", 270)
    if (
        arguments.get("force") is not True
        or not isinstance(identifier, str)
        or not session_launchers.valid_launcher(identifier)
        or not isinstance(timeout, (int, float))
        or isinstance(timeout, bool)
        or not 0 <= timeout < float("inf")
        or not isinstance(arguments.get("stop", False), bool)
    ):
        raise ActionableError(
            "強制停止の引数が不正のため未配送です", next_action="session_id、timeout、stopの型と値を確認して送り直す"
        )
    return identifier


class ServiceForceControl:
    if TYPE_CHECKING:
        directory: pathlib.Path
        generation: str
        managers: ServiceManagers
        _force_locks: weakref.WeakValueDictionary[str, asyncio.Lock]

        async def _owner(self, identifier: str) -> dict | None: ...  # pylint: disable=unused-argument

    async def _require_unfenced(self, identifier: str) -> dict[str, str]:
        def read_unfenced() -> dict[str, str]:
            epoch = _launch_fence(self.directory.parent.parent, identifier)
            if any(_active_launch_fence(value) for value in epoch.values()):
                raise ActionableError("委譲経路の強制停止中のため要求は未配送です", next_action="停止結果を確認する")
            return epoch

        return await run_file_operation(read_unfenced)

    async def _recheck_send_epoch(self, identifier: object, epoch: dict[str, str] | None) -> None:
        if epoch is None:
            return
        assert isinstance(identifier, str)
        if await self._require_unfenced(identifier) != epoch:
            raise ActionableError(
                "受付待ちの間に強制停止が行われたため追送は未配送です", next_action="停止結果を確認してから追送する"
            )

    async def force_owned(self, launcher: str, identifier: str, timeout: float) -> None:
        async with self._force_locks.setdefault(identifier, asyncio.Lock()):
            await self._force_owned_once(launcher, identifier, timeout)

    async def _force_owned_once(self, launcher: str, identifier: str, timeout: float) -> None:
        for parent, conversation in tuple(self.managers.conversations.items()):
            manager = conversation.manager
            if (
                identifier not in manager.sessions
                and identifier not in manager._pending_resumes
                and identifier not in manager._active_sends
            ) or (parent != launcher and manager.launcher_session_id() != launcher):
                continue
            params = CallToolRequestParams(name="kill", arguments={"session_id": identifier, "timeout": timeout, "force": True})
            async with self.managers.handoff_scope(conversation):
                selected = mcp_tools._SELECTED_MANAGER.set(manager)
                try:
                    result = await mcp_tools.mcp._call_registered_tool(None, params)
                finally:
                    mcp_tools._SELECTED_MANAGER.reset(selected)
            if isinstance(result, CallToolResult) and not result.is_error:
                details = result.structured_content or {}
                if details.get("status") in {"interrupted", "completed", "failed"}:
                    return
            break
        raise ActionableError(
            f"下位session {identifier} の所有または停止を確認できません",
            next_action="管理サービスの登録簿を照会し、所有を確認してから再試行する",
        )

    async def _stop_descendant(self, launcher: str, child: str, timeout: float) -> None:
        owner = await self._owner(child)
        if owner is not None and owner["generation"] != self.generation:
            await service_rpc.request(owner, "force_owned", {"launcher": launcher, "session_id": child, "timeout": timeout})
        else:
            await self.force_owned(launcher, child, timeout)

    async def _force_dispatch[T](self, identifier: str, timeout: float, root: Awaitable[T]) -> T:
        result, descendants = await asyncio.gather(root, self._force_children(identifier, timeout), return_exceptions=True)
        if isinstance(result, BaseException):
            raise result
        if isinstance(descendants, BaseException):
            raise descendants
        return result

    async def _force_children(self, identifier: str, timeout: float, *, unconfirmed_launcher: str | None = None) -> None:
        state_root = self.directory.parent.parent
        deadline = asyncio.get_running_loop().time() + (timeout or 30)
        try:
            async with asyncio.timeout_at(deadline):
                await self._reconcile_force_children(identifier, timeout, state_root, unconfirmed_launcher)
        except TimeoutError as exc:
            raise ActionableError(
                "受付済みの下位の起動または実行の停止を期限までに確認できません",
                next_action="下位の実行状態を確認し、停止を再試行する",
            ) from exc

    async def _reconcile_force_children(
        self, identifier: str, timeout: float, state_root: pathlib.Path, unconfirmed_launcher: str | None
    ) -> None:
        attempts = await run_file_operation(_launch_failure_attempts, state_root, identifier)
        verified: dict[str, _TurnStamp] = {}
        quiet = False
        while True:
            children = await run_file_operation(session_registry.delegation_children, state_root=state_root)
            descendants = _descendant_launchers(children, identifier)
            pending, failures, lost = await run_file_operation(_pending_launches, state_root, identifier)
            if (descendants or pending or failures or lost) and (
                unconfirmed_launcher is not None and identifier not in children.get(unconfirmed_launcher, ())
            ):
                raise ActionableError(
                    f"session {identifier} の起動元を確認できないため、下位session は未停止です",
                    next_action="元の委譲元会話から強制停止を再試行する",
                )
            current_failures = [failure for failure in failures if attempts & failure.attempts]
            earlier_failures = [failure for failure in failures if failure not in current_failures]
            retry = {failure.child: failure.launcher for failure in earlier_failures}
            ready: dict[str, tuple[str, _TurnStamp]] = {}
            for child, launcher in (descendants | retry).items():
                resolution = await run_file_operation(session_registry.resolve, child, state_root=state_root)
                stamp = _turn_stamp(resolution)
                if verified.get(child) == stamp:
                    continue
                must_retry = child in retry and (
                    resolution.state is not session_registry.Resolution.TERMINAL
                    or any(
                        process.exists() or reclaim_records.pending(process.pid, process.create_time)
                        for process in resolution.executor_processes
                    )
                )
                if resolution.state is session_registry.Resolution.RUNNING or resolution.resume_pending or must_retry:
                    ready[child] = launcher, stamp
                else:
                    verified[child] = stamp
            outcomes = await asyncio.gather(
                *(self._stop_descendant(launcher, child, timeout) for child, (launcher, _) in ready.items()),
                return_exceptions=True,
            )
            for child, outcome in zip(ready, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    raise outcome
                verified[child] = ready[child][1]
            if current_failures or lost:
                launcher = current_failures[0].launcher if current_failures else lost[0]
                raise ActionableError(
                    f"下位の起動または回収の完了を確認できません: launcher={launcher}",
                    next_action="下位の起動と回収の記録を確認し、残る資源を停止する",
                )
            for failure in earlier_failures:
                if failure.child in verified:
                    await run_file_operation(_retire_launch_failure, state_root, failure)
            if not ready and not pending and quiet:
                return
            quiet = not pending
            if pending and not ready:
                await asyncio.sleep(0.02)
