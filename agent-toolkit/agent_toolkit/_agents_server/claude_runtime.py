"""Claude SDKクライアントと所有プロセスの資源管理。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
import asyncio
import contextlib
import weakref
from collections.abc import Callable
from typing import Any

from anyio.abc import AnyByteSendStream, ByteSendStream
lazy import psutil

from agent_toolkit._agents_server import lifetime, session_errors, session_identity
from agent_toolkit._agents_server.claude_channels import ChildWriteResult, _CommandChannel, child_delivery_error
from agent_toolkit._agents_server.claude_plugins import ClaudePluginReloader
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._common import process_tree

_DISCONNECT_TIMEOUT = 5.0


class _ObservedStdin(ByteSendStream):
    def __init__(self, stream: AnyByteSendStream) -> None:
        self.stream = stream
        self.writer = asyncio.current_task()
        self.started = False

    async def send(self, item: bytes) -> None:
        if asyncio.current_task() is self.writer:
            self.started = True
        await self.stream.send(item)

    async def aclose(self) -> None:
        await self.stream.aclose()


async def query_child_completion(client: Any, prompt: str) -> ChildWriteResult:
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    transport = getattr(client, "_transport", None)
    subprocess = transport if isinstance(transport, SubprocessCLITransport) else None
    stdin = subprocess._stdin_stream if subprocess is not None else None  # pylint: disable=protected-access
    observed = _ObservedStdin(stdin.transport_stream) if stdin is not None else None
    if stdin is not None and observed is not None:
        stdin.transport_stream = observed
    try:
        await client.query(prompt)
    except Exception as error:  # pylint: disable=broad-exception-caught
        cause = child_delivery_error(error)
        if observed is not None and not observed.started:
            return "not_sent", cause
        if subprocess is not None and stdin is None and subprocess._stdin_stream is None:  # pylint: disable=protected-access
            return "not_sent", cause
        return "unconfirmed", cause
    finally:
        if stdin is not None and observed is not None and stdin.transport_stream is observed:
            stdin.transport_stream = observed.stream
    return "sent", None


class ClaudeRuntime:
    def __init__(
        self,
        sessions: dict[str, SessionState] | None = None,
        condition: asyncio.Condition | None = None,
        client_factory: Callable[[Any], Any] | None = None,
        expire_session: Callable[[str], None] | None = None,
        publish_registry: bool = False,
        *,
        root_session_id: str | None = None,
        validate_session_id: session_identity.IdentityValidator | None = None,
    ) -> None:
        self.sessions = sessions if sessions is not None else {}
        self._condition = condition if condition is not None else asyncio.Condition()
        self._client_factory = client_factory or self._default_client_factory
        self._expire_session = expire_session or self._expire_local_session
        self._publish_registry = publish_registry
        self._root_session_id = root_session_id
        self._validate_session_id = validate_session_id or session_identity.identity_validator(self.sessions, root_session_id)
        self._tasks: set[asyncio.Task[Any]] = set()
        self._task_sessions: dict[asyncio.Task[Any], str] = {}
        self._channels: dict[str, _CommandChannel] = {}
        self._reply_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._sdk_clients: dict[str, Any] = {}

        self._disconnects: set[asyncio.Task[None]] = set()
        self._residual_processes: dict[str, list[psutil.Process]] = {}

        self._unresolved_runtime: dict[str, list[str]] = {}

        self._reclaim_failures: dict[str, list[process_tree.ReclaimError]] = {}

    def _expire_local_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    @staticmethod
    def _default_client_factory(options: Any) -> Any:
        from claude_agent_sdk import ClaudeSDKClient

        return ClaudeSDKClient(options)

    async def _stop_owned_task(self, session_id: str) -> None:
        """同じsession IDを所有する旧タスクを終了し、再開時のキュー競合を防ぐ。"""
        task = next((item for item in self._tasks if self._task_sessions.get(item) == session_id), None)
        if task is None:
            await self._reclaim_runtime(session_id)
            return
        if not task.done():
            task.cancel()
        outcomes = await asyncio.gather(task, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                raise outcome

    async def _reclaim_runtime(self, session_id: str, *, pins: process_tree.ProcessIdentityPins | None = None) -> None:
        """未回収の所有資源を再試行し、終了できないhandleと生成停止の未確認理由を次の停止要求へ保持する。

        生成の停止を確認できなかった理由は再試行で解消しない。一時停止や列挙をすり抜けた子孫は親の終了後に
        所有対象から辿れないため、既知対象の回収だけでは停止を確かめられず、この会話の再開を止め続ける。
        """
        targets = self._residual_processes.get(session_id, [])
        unresolved = self._unresolved_runtime.get(session_id, [])
        failures = self._reclaim_failures.get(session_id, [])
        if not targets and not unresolved and not failures:
            return
        result = await run_file_operation(process_tree.stop_tree, None, known=targets, pins=pins)
        unresolved = [*unresolved, *(reason for reason in result.unresolved if reason not in unresolved)]

        remaining: list[process_tree.ReclaimError] = []
        failure_residual: list[str] = []
        for failure in failures:
            if left := await run_file_operation(failure.retry):
                remaining.append(failure)
                failure_residual.extend(left)
        process_tree.log_residual(
            [result.describe_residual(process) for process in result.residual]
            + [f"生成の停止を確認できない: {reason}" for reason in unresolved]
            + failure_residual,
            context=f"claude runtime {session_id}",
        )
        if remaining:
            self._reclaim_failures[session_id] = remaining
        else:
            self._reclaim_failures.pop(session_id, None)
        reasons = [*unresolved]
        reasons.extend(reason for failure in remaining for reason in failure.unresolved if reason not in reasons)
        if reasons:
            if unresolved:
                self._unresolved_runtime[session_id] = unresolved
            # 未確認の間は、残存一覧に無い子孫も次の再試行で辿れるよう所有対象全体を保持する。
            self._residual_processes[session_id] = result.targets
            raise session_errors.DelegateBackendError(
                f"Claude CLIの所有資源について生成の停止を確認できない: {reasons}。"
                "起点から辿れない子孫が残り得るため、停止の再試行では解消しない。"
                "この会話が起動した不要なプロセスを探して終了させた後、agents_serverを再起動すると再開できる。"
            )
        if result.residual or remaining:
            self._residual_processes[session_id] = result.residual
            raise session_errors.DelegateBackendError(
                "Claude CLIの所有資源を終了できなかった。所有状態を確認して停止を再試行する。"
            )
        self._residual_processes.pop(session_id, None)

    def _adopt_unreclaimed(self, runtime_key: str, reloader: ClaudePluginReloader) -> None:
        if reloader.unreclaimed:
            held = self._reclaim_failures.setdefault(runtime_key, [])
            held.extend(failure for failure in reloader.unreclaimed if failure not in held)

            self._residual_processes.setdefault(runtime_key, [])

    async def release_session(self, session: SessionState) -> None:
        await self._stop_owned_task(session.session_id)
        await session.drain_registration()

    async def _release_unstarted_task(self, task: asyncio.Task[Any]) -> None:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._forget_task(task)

    async def _disconnect_client(self, client: Any) -> None:
        """SDKクライアントの切断を、所有タスクの取り消しから切り離して完走させる。

        SDKが所有するCLIへの通常の終了要求は切断処理が担当する。
        取り消された実行では、SDKの切断処理が内部のcheckpointで例外を送出し、
        子プロセスの終了処理へ到達しないまま接続だけを閉じる。
        このため切断は別taskで実行し、待機の側が取り消されてもそのtaskの実行を継続させる。
        """
        task: asyncio.Task[None] = asyncio.create_task(self._close_client(client))
        self._disconnects.add(task)
        task.add_done_callback(self._disconnects.discard)
        with contextlib.suppress(asyncio.CancelledError):
            done, _ = await asyncio.wait({task}, timeout=_DISCONNECT_TIMEOUT)
            if not done:
                task.cancel()

    @staticmethod
    async def _close_client(client: Any) -> None:
        """SDKクライアントを切断し、切断処理自体の失敗は無視する。"""
        with contextlib.suppress(Exception):
            await client.disconnect()

    def _forget_task(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        self._task_sessions.pop(task, None)

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await lifetime.wait_for_shutdown(tasks)
        # 切断taskは所有タスクの終了処理が登録するため、待機の対象は所有タスクの終了後に集める。
        disconnects = tuple(self._disconnects)
        if disconnects:
            await lifetime.wait_for_shutdown(disconnects, timeout=_DISCONNECT_TIMEOUT)
        # 生成停止の未確認は再試行で解消しないため、1件の回収不能で他の会話の回収と登録の確定を止めず、最後に伝える。
        failures: list[session_errors.DelegateBackendError] = []
        for session_id in tuple(self._residual_processes):
            try:
                await self._reclaim_runtime(session_id)
            except session_errors.DelegateBackendError as error:
                failures.append(error)
        for session in tuple(self.sessions.values()):
            if session.engine == "claude":
                await session.drain_registration()
        if failures:
            raise failures[0]
