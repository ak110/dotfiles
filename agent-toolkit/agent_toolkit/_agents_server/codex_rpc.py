"""Codex App ServerとのJSON-RPC通信を担当する。

App Serverのwire protocolはJSON-RPC 2.0に準拠するが、stdioの各行では
``jsonrpc``フィールドを省略できる（OpenAI公式資料）。この実装では送信時に
``jsonrpc``を省略し、受信時は存在していても受理する。

公式資料: https://developers.openai.com/codex/app-server
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import typing
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from agent_toolkit._agents_server import child_environment, lifetime
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._agents_server import state as shared_state
from agent_toolkit._agents_server.codex_protocol import AppServerError, JsonRpcResponseError, ResourceReleaseError, _as_text
from agent_toolkit._agents_server.file_operations import finish_task, run_file_operation
from agent_toolkit._agents_server.session_errors import SessionInitializationTimeoutError
from agent_toolkit._agents_server.state import LaunchKind, append_bounded, utc_now
from agent_toolkit._common import delegated_session as _delegated_session
from agent_toolkit._common import inherited_venv, plugin_environment, process_tree, windows_job

if typing.TYPE_CHECKING:
    import psutil


_LOG = logging.getLogger("agent-toolkit.agents-server.codex")


APP_SERVER_COMMAND = ("codex", "app-server", "--stdio", "-c", "thread_unload_delay_secs=0")
# Codexホストでは親の作業ディレクトリが版数付きplugin cacheとなり、
# plugin更新で消失すると生存中のApp Serverが設定を読み込めないため継承しない。
APP_SERVER_WORKING_DIRECTORY = str(Path.home())


# App Serverのstdio用StreamReaderのbuffer上限と、stdoutを1回に読み取るchunk長を兼ねる値（バイト）。
# JSON-RPC recordの最大値には使わず、この長さを超える行もchunkを結合して復元する。
APP_SERVER_STREAM_LIMIT_BYTES = 8 * 1024 * 1024
APP_SERVER_STDERR_LIMIT_CHARS = 4000
APP_SERVER_EXIT_DIAGNOSTIC_TIMEOUT = 1.0


class JsonRpcProcess:
    """stdio App Serverとの要求・応答を多重化するクライアント。"""

    _close_task: asyncio.Task[list[str]] | None = None

    async def __aenter__(self) -> typing.Self:
        return self

    async def __aexit__(self, *_exception: object) -> None:
        residual = await self.close()
        if residual:
            raise ResourceReleaseError("unassigned", residual)

    def __init__(
        self,
        on_notification: Callable[[dict[str, Any]], Awaitable[None]],
        on_server_request: Callable[[dict[str, Any]], Awaitable[None]],
        on_failure: Callable[[BaseException], Awaitable[None]] | None = None,
        *,
        root_session_id: str | None = None,
        launch_kind: LaunchKind | None = None,
        handoff_prompt: str = "",
    ) -> None:
        self._on_notification = on_notification
        self._on_server_request = on_server_request
        self._on_failure = on_failure
        self._root_session_id = root_session_id
        self._launch_kind = launch_kind
        self._handoff_prompt = handoff_prompt
        self.process: asyncio.subprocess.Process | None = None
        self.parent_environment: Mapping[str, str] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self._closed = False
        self._reader_failure: BaseException | None = None
        self._stderr_text = ""
        self._initialization_stage = "created"
        self._received_message_types: dict[str, int] = {}
        self._residual: list[psutil.Process] = []
        # 生成の停止を確認できなかった前回の停止の理由と、その停止が所有した対象。再closeで同じ対象から回収を再試行する。
        self._unresolved: list[str] = []
        self._owned: list[psutil.Process] = []
        self._job: windows_job.WindowsJob | None = None

    def initialization_diagnostic(self) -> dict[str, Any]:
        """初期化の到達段階と、長さに上限を設けた子プロセスの診断値を返す。"""
        process = self.process
        return {
            "stage": self._initialization_stage,
            "received_message_types": dict(sorted(self._received_message_types.items())),
            "child_pid": None if process is None else process.pid,
            "stderr": self._stderr_text.strip(),
        }

    async def start(self) -> None:
        """子プロセスを起動し、initialize/initializedを完了する。"""
        if self.process is not None:
            return
        self._initialization_stage = "starting_process"
        try:
            environment = plugin_environment.cli_environment(plugin_roots.SERVER_PLUGIN_ROOT, child_environment.inherited())
            inherited_venv.strip_inherited_venv(environment)
            environment.pop(_delegated_session.DELEGATED_SESSION_ENV, None)
            # 委譲先のhookが軽量起動を判別する。turnを実行しない照会用の接続は起動区分を持たない。
            environment.pop(_delegated_session.LAUNCH_KIND_ENV, None)
            environment[_delegated_session.PLUGIN_ROOT_ENV] = str(plugin_roots.SERVER_PLUGIN_ROOT)
            if self._launch_kind is not None:
                environment[_delegated_session.LAUNCH_KIND_ENV] = self._launch_kind
            environment.pop(_delegated_session.HANDOFF_PATH_ENV, None)
            environment.update(_delegated_session.handoff_environment(self._handoff_prompt))
            owner_session_id = self._root_session_id
            if owner_session_id is not None:
                environment[_delegated_session.OWNER_SESSION_ENV] = owner_session_id
            self.parent_environment = environment
            creation_options: dict[str, Any] = {}
            if sys.platform == "win32":
                self._job = windows_job.WindowsJob()
                creation_options["creationflags"] = windows_job.CREATE_SUSPENDED
            # 時間上限なし: 常駐の接続であり、要求ごとの応答待ちの上限は呼び出し側が持つ。
            self.process = await asyncio.create_subprocess_exec(
                *APP_SERVER_COMMAND,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=APP_SERVER_STREAM_LIMIT_BYTES,
                cwd=APP_SERVER_WORKING_DIRECTORY,
                env=environment,
                **creation_options,
            )
            if self._job is not None:
                # asyncioの公開ProcessはOS handleを公開しない。CPython transportが保持する
                # 元世代のPopen handleを使い、PIDの再解決を経ずJob所属を確定する。
                transport = typing.cast(Any, self.process)._transport  # noqa: SLF001  # pylint: disable=protected-access
                owned_process = transport.get_extra_info("subprocess")
                handle = int(owned_process._handle)  # noqa: SLF001  # pylint: disable=protected-access
                await run_file_operation(self._job.assign_suspended, handle, self.process.pid)
        except OSError as exc:
            await self.close()
            _LOG.exception("Codex App Serverを起動できません: command=%s", " ".join(APP_SERVER_COMMAND))
            raise AppServerError(f"failed to start {' '.join(APP_SERVER_COMMAND)}: {exc}") from exc
        except BaseException:
            await self.close()
            raise
        _LOG.info(
            "Codex App Serverを起動しました: command=%s cwd=%s pid=%s",
            " ".join(APP_SERVER_COMMAND),
            APP_SERVER_WORKING_DIRECTORY,
            self.process.pid,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self._initialization_stage = "process_started"
        _LOG.info("Codex App Server初期化段階: %s", self.initialization_diagnostic())
        try:
            # 子プロセスが応答を返さないまま生存する場合、読取taskが失敗しても要求の応答futureは解消しない。
            async with asyncio.timeout(shared_state.SESSION_INITIALIZATION_TIMEOUT):
                self._initialization_stage = "initialize_requested"
                initialize_result = await self.request(
                    "initialize",
                    {
                        "clientInfo": {"name": "agent-toolkit-codex-app-server", "version": "1.0"},
                        "capabilities": {},
                    },
                )
                self._initialization_stage = "initialize_received"
                _LOG.info("Codex App Serverのinitialize応答を受信しました: keys=%s", sorted(initialize_result))
                await self.notify("initialized", {})
                self._initialization_stage = "initialized_sent"
                _LOG.info("Codex App Server初期化完了: %s", self.initialization_diagnostic())
        except TimeoutError as exc:
            diagnostic = self.initialization_diagnostic()
            _LOG.error("Codex App Server初期化timeout: diagnostic=%s", diagnostic)
            await self.close()
            raise SessionInitializationTimeoutError(
                f"Codex App Server did not complete initialize within {shared_state.SESSION_INITIALIZATION_TIMEOUT:.0f}s: "
                f"command={' '.join(APP_SERVER_COMMAND)}; diagnostic={diagnostic}"
            ) from exc
        except BaseException as exc:
            _LOG.error(
                "Codex App Server初期化失敗: exception_type=%s exception=%s diagnostic=%s",
                type(exc).__name__,
                exc,
                self.initialization_diagnostic(),
            )
            await self.close()
            raise

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_sent: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """JSON-RPC requestを送り、対応するIDの応答を返す。"""
        if self.process is None or self.process.stdin is None:
            raise AppServerError("Codex App Server is not running")
        if self._closed or self._reader_failure is not None:
            raise AppServerError("Codex App Server client is closed")
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params or {}})
            if on_sent is not None:
                on_sent()
            response = await future
        finally:
            self._pending.pop(request_id, None)
        if "error" in response:
            error = response.get("error")
            if isinstance(error, dict):
                message = _as_text(error.get("message")) or "JSON-RPC request failed"
                raise JsonRpcResponseError(method, error.get("code"), message, error.get("data"))
            message = "JSON-RPC request failed"
            raise JsonRpcResponseError(method, None, message)
        result = response.get("result", {})
        return result if isinstance(result, dict) else {}

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """JSON-RPC notificationを送る。"""
        await self._send({"method": method, "params": params or {}})

    async def _send(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None or self._closed:
            raise AppServerError("Codex App Server stdin is unavailable")
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                self.process.stdin.write(encoded)
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionError) as exc:
                diagnostic = self.initialization_diagnostic()
                _LOG.error(
                    "Codex App Serverへの書込失敗: exception_type=%s exception=%s diagnostic=%s",
                    type(exc).__name__,
                    exc,
                    diagnostic,
                )
                raise AppServerError(f"failed to write to Codex App Server: {exc}; diagnostic={diagnostic}") from exc

    @property
    def closed(self) -> bool:
        """子プロセスとの接続が終了処理済みであるかを返す。"""
        return self._closed

    @property
    def reader_failure(self) -> BaseException | None:
        """Stdout readerが検出した失敗を返す。"""
        return self._reader_failure

    async def send(self, message: dict[str, Any]) -> None:
        """JSON-RPC応答を接続へ送る。"""
        await self._send(message)

    async def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        buffer = bytearray()
        try:
            while True:
                chunk = await self.process.stdout.read(APP_SERVER_STREAM_LIMIT_BYTES)
                if not chunk:
                    if buffer:
                        await self._dispatch_stdout_line(bytes(buffer))
                    error = await self._stdout_closed_error()
                    _LOG.error("%s", error)
                    raise error
                buffer.extend(chunk)
                while (newline_index := buffer.find(b"\n")) >= 0:
                    raw_line = bytes(buffer[:newline_index])
                    del buffer[: newline_index + 1]
                    await self._dispatch_stdout_line(raw_line)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._reader_failure = exc
            for future in tuple(self._pending.values()):
                if not future.done():
                    future.set_exception(AppServerError(str(exc)))
            if self._on_failure is not None:
                with contextlib.suppress(Exception):
                    await self._on_failure(exc)

    async def _dispatch_stdout_line(self, raw_line: bytes) -> None:
        """復元済みの1 JSON-RPC recordを既存の応答・要求・通知処理へ渡す。"""
        try:
            message = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppServerError(f"invalid Codex App Server JSON line: {exc}") from exc
        if not isinstance(message, dict):
            return
        if "id" in message and ("result" in message or "error" in message):
            message_type = "response"
        elif "id" in message and isinstance(message.get("method"), str):
            message_type = "server_request"
        else:
            message_type = str(message.get("method", "notification"))
        self._received_message_types[message_type] = self._received_message_types.get(message_type, 0) + 1
        if "id" in message and ("result" in message or "error" in message):
            request_id = message.get("id")
            future = self._pending.get(request_id) if isinstance(request_id, int) else None
            if future is not None and not future.done():
                future.set_result(message)
            return
        if "id" in message and isinstance(message.get("method"), str):
            await self._on_server_request(message)
            return
        if isinstance(message.get("method"), str):
            await self._on_notification(message)

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    return
                text = line.decode("utf-8", errors="replace")
                self._stderr_text = append_bounded(
                    self._stderr_text,
                    text,
                    APP_SERVER_STDERR_LIMIT_CHARS,
                )
                print(
                    text,
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
        except OSError as exc:
            _LOG.debug("Codex App Server stderrの読取を終了しました: %s", exc)

    async def _stdout_closed_error(self) -> AppServerError:
        """子プロセス終了時の診断情報を、待ち時間と長さに上限を設けて収集する。"""
        assert self.process is not None
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                self.process.wait(),
                timeout=APP_SERVER_EXIT_DIAGNOSTIC_TIMEOUT,
            )
        stderr_task = self._stderr_task
        if stderr_task is not None and stderr_task is not asyncio.current_task():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.shield(stderr_task),
                    timeout=APP_SERVER_EXIT_DIAGNOSTIC_TIMEOUT,
                )

        command = " ".join(APP_SERVER_COMMAND)
        message = f"Codex App Server stdout closed: command={command}; returncode={self.process.returncode}"
        stderr = self._stderr_text.strip()
        if stderr:
            message = f"{message}; stderr={stderr}"
        return AppServerError(message)

    async def close(self) -> list[str]:
        """自身が起動した子プロセスと、その子孫プロセスを終了し、関連taskを回収する。

        対象は自身が起動したプロセスからの子孫関係だけで特定する。
        戻り値は資源を保持したまま残った対象（App Server自身を含む）と、生成の停止を確認できなかった理由の記述であり、
        空なら所有を確認できた対象は全て終了している。
        POSIXで一時停止できなかった対象が終了前に起動した子は辿れないため、再closeで所有対象を回収した後は
        確認できないまま解放へ進み、その可能性を残存診断へ記録する（`_common/process_tree.py`の保証境界）。
        残った対象は保持し、同じ接続への再度の`close`で回収を再試行する。
        """
        task = self._close_task
        if task is None or task.done() and (task.cancelled() or task.exception() is not None or task.result()):
            task = self._close_task = asyncio.create_task(self._close())
        return await finish_task(task)

    async def _close(self) -> list[str]:
        if self._closed and self.process is None and self._job is None and not self._residual and not self._unresolved:
            return []
        process = self.process
        reclaimed = False
        job_reclamation = self._job is not None
        diagnostic_targets = [] if process is None else [process.pid]
        if sys.platform == "win32":
            _LOG.info(
                "Codex App Server終了要求前: at=%s observer_pid=%s owned=%s",
                utc_now(),
                os.getpid(),
                await run_file_operation(lambda: [process_tree.identity(pid) for pid in diagnostic_targets]),
            )
        if not self._closed:
            self._closed = True
            tasks = tuple(
                task
                for task in (self._reader_task, self._stderr_task)
                if task is not None and task is not asyncio.current_task()
            )
            self._reader_task = None
            self._stderr_task = None
            for task in tasks:
                if not task.done():
                    task.cancel()
            # App Serverと子孫の生成を止めてから終了させる。終了要求前の1回の列挙だけを回収すると、
            # その直後に起動した孫が漏れ、Windowsではcodex-homeを保持したまま残った。
            if self._job is None and process is not None and process.returncode is None:
                result = await asyncio.to_thread(process_tree.stop_tree_by_pid, process.pid)
                if result is not None:
                    reclaimed = True
                    self._residual = [item for item in result.residual if item.pid != process.pid]
                    self._unresolved = result.unresolved_descriptions()
                    self._owned = result.targets
                    diagnostic_targets += [item.pid for item in result.targets if item.pid != process.pid]
        else:
            tasks = ()
        if self._job is not None:
            try:
                await run_file_operation(self._job.terminate)
                await run_file_operation(self._job.close)
            except OSError as error:
                # Jobの所有を保ち、失敗とActiveProcesses未確認を成功へ変換しない。
                self._unresolved = [f"Windows Jobの終了を確認できない: {error}"]
            else:
                self._job = None
                self._unresolved = []
            reclaimed = True
        if process is not None:
            # 停止処理が終了させた後も、asyncioが終了状態を回収するまでreturncodeは未確定のまま残る。
            with contextlib.suppress(OSError, ProcessLookupError):
                if process.returncode is None:
                    process.terminate()
            if process.returncode is None:
                with contextlib.suppress(asyncio.TimeoutError):
                    await lifetime.wait_for_shutdown((asyncio.create_task(process.wait()),), timeout=5)
            if process.returncode is None:
                with contextlib.suppress(OSError, ProcessLookupError):
                    process.kill()
                with contextlib.suppress(asyncio.TimeoutError):
                    await lifetime.wait_for_shutdown((asyncio.create_task(process.wait()),), timeout=5)
        if tasks:
            await lifetime.wait_for_shutdown(tasks)
        if process is not None:
            _LOG.info("Codex App Serverを終了しました: returncode=%s", process.returncode)
        if not job_reclamation and not reclaimed and self._unresolved:
            # 前回の停止で生成の停止を確認できなかった。所有した対象を起点に、残る子孫を改めて回収する。
            retry = await asyncio.to_thread(process_tree.stop_tree, None, known=[*self._owned, *self._residual])
            self._residual = retry.residual
            if sys.platform != "win32" and not retry.unresolved:
                # POSIXでは親を失った子がinitへ付け替わり、再試行の列挙でも見つけられない（保証境界）。
                # 未確定を保持し続けると解放もサーバーの停止も完了しないため、再試行で所有対象を回収した後は
                # 解放へ進み、確認できないまま残る可能性を残存診断へ記録して観測できるようにする。
                process_tree.log_residual(
                    [f"再試行でも確認できない: {reason}" for reason in self._unresolved],
                    context="codex app-server retry",
                    retryable=False,
                )
            self._unresolved = retry.unresolved_descriptions()
            self._owned = retry.targets
        elif not reclaimed:
            self._residual = await asyncio.to_thread(process_tree.reclaim_processes, self._residual)
        if sys.platform == "win32":
            _LOG.info(
                "Codex App Server終了確認後: at=%s observer_pid=%s app_server_pid=%s returncode=%s owned=%s",
                utc_now(),
                os.getpid(),
                None if process is None else process.pid,
                None if process is None else process.returncode,
                await run_file_operation(lambda: [process_tree.identity(pid) for pid in diagnostic_targets]),
            )
        residual = [process_tree.describe(item) for item in self._residual] + self._unresolved
        if process is not None and process.returncode is not None:
            self.process = None
        elif process is not None:
            residual.insert(0, f"{process.pid}: {' '.join(APP_SERVER_COMMAND)}")
        process_tree.log_residual(residual, context="codex app-server")
        error = AppServerError("Codex App Server client closed")
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        return residual
