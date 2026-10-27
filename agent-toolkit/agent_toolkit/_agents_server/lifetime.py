"""stdio起動単位の停止期限を、イベントループと診断I/Oから独立して守る。"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import ctypes.wintypes
import os
import select
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

import psutil

from agent_toolkit._common import process_tree

SHUTDOWN_TIMEOUT = 15.0
POLL_INTERVAL = 0.1


async def wait_for_shutdown(tasks: Iterable[asyncio.Task[Any]], *, timeout: float = SHUTDOWN_TIMEOUT) -> list[Any]:
    """取り消しを無視するtaskの完了待ちを上限で打ち切り、他の回収を継続する。"""
    tasks = tuple(tasks)
    if not tasks:
        return []
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    outcomes: list[Any] = []
    for task in tasks:
        if task in pending:
            task.cancel()
            task.add_done_callback(_consume_exception)
            outcomes.append(TimeoutError("停止taskが期限内に終了しませんでした"))
        elif task.cancelled():
            outcomes.append(asyncio.CancelledError())
        elif (error := task.exception()) is not None:
            outcomes.append(error)
        else:
            outcomes.append(task.result())
    return outcomes


def _consume_exception(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()


class ServerLifetime:
    """起動時の親identityと、自身の子孫handleだけを監視・回収する。"""

    def __init__(self, *, timeout: float | None = None) -> None:
        self._process = psutil.Process()
        self._parent = self._process.parent()
        self._timeout = SHUTDOWN_TIMEOUT if timeout is None else timeout
        self._deadline: float | None = None
        self._exitcode = 0
        self._finished = threading.Event()
        self._targets: dict[tuple[int, float], psutil.Process] = {}
        self._callback: Callable[[], None] | None = None
        self._notified = False
        self._cancel_transport = False
        self._handlers: dict[int, Any] = {}
        self._thread = threading.Thread(target=self._watch, name="agents-server-lifetime", daemon=True)
        # MCP SDKはfd 0をnullへ差し替えるため、起動時のwireを別descriptorとして保持する。
        self._stdin_fd = os.dup(0)
        self._stdin_poll = None
        self._pipe_closed: Callable[[], bool] | None = None
        if sys.platform != "win32":
            self._stdin_poll = select.poll()
            self._stdin_poll.register(self._stdin_fd, select.POLLHUP | select.POLLERR)
        else:
            import msvcrt  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error  # WindowsのCRT descriptorを参照する。

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.PeekNamedPipe.argtypes = [
                ctypes.wintypes.HANDLE,
                ctypes.c_void_p,
                ctypes.wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            stdin = msvcrt.get_osfhandle(self._stdin_fd)
            self._pipe_closed = lambda: (
                not kernel.PeekNamedPipe(stdin, None, 0, None, None, None) and ctypes.get_last_error() in (109, 232)
            )

    def start(self) -> None:
        """通常の初期化より先に親監視と停止signalの受信を開始する。"""
        try:
            for name in ("SIGINT", "SIGTERM", "SIGHUP"):
                if (signum := getattr(signal, name, None)) is not None:
                    self._handlers[signum] = signal.signal(signum, self._signal)
            self._thread.start()
        except BaseException:
            os.close(self._stdin_fd)
            for signum, previous in self._handlers.items():
                signal.signal(signum, previous)
            raise

    def _signal(self, signum: int, _frame: Any) -> None:
        # handlerからlogger、ファイルI/O、Eventの内部ロックを呼ばない。
        self.request_stop(128 + signum)

    def request_stop(self, exitcode: int = 0, *, cancel_transport: bool = True) -> None:
        """最初の停止要求で期限を固定し、再signalで延長しない。"""
        if self._deadline is None:
            self._exitcode = exitcode
            self._deadline = time.monotonic() + self._timeout
        self._cancel_transport |= cancel_transport

    def set_stop_callback(self, callback: Callable[[], None] | None) -> None:
        """監視threadから通常のtransport停止を要求する操作を設定する。"""
        self._callback = callback

    @property
    def exitcode(self) -> int:
        """最初の停止要求に対応する終了コード。"""
        return self._exitcode

    @property
    def stopping(self) -> bool:
        """EOF、親消滅またはsignalから停止が要求されたかを返す。"""
        return self._deadline is not None

    def finish(self) -> None:
        """正常な終了処理の完了後に監視を解除する。"""
        self.request_stop(cancel_transport=False)
        targets = list(self._targets.values()) + process_tree.collect_descendants(self._process.pid)
        process_tree.reclaim_processes(targets)
        self._finished.set()
        self._thread.join(timeout=POLL_INTERVAL * 2)
        os.close(self._stdin_fd)
        for signum, previous in self._handlers.items():
            signal.signal(signum, previous)

    def _collect(self) -> None:
        for key, process in tuple(self._targets.items()):
            with contextlib.suppress(psutil.Error, OSError):
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    continue
            self._targets.pop(key, None)
        for process in process_tree.collect_descendants(self._process.pid):
            with contextlib.suppress(psutil.Error, OSError):
                self._targets[(process.pid, process.create_time())] = process

    def _parent_gone(self) -> bool:
        if self._parent is None:
            return True
        if os.name != "nt" and self._parent.pid == 1:
            return True
        try:
            return not self._parent.is_running() or self._parent.status() == psutil.STATUS_ZOMBIE
        except psutil.Error, OSError:
            return True

    def _watch(self) -> None:
        while not self._finished.wait(POLL_INTERVAL):
            self._collect()
            if self._parent_gone():
                self.request_stop()
            elif (
                self._stdin_poll is not None
                and self._stdin_poll.poll(0)
                or self._pipe_closed is not None
                and self._pipe_closed()
            ):
                # EOFはSDK自身の正常closeに任せ、loopが停止していれば独立した期限で回収する。
                self.request_stop(cancel_transport=False)
            if self._deadline is None:
                continue
            if self._callback is not None and not self._notified and self._cancel_transport:
                self._notified = True
                with contextlib.suppress(RuntimeError):
                    self._callback()
            if time.monotonic() >= self._deadline:
                # shutdownやloggingのロック、asyncio.runの残task待機へ戻らず終了する。
                process_tree.reclaim_processes(list(self._targets.values()))
                if not self._finished.is_set():
                    os._exit(self._exitcode)
                return
