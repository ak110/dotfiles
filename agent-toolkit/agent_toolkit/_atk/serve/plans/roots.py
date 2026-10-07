"""計画ファイル画面の設定値、一覧の項目の型、更新通知の配信。

計画rootの定義と解決はSSH先のリモートヘルパーと共有する`_plan.viewer_files`が持つ。
"""

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

# debounce窓。watchdogは1回の書き込みで複数イベントを発火するため、時間窓で畳み込む。
_BROADCAST_DEBOUNCE_SEC = 0.3
_SSE_REFRESH_PAYLOAD = json.dumps({"type": "refresh"}, ensure_ascii=False)

# 読み取り由来の`FileOpenedEvent`・`FileClosedNoWriteEvent`を除外した監視対象イベント型。
# これらを通過させると本文取得がwatchdog経由でSSEを誘発するfeedback loopになる。
_WATCHED_EVENT_TYPES: tuple[type[watchdog.events.FileSystemEvent], ...] = (
    watchdog.events.FileCreatedEvent,
    watchdog.events.FileModifiedEvent,
    watchdog.events.FileDeletedEvent,
    watchdog.events.FileMovedEvent,
    watchdog.events.FileClosedEvent,
)

# Markdownレンダリング結果LRUキャッシュの上限。
# エントリ数とバイト数の二重上限のうち、先に到達した側で古い順に削除する。
MARKDOWN_CACHE_MAX_ENTRIES = 128
MARKDOWN_CACHE_MAX_BYTES = 16 * 1024 * 1024


# SSH接続時に共通付与するオプション。
# `BatchMode=yes`で鍵認証失敗時にパスワードプロンプトでハングしないようにする。
SSH_BASE_OPTIONS = ("-o", "BatchMode=yes")
# 単発SSH呼び出し（fallback用`read`）のタイムアウト秒。
SSH_TIMEOUT_SEC = 30.0
# 警告本文へ引き継ぐ標準エラー出力の最大文字数。値と選定理由はセッション画面と共通とする。
STDERR_EXCERPT_MAX_CHARS = _atk_serve_remote.STDERR_EXCERPT_MAX_CHARS
# RPCリクエスト1件あたりのタイムアウト秒。
RPC_REQUEST_TIMEOUT_SEC = 30.0
# SSHによる代替の検索を同時に実行する上限（全ホスト合計）。
DEFAULT_REMOTE_SEARCH_LIMIT = 4
# `serve`用のSSH追加オプション。ネットワーク途絶を最大30秒程度で検知する。
SSH_WATCH_OPTIONS = (
    "-o",
    "ConnectTimeout=5",
    "-o",
    "ServerAliveInterval=10",
    "-o",
    "ServerAliveCountMax=3",
)
# `RemoteWatcher`の再接続バックオフ。
REMOTE_BACKOFF_INITIAL_SEC = 1.0
REMOTE_BACKOFF_MAX_SEC = 30.0
REMOTE_BACKOFF_JITTER_RANGE = (0.8, 1.2)
# リモートwatch subprocessのstdout用StreamReader上限（バイト）。
# helperが1行JSONとして全エントリーを出力するsnapshot行は、asyncioが標準で使う64KiBを超えるため、上限を引き上げる。
REMOTE_STREAM_LIMIT_BYTES = 8 * 1024 * 1024

# SSHランナーの抽象シグネチャ。テストではfake実装を注入し、本番は`default_ssh_runner`を使う。
SshRunner = typing.Callable[[str, str, list[str]], typing.Awaitable[str]]
# 行ジェネレーターのプロトコル。テストではメモリー上のリストから供給する。
LineSource = typing.AsyncIterator[str]

# Pygmentsはmarkdown-itの`highlight`コールバックから呼ぶ。
_PYGMENTS_FORMATTER = HtmlFormatter(nowrap=True, style="monokai")
_PYGMENTS_CSS_CLASS = "codehilite"


# --------------------------------------------------------------------------------------
# root定義と共有状態
# --------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class FileEntry:
    """計画ファイル一覧が返すエントリ。"""

    host: str
    path: str
    name: str
    mtime: str
    ctime: str
    mtime_epoch: float
    ctime_epoch: float
    # 明示rootを単一で指定した場合は空文字列とし、host+pathの識別子を維持する。
    source_id: str = ""


@dataclasses.dataclass(slots=True)
class BroadcastState:
    """SSE購読者集合・debounce状態・リモートホストキャッシュ・接続状態を保持する。"""

    subscribers: set[asyncio.Queue[str]] = dataclasses.field(default_factory=set)
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    debounce_task: asyncio.Task[None] | None = None
    # debounce窓（秒）。テストでは短縮値を注入し実時間待ちを避ける。
    debounce_sec: float = _BROADCAST_DEBOUNCE_SEC
    loop: asyncio.AbstractEventLoop | None = None
    # ホスト名 -> 最後に観測したFileEntry一覧。リモートwatchで更新される。
    remote_files: dict[str, list[FileEntry]] = dataclasses.field(default_factory=dict)
    # 起動中のリモートwatchタスク群。after_servingで一括キャンセルする。
    remote_tasks: list[asyncio.Task[None]] = dataclasses.field(default_factory=list)
    # ローカルrootを監視中のobserver。監視対象が1件も無い場合はNoneのままとする。
    local_observer: watchdog.observers.api.BaseObserver | None = None
    # ホスト名 -> "connected"|"connecting"|"disconnected"。
    host_status: dict[str, str] = dataclasses.field(default_factory=dict)
    # ホスト名 -> 対応する`RemoteWatcher`。本文取得がwatch常駐SSH接続経由のRPCで読む際に参照する。
    remote_watchers: dict[str, RemoteWatcher] = dataclasses.field(default_factory=dict)
    # ホスト名 -> {"root": ..., "home": ..., "os_type": ..., "os_name": ...}。
    # 接続喪失時はキー自体を削除する（`None`値保持ではない）。
    host_info: dict[str, dict[str, str]] = dataclasses.field(default_factory=dict)
    # ホスト名 -> 保存元ID -> root情報。
    root_info: dict[str, dict[str, dict[str, typing.Any]]] = dataclasses.field(default_factory=dict)
    # ホスト名 -> 保存元ID -> 状態。状態値は"ok"またはroot単位の警告本文。
    root_status: dict[str, dict[str, dict[str, str]]] = dataclasses.field(default_factory=dict)
    # サーバーの停止要求。スレッドで動くローカルの一覧・検索の走査が反復の途中で参照して打ち切る。
    stop_requested: threading.Event = dataclasses.field(default_factory=threading.Event)


def make_file_entry(host: str, item: typing.Mapping[str, typing.Any]) -> FileEntry:
    """リモートヘルパー由来のdictを`FileEntry`に変換する。snapshot/upsertの両方から使う。"""
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
    """SSE購読キューを生成して登録し返す。"""
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    async with state.lock:
        state.subscribers.add(queue)
    return queue


async def unsubscribe(state: BroadcastState, queue: asyncio.Queue[str]) -> None:
    """購読キューを解除する。存在しない場合はエラーにしない。"""
    async with state.lock:
        state.subscribers.discard(queue)


async def schedule_broadcast(state: BroadcastState) -> None:
    """debounce窓を使って`deliver_refresh`を遅延実行する。

    既にdebounceタスクが実行中の場合は何もしない。
    タイマー中に追加イベントを無視することで時間窓で畳み込む動作となる。
    """
    async with state.lock:
        if state.debounce_task is not None and not state.debounce_task.done():
            return
        state.debounce_task = asyncio.create_task(_debounced_deliver(state))


async def _debounced_deliver(state: BroadcastState) -> None:
    """debounce窓満了後に全購読者へ`refresh`を配信する。"""
    await asyncio.sleep(state.debounce_sec)
    await deliver_refresh(state)


async def deliver_refresh(state: BroadcastState) -> None:
    """全購読者へ`{"type":"refresh"}`を配信する。

    キューが既に満杯の場合は新規通知を破棄する（既に未配信の通知があるため、
    クライアントは次に取り出した時点で最新化される）。
    """
    await _broadcast(state, _SSE_REFRESH_PAYLOAD)


async def deliver_host_status(state: BroadcastState, host: str, status: str) -> None:
    """全購読者へホストの接続状態を配信する。"""
    payload = json.dumps({"type": "host-status", "host": host, "status": status}, ensure_ascii=False)
    await _broadcast(state, payload)


async def deliver_host_info(state: BroadcastState, host: str, info: dict[str, str] | None) -> None:
    """全購読者へホストのroot情報更新を配信する。`info=None`は接続喪失を意味する。"""
    payload = json.dumps({"type": "host_info_update", "host": host, "info": info}, ensure_ascii=False)
    await _broadcast(state, payload)


async def deliver_root_info(
    state: BroadcastState,
    host: str,
    info: dict[str, dict[str, typing.Any]] | None,
) -> None:
    """root単位の保存先情報更新をSSEで配信する。"""
    payload = json.dumps({"type": "root_info_update", "host": host, "info": info}, ensure_ascii=False)
    await _broadcast(state, payload)


async def deliver_root_status(
    state: BroadcastState,
    host: str,
    status: dict[str, dict[str, str]],
) -> None:
    """root単位の利用可能状態・警告をSSEで配信する。"""
    payload = json.dumps({"type": "root-status", "host": host, "status": status}, ensure_ascii=False)
    await _broadcast(state, payload)


async def _broadcast(state: BroadcastState, payload: str) -> None:
    async with state.lock:
        targets = list(state.subscribers)
    for queue in targets:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(payload)
