"""接続の寿命から独立して会話ごとのManagerと呼出元contextを保持する。"""

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

from agent_toolkit._agents_server import effort_control, service_io, service_turns, shared_roots, state, status_file
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.manager import AgentsServerManager
from agent_toolkit._agents_server.mcp_tools import _manager_lifespan
from agent_toolkit._agents_server.service_projection import ProjectionHub, ServiceStatusWriter
from agent_toolkit._common import effort_requests, process_identity, runtime_environment, session_release
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger("agent-toolkit.agents-server.service")


@dataclasses.dataclass
class Conversation:
    """状態の所有者と、再接続後も保持する会話固有の非永続context。"""

    manager: AgentsServerManager
    caller: runtime_environment.CallerContext
    runtime: service_io.Runtime | None = None
    stack: contextlib.AsyncExitStack = dataclasses.field(default_factory=contextlib.AsyncExitStack)
    hosts: dict[int, process_identity.ProcessIdentity] = dataclasses.field(default_factory=dict)
    connections: set[str] = dataclasses.field(default_factory=set)
    active: int = 0


class ServiceManagers:
    """同じ管理領域の会話を合流させ、個別の接続切断ではcloseしない。"""

    def __init__(self, *, state_root: pathlib.Path | None = None, projections: ProjectionHub | None = None) -> None:
        self.conversations: dict[str, Conversation] = {}
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._state_root = state_root
        self._monitor_task: asyncio.Task[None] | None = None
        self._projections = projections
        self.before_controls: Callable[[], Awaitable[bool]] | None = None
        # 同じ親会話のうち別世代がまだ所有するsessionを一覧へ加える照会。管理サービスの調停役が設定する。
        self.foreign_sessions: Callable[[str, bool], Awaitable[dict[str, list]]] | None = None

    async def other_generation_sessions(self, manager: object, *, include_terminated: bool) -> dict[str, list]:
        """照会したmanagerの親会話について、別世代が所有するsessionと応答しなかった世代を返す。"""
        parent = next((key for key, item in self.conversations.items() if item.manager is manager), None)
        collect = self.foreign_sessions
        if parent is None or collect is None:
            return {"sessions": [], "unreachable": []}
        # Pylintは初期値Noneだけで推論するが、調停役が型付きcallbackを登録している。
        return await collect(parent, include_terminated)  # pylint: disable=not-callable

    async def generations(self) -> list[dict[str, object]]:
        """照会した世代と、同じ管理領域で退出していない全世代を区別して返す。

        更新中の正常な世代の併存と、旧世代が退出できずに残る状態を、照会の応答から区別できるようにする。
        """
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
            if not entry.get("retired")
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

    async def close_conversation(self, key: str) -> None:
        async with self._locks.setdefault(key, asyncio.Lock()):
            item = self.conversations.get(key)
            if item is None:
                return
            with self._bound(item):
                await item.stack.aclose()
            self.conversations.pop(key, None)

    async def _monitor(self) -> None:
        """PID宛ての要求を一度だけ読み、対象会話の環境で操作する。"""
        next_heartbeat = 0.0
        while True:
            try:
                owners = {
                    session_id: conversation
                    for conversation in tuple(self.conversations.values())
                    for session_id in conversation.manager.sessions
                }
                sessions = {
                    identifier: conversation.manager.sessions[identifier] for identifier, conversation in owners.items()
                }
                pending = await run_file_operation(effort_requests.pending, state_root=self._state_root)
                for path, payload in pending:
                    identifier = payload.get("session_id")
                    conversation = owners.get(identifier) if isinstance(identifier, str) else None
                    if conversation is None:
                        continue
                    with self._bound(conversation):
                        manager = conversation.manager
                        if identifier in manager._service_paused:
                            continue
                        gate = conversation.runtime.gate if conversation.runtime else None
                        try:
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
                            continue

                await session_release.process_requests(sessions, self._stop_session, state_root=self._state_root)
                heartbeat = asyncio.get_running_loop().time() >= next_heartbeat
                awaiting = False
                before_controls = self.before_controls
                # Pylintは初期値Noneだけで推論するが、型付きcallbackを同じloopで登録している。
                try:
                    allow_resume = before_controls is None or await before_controls()  # pylint: disable=not-callable
                except Exception:
                    # 世代移行の1件の失敗で全会話の状態保存まで毎巡止めない（2026-11-17に旧世代の
                    # 状態がrunningのまま固定された）。再開は移行の成否が確定するまで見送る。
                    _LOG.exception(
                        "世代移行の確認が失敗しました。自動再開を見送り、状態保存は続けます。"
                        "次の操作: この例外を直すまで旧世代の会話を新世代へ移せず、自動再開も止まる。"
                        "`atk agents list`の`server_generations`で残っている世代を確かめ、"
                        "例外の原因（状態の符号化・移譲先との通信）を直してからサーバーを更新する"
                    )
                    allow_resume = False
                for conversation in tuple(self.conversations.values()):
                    with self._bound(conversation):
                        if allow_resume:
                            gate = conversation.runtime.gate if conversation.runtime else None
                            try:
                                async with gate.admit(new_turn=True) if gate else contextlib.nullcontext():
                                    awaiting |= await conversation.manager._process_auto_resume(process_controls=False)
                            except service_turns.MoveRequired:
                                pass
                        if heartbeat:
                            await conversation.manager.save_control_state()
                if heartbeat:
                    next_heartbeat = asyncio.get_running_loop().time() + status_file.HEARTBEAT_INTERVAL_SECONDS
                for key, conversation in tuple(self.conversations.items()):
                    if conversation.active:
                        continue
                    dead = bool(conversation.hosts) and not any(host.exists() for host in conversation.hosts.values())
                    empty = (
                        not conversation.connections
                        and not conversation.manager.sessions
                        and not conversation.manager.expired_sessions
                    )
                    if dead or empty:
                        await self.close_conversation(key)
            except Exception:
                _LOG.exception("共有監視の1巡が失敗しました。所有状態を保持して次の巡回で再確認します")
                awaiting = False
            await asyncio.sleep(0.1 if awaiting else 1.0)

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
        """要求が属する会話だけを選び、生成されるtaskへ同じ環境を渡す。"""
        caller = runtime_environment.current()
        if caller is None:
            raise RuntimeError("管理サービスの呼出元contextがありません")
        env = dict(caller.environment)
        metadata = params.meta
        identifier = metadata.get("threadId") if metadata else None
        if identifier is not None and (not isinstance(identifier, str) or not shared_roots.valid_session_id(identifier)):
            raise ActionableError("MCP要求のthreadIdが不正", next_action="ホストのMCP要求の会話IDを確認して再接続する")
        native_codex = identifier is not None or bool(env.get("CODEX_THREAD_ID"))
        if identifier is not None:
            env["CODEX_THREAD_ID"] = identifier
        if identifier_override is not None:
            identifier = identifier_override
        if identifier is None:
            identifier = (
                env.get("CODEX_THREAD_ID") or env.get("CLAUDE_CODE_SESSION_ID") or env.get("AGENT_TOOLKIT_STATUS_HOST_SESSION")
            )
        if not identifier:
            # 会話IDの無い接続だけは接続側で生成した匿名IDを使う。他の匿名接続とは混ぜない。
            identifier = caller.connection_id or "mcp-" + uuid.uuid4().hex
        if not shared_roots.valid_session_id(identifier):
            raise ActionableError("MCP接続の会話IDが不正", next_action="接続元の会話識別子を確認して再接続する")
        async with self._locks.setdefault(identifier, asyncio.Lock()):
            conversation = self.conversations.get(identifier)
            if conversation is None:
                runtime = service_io.current()
                gate = runtime.gate if runtime else None
                # 初期化による既存の復旧も新turnを始め得るため、Managerを作る前に予約する。
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
