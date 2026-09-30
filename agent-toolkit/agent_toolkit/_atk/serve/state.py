"""WIの変更と同期結果をSSE購読者へ中継する。"""

import asyncio
import collections.abc
import contextlib
import pathlib
import threading
import time
import typing

import watchdog.events
import watchdog.observers

from agent_toolkit._atk.wi import common

_TimerFactory = collections.abc.Callable[[float, collections.abc.Callable[..., None], tuple[typing.Any, ...]], threading.Timer]


class ServeState(watchdog.events.FileSystemEventHandler):
    """変更通知と購読者を管理する。

    `monotonic`は保留期間の経過判定に使う単調時計、`timer_factory`は保留通知の遅延発火に使う
    タイマーの生成関数とする。既定値は標準ライブラリの実装であり、呼び出し側が別の実装を渡すと
    実時間の経過に依存せず発火時刻の決定を確認できる。
    """

    # 末尾発行の先送りが無限に続くことを防ぐ最大待機時間の倍率。
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
        self.observer = watchdog.observers.Observer()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queues: set[asyncio.Queue[str]] = set()
        self._lock = threading.Lock()
        self._pending_notification: threading.Timer | None = None
        self._pending_started_at: float | None = None
        self._pending_generation = 0
        self._stopped = False
        # サーバーの停止要求。SSEの生成処理はこれを参照して応答を完了させる。
        self.shutdown_requested = asyncio.Event()

    def request_shutdown(self) -> None:
        """停止要求を設定する。購読中のSSE応答は次の待ちでこの要求を受けて終わる。

        hypercornは停止時に全接続の切断を待ってから`graceful_timeout`を適用するため、
        クライアントが切断するまで続くSSE応答を停止要求で完了させないと、停止が無期限に待つ。
        """
        self.shutdown_requested.set()

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """監視を開始する。"""
        self._loop = loop
        self._stopped = False
        for relative in common.WI_STATES:
            path = self.root / relative
            path.mkdir(parents=True, exist_ok=True)
            self.observer.schedule(self, str(path), recursive=False)
        self.observer.start()

    def stop(self) -> None:
        """監視を停止してスレッド終了を待つ。"""
        with self._lock:
            self._stopped = True
            if self._pending_notification is not None:
                self._pending_notification.cancel()
                self._pending_notification = None
            self._pending_started_at = None
            self._pending_generation += 1
        self.observer.stop()
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
        """監視停止後のイベントループ上では通知を発行しない。"""
        with self._lock:
            if self._stopped:
                return
        self.publish()

    def _publish_markdown_change(self, event: watchdog.events.FileSystemEvent) -> None:
        """Markdownの内容または配置の変更を購読者へ通知する。"""
        paths = (event.src_path, getattr(event, "dest_path", ""))
        if event.is_directory or not any(str(path).endswith(".md") for path in paths):
            return
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
                # 期限を上界として保証するため、静穏期間と期限までの残り時間の短い方を待つ。
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
        """Markdown作成を購読者へ通知する。"""
        self._publish_markdown_change(event)

    def on_modified(self, event: watchdog.events.FileSystemEvent) -> None:
        """Markdown変更を購読者へ通知する。"""
        self._publish_markdown_change(event)

    def on_deleted(self, event: watchdog.events.FileSystemEvent) -> None:
        """Markdown削除を購読者へ通知する。"""
        self._publish_markdown_change(event)

    def on_moved(self, event: watchdog.events.FileSystemEvent) -> None:
        """Markdown移動を購読者へ通知する。"""
        self._publish_markdown_change(event)

    def publish(self, event: str = "changed") -> None:
        """WIの変更または同期結果を全購読者へ配信する。"""
        for queue in tuple(self._queues):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)

    async def events(self, *, heartbeat: float = 15.0):
        """SSE形式の変更イベントとheartbeatを生成する。

        heartbeatはコメント行ではなく名前付きイベントで送る。ブラウザーの`EventSource`はコメント行を
        スクリプトへ渡さないため、名前付きイベントにしないとクライアントが無通信を判定できない。
        """
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=2)
        self._queues.add(queue)
        try:
            while True:
                received = await next_subscription_item(queue, self.shutdown_requested, heartbeat)
                if received is SHUTDOWN:
                    return
                if received is HEARTBEAT:
                    yield "event: heartbeat\ndata: {}\n\n"
                else:
                    yield f"event: {received}\ndata: {{}}\n\n"
        finally:
            self._queues.discard(queue)


class _Signal:
    """`next_subscription_item`が通知本文の代わりに返す区分。"""

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
    """購読キューの次の通知、heartbeat間隔の経過（`HEARTBEAT`）、停止要求（`SHUTDOWN`）のいずれかを返す。

    SSEの各経路は本関数で待ち、`SHUTDOWN`を受けたら生成処理を終える。停止要求を参照しない待ちは、
    クライアントが切断するまでサーバーの停止を妨げる。
    """
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
