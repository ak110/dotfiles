"""WIの変更と同期結果をSSE購読者へ中継する。"""

import asyncio
import collections.abc
import contextlib
import logging
import pathlib
import threading
import time
import typing

import watchdog.events
import watchdog.observers
import watchdog.observers.api

from agent_toolkit._common import watch_observer as _watch_observer
from agent_toolkit._common.async_operations import run_sync_operation

_logger = logging.getLogger(__name__)

_TimerFactory = collections.abc.Callable[[float, collections.abc.Callable[..., None], tuple[typing.Any, ...]], threading.Timer]


class ServeState(watchdog.events.FileSystemEventHandler):
    # 変更が続く間に保留通知の発行を際限なく先送りしないよう、最大待機時間を決める倍率。
    # 閾値未満の間隔で変更が到着し続ける場合でも、最初の保留から
    # `debounce_seconds * _MAX_DEBOUNCE_FACTOR`が経過した時点で1回発行する。
    _MAX_DEBOUNCE_FACTOR = 5

    def __init__(
        self,
        root: pathlib.Path,
        *,
        debounce_seconds: float = 0.1,
        monotonic: collections.abc.Callable[[], float] = time.monotonic,
        timer_factory: _TimerFactory = threading.Timer,
    ) -> None:
        self.root = root
        self.debounce_seconds = debounce_seconds
        self._monotonic = monotonic
        self._timer_factory = timer_factory

        self.observer: watchdog.observers.api.BaseObserver | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue[str]] = set()
        self._lock = threading.Lock()
        self._pending_notification: threading.Timer | None = None
        self._pending_started_at: float | None = None
        self._pending_generation = 0
        self._stopped = False

        self.shutdown_requested = asyncio.Event()

        self.stop_requested = threading.Event()

        self.publish_listener: collections.abc.Callable[[str], None] | None = None

        self.change_listener: collections.abc.Callable[[], None] | None = None

    async def __aenter__(self) -> ServeState:
        """監視を開始し、開始途中の失敗でも資源を回収する。"""
        await self.start_async(asyncio.get_running_loop())
        return self

    async def __aexit__(self, *_exception: object) -> None:
        await self.stop_async()

    def request_shutdown(self) -> None:
        """停止要求を設定する。

        hypercornは停止時に全接続の切断を待ってから`graceful_timeout`を適用し、`asyncio.run`の終了処理は
        イベントループが作成したexecutorのスレッドの終了を待つ。停止要求で処理中の要求、購読中のSSE応答、スレッドの走査および
        リモート接続を終えないと、停止がそれらの完了まで待つ。
        """
        self.shutdown_requested.set()
        self.stop_requested.set()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._stopped = False
        try:
            self._start_observer()
        except BaseException:
            self.stop()
            raise

    def _start_observer(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.observer = _watch_observer.start_observer(
            lambda observer: observer.schedule(self, str(self.root), recursive=True),
            observer=self.observer,
            on_fallback=lambda error: _logger.warning("%s", _watch_observer.fallback_message(error, restart="`atk serve`")),
        )

    def is_watching(self) -> bool:
        return self.observer is not None and self.observer.is_alive()

    async def start_async(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._stopped = False
        try:
            await run_sync_operation(self._start_observer)
        except BaseException:
            await self.stop_async()
            raise

    async def stop_async(self) -> None:
        await run_sync_operation(self.stop)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            pending = self._pending_notification
            if self._pending_notification is not None:
                self._pending_notification.cancel()
                self._pending_notification = None
            self._pending_started_at = None
            self._pending_generation += 1
        try:
            if (
                isinstance(pending, threading.Thread)
                and pending.ident is not None
                and pending is not threading.current_thread()
            ):
                pending.join()
        finally:
            if self.observer is not None:
                self.observer.stop()
                if self.observer.ident is not None:
                    self.observer.join()

    def _publish_pending(self, generation: int) -> None:
        """静穏期間の経過後または最大待機時間の到達後に保留通知を1回だけ発行する。"""
        with self._lock:
            if generation != self._pending_generation:
                return
            self._pending_notification = None
            self._pending_started_at = None
            if self._stopped:
                return
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._publish_if_running)

    def _publish_if_running(self) -> None:
        with self._lock:
            if self._stopped:
                return
        self.publish()

    def _publish_markdown_change(self, event: watchdog.events.FileSystemEvent) -> None:
        paths = (event.src_path, getattr(event, "dest_path", ""))
        if event.is_directory or not any(str(path).endswith(".md") for path in paths):
            return

        if self.change_listener is not None:
            self.change_listener()
        if self.debounce_seconds <= 0:
            with self._lock:
                if self._stopped:
                    return
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._publish_if_running)
            return
        publish_at_deadline = False
        with self._lock:
            if self._stopped:
                return
            now = self._monotonic()
            if self._pending_started_at is None:
                self._pending_started_at = now
            deadline = self._pending_started_at + self.debounce_seconds * self._MAX_DEBOUNCE_FACTOR
            if self._pending_notification is not None:
                self._pending_notification.cancel()
            self._pending_generation += 1
            if now >= deadline:
                # 期限に到達したため新しいタイマーを設定せず、ロック外で直ちに発行する。
                self._pending_notification = None
                self._pending_started_at = None
                publish_at_deadline = True
            else:
                # 発行が期限より遅れないよう、静穏期間と期限までの残り時間の短い方を待つ。
                interval = min(self.debounce_seconds, deadline - now)
                self._pending_notification = self._timer_factory(
                    interval,
                    self._publish_pending,
                    (self._pending_generation,),
                )
                self._pending_notification.daemon = True
                self._pending_notification.start()
        if publish_at_deadline and self._loop is not None:
            self._loop.call_soon_threadsafe(self._publish_if_running)

    def on_created(self, event: watchdog.events.FileSystemEvent) -> None:
        self._publish_markdown_change(event)

    def on_modified(self, event: watchdog.events.FileSystemEvent) -> None:
        self._publish_markdown_change(event)

    def on_deleted(self, event: watchdog.events.FileSystemEvent) -> None:
        self._publish_markdown_change(event)

    def on_moved(self, event: watchdog.events.FileSystemEvent) -> None:
        self._publish_markdown_change(event)

    def publish(self, event: str = "changed") -> None:
        """WIの変更または同期結果を全購読者へ配信する。"""
        if self.publish_listener is not None:
            self.publish_listener(event)
        for queue in tuple(self._queues):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)

    async def events(self, *, heartbeat: float = 15.0):
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=2)
        self._queues.add(queue)
        try:
            yield "event: subscribed\ndata: {}\n\n"
            while True:
                received = await next_subscription_item(queue, self.shutdown_requested, heartbeat)
                if received is SHUTDOWN:
                    return
                if received is HEARTBEAT:
                    yield "event: heartbeat\ndata: {}\n\n"
                else:
                    yield f'event: {received}\ndata: {{"published_at_ms": {time.time() * 1000:.0f}}}\n\n'
        finally:
            self._queues.discard(queue)


class _Signal:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


HEARTBEAT = _Signal("HEARTBEAT")
SHUTDOWN = _Signal("SHUTDOWN")


async def next_subscription_item(
    queue: asyncio.Queue[str],
    shutdown: asyncio.Event,
    heartbeat: float,
) -> str | _Signal:
    if shutdown.is_set():
        return SHUTDOWN
    get_task = asyncio.ensure_future(queue.get())
    stop_task = asyncio.ensure_future(shutdown.wait())
    try:
        done, _ = await asyncio.wait({get_task, stop_task}, timeout=heartbeat, return_when=asyncio.FIRST_COMPLETED)
    finally:
        get_task.cancel()
        stop_task.cancel()
    if get_task in done:
        return get_task.result()
    if stop_task in done:
        return SHUTDOWN
    return HEARTBEAT
