"""共有状態とSSE配信ロジック。

Quartアプリの`app.config`へ`BroadcastState`を格納してモジュールレベルの可変状態を避ける。
debounce窓は`watchdog`が1回の書き込みで複数イベントを発火する性質に対する畳み込み。

クラス・関数名は同一パッケージ内の兄弟モジュールから参照される前提のため、
underscore接頭辞を付けない（package-internalとして扱う）。
パッケージ外への公開可否は`__init__.py`の再export一覧で制御する。
"""

import asyncio
import contextlib
import dataclasses
import datetime
import json
import typing

# debounce窓。watchdogは1回の書き込みで複数イベントを発火するため、時間窓で畳み込む。
_BROADCAST_DEBOUNCE_SEC = 0.3

# SSE配信メッセージ型: refreshはサイドペイン全体の再同期、host-statusはバッジ更新を意味する。
# 旧クライアント互換のためサーバー側はJSON文字列を1行で配信する（クライアントは`type`不在を
# refreshとみなすフォールバックを持つ）。
_SSE_REFRESH_PAYLOAD = json.dumps({"type": "refresh"}, ensure_ascii=False)


@dataclasses.dataclass(frozen=True, slots=True)
class FileEntry:
    """/api/filesで返すエントリ。"""

    host: str
    path: str
    name: str
    mtime: str
    mtime_epoch: float


@dataclasses.dataclass(slots=True)
class BroadcastState:
    """SSE購読者集合・debounce状態・リモートホストキャッシュ・接続状態を束ねる。

    Quartアプリの`app.config`に格納して保持することでモジュールレベルの可変状態を避ける。
    """

    subscribers: set[asyncio.Queue[str]] = dataclasses.field(default_factory=set)
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    debounce_task: asyncio.Task[None] | None = None
    loop: asyncio.AbstractEventLoop | None = None
    # ホスト名 -> 最後に観測したFileEntry一覧。リモートwatchで更新される。
    remote_files: dict[str, list[FileEntry]] = dataclasses.field(default_factory=dict)
    # 起動中のリモートwatchタスク群。after_servingで一括キャンセルする。
    remote_tasks: list[asyncio.Task[None]] = dataclasses.field(default_factory=list)
    # ホスト名 -> "connected"|"connecting"|"disconnected"。
    # フロントエンドのサイドペインに切断バッジを表示するための状態。
    host_status: dict[str, str] = dataclasses.field(default_factory=dict)


def make_file_entry(host: str, item: typing.Mapping[str, typing.Any]) -> FileEntry:
    """リモートヘルパー由来のdictを`FileEntry`に変換する。

    snapshot/upsertの両方から共通に使う。
    """
    mtime_epoch = float(item["mtime_epoch"])
    tzinfo = datetime.datetime.now().astimezone().tzinfo
    mtime = datetime.datetime.fromtimestamp(mtime_epoch, tz=tzinfo)
    return FileEntry(
        host=host,
        path=str(item["path"]),
        name=str(item["name"]),
        mtime=mtime.strftime("%Y/%m/%d %H:%M"),
        mtime_epoch=mtime_epoch,
    )


async def subscribe(state: BroadcastState) -> asyncio.Queue[str]:
    """SSE購読キューを生成して登録し返す。"""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    async with state.lock:
        state.subscribers.add(q)
    return q


async def unsubscribe(state: BroadcastState, q: asyncio.Queue[str]) -> None:
    """購読キューを解除する。存在しない場合もエラーにしない。"""
    async with state.lock:
        state.subscribers.discard(q)


async def schedule_broadcast(state: BroadcastState) -> None:
    """debounce窓を使って`deliver_refresh`を遅延実行する。

    既にdebounceタスクが実行中の場合は何もしない。
    タイマー中に追加イベントを無視することで時間窓で畳み込む。
    """
    async with state.lock:
        if state.debounce_task is not None and not state.debounce_task.done():
            return
        state.debounce_task = asyncio.create_task(_debounced_deliver(state))


async def _debounced_deliver(state: BroadcastState) -> None:
    """debounce窓満了後に全購読者へ`refresh`を配信する。"""
    await asyncio.sleep(_BROADCAST_DEBOUNCE_SEC)
    await deliver_refresh(state)


async def deliver_refresh(state: BroadcastState) -> None:
    """全購読者へ`{"type":"refresh"}`を配信する。

    キューがすでに満杯の場合は新規通知を破棄する（既に未配信の通知がある状態のため、
    クライアントは次に取り出した時点で最新化される）。
    """
    await _broadcast(state, _SSE_REFRESH_PAYLOAD)


async def deliver_host_status(state: BroadcastState, host: str, status: str) -> None:
    """全購読者へ`{"type":"host-status","host":...,"status":...}`を配信する。

    SSE経路で取りこぼした場合は接続時に`/api/host-status`から再同期できるため、
    `Queue`満杯時の破棄も許容する。
    """
    payload = json.dumps({"type": "host-status", "host": host, "status": status}, ensure_ascii=False)
    await _broadcast(state, payload)


async def _broadcast(state: BroadcastState, payload: str) -> None:
    async with state.lock:
        targets = list(state.subscribers)
    for q in targets:
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(payload)
