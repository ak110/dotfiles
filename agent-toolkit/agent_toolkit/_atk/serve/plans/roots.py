from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime
import json
import logging
import threading
import typing

import watchdog.events
import watchdog.observers
import watchdog.observers.api
from pygments.formatters.html import HtmlFormatter

from agent_toolkit._atk.serve import remote as _atk_serve_remote

if typing.TYPE_CHECKING:
    from agent_toolkit._atk.serve.plans.remote import RemoteWatcher

logger = logging.getLogger(__name__)


_BROADCAST_DEBOUNCE_SEC = 0.3
_SSE_REFRESH_PAYLOAD = json.dumps({"type": "refresh"}, ensure_ascii=False)


_WATCHED_EVENT_TYPES: tuple[type[watchdog.events.FileSystemEvent], ...] = (
    watchdog.events.FileCreatedEvent,
    watchdog.events.FileModifiedEvent,
    watchdog.events.FileDeletedEvent,
    watchdog.events.FileMovedEvent,
    watchdog.events.FileClosedEvent,
)


MARKDOWN_CACHE_MAX_ENTRIES = 128
MARKDOWN_CACHE_MAX_BYTES = 16 * 1024 * 1024


# SSH接続時に共通付与するオプション。
# `BatchMode=yes`で鍵認証失敗時にパスワードプロンプトでハングしないようにする。
SSH_BASE_OPTIONS = ("-o", "BatchMode=yes")

SSH_TIMEOUT_SEC = 30.0

STDERR_EXCERPT_MAX_CHARS = _atk_serve_remote.STDERR_EXCERPT_MAX_CHARS

RPC_REQUEST_TIMEOUT_SEC = 30.0
# SSHによる代替の検索を同時に実行する上限（全ホスト合計）。
DEFAULT_REMOTE_SEARCH_LIMIT = 4

SSH_WATCH_OPTIONS = (
    "-o",
    "ConnectTimeout=5",
    "-o",
    "ServerAliveInterval=10",
    "-o",
    "ServerAliveCountMax=3",
)

REMOTE_BACKOFF_INITIAL_SEC = 1.0
REMOTE_BACKOFF_MAX_SEC = 30.0
REMOTE_BACKOFF_JITTER_RANGE = (0.8, 1.2)
# リモートwatch subprocessのstdout用bufferのフロー制御閾値（バイト）。
# JSONの行長はこの閾値と独立に扱い、chunkからLF区切りのrecordを復元する。
REMOTE_STREAM_LIMIT_BYTES = 8 * 1024 * 1024


SshRunner = typing.Callable[[str, str, list[str]], typing.Awaitable[str]]
# 行ジェネレーターのプロトコル。テストではメモリー上のリストから供給する。
LineSource = typing.AsyncIterator[str]


_PYGMENTS_FORMATTER = HtmlFormatter(nowrap=True, style="monokai")
_PYGMENTS_CSS_CLASS = "codehilite"


@dataclasses.dataclass(frozen=True, slots=True)
class FileEntry:
    host: str
    path: str
    name: str
    mtime: str
    ctime: str
    mtime_epoch: float
    ctime_epoch: float

    source_id: str = ""


@dataclasses.dataclass(slots=True)
class BroadcastState:
    subscribers: set[asyncio.Queue[str]] = dataclasses.field(default_factory=set)
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    debounce_task: asyncio.Task[None] | None = None

    debounce_sec: float = _BROADCAST_DEBOUNCE_SEC
    loop: asyncio.AbstractEventLoop | None = None

    remote_files: dict[str, list[FileEntry]] = dataclasses.field(default_factory=dict)

    remote_tasks: list[asyncio.Task[None]] = dataclasses.field(default_factory=list)

    local_observer: watchdog.observers.api.BaseObserver | None = None

    host_status: dict[str, str] = dataclasses.field(default_factory=dict)

    remote_watchers: dict[str, RemoteWatcher] = dataclasses.field(default_factory=dict)

    host_info: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)

    root_info: dict[str, dict[str, dict[str, typing.Any]]] = dataclasses.field(default_factory=dict)

    root_status: dict[str, dict[str, dict[str, str]]] = dataclasses.field(default_factory=dict)

    stop_requested: threading.Event = dataclasses.field(default_factory=threading.Event)


def make_file_entry(host: str, item: typing.Mapping[str, typing.Any]) -> FileEntry:
    mtime_epoch = float(item["mtime_epoch"])
    ctime_epoch = float(item["ctime_epoch"])
    tzinfo = datetime.datetime.now().astimezone().tzinfo
    mtime = datetime.datetime.fromtimestamp(mtime_epoch, tz=tzinfo)
    ctime = datetime.datetime.fromtimestamp(ctime_epoch, tz=tzinfo)
    return FileEntry(
        host=host,
        path=str(item["path"]),
        name=str(item["name"]),
        mtime=mtime.strftime("%Y/%m/%d %H:%M"),
        ctime=ctime.strftime("%Y/%m/%d %H:%M"),
        mtime_epoch=mtime_epoch,
        ctime_epoch=ctime_epoch,
        source_id=str(item.get("source_id", item.get("source", ""))),
    )


async def subscribe(state: BroadcastState) -> asyncio.Queue[str]:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    async with state.lock:
        state.subscribers.add(queue)
    return queue


async def unsubscribe(state: BroadcastState, queue: asyncio.Queue[str]) -> None:
    async with state.lock:
        state.subscribers.discard(queue)


async def schedule_broadcast(state: BroadcastState) -> None:
    """debounce窓を使って`deliver_refresh`を遅延実行する。

    既にdebounceタスクが実行中の場合は何もしない。
    待機中の追加イベントを無視するため、時間窓内のイベントは1回の配信にまとまる。
    """
    async with state.lock:
        if state.debounce_task is not None and not state.debounce_task.done():
            return
        state.debounce_task = asyncio.create_task(_debounced_deliver(state))


async def _debounced_deliver(state: BroadcastState) -> None:
    await asyncio.sleep(state.debounce_sec)
    await deliver_refresh(state)


async def deliver_refresh(state: BroadcastState) -> None:
    await _broadcast(state, _SSE_REFRESH_PAYLOAD)


async def deliver_host_status(state: BroadcastState, host: str, status: str) -> None:
    payload = json.dumps({"type": "host-status", "host": host, "status": status}, ensure_ascii=False)
    await _broadcast(state, payload)


async def deliver_host_info(state: BroadcastState, host: str, info: dict[str, str] | None) -> None:
    payload = json.dumps({"type": "host_info_update", "host": host, "info": info}, ensure_ascii=False)
    await _broadcast(state, payload)


async def deliver_root_info(
    state: BroadcastState,
    host: str,
    info: dict[str, dict[str, typing.Any]] | None,
) -> None:
    payload = json.dumps({"type": "root_info_update", "host": host, "info": info}, ensure_ascii=False)
    await _broadcast(state, payload)


async def deliver_root_status(
    state: BroadcastState,
    host: str,
    status: dict[str, dict[str, str]],
) -> None:
    payload = json.dumps({"type": "root-status", "host": host, "status": status}, ensure_ascii=False)
    await _broadcast(state, payload)


async def _broadcast(state: BroadcastState, payload: str) -> None:
    async with state.lock:
        targets = list(state.subscribers)
    for queue in targets:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(payload)
