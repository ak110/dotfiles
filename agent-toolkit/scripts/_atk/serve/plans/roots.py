# pylint: disable=function-redefined,undefined-variable,wildcard-import,unused-wildcard-import,function-redefined,pointless-string-statement,undefined-variable,ungrouped-imports,unused-import,unused-wildcard-import,wildcard-import,wrong-import-order,wrong-import-position
# ruff: noqa: E402,F401,F821,I001
# pylint: disable=unused-import,used-before-assignment,wrong-import-order
"""`atk serve`の計画ファイル画面の処理本体。

ローカルと設定済みリモートホストの計画ファイルを集約し、全文検索、Markdownと
レビュー指摘管理表のHTML変換、付属計画間の移動リンク生成、更新通知の配信を担う。
ルート登録は`_atk_serve_app.py`の`_register_plan_routes`が行い、本モジュールは処理の実装だけを持つ。

記録の保存先とrootの規約は`agent-toolkit/skills/plan-mode`が定める計画ファイルの配置に従う。
リモートホスト側で実行するヘルパーは`atk_serve_plans_remote_helper.py`とする。
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess as _async_subprocess
import base64
import collections
import collections.abc
import contextlib
import dataclasses
import datetime
import hashlib
import html as html_lib
import importlib
import json
import logging
import os
import pathlib
import random
import re
import socket
import subprocess
import typing

import markdown_it
import markdown_it.renderer
import markdown_it.token
import markdown_it.utils
import platformdirs
import pygments
import watchdog.events
import watchdog.observers
import watchdog.observers.api
from _common import file_lock as _file_lock
from pygments.formatters.html import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from _atk.serve import remote as _atk_serve_remote


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _atk.serve.plans.rendering import (
        MarkdownCache,
        MarkdownCacheKey,
        _highlight_code,
        _render_fence,
        make_md_renderer,
        markdown_to_html,
    )
    from _atk.serve.plans.ctime_index import (
        _enter_index_lock,
        _entry_ctime,
        _exclusive_file_lock,
        _index_key,
        _index_lock_path,
        _load_index,
        _load_legacy_entries,
        _root_key,
        _write_index,
        cleanup_creation_time_temporaries,
        update_creation_time_index,
    )
    from _atk.serve.plans.local_scan import (
        PlansEventHandler,
        REMOTE_BOOTSTRAP,
        _STATIC_DIR,
        _ctime_epoch,
        is_listed_path,
        is_target_path,
        list_files,
        local_host_info,
        read_mermaid_bundle,
        read_pygments_css,
        resolve_under_root,
        root_info,
        root_status,
        root_warning,
        scan_files,
        search_files,
    )
    from _atk.serve.plans.remote import (
        RemoteHelperError,
        RemoteSearchCoordinator,
        RemoteSearchResult,
        RemoteSearchRunner,
        RemoteSearchSuperseded,
        RemoteWatcher,
        _PendingSearch,
        _build_remote_command_argv,
        _decode_read_payload,
        _decode_root_info,
        _decode_root_status,
        _drain_stderr,
        _is_listed_remote_path,
        _iter_stream_lines,
        _stderr_excerpt,
        _terminate_process,
        _wait_with_timeout,
        default_ssh_runner,
        fetch_remote_file,
        is_safe_remote_relpath,
        search_remote_files,
    )
    from _atk.serve.plans.views import (
        PlanFileError,
        PlansContext,
        _oldest_host_per_file,
        _plan_exists,
        _plan_paths,
        _read_with_mtime,
        _scan_local_root,
        _search_remote,
        all_entries,
        create_context,
        is_review_table_path,
        listed_plan_path,
        plan_links_html,
        render_file_html,
        resolve_source_id,
        resolve_text_and_mtime,
        review_table_html,
        search_entries,
        start_local_watchers,
        start_remote_watchers,
        stop_local_watchers,
        stop_remote_watchers,
    )

logger = logging.getLogger(__name__)

# 配布物独立性を保つため同等機能を独立実装する。

NEW_SOURCE_ID = "private-notes-plans"
LEGACY_SOURCE_ID = "claude-plans"
NEW_PORTABLE_ROOT = "$(atk config get private_notes)/plans"
LEGACY_PORTABLE_ROOT = "~/.claude/plans"
_UNRESOLVED_PRIVATE_NOTES_ROOT = pathlib.Path.home() / ".claude" / ".plans-viewer-private-notes-unresolved"

# 付属計画ファイルの接尾辞。計画一覧からは除外されるため、表示応答内のリンクが到達経路になる。
_DETAIL_SUFFIX = ".detail.md"
_BUGS_SUFFIX = ".bugs.md"
_TARGET_TSV_SUFFIXES = (".plan-review.tsv", ".exec-review.tsv")
_LISTED_EXCLUDED_SUFFIXES = (_DETAIL_SUFFIX, _BUGS_SUFFIX, *_TARGET_TSV_SUFFIXES)
_PLAN_SUFFIX_LABELS = (
    (_DETAIL_SUFFIX, "詳細"),
    (_BUGS_SUFFIX, "バグ"),
    (_TARGET_TSV_SUFFIXES[0], "計画レビュー指摘管理表"),
    (_TARGET_TSV_SUFFIXES[1], "実行レビュー指摘管理表"),
)
_REVIEW_TABLE_HEADERS = (
    "ラウンド",
    "系統",
    "箇所",
    "指摘内容",
    "指摘レベル",
    "対応要否",
    "対応内容",
    "対応不要理由",
)

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

# 作成日時の永続インデックス。ホスト・root・相対パスの3項をキーとする単一JSONへ集約する。
# 同一ホスト上でリモートヘルパー（`atk_serve_plans_remote_helper.py`）も同じファイルを共有するため、
# キーと値の形式を両実装で一致させる。
# ディレクトリ名は計画ファイル閲覧機能が`atk serve`へ統合される前から蓄積した索引をそのまま使うため維持する。
# 名前を変えると初回観測時刻が失われ、一覧の並び順が変わる。
_CREATION_TIME_INDEX_PATH = (
    pathlib.Path(platformdirs.user_cache_dir("claude-plans-viewer", appauthor=False)) / "creation-times" / "index.json"
)
# 旧形式（1エントリ1ファイル）のキャッシュ名。sha256 hexdigestと`.json`から成る。
_LEGACY_CACHE_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$")
# 旧実装が生成した一時ファイル名。`.<sha256 hexdigest>.json.<pid>.<スレッドID>.tmp`。
_LEGACY_TEMPORARY_NAME_RE = re.compile(r"^\.[0-9a-f]{64}\.json\.\d+\.\d+\.tmp$")

# SSH接続時に共通付与するオプション。
# `BatchMode=yes`で鍵認証失敗時にパスワードプロンプトでハングしないようにする。
SSH_BASE_OPTIONS = ("-o", "BatchMode=yes")
# 単発SSH呼び出し（fallback用`read`）のタイムアウト秒。
SSH_TIMEOUT_SEC = 30.0
# 警告本文へ引き継ぐ標準エラー出力の最大文字数。原因の判別に足りる長さを残しつつ、記録を占有させない。
STDERR_EXCERPT_MAX_CHARS = 500
# RPCリクエスト1件あたりのタイムアウト秒。
RPC_REQUEST_TIMEOUT_SEC = 30.0
# SSHフォールバック経路の検索を同時に実行する上限（全ホスト合計）。
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
# helperが1行JSONとして全エントリーを出力するsnapshot行がasyncio既定の64KiBを超えるため引き上げる。
REMOTE_STREAM_LIMIT_BYTES = 8 * 1024 * 1024
# 各停止段階で`proc.wait()`に与える既定タイムアウト（秒）。
TERMINATE_GRACE_TIMEOUT_SEC = 2.0

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
class RootSpec:
    """一つの計画保存rootと、画面へ返す可搬表記をまとめた定義。"""

    source_id: str
    path: pathlib.Path
    portable_path: str
    # root解決前に判明した障害（例: private_notes解決失敗）を保持する。
    warning: str | None = None
    # Noneはsource_idによる従来判定を使う。重複排除後は旧rootの資格を論理和で保持する。
    migrate_legacy_ctime: bool | None = None


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


def normalize_root_specs(specs: typing.Iterable[RootSpec]) -> tuple[RootSpec, ...]:
    """rootを正規化し、同一canonical pathまたは同一実体の重複だけを除く。"""
    normalized: list[RootSpec] = []
    for spec in specs:
        path = spec.path.expanduser().resolve()
        migrate_legacy = (
            spec.source_id in ("", LEGACY_SOURCE_ID) if spec.migrate_legacy_ctime is None else spec.migrate_legacy_ctime
        )
        candidate = dataclasses.replace(spec, path=path, migrate_legacy_ctime=migrate_legacy)
        duplicate_index: int | None = None
        for index, existing in enumerate(normalized):
            if path == existing.path:
                duplicate_index = index
                break
            try:
                if path.exists() and existing.path.exists() and path.samefile(existing.path):
                    duplicate_index = index
                    break
            except OSError:
                # 実体照合に失敗しても、当該rootの障害により他rootの処理を停止しない。
                continue
        if duplicate_index is None:
            normalized.append(candidate)
        elif candidate.migrate_legacy_ctime and not normalized[duplicate_index].migrate_legacy_ctime:
            normalized[duplicate_index] = dataclasses.replace(normalized[duplicate_index], migrate_legacy_ctime=True)
    return tuple(normalized)


def _canonical(path: pathlib.Path) -> pathlib.Path:
    """rootの比較・ファイル参照に使う正規化済みパスを返す。"""
    return path.expanduser().resolve()


def explicit_root_spec(root: str | pathlib.Path) -> RootSpec:
    """設定で明示されたrootを単一root定義へ変換する。"""
    path = _canonical(pathlib.Path(root))
    legacy = _canonical(pathlib.Path.home() / ".claude" / "plans")
    portable = LEGACY_PORTABLE_ROOT if path == legacy else str(path).replace("\\", "/")
    return RootSpec(source_id="", path=path, portable_path=portable)


def _private_notes_result() -> tuple[pathlib.Path | None, str | None]:
    """private-notesリポジトリのrootと、解決できない場合の警告を返す。

    `atk`の設定解決処理を同一プロセス内で呼ぶ。外部コマンドの起動を経ないため、
    常駐サービスのPATHに依存しない。
    """
    try:
        private_notes_path = vars(importlib.import_module("_atk.wi.common"))["_private_notes_path"]
        value = private_notes_path(pathlib.Path.home())
    except Exception as error:  # pylint: disable=broad-exception-caught
        warning = f"private_notesの取得に失敗しました: {error}"
        logger.warning("%s。旧rootを継続します", warning)
        return None, warning
    return _canonical(pathlib.Path(value)), None


def default_root_specs() -> tuple[RootSpec, ...]:
    """設定で明示されない場合に使う新旧rootを解決し、重複rootを除いた定義を返す。"""
    specs: list[RootSpec] = []
    private_notes, warning = _private_notes_result()
    if private_notes is not None:
        specs.append(
            RootSpec(
                source_id=NEW_SOURCE_ID,
                path=private_notes / "plans",
                portable_path=NEW_PORTABLE_ROOT,
                migrate_legacy_ctime=False,
            )
        )
    else:
        specs.append(
            RootSpec(
                source_id=NEW_SOURCE_ID,
                path=_UNRESOLVED_PRIVATE_NOTES_ROOT,
                portable_path=NEW_PORTABLE_ROOT,
                warning=warning or "private_notesを解決できません",
                migrate_legacy_ctime=False,
            )
        )
    specs.append(
        RootSpec(
            source_id=LEGACY_SOURCE_ID,
            path=pathlib.Path.home() / ".claude" / "plans",
            portable_path=LEGACY_PORTABLE_ROOT,
            migrate_legacy_ctime=True,
        )
    )
    return normalize_root_specs(specs)


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
