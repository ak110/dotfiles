"""Codex接続の所有と回収。"""

from __future__ import annotations

import asyncio
import contextlib
import typing
from collections.abc import Coroutine
from typing import Any

from agent_toolkit._agents_server import lifetime, service_io, session_errors
from agent_toolkit._agents_server.codex_protocol import AppServerError, ResourceReleaseError
from agent_toolkit._agents_server.codex_rpc import JsonRpcProcess
from agent_toolkit._agents_server.file_operations import finish_task
from agent_toolkit._agents_server.state import LaunchKind, SessionState
from agent_toolkit._common import codex_models

if typing.TYPE_CHECKING:
    from agent_toolkit._agents_server.codex_manager_contract import CodexManager


class CodexClientsMixin:
    """Codex接続の所有と回収。

    AppServerManagerへ合成して使うため、各メソッドでは合成後の共有状態と他の責務を参照する。
    """

    _close_task: asyncio.Task[None] | None = None

    async def adopt_session(self: CodexManager, session: SessionState, descriptor: dict, _auxiliary: dict) -> None:
        runtime = service_io.current()
        if runtime is None:
            raise RuntimeError("サービス実行contextがありません")

        async def notification(message: dict) -> None:
            await self._handle_notification(message)
            params = message.get("params")
            if (
                runtime.started is not None
                and message.get("method") == "turn/started"
                and isinstance(params, dict)
                and self._find_session(params) is session
            ):
                runtime.started(session)
            if (
                runtime.boundary is not None
                and message.get("method") == "turn/completed"
                and isinstance(params, dict)
                and self._find_session(params) is session
                and self._notification_turn_id(params) == session.turn_id
                and session.turn_completed
            ):
                runtime.boundary(session, client)

        async def request(message: dict) -> None:
            await self._handle_server_request(message, client)

        async def failure(error: BaseException) -> None:
            await self._handle_client_failure(error, client)

        self.sessions[session.session_id] = session
        client = service_io.CodexResource.attach(runtime, descriptor, notification, request, failure)
        self._clients[session.session_id] = typing.cast(JsonRpcProcess, client)
        await self._notify_waiters()

    def _schedule(self: CodexManager, awaitable: Coroutine[Any, Any, None]) -> None:
        task: asyncio.Task[None] = asyncio.create_task(awaitable)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _ensure_client(
        self: CodexManager, launch_kind: LaunchKind | None = None, handoff_prompt: str = ""
    ) -> JsonRpcProcess:
        """新しいApp Serverを起動して初期化した接続を返す。

        `launch_kind`はturnを実行するsessionの起動区分で、App Serverの子環境を通じて委譲先のhookへ届く。

        接続は生成直後からmanagerが保持し、呼び出し元の文脈でsessionへ移譲するか、
        用途を終えた時点で`_discard_client`で終了させる。解放に失敗した接続はmanagerの停止で再試行する。
        サーバー要求への応答と接続障害の反映は、この接続が所有するsessionだけを対象にする。
        """
        client: JsonRpcProcess | None = None
        runtime = service_io.current()

        async def on_notification(message: dict[str, Any]) -> None:
            await self._handle_notification(message)
            params = message.get("params")
            if (
                runtime is not None
                and runtime.started is not None
                and message.get("method") == "turn/started"
                and isinstance(params, dict)
                and (session := self._find_session(params)) is not None
                and self._clients.get(session.session_id) is client
            ):
                runtime.started(session)
            if (
                runtime is None
                or runtime.boundary is None
                or client is None
                or message.get("method") != "turn/completed"
                or not isinstance(params, dict)
            ):
                return
            session = self._find_session(params)
            if (
                session is not None
                and self._clients.get(session.session_id) is client
                and self._notification_turn_id(params) == session.turn_id
                and session.turn_completed
            ):
                runtime.boundary(session, client)

        async def on_server_request(message: dict[str, Any]) -> None:
            assert client is not None
            await self._handle_server_request(message, client)

        async def on_failure(error: BaseException) -> None:
            assert client is not None
            await self._handle_client_failure(error, client)

        def factory(notification: Any, server_request: Any, failure: Any) -> JsonRpcProcess:
            return JsonRpcProcess(
                notification,
                server_request,
                failure,
                root_session_id=self._root_session_id,
                launch_kind=launch_kind,
                handoff_prompt=handoff_prompt,
            )

        client = (
            factory(on_notification, on_server_request, on_failure)
            if runtime is None
            else typing.cast(
                JsonRpcProcess,
                service_io.CodexResource.create(runtime, factory, on_notification, on_server_request, on_failure),
            )
        )
        self._unassigned_clients[id(client)] = client
        async with contextlib.AsyncExitStack() as resources:
            resources.push_async_callback(self._discard_client, client)
            await client.start()
            resources.pop_all()
        return client

    async def _discard_client(self: CodexManager, client: Any) -> None:
        residual = await client.close()
        if residual:
            raise session_errors.ActionableRuntimeError(
                f"Codex App Server resources before session assignment are still running: {'; '.join(residual)}",
                next_action="MCPサーバーの停止時に再度解放する。残存するPIDの終了を妨げる原因を調べる",
            )
        self._unassigned_clients.pop(id(client), None)

    def _owned_client(self: CodexManager, session: SessionState) -> Any:
        client = self._clients.get(session.session_id)
        if client is None or getattr(client, "closed", False) or getattr(client, "reader_failure", None) is not None:
            return None
        return client

    async def _acquire_owned_client(
        self: CodexManager, session_id: str, launch_kind: LaunchKind, handoff_prompt: str = ""
    ) -> JsonRpcProcess:
        client = self._clients.get(session_id)
        if client is not None and not getattr(client, "closed", False) and getattr(client, "reader_failure", None) is None:
            return client
        if client is not None:
            await self._release_owned_client(session_id)
        original = self.sessions.get(session_id)
        client = await self._ensure_client(launch_kind, original.prompt if original else handoff_prompt)
        self._clients[session_id] = client
        self._unassigned_clients.pop(id(client), None)
        return client

    async def _release_owned_client(self: CodexManager, session_id: str) -> None:
        await self._stop_auto_reset(session_id)
        client = self._clients.get(session_id)
        if client is None:
            return
        residual = await client.close()
        if residual:
            raise ResourceReleaseError(session_id, list(residual))
        if self._clients.get(session_id) is client:
            del self._clients[session_id]

    async def read_thread_turns(self: CodexManager, session_id: str) -> list[tuple[str, str]]:
        async with contextlib.AsyncExitStack() as resources:
            client = await self._ensure_client()
            resources.push_async_callback(self._discard_client, client)
            response = await client.request("thread/read", {"threadId": session_id, "includeTurns": True})
        thread = response.get("thread") if isinstance(response, dict) else None
        turns = thread.get("turns") if isinstance(thread, dict) else None
        if not isinstance(turns, list):
            raise AppServerError("thread/read returned no thread.turns")
        result: list[tuple[str, str]] = []
        for turn in turns:
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not isinstance(turn.get("status"), str):
                raise AppServerError("thread/read returned a turn without id or status")
            result.append((turn["id"], turn["status"]))
        return result

    async def list_models(self: CodexManager) -> list[dict[str, Any]]:
        async with contextlib.AsyncExitStack() as resources:
            client = await self._ensure_client()
            resources.push_async_callback(self._discard_client, client)
            return await codex_models.fetch_catalog(client.request)

    async def close(self: CodexManager) -> None:
        task = self._close_task
        if task is None or task.done() and (task.cancelled() or task.exception() is not None):
            task = self._close_task = asyncio.create_task(self._close())
        await finish_task(task)

    async def _close(self: CodexManager) -> None:
        current_task = asyncio.current_task()
        tasks = tuple(task for task in self._background_tasks if task is not current_task)
        # 監視taskや先行clientの待機に依存させず、所有する全接続の停止を開始する。
        releases = [
            asyncio.create_task(self._discard_client(client)) for client in tuple(self._unassigned_clients.values())
        ] + [asyncio.create_task(self._release_owned_client(session_id)) for session_id in tuple(self._clients)]
        for task in tasks:
            if not task.done():
                task.cancel()
        outcomes = await lifetime.wait_for_shutdown((*tasks, *releases))
        failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
        for session in self._codex_sessions():
            try:
                await session.drain_registration()
            except Exception as error:
                failures.append(error)
        if failures:
            for other in failures[1:]:
                failures[0].add_note(str(other))
            raise failures[0]
