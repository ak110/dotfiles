from __future__ import annotations

# Managerの起動・監視・終了を所有する協調役として、その内部操作を呼ぶ。
# pylint: disable=protected-access
import asyncio
import contextlib
import dataclasses
import logging
import pathlib
import types
import uuid
import weakref
from collections.abc import AsyncGenerator, Awaitable, Callable

from mcp.types import CallToolRequestParams

from agent_toolkit._agents_server import (
    effort_control,
    service_io,
    service_transport,
    service_turns,
    shared_roots,
    state,
    status_file,
)
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.manager import AgentsServerManager
from agent_toolkit._agents_server.mcp_tools import _manager_lifespan
from agent_toolkit._agents_server.service_projection import ProjectionHub, ServiceStatusWriter
from agent_toolkit._common import effort_requests, process_identity, runtime_environment, session_release
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger("agent-toolkit.agents-server.service")


@dataclasses.dataclass
class Conversation:
    manager: AgentsServerManager
    caller: runtime_environment.CallerContext
    runtime: service_io.Runtime | None = None
    stack: contextlib.AsyncExitStack = dataclasses.field(default_factory=contextlib.AsyncExitStack)
    hosts: dict[int, process_identity.ProcessIdentity] = dataclasses.field(default_factory=dict)
    connections: set[str] = dataclasses.field(default_factory=set)
    active: int = 0
    started_children: set[str] = dataclasses.field(default_factory=set)
    handoff_successors: dict[str, str] = dataclasses.field(default_factory=dict)


class ServiceManagers:
    def __init__(self, *, state_root: pathlib.Path | None = None, projections: ProjectionHub | None = None) -> None:
        self.conversations: dict[str, Conversation] = {}
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._state_root = state_root
        self._handoff_children: dict[str, tuple[str, Conversation, frozenset[str]]] = {}
        self._monitor_task: asyncio.Task[None] | None = None
        self._projections = projections
        self.before_controls: Callable[[], Awaitable[bool]] | None = None

        self.foreign_sessions: Callable[[str, bool], Awaitable[dict[str, list]]] | None = None

    async def other_generation_sessions(self, manager: object, *, include_terminated: bool) -> dict[str, list]:
        parent = next((key for key, item in self.conversations.items() if item.manager is manager), None)
        collect = self.foreign_sessions
        if parent is None or collect is None:
            return {"sessions": [], "unreachable": []}

        return await collect(parent, include_terminated)  # pylint: disable=not-callable

    async def generations(self) -> list[dict[str, object]]:
        if self._projections is None:
            return []
        queried = self._projections.generation
        entries = await run_file_operation(self._projections.registry.generations)
        current = await run_file_operation(self._projections.registry.current)
        return [
            {
                "generation": name,
                "epoch": entry.get("epoch"),
                "server_instance": entry.get("server_instance"),
                "current": current is not None and current.get("generation") == name,
                "queried": name == queried,
            }
            for name, entry in sorted(entries.items(), key=lambda item: item[1].get("epoch", 0))
            if not entry.get("retired") and ("process" not in entry or service_transport.process_alive(entry["process"]))
        ]

    async def __aenter__(self) -> ServiceManagers:
        self._monitor_task = asyncio.create_task(self._monitor())
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, traceback: types.TracebackType | None
    ) -> None:
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
        failures = []
        for key in tuple(self.conversations):
            try:
                await self.close_conversation(key)
            except Exception as failure:
                failures.append(failure)
        if failures:
            raise ExceptionGroup("会話の資源を回収できませんでした", failures)

    def disconnect(self, connection_id: str) -> None:
        for item in self.conversations.values():
            item.connections.discard(connection_id)

    async def close_conversation(self, key: str, *, disconnected_only: bool = False) -> None:
        async with self._locks.setdefault(key, asyncio.Lock()):
            item = self.conversations.get(key)
            if item is None or (disconnected_only and not self._retirable(item)):
                return
            if disconnected_only:
                _LOG.info("接続と実行中のturnがない会話を回収します: parent=%s", key)
            with self._bound(item):
                await item.stack.aclose()
            self.conversations.pop(key, None)
            self._handoff_children = {
                successor: grant for successor, grant in self._handoff_children.items() if grant[1] is not item
            }

    async def _collect_disconnected_conversations(self) -> None:
        for key, conversation in tuple(self.conversations.items()):
            if self._retirable(conversation):
                await self.close_conversation(key, disconnected_only=True)

    async def prepare_handoff_children(
        self, origin: Conversation, predecessor: str
    ) -> tuple[Conversation, frozenset[str]] | None:
        caller = origin.manager
        if predecessor not in origin.started_children or self._handoff_child_state(caller, predecessor) is None:
            return None
        owner = self.conversations.get(predecessor)
        if (
            owner is None
            or caller.status_owner()[0] is None
            or owner.manager.launcher_session_id() != predecessor
            or owner.manager.status_owner()[0] != caller.status_owner()[0]
        ):
            return None
        manager = owner.manager
        direct: set[str] = set()
        async with self.handoff_scope(owner):
            for identifier in owner.started_children.intersection(manager._wait_target_ids()):
                child = self._uncollected_handoff_child(manager, identifier)
                if child is None:
                    continue
                writer = manager._status_writer
                if (
                    child.finalized_at is not None
                    and writer is not None
                    and await writer.result_state_async(identifier, session=child) == "consumed"
                ):
                    continue
                direct.add(identifier)
        return owner, frozenset(direct)

    def publish_handoff_children(
        self, origin: Conversation, predecessor: str, successor: str, prepared: tuple[Conversation, frozenset[str]]
    ) -> None:
        owner, direct = prepared
        if self.conversations.get(predecessor) is owner:
            direct = frozenset(
                identifier for identifier in direct if self._uncollected_handoff_child(owner.manager, identifier) is not None
            )
            if direct:
                self._handoff_children[successor] = predecessor, owner, direct
        origin.handoff_successors[predecessor] = successor

    @staticmethod
    def _handoff_child_state(
        manager: AgentsServerManager, identifier: str
    ) -> state.SessionState | state.SessionResumeState | None:
        if (session := manager.sessions.get(identifier)) is not None:
            return session
        pending = manager._pending_resumes.get(identifier)
        previous = (
            pending.state
            if pending is not None
            else (manager.expired_sessions.get(identifier) or manager.stopped_sessions.get(identifier))
        )
        return None if manager._is_registry_resume_state(previous) else previous

    @classmethod
    def _uncollected_handoff_child(
        cls, manager: AgentsServerManager, identifier: str
    ) -> state.SessionState | state.SessionResumeState | None:
        child = cls._handoff_child_state(manager, identifier)
        return child if child is not None and not child.result_delivered else None

    def handoff_child_owner(self, successor: str, identifier: str) -> Conversation | None:
        grant = self._handoff_children.get(successor)
        if grant is None:
            return None
        predecessor, owner, children = grant
        if (
            identifier in children
            and self.conversations.get(predecessor) is owner
            and self._handoff_child_state(owner.manager, identifier) is not None
        ):
            return owner
        return None

    async def poll_handoff_children(self, successor: AgentsServerManager, deadline: float) -> dict:
        identifier = next((key for key, item in self.conversations.items() if item.manager is successor), None)
        grant = None if identifier is None else self._handoff_children.get(identifier)
        if grant is None or identifier is None:
            return {"status": "expired"}
        _, owner, children = grant
        targets = [child for child in children if self.handoff_child_owner(identifier, child) is owner]
        if not targets:
            return {"status": "expired"}
        async with self.handoff_scope(owner):
            return await owner.manager.wait(inherited_targets=targets, inherited_deadline=deadline)

    @contextlib.asynccontextmanager
    async def handoff_scope(self, owner: Conversation) -> AsyncGenerator[None]:
        owner.active += 1
        try:
            with self._bound(owner):
                yield
        finally:
            owner.active -= 1

    def _retirable(self, conversation: Conversation) -> bool:
        if conversation.active or conversation.connections:
            return False
        manager = conversation.manager
        if (
            manager._pending_resumes
            or manager._recovery_attempts
            or any(not session.terminal or session.awaiting_auto_resume for session in manager.sessions.values())
        ):
            # 祖先として観測したCLIの終了だけでは、再接続先や進行中の子の終了を確認できない。
            return False
        launcher = manager.launcher_session_id() if conversation.started_children else None
        if (
            launcher is not None
            and self.conversations.get(launcher) is conversation
            and (
                any(owner is conversation for _, owner, _ in self._handoff_children.values())
                or any(
                    launcher in parent.started_children and self._handoff_child_state(parent.manager, launcher) is not None
                    for parent in self.conversations.values()
                )
            )
            and any(
                self._uncollected_handoff_child(manager, identifier) is not None
                for identifier in conversation.started_children.intersection(manager._wait_target_ids())
            )
        ):
            return False
        dead = bool(conversation.hosts) and not any(host.exists() for host in conversation.hosts.values())
        return dead or not (manager.sessions or manager.expired_sessions)

    async def _monitor(self) -> None:
        async with asyncio.TaskGroup() as group:
            await self._monitor_requests(group)

    async def _monitor_requests(self, group: asyncio.TaskGroup) -> None:
        active_controls: dict[str, asyncio.Task[None]] = {}
        next_heartbeat = 0.0
        # 毎巡（1秒）同じ例外の全文を記録するとログが埋まるため、失敗の種類が変わった時と回復した時だけ記録する。
        migration_failure: tuple[str, str] | None = None
        repeated_failures = 0
        while True:
            try:
                owners = {
                    session_id: conversation
                    for conversation in tuple(self.conversations.values())
                    for session_id in conversation.manager.sessions
                }
                effort_requests_found = await run_file_operation(effort_requests.pending, state_root=self._state_root)
                release_requests_found = await run_file_operation(session_release.pending, self._state_root)
                active_controls = {identifier: task for identifier, task in active_controls.items() if not task.done()}
                for effort_request, requests in ((True, effort_requests_found), (False, release_requests_found)):
                    for path, payload in requests:
                        identifier = payload.get("session_id")
                        if not isinstance(identifier, str) or identifier in active_controls:
                            continue
                        conversation = owners.get(identifier)
                        if conversation is not None or not effort_request:
                            active_controls[identifier] = group.create_task(
                                self._process_control(conversation, path, payload, effort_request=effort_request)
                            )
                heartbeat = asyncio.get_running_loop().time() >= next_heartbeat
                awaiting = False
                before_controls = self.before_controls

                try:
                    allow_resume = before_controls is None or await before_controls()  # pylint: disable=not-callable
                except Exception as error:
                    # 世代移行の1件の失敗で全会話の状態保存まで毎巡止めない（2026-11-17に旧世代の
                    # 状態がrunningのまま固定された）。再開は移行の成否が確定するまで見送る。
                    allow_resume = False
                    failure = (type(error).__qualname__, str(error))
                    if failure == migration_failure:
                        repeated_failures += 1
                    else:
                        migration_failure, repeated_failures = failure, 1
                        _LOG.exception(
                            "世代移行の確認が失敗しました。自動再開を見送り、状態保存は続けます。"
                            "次の巡回（約1秒後）で自動的に再確認し、同じ失敗が続く間はこの記録を繰り返さず、回復時に失敗の回数を記録します。"
                            "次の操作: 回復の記録が続かない場合、旧世代の会話を新世代へ移せず自動再開も止まったままになる。"
                            "agents_serverの`list`が返す`server_generations`で退出していない世代を確かめ、"
                            "この記録の例外（状態の符号化・移譲先との通信）を直してからサーバーを更新する"
                        )
                else:
                    if migration_failure is not None:
                        _LOG.info("世代移行の確認が回復しました（連続失敗%d回）。自動再開を再開します", repeated_failures)
                        migration_failure, repeated_failures = None, 0
                for conversation in tuple(self.conversations.values()):
                    with self._bound(conversation):
                        if allow_resume:
                            gate = conversation.runtime.gate if conversation.runtime else None
                            try:
                                async with gate.admit(new_turn=True) if gate else contextlib.nullcontext():
                                    awaiting |= await conversation.manager._process_auto_resume(process_controls=False)
                            except service_turns.MoveRequired:
                                pass
                        if heartbeat and not any(identifier in active_controls for identifier in conversation.manager.sessions):
                            await conversation.manager.save_control_state()
                if heartbeat:
                    next_heartbeat = asyncio.get_running_loop().time() + status_file.HEARTBEAT_INTERVAL_SECONDS
                await self._collect_disconnected_conversations()
            except Exception:
                _LOG.exception("共有監視の1巡が失敗しました。所有状態を保持して次の巡回で再確認します")
                awaiting = False
            await asyncio.sleep(0.1 if awaiting else 1.0)

    async def _process_control(
        self, conversation: Conversation | None, path: pathlib.Path, payload: dict, *, effort_request: bool
    ) -> None:
        if conversation is None:
            await session_release.process_requests(
                {}, self._stop_session, state_root=self._state_root, requests=[(path, payload)]
            )
            return
        conversation.active += 1
        try:
            with self._bound(conversation):
                manager = conversation.manager
                if not effort_request:
                    await session_release.process_requests(
                        manager.sessions, self._stop_session, state_root=self._state_root, requests=[(path, payload)]
                    )
                    return
                identifier = payload["session_id"]
                if identifier in manager._service_paused:
                    return
                gate = conversation.runtime.gate if conversation.runtime else None
                async with gate.admit(new_turn=False, session_id=identifier) if gate else contextlib.nullcontext():
                    await effort_control.process_requests(
                        manager.sessions,
                        manager._effort_catalog,
                        manager._apply_effort,
                        state_root=self._state_root,
                        requests=[(path, payload)],
                    )
                    await manager.save_control_state()
        except service_turns.MoveRequired:
            return
        except Exception:
            _LOG.exception("操作要求を処理できませんでした: path=%s。要求の所在と所有状態を確認してください", path)
        finally:
            conversation.active -= 1

    async def _stop_session(self, identifier: str, *, retain_result: bool = False) -> dict:
        conversation = next(item for item in self.conversations.values() if identifier in item.manager.sessions)
        with self._bound(conversation):
            gate = conversation.runtime.gate if conversation.runtime else None
            try:
                if identifier in conversation.manager._service_paused:
                    raise service_turns.MoveRequired({})
                async with gate.admit(new_turn=False, session_id=identifier) if gate else contextlib.nullcontext():
                    return await conversation.manager.stop(identifier, retain_result=retain_result)
            except service_turns.MoveRequired:
                raise ActionableError(
                    "対象会話の所有を移譲中のため解放は未実行",
                    next_action="所有の移譲完了後に登録簿と終了状態を確認して解放を再要求する",
                ) from None

    @staticmethod
    @contextlib.contextmanager
    def _bound(conversation: Conversation):
        resources = service_io.bind(conversation.runtime) if conversation.runtime else contextlib.nullcontext()
        gate = conversation.runtime.gate if conversation.runtime else None
        with (
            resources,
            runtime_environment.bind(conversation.caller),
            service_turns.bind(gate) if gate is not None else contextlib.nullcontext(),
            service_io.save_with(conversation.manager.save_control_state),
        ):
            yield

    @contextlib.asynccontextmanager
    async def scope(
        self,
        params: CallToolRequestParams,
        *,
        identifier_override: str | None = None,
    ) -> AsyncGenerator[AgentsServerManager]:
        caller = runtime_environment.current()
        if caller is None:
            raise RuntimeError("管理サービスの呼出元contextがありません")
        env = dict(caller.environment)
        metadata = params.meta
        identifier = metadata.get("threadId") if metadata else None
        if identifier is not None and (not isinstance(identifier, str) or not shared_roots.valid_session_id(identifier)):
            raise ActionableError("MCP要求のthreadIdが不正", next_action="ホストのMCP要求の会話IDを確認して再接続する")
        if identifier is not None:
            env["CODEX_THREAD_ID"] = identifier
        elif env.get("CLAUDE_CODE_SESSION_ID"):
            env.pop("CODEX_THREAD_ID", None)
        native_codex = bool(env.get("CODEX_THREAD_ID"))
        if identifier_override is not None:
            identifier = identifier_override
        if identifier is None:
            identifier = (
                env.get("CODEX_THREAD_ID") or env.get("CLAUDE_CODE_SESSION_ID") or env.get("AGENT_TOOLKIT_STATUS_HOST_SESSION")
            )
        if not identifier:
            identifier = caller.connection_id or "mcp-" + uuid.uuid4().hex
        if not shared_roots.valid_session_id(identifier):
            raise ActionableError("MCP接続の会話IDが不正", next_action="接続元の会話識別子を確認して再接続する")
        async with self._locks.setdefault(identifier, asyncio.Lock()):
            conversation = self.conversations.get(identifier)
            if conversation is None:
                runtime = service_io.current()
                gate = runtime.gate if runtime else None

                async with gate.admit(new_turn=True) if gate else contextlib.nullcontext():
                    conversation = await self._create_conversation(identifier, caller, env, native_codex)
                    self.conversations[identifier] = conversation
            bound = runtime_environment.CallerContext(
                env,
                caller.cwd,
                conversation.caller.credit_shortages,
                caller.connection_id,
                caller.parent_settings,
                caller.host_process,
            )
            conversation.caller = bound
            conversation.manager._caller_context = bound
            if caller.connection_id:
                conversation.connections.add(caller.connection_id)
            if caller.host_process is not None:
                host = process_identity.ProcessIdentity.parse(caller.host_process)
                conversation.hosts[host.pid] = host
            conversation.active += 1
        try:
            with self._bound(conversation):
                yield conversation.manager
        finally:
            conversation.active -= 1

    async def _create_conversation(
        self,
        identifier: str,
        caller: runtime_environment.CallerContext,
        env: dict[str, str],
        native_codex: bool,
    ) -> Conversation:
        if native_codex and not env.get("AGENT_TOOLKIT_STATUS_HOST_SESSION"):
            env["CODEX_THREAD_ID"] = identifier
            identity = shared_roots.StatusFileIdentity(identifier, "root.json", None)
        else:
            identity = shared_roots.resolve_status_file_identity(env) or shared_roots.create_process_root_identity()
        sessions: dict[str, state.SessionState] = {}
        writer = (
            status_file.StatusFileWriter(sessions, identity)
            if self._projections is None
            else ServiceStatusWriter(
                sessions,
                identity,
                self._projections,
            )
        )
        manager = AgentsServerManager(writer, launcher_environment=env)
        bound = runtime_environment.CallerContext(
            env,
            caller.cwd,
            connection_id=caller.connection_id,
            parent_settings=caller.parent_settings,
            host_process=caller.host_process,
        )
        manager._caller_context = bound
        stack = contextlib.AsyncExitStack()
        with runtime_environment.bind(bound), service_io.save_with(manager.save_control_state):
            await stack.enter_async_context(
                _manager_lifespan(
                    manager,
                    ready_for_requests=not identifier.startswith(shared_roots.PROCESS_ROOT_PREFIX),
                    shared_monitor=True,
                )
            )
        return Conversation(manager, bound, service_io.current(), stack=stack)
