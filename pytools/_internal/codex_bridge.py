"""通常TUIの寿命内で、daemon再起動後の公開RPC接続と同じ仕事を復旧する。"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import enum
import json
import pathlib
import secrets
import time
import uuid
from collections.abc import Mapping
from typing import Any, Self

from pytools._internal.codex_recovery import ConversationRecovery, RunningTurn
from pytools._internal.codex_transport import RpcChannel, accept_tui, native_proxy


class RecoveryPhase(enum.Enum):
    """会話のturn状態と独立した、接続復旧の進行段階。"""

    RELAYING = enum.auto()
    DAEMON_CHANGED = enum.auto()
    RECONNECTING = enum.auto()
    INITIALIZING = enum.auto()
    RESUMING = enum.auto()
    RESTORING_DELEGATIONS = enum.auto()
    CHECKING_CONTINUATION = enum.auto()
    FAILED = enum.auto()


class RecoveryBridge:
    """標準入出力を変更せず、起動したTUIだけのIPC接続を所有する。"""

    def __init__(self, binary: str, environment: Mapping[str, str], home: pathlib.Path, *, timeout: float = 60.0) -> None:
        self.binary, self.environment, self.home = binary, dict(environment), home
        self.timeout = timeout
        self.state = ConversationRecovery()
        self.phase = RecoveryPhase.RELAYING
        self.token = secrets.token_hex(32)
        self.endpoint = ""
        self.failure: asyncio.Future[Exception] | None = None
        self.recovery_count = 0
        self.connected_generation: tuple[int, str] | None = None
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.Task[Any]] = set()
        self._authenticated: set[asyncio.Task[Any]] = set()
        self._connection_lock = asyncio.Lock()
        self._server_requests: dict[str, tuple[str | int, str | None]] = {}
        self._user_turn_revision = 0
        self._resuming_tui: ConversationRecovery | None = None
        self._public_resume_result: dict[str, Any] | None = None
        self._generation_error_since: float | None = None
        self._bootstrap_thread: str | None = None

    def prepare_tui_resume(self) -> None:
        """daemonの交代で終了した所有TUIを、同じ会話へ起動し直す準備をする。"""
        self._resuming_tui = copy.deepcopy(self.state)
        self.state = ConversationRecovery()
        self._server_requests.clear()

    def daemon_generation(self) -> tuple[int, str] | None:
        """nativeの現行世代を返す。所有TUIの終了と再起動の競合にも使用する。"""
        return self._generation()

    async def wait_tui_closed(self) -> None:
        """終了したTUIの接続とcallbackを回収してから、次の起動へ所有を渡す。"""
        async with asyncio.timeout(self.timeout):
            await asyncio.gather(*tuple(self._authenticated), return_exceptions=True)
        assert self.failure is not None
        if self.failure.done():
            raise self.failure.result()

    async def __aenter__(self) -> Self:
        self.failure = asyncio.get_running_loop().create_future()
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.endpoint = f"ws://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"
        return self

    async def __aexit__(self, *_args: Any) -> None:
        assert self._server is not None
        self._server.close()
        self._server.close_clients()
        for task in tuple(self._connections):
            task.cancel()
        await asyncio.gather(*self._connections, return_exceptions=True)
        await self._server.wait_closed()

    def _generation(self) -> tuple[int, str] | None:
        try:
            content = (self.home / "app-server-daemon" / "daemon.pid").read_text(encoding="utf-8")
        except FileNotFoundError:
            self._generation_error_since = None
            return None
        if not content:
            # native 0.166はO_CREAT|O_EXCLで空の予約ファイルを作成してから、
            # 完全なJSONを別inodeからrenameする。予約状態は世代として使用しない。
            now = time.monotonic()
            if self._generation_error_since is None:
                self._generation_error_since = now
            if now - self._generation_error_since >= self.timeout:
                raise RuntimeError("Codexのdaemon世代を読み取れない。codex doctorで管理daemonを確認する")
            return None
        self._generation_error_since = None
        value = json.loads(content)
        # 停止対象の指定には使わず、native自身が公開した世代の変化だけを観測する。
        # nativeのPidRecordはprocessIdentityを省略可能とし、Linux・macOS以外（Windows）では書かない。
        # 全OSで書くprocessStartTimeを、その場合の同じ世代の識別に使う。
        identity = value.get("processIdentity") if isinstance(value, dict) else None
        if identity is None and isinstance(value, dict) and isinstance(value.get("processStartTime"), str):
            identity = value["processStartTime"]
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("pid"), int)
            or isinstance(value.get("pid"), bool)
            or identity is None
        ):
            raise ValueError("Codexのdaemon世代を読み取れない。codex doctorで管理daemonを確認する")
        return value["pid"], json.dumps(identity, sort_keys=True)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._connections.add(task)
        try:
            async with accept_tui(reader, writer, self.token, timeout=self.timeout) as front:
                self._authenticated.add(task)
                async with self._connection_lock:
                    await self._relay(front)
        except Exception as error:  # pylint: disable=broad-exception-caught
            if isinstance(error, PermissionError) and task not in self._authenticated:
                # 未認証のローカル接続にはHTTP 401を返し、所有TUIの寿命へ作用させない。
                return
            # サーバーcallbackの失敗を起動所有者へ返し、正常終了と区別する。
            assert self.failure is not None
            if not self.failure.done():
                self.phase = RecoveryPhase.FAILED
                self.failure.set_result(error)
        finally:
            self._authenticated.discard(task)
            self._connections.discard(task)

    def _from_client(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if message.get("method") == "turn/start":
            self._user_turn_revision += 1
        self.state.observe_client(message)
        identifier = message.get("id")
        if "method" not in message and isinstance(identifier, str) and identifier.startswith("dotfiles-server-"):
            request = self._server_requests.pop(identifier, None)
            if request is None:
                # 旧daemonの承認応答を、新しいdaemonの同じ数値IDへ誤適用しない。
                return None
            return dict(message, id=request[0])
        return message

    async def _to_client(self, front: RpcChannel, message: dict[str, Any]) -> None:
        previous_thread = self.state.thread_id
        self.state.observe_server(message)
        if self.state.thread_id is not None and self.state.thread_id != previous_thread:
            self._bootstrap_thread = self.state.thread_id
        result = message.get("result")
        if (
            self._resuming_tui is not None
            and isinstance(result, dict)
            and isinstance(result.get("thread"), dict)
            and result["thread"].get("id") == self._resuming_tui.thread_id == self.state.thread_id
        ):
            initialize = self.state.initialize
            self.state, self._resuming_tui = self._resuming_tui, None
            self.state.initialize = initialize
            self._public_resume_result = result
        identifier, method = message.get("id"), message.get("method")
        if method is not None and isinstance(identifier, (str, int)):
            public_id = f"dotfiles-server-{uuid.uuid4()}"
            params = message.get("params")
            thread = params.get("threadId") if isinstance(params, dict) else None
            self._server_requests[public_id] = (identifier, thread if isinstance(thread, str) else self.state.thread_id)
            message = dict(message, id=public_id)
        elif method == "serverRequest/resolved":
            params = message.get("params")
            if isinstance(params, dict):
                request_id = params.get("requestId")
                for public_id, (native_id, _thread) in tuple(self._server_requests.items()):
                    if native_id == request_id:
                        message = dict(message, params=dict(params, requestId=public_id))
                        self._server_requests.pop(public_id)
                        break
        await front.send(message)

    async def _invalidate_server_requests(self, front: RpcChannel) -> None:
        # 同じ公開通知で古い承認表示を終了させる。承認したという応答は生成しない。
        for identifier, (_native, thread) in tuple(self._server_requests.items()):
            if thread is not None:
                await front.send({"method": "serverRequest/resolved", "params": {"threadId": thread, "requestId": identifier}})
        self._server_requests.clear()

    async def _request(self, front: RpcChannel, back: RpcChannel, method: str, params: dict[str, Any]) -> Any:
        identifier = f"dotfiles-recovery-{uuid.uuid4()}"
        await back.send({"id": identifier, "method": method, "params": params})
        while True:
            message = await back.receive()
            if message.get("id") == identifier:
                if "error" in message:
                    # RPC本文は認証情報を含み得るため、例外に複製しない。
                    raise RuntimeError(f"Codexの{method}が失敗した。codex doctorで接続を確認して同じ会話をresumeする")
                return message.get("result")
            await self._to_client(front, message)

    async def _recover(self, front: RpcChannel, back: RpcChannel) -> None:
        initialize = self.state.initialize
        if initialize is None:
            raise RuntimeError("Codexの初期化が完了する前にIPCが終了した。codexを再実行する")
        original, revision = self.state.conversation, self._user_turn_revision
        await self._invalidate_server_requests(front)
        self.phase = RecoveryPhase.INITIALIZING
        await self._request(front, back, "initialize", initialize)
        await back.send({"method": "initialized", "params": {}})
        if self.state.thread_id is None:
            return
        self.phase = RecoveryPhase.RESUMING
        resumed = await self._request(front, back, "thread/resume", dict(self.state.resume_parameters(), excludeTurns=True))
        await self._restore_work(
            front, back, resumed, original=original if isinstance(original, RunningTurn) else None, revision=revision
        )

    async def _restore_work(
        self, front: RpcChannel, back: RpcChannel, resumed: Any, *, original: RunningTurn | None, revision: int
    ) -> None:
        if not isinstance(resumed, dict) or not isinstance(resumed.get("thread"), dict):
            raise RuntimeError("Codexの再開応答に会話が無い。同じ会話をresumeする")
        turns = await self._request(front, back, "thread/turns/list", {"threadId": self.state.thread_id, "limit": 1})
        if not isinstance(turns, dict) or not isinstance(turns.get("data"), list):
            raise RuntimeError("Codexの再開前のturnを確認できない。同じ会話をresumeする")
        await self._restore_delegations(front, back)
        self.phase = RecoveryPhase.CHECKING_CONTINUATION
        if original is not None and revision == self._user_turn_revision and self.state.restore_running_turn(original):
            continuation = self.state.continuation_parameters(resumed["thread"], turns["data"])
            if continuation is not None:
                await self._request(front, back, "turn/start", continuation)
        self.recovery_count += 1

    async def _restore_delegations(self, front: RpcChannel, back: RpcChannel) -> None:
        self.phase = RecoveryPhase.RESTORING_DELEGATIONS
        cursor = None
        while True:
            statuses = await self._request(
                front, back, "mcpServerStatus/list", {"threadId": self.state.thread_id, "cursor": cursor}
            )
            if not isinstance(statuses, dict) or not isinstance(statuses.get("data"), list):
                raise RuntimeError("CodexのMCP状態を確認できない。同じ会話をresumeする")
            for server in statuses["data"]:
                if not isinstance(server, dict):
                    continue
                name, tools = server.get("name"), server.get("tools")
                if isinstance(name, str) and (name == "agents_server" or name.endswith("__agents_server")):
                    if not isinstance(tools, dict) or "list" not in tools:
                        raise RuntimeError("agents_serverの復旧用ツールを確認できない。MCPの認証・起動状態を確認する")
                    result = await self._request(
                        front,
                        back,
                        "mcpServer/tool/call",
                        {"threadId": self.state.thread_id, "server": name, "tool": "list", "arguments": {}},
                    )
                    if not isinstance(result, dict) or result.get("isError"):
                        raise RuntimeError("agents_serverの子孫復旧に失敗した。MCPの状態を確認して同じ会話をresumeする")
            cursor = statuses.get("nextCursor")
            if cursor is None:
                return
            if not isinstance(cursor, str):
                raise RuntimeError("CodexのMCP一覧の継続位置が不正。同じ会話をresumeする")

    async def _relay(self, front: RpcChannel) -> None:
        incoming: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def receive_front() -> None:
            try:
                while True:
                    received = await front.receive()
                    identifier = received.get("id")
                    response = (
                        "method" not in received and isinstance(identifier, str) and identifier.startswith("dotfiles-server-")
                    )
                    message = self._from_client(received)
                    if message is not None:
                        if response:
                            # 復旧用RPCが対話要求を待つ間も応答を送る。旧daemonの要求は交代前に無効化する。
                            with contextlib.suppress(ConnectionError):
                                await back.send(message)
                        else:
                            await incoming.put(message)
            except EOFError, ConnectionError:
                await incoming.put(None)

        async with asyncio.TaskGroup() as readers, contextlib.AsyncExitStack() as resources:
            back = await resources.enter_async_context(native_proxy(self.binary, self.environment, timeout=self.timeout))
            pump = readers.create_task(receive_front())
            generation = self._generation()
            self.connected_generation = generation
            from_front = asyncio.create_task(incoming.get())
            from_back = asyncio.create_task(back.receive())
            tick = asyncio.create_task(asyncio.sleep(1))
            pending: dict[str | int, str] = {}
            try:
                while True:
                    done, _ = await asyncio.wait((from_front, from_back, tick), return_when=asyncio.FIRST_COMPLETED)
                    restart = False
                    if from_front in done:
                        message = from_front.result()
                        if message is None:
                            return
                        identifier = message.get("id")
                        if "method" in message and isinstance(identifier, (str, int)):
                            pending[identifier] = str(message["method"])
                        try:
                            await back.send(message)
                        except ConnectionError:
                            restart = True
                        from_front = asyncio.create_task(incoming.get())
                    if from_back in done:
                        try:
                            message = from_back.result()
                        except EOFError, ConnectionError:
                            restart = True
                        else:
                            identifier = message.get("id")
                            if isinstance(identifier, (str, int)):
                                pending.pop(identifier, None)
                            await self._to_client(front, message)
                            if self._public_resume_result is not None:
                                resumed, self._public_resume_result = self._public_resume_result, None
                                async with asyncio.timeout(self.timeout):
                                    await self._restore_work(
                                        front,
                                        back,
                                        resumed,
                                        original=self.state.conversation
                                        if isinstance(self.state.conversation, RunningTurn)
                                        else None,
                                        revision=self._user_turn_revision,
                                    )
                                self._bootstrap_thread = None
                                self.phase = RecoveryPhase.RELAYING
                            elif self._bootstrap_thread is not None:
                                # 通常resumeも最初のモデル要求に依存せず、保存済みの子孫へ接続する。
                                async with asyncio.timeout(self.timeout):
                                    await self._restore_delegations(front, back)
                                self._bootstrap_thread = None
                                self.phase = RecoveryPhase.RELAYING
                            from_back = asyncio.create_task(back.receive())
                    if tick in done:
                        current = self._generation()
                        restart = restart or generation is not None and current is not None and current != generation
                        generation = current or generation
                        tick = asyncio.create_task(asyncio.sleep(1))
                    if not restart:
                        continue
                    self.phase = RecoveryPhase.DAEMON_CHANGED
                    await self._invalidate_server_requests(front)
                    from_back.cancel()
                    await asyncio.gather(from_back, return_exceptions=True)
                    await resources.aclose()
                    for identifier, method in pending.items():
                        next_action = (
                            "この会話の入力欄へ「直前の依頼・実行履歴と対象ファイルやサービスの状態を調べ、"
                            "操作済みか確認してから続けてください」と送信する"
                            if self.state.thread_id is not None
                            else "codex doctorで接続を確認し、codex resumeで元の会話を選んで直前の依頼・実行履歴を確認する"
                        )
                        await front.send(
                            {
                                "id": identifier,
                                "error": {
                                    "code": -32000,
                                    "message": f"daemon再起動で{method}の応答を失い、操作の成否が不明。"
                                    f"{next_action}。結果を確認するまで同じ操作を再実行しない",
                                },
                            }
                        )
                    pending.clear()
                    self.phase = RecoveryPhase.RECONNECTING
                    async with asyncio.timeout(self.timeout):
                        delay = 0.05
                        while True:
                            try:
                                back = await resources.enter_async_context(
                                    native_proxy(self.binary, self.environment, timeout=self.timeout)
                                )
                                break
                            except ConnectionError:
                                await asyncio.sleep(delay + secrets.randbelow(50) / 1000)
                                delay = min(1, delay * 2)
                        await self._recover(front, back)
                    self.phase = RecoveryPhase.RELAYING
                    generation = self._generation()
                    self.connected_generation = generation
                    from_back = asyncio.create_task(back.receive())
            finally:
                for reader in (from_front, from_back, tick):
                    reader.cancel()
                await asyncio.gather(from_front, from_back, tick, return_exceptions=True)
                pump.cancel()
