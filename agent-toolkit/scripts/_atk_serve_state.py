"""watchdogの変更通知をSSE購読者へ中継する。"""

import asyncio
import contextlib
import pathlib
import threading
import time

import watchdog.events
import watchdog.observers


class ServeState(watchdog.events.FileSystemEventHandler):
    """変更通知と購読者を管理する。"""

    def __init__(self, root: pathlib.Path, *, debounce_seconds: float = 0.1) -> None:
        self.root = root
        self.debounce_seconds = debounce_seconds
        self.observer = watchdog.observers.Observer()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue[str]] = set()
        self._lock = threading.Lock()
        self._last_event = 0.0

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """監視を開始する。"""
        self._loop = loop
        for relative in (
            "feedback/inbox",
            "feedback/processing",
            "feedback/adopted",
            "feedback/rejected",
            "tbd/inbox",
            "tbd/adopted",
        ):
            path = self.root / relative
            path.mkdir(parents=True, exist_ok=True)
            self.observer.schedule(self, str(path), recursive=False)
        self.observer.start()

    def stop(self) -> None:
        """監視を停止してスレッド終了を待つ。"""
        self.observer.stop()
        self.observer.join()

    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        """Markdown変更を購読者へ通知する。"""
        if event.is_directory or not str(event.src_path).endswith(".md"):
            return
        now = time.monotonic()
        with self._lock:
            if now - self._last_event < self.debounce_seconds:
                return
            self._last_event = now
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.publish)

    def publish(self) -> None:
        """最新変更通知を全購読者へ配信する。"""
        for queue in tuple(self._queues):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait("changed")

    async def events(self, *, heartbeat: float = 15.0):
        """SSE形式の変更イベントとheartbeatを生成する。"""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        self._queues.add(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                    yield f"event: {event}\ndata: {{}}\n\n"
                except TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            self._queues.discard(queue)
