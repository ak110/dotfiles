from __future__ import annotations

# 分割前と同じサービス内の所有状態を、このmixinと実体の間で共有する。
# pylint: disable=protected-access
import asyncio
import contextlib
import dataclasses
import os
import pathlib
from typing import TYPE_CHECKING, Any

from mcp.types import CallToolRequestParams, CallToolResult, InputRequiredResult, TextContent
from pydantic import TypeAdapter

from agent_toolkit._agents_server import manager_start, mcp_tools, plugin_root, service_rpc, service_transport, service_turns
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.service_force import (
    ServiceForceControl,
    _force_launch_fence,
    _force_target,
    _launch_fence,
    _pending_launch,
    _record_launch_failure,
)
from agent_toolkit._agents_server.service_session_move import ServiceSessionMove, _caller_payload, _restore_caller
from agent_toolkit._agents_server.service_start_requests import StartRequests
from agent_toolkit._common import runtime_environment, session_launchers, session_registry
from agent_toolkit._common.next_action import ActionableError, recover_from_failure

if TYPE_CHECKING:
    from agent_toolkit._agents_server.service_generations import GenerationRegistry
    from agent_toolkit._agents_server.service_projection import ProjectionHub
    from agent_toolkit._agents_server.session_state import SessionState

_RESULT = TypeAdapter(CallToolResult | InputRequiredResult)
_OWNER_PROBE_SECONDS = 10.0


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


class ServiceSessionControl(ServiceForceControl, ServiceSessionMove):  # pylint: disable=abstract-method
    if TYPE_CHECKING:  # pylint: disable=unused-argument
        directory: pathlib.Path
        projections: ProjectionHub
        registry: GenerationRegistry
        start_requests: StartRequests
        _contexts: set[str]

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
                            "この世代が所有するsessionは一覧に含まれていません。agents_serverの`list`が返す`server_generations`で"
                            "その世代のプロセスの生存を確かめ、応答が再開した後にlistを再実行してください。"
                            "応答しないままならそのプロセスを処置してください（処置前に自身のMCP接続先でないことを確かめてください）"
                        ),
                    }
                )
        return {"sessions": sessions, "unreachable": unreachable}

    def owns(self, parent: str, identifier: str) -> bool:
        conversation = self.managers.conversations.get(parent)
        return conversation is not None and (
            identifier in conversation.manager.sessions
            or identifier in conversation.manager._pending_resumes
            or identifier in conversation.manager._active_sends
        )

    async def owned_call(self, parent: str, caller: runtime_environment.CallerContext, params: CallToolRequestParams) -> dict:
        conversation = self.managers.conversations.get(parent)
        identifier = (params.arguments or {}).get("session_id")
        if conversation is None or not isinstance(identifier, str):
            return {"move_required": True}
        session = conversation.manager.sessions.get(identifier)
        force = params.name == "kill" and (params.arguments or {}).get("force") is True
        if session is None and not (
            force and (identifier in conversation.manager._pending_resumes or identifier in conversation.manager._active_sends)
        ):
            return {"move_required": True}
        send_epoch = await self._require_unfenced(identifier) if params.name == "send_message" else None
        lock = self._locks.setdefault(identifier, asyncio.Lock()) if send_epoch is not None else contextlib.nullcontext()
        await self._finish_adoption(identifier)
        next_turn = params.name == "send_message" and session is not None and (session.terminal or self._ended_turn(session))
        try:
            async with lock, self.gate.admit(new_turn=next_turn, session_id=identifier):
                await self._recheck_send_epoch(identifier, send_epoch)
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
        if params.name in {"start", "send_to_user"}:
            root = plugin_root.SERVER_PLUGIN_ROOT
            if not root.is_dir():
                raise ActionableError(
                    f"管理サービスの保持配布物が見つかりません: {root}。{params.name}は実行していません",
                    next_action=recover_from_failure(
                        component="管理サービスの配布物",
                        evidence=f"欠落したroot {root}、管理サービスのPID {os.getpid()}、agents-server.log",
                        first_step="受付世代の記録と配布物の回収記録を照合する",
                        retry=(
                            "認可の範囲でupdate-dotfilesを実行し、更新した配布物から再接続して受付世代を切り替える。"
                            "同じ版で復旧する場合、窓口だけの再接続では切り替わらないため、"
                            "実行中の委譲があれば停止を保留し、完了または引継ぎを調整する。"
                            f"停止の認可があり処理が残っていなければ、窓口を切断し、管理サービス（PID {os.getpid()}）を"
                            "終了してから実在する配布物で再接続する。回復を確認して同じ操作を再実行する"
                        ),
                    ),
                )
        if params.name in {"start_submit", "start_status"}:
            owner, _env, _native_codex = self.managers.caller_identity(caller, params)
            if not owner:
                raise ActionableError(
                    "再接続で照合できる呼出元の会話IDがありません",
                    next_action="MCP接続元の会話IDを確認する。会話IDを提供できないホストは同期のstartを使う",
                )
            return await self.start_requests.call(owner, params, self._start_detached)
        async with self.managers.scope(params) as manager, contextlib.AsyncExitStack() as scopes:
            parent = next(key for key, item in self.managers.conversations.items() if item.manager is manager)
            arguments = params.arguments or {}
            identifier = arguments.get("session_id")
            predecessor = arguments.get("handoff_from") if params.name == "start" else None
            state_root = self.directory.parent.parent
            launcher = manager.launcher_session_id() or parent
            launch_ticket: pathlib.Path | None = None
            epoch: dict[str, str] | None = None
            if params.name == "start":
                launch_ticket, epoch = await scopes.enter_async_context(_pending_launch(state_root, launcher))
                if epoch != await self._require_unfenced(launcher):
                    raise ActionableError(
                        "強制停止と競合したため起動は未配送です", next_action="停止結果を確認してから起動する"
                    )
            force_id = _force_target(params.name, arguments)
            inherited_owner = (
                self.managers.handoff_child_owner(parent, identifier)
                if params.name in {"show", "send_message"} and isinstance(identifier, str)
                else None
            )
            if inherited_owner is not None and isinstance(identifier, str):
                await scopes.enter_async_context(self.managers.handoff_scope(inherited_owner))
                manager = inherited_owner.manager
            if inherited_owner is None and isinstance(identifier, str) and identifier not in manager.sessions:
                owner = await self._owner(identifier)
                if owner is not None and owner["generation"] != self.generation and await self._owns(owner, parent, identifier):
                    fence = (
                        _force_launch_fence(self.directory.parent.parent, force_id)
                        if force_id is not None
                        else contextlib.nullcontext()
                    )
                    async with fence:
                        operation = _forward_call(
                            owner, "owned_call", params, {"parent": parent, "caller": _caller_payload(caller)}
                        )
                        value = (
                            await self._force_dispatch(force_id, float(arguments.get("timeout", 270)), operation)
                            if force_id is not None
                            else await operation
                        )
                    if not value.get("move_required"):
                        result = mcp_tools.relay_result(
                            params.name, _RESULT.validate_python(value["result"]), owner, self.target
                        )
                        return result
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
            send_epoch = None
            if params.name == "send_message" and isinstance(identifier, str) and session_launchers.valid_launcher(identifier):
                send_epoch = await self._require_unfenced(identifier)
            if (
                force_id is None
                and isinstance(identifier, str)
                and identifier in self._pending_restores
                and (task := self._moving.get(identifier))
            ):
                # 巡回が予約した所有確認に合流する。照会自身は再試行を増やさない。
                await asyncio.wait((task,))
            lock_id = predecessor if isinstance(predecessor, str) else identifier
            lock = self._locks.setdefault(lock_id, asyncio.Lock()) if isinstance(lock_id, str) else None
            show_local = (
                params.name == "show"
                and inherited_owner is None
                and isinstance(identifier, str)
                and identifier in manager.sessions
                and identifier not in self._pending_restores
                and identifier not in self._uncertain
                and lock is not None
                and lock.locked()
            )
            if lock is not None and lock.locked() and params.name in {"kill", "stop"} and force_id is None:
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
            lock_scope = lock if lock is not None and force_id is None else contextlib.nullcontext()
            async with lock_scope:
                origin = self.managers.conversations[parent]
                if isinstance(predecessor, str) and (existing := origin.handoff_successors.get(predecessor)) is not None:
                    raise ActionableError(
                        f"前任 {predecessor} は後任 {existing} へ引き継ぎ済みです",
                        next_action=f"`show` に後任 {existing} を指定し、同じ後任の作業を確認する",
                    )
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
                    prepared = (
                        await self.managers.prepare_handoff_children(origin, predecessor)
                        if isinstance(predecessor, str)
                        else None
                    )
                    if inherited_owner is not None and isinstance(identifier, str):
                        child = await self.managers.authorize_handoff_child(parent, identifier, inherited_owner)
                        if params.name == "send_message" and not pathlib.Path(child.cwd).is_absolute():
                            raise ActionableError(
                                "所有managerの子sessionに絶対cwdの記録がなく、追送は未配送です",
                                next_action="所有managerの起動記録でcwdを確認し、確認できるまで追送しない",
                            )
                    unconfirmed_launcher = None
                    if force_id is not None and force_id not in manager.sessions and force_id not in manager._pending_resumes:
                        unconfirmed_launcher = manager.launcher_session_id() or parent
                    if force_id is not None:
                        if unconfirmed_launcher is not None and session_launchers.valid_launcher(force_id):
                            record = session_registry.registry_directory(state_root) / f"{force_id}.json"
                            if await run_file_operation(session_launchers.read_launcher, record) == unconfirmed_launcher:
                                unconfirmed_launcher = None
                        if unconfirmed_launcher is None:
                            await scopes.enter_async_context(_force_launch_fence(state_root, force_id))

                    async def guard(started: SessionState | None) -> None:
                        if epoch == await run_file_operation(_launch_fence, state_root, launcher):
                            return
                        if started is not None:
                            try:
                                await self.force_owned(parent, started.session_id, 270)
                                await self._force_children(started.session_id, 270)
                            except BaseException:
                                assert launch_ticket is not None
                                await run_file_operation(
                                    _record_launch_failure, state_root, launch_ticket, launcher, started.session_id
                                )
                                raise
                        raise ActionableError(
                            "強制停止と競合したため起動を取り消しました", next_action="停止結果を確認してから起動する"
                        )

                    await self._recheck_send_epoch(identifier, send_epoch)
                    with (
                        manager_start.START_RESOURCE_GUARD.set(guard if epoch is not None else None),
                        mcp_tools._SELECTED_MANAGER.set(manager),
                    ):
                        operation = mcp_tools.mcp._call_registered_tool(context, params)
                        if force_id is not None and unconfirmed_launcher is None:
                            result = await self._force_dispatch(force_id, float(arguments.get("timeout", 270)), operation)
                        else:
                            result = await operation
                    if params.name == "start" and isinstance(result, CallToolResult) and not result.is_error:
                        details = result.structured_content or {}
                        successor = details.get("session_id")
                        if isinstance(successor, str) and successor in manager.sessions:
                            await guard(manager.sessions[successor])
                            origin.started_children.add(successor)
                            if (
                                prepared is not None
                                and isinstance(predecessor, str)
                                and details.get("status") in {"running", "completed"}
                            ):
                                self.managers.publish_handoff_children(origin, predecessor, successor, prepared)
                    if (
                        force_id is not None
                        and unconfirmed_launcher is not None
                        and isinstance(result, CallToolResult)
                        and not result.is_error
                    ):
                        await self._force_children(
                            force_id, float(arguments.get("timeout", 270)), unconfirmed_launcher=unconfirmed_launcher
                        )
                    return result

    async def _start_detached(self, request: CallToolRequestParams) -> Any:
        caller = runtime_environment.current()
        assert caller is not None
        with runtime_environment.bind(dataclasses.replace(caller, connection_id=None)):
            return await self.call_tool(None, request)

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
