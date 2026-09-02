"""`atk serve`の計画ファイル画面の処理本体。

ローカルと設定済みリモートホストの計画ファイルを集約し、全文検索、Markdownと
レビュー指摘管理表のHTML変換、付属計画間の移動リンク生成、更新通知の配信を担う。
ルート登録は`_atk_serve_app.py`の`_register_plan_routes`が行い、本モジュールは処理の実装だけを持つ。

記録の保存先とrootの規約は`agent-toolkit/skills/plan-mode`が定める計画ファイルの配置に従う。
リモートホスト側で実行するヘルパーは`atk_serve_plans_remote_helper.py`とする。
"""

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

import _file_lock
import markdown_it
import markdown_it.renderer
import markdown_it.token
import markdown_it.utils
import platformdirs
import pygments
import watchdog.events
from pygments.formatters.html import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

logger = logging.getLogger(__name__)

# pylint: disable=duplicate-code  # 配布物独立性を保つため同等機能を独立実装する。

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
_REVIEW_TABLE_HEADERS = ("ラウンド", "系統", "箇所", "指摘内容", "対応要否", "対応内容", "対応不要理由")

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

_STATIC_DIR = pathlib.Path(__file__).with_name("_atk_serve_static")

# リモート側で実行する短いPython bootstrap。
# `$`・`%`・`<`・`>`・`|`・`&`・`^`はPOSIXシェル/cmd.exe双方で意味を持つためコード本体に含めない。
REMOTE_BOOTSTRAP = (
    "import os, pathlib; "
    "p = pathlib.Path(os.path.expanduser('~')) / "
    "'dotfiles/agent-toolkit/scripts/atk_serve_plans_remote_helper.py'; "
    "exec(compile(p.read_text(encoding='utf-8'), str(p), 'exec'))"
)


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
    # ホスト名 -> "connected"|"connecting"|"disconnected"。
    host_status: dict[str, str] = dataclasses.field(default_factory=dict)
    # ホスト名 -> 対応する`RemoteWatcher`。本文取得がwatch常駐SSH接続経由のRPCで読む際に参照する。
    remote_watchers: dict[str, "RemoteWatcher"] = dataclasses.field(default_factory=dict)
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
        private_notes_path = vars(importlib.import_module("_atk_mq_common"))["_private_notes_path"]
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


# --------------------------------------------------------------------------------------
# Markdownレンダリングとキャッシュ
# --------------------------------------------------------------------------------------


def _highlight_code(code: str, name: str, _attrs: str) -> str:
    """markdown-itのフェンスコードブロックをPygmentsでハイライトする。

    言語指定なし・未知言語フェンスは空文字を返し、markdown-it既定の素通し描画にフォールバックする。
    """
    if not name:
        return ""
    try:
        lexer = get_lexer_by_name(name, stripall=False)
    except ClassNotFound:
        return ""
    escaped_lang = html_lib.escape(name, quote=True)
    body = pygments.highlight(code, lexer, _PYGMENTS_FORMATTER).rstrip("\n")
    return f'<pre><code class="{_PYGMENTS_CSS_CLASS} language-{escaped_lang}">{body}\n</code></pre>\n'


def _render_fence(
    renderer: markdown_it.renderer.RendererHTML,
    tokens: typing.Sequence[markdown_it.token.Token],
    idx: int,
    options: markdown_it.utils.OptionsDict,
    env: collections.abc.MutableMapping[str, typing.Any],
) -> str:
    """MermaidとSVGのフェンスを専用HTML構造へ変換する。"""
    token = tokens[idx]
    info = token.info.strip() if token.info else ""
    name = info.split(maxsplit=1)[0].lower() if info else ""
    if name not in {"mermaid", "svg"}:
        return renderer.fence(tokens, idx, options, env)

    source = html_lib.escape(token.content)
    if name == "mermaid":
        return (
            '<figure class="diagram diagram-mermaid">\n'
            f'  <div class="diagram-output mermaid-output">{source}</div>\n'
            '  <details class="diagram-source"><summary>Mermaid原文</summary>'
            f"<pre>{source}</pre></details>\n"
            "</figure>\n"
        )
    return (
        '<figure class="diagram diagram-svg">\n'
        '  <img class="diagram-output svg-output" alt="SVG図">\n'
        '  <details class="diagram-source"><summary>SVG原文</summary>'
        f"<pre>{source}</pre></details>\n"
        "</figure>\n"
    )


def make_md_renderer() -> markdown_it.MarkdownIt:
    """Raw HTMLを無効化しPygmentsハイライトを注入したGFM相当のMarkdownレンダラを返す。

    GFM相当プリセットの表・取り消し線は維持し、誤リンクを防ぐため裸URLの自動リンクだけを無効化する。
    `html`も明示的に`False`へ上書きしてXSS経路を塞ぐ。
    `highlight`コールバックの戻り値はそのままHTMLとして埋め込まれるため、
    Pygmentsのエスケープ済み出力のみを返す。
    """
    renderer = markdown_it.MarkdownIt(
        "gfm-like",
        {"html": False, "highlight": _highlight_code, "linkify": False},
    )
    renderer.add_render_rule("fence", _render_fence)
    return renderer


def markdown_to_html(text: str, renderer: markdown_it.MarkdownIt | None = None) -> str:
    """Markdown文字列をHTMLへ変換する。"""
    md = renderer if renderer is not None else make_md_renderer()
    return md.render(text)


# 単一root互換時のキーは(host, path, mtime_epoch)、複数root時は(host, source_id, path, mtime_epoch)。
# `mtime_epoch`がキーに含まれるため、ファイル更新時は自動的に新しいエントリとなり明示的な無効化は不要。
MarkdownCacheKey = tuple[str, str, float] | tuple[str, str, str, float]


class MarkdownCache:
    """Markdownレンダリング結果のLRUキャッシュ。

    リモート分は`fetch_remote_file`が本文と同時取得した`mtime_epoch`をそのまま使うことで、
    watch通知の遅延に左右されず整合する。
    `mtime_epoch`が`None`の場合、呼び出し側はキャッシュをバイパスする（本クラスは`None`を扱わない）。
    """

    def __init__(
        self,
        max_entries: int = MARKDOWN_CACHE_MAX_ENTRIES,
        max_bytes: int = MARKDOWN_CACHE_MAX_BYTES,
    ) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        # OrderedDictで挿入順を保ち、`move_to_end`でLRU順に保つ。
        self._entries: collections.OrderedDict[MarkdownCacheKey, str] = collections.OrderedDict()
        self._total_bytes = 0

    def get(self, key: MarkdownCacheKey) -> str | None:
        """キャッシュ済みHTMLを返す。未保持ならNone。"""
        html = self._entries.get(key)
        if html is None:
            return None
        # アクセスのたびに末尾へ移して最近使用扱いにする。
        self._entries.move_to_end(key)
        return html

    def put(self, key: MarkdownCacheKey, html: str) -> None:
        """HTMLを保持し、上限超過分を古い順に破棄する。"""
        existing = self._entries.pop(key, None)
        if existing is not None:
            self._total_bytes -= len(existing.encode("utf-8"))
        size = len(html.encode("utf-8"))
        # 単一エントリが上限を超える場合は保持せずに諦める（次回はミスのまま再レンダリング）。
        if size > self._max_bytes:
            return
        self._entries[key] = html
        self._total_bytes += size
        self._evict_excess()

    def _evict_excess(self) -> None:
        while self._entries and (len(self._entries) > self._max_entries or self._total_bytes > self._max_bytes):
            _, evicted = self._entries.popitem(last=False)
            self._total_bytes -= len(evicted.encode("utf-8"))

    def __len__(self) -> int:
        return len(self._entries)

    def total_bytes(self) -> int:
        """テスト・観測用に現在の総バイト数を返す。"""
        return self._total_bytes


# --------------------------------------------------------------------------------------
# 作成日時インデックス
# --------------------------------------------------------------------------------------


@contextlib.contextmanager
def _exclusive_file_lock(path: pathlib.Path) -> typing.Iterator[None]:
    """`path`をロックファイルとしてプロセス間の排他ロックを保持する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        _file_lock.acquire_lock(handle)
        try:
            yield
        finally:
            _file_lock.release_lock(handle)


def _index_lock_path() -> pathlib.Path:
    """作成日時インデックスの排他ロックファイルのパス。"""
    return _CREATION_TIME_INDEX_PATH.with_name(_CREATION_TIME_INDEX_PATH.name + ".lock")


def _enter_index_lock(stack: contextlib.ExitStack) -> bool:
    """作成日時インデックスの排他ロックを`stack`へ登録する。取得できない場合は`False`を返す。

    キャッシュディレクトリを作成・書き込みできない環境ではロックファイルを開けず`OSError`となる。
    作成日時キャッシュの失敗で一覧機能を止めないため、当該例外は呼び出し元へ伝播させない。
    """
    try:
        stack.enter_context(_exclusive_file_lock(_index_lock_path()))
    except OSError:
        return False
    return True


def _index_key(host: str, root_key: str, rel: str) -> str:
    r"""インデックスのキー。`(host, root, rel)`を`\0`で連結した文字列のsha256 hexdigest。"""
    return hashlib.sha256(f"{host}\0{root_key}\0{rel}".encode()).hexdigest()


def _root_key(root: pathlib.Path) -> str:
    """インデックスのキーと値へ用いる`root`の正規化表記。"""
    return str(root.resolve()).replace("\\", "/")


def _entry_ctime(entry: typing.Any) -> float | None:
    """インデックスまたは旧形式のエントリから作成日時を取り出す。取得できない場合はNone。"""
    value = entry.get("ctime_epoch")
    return float(value) if isinstance(value, (int, float)) else None


def _load_index() -> dict[str, typing.Any]:
    """インデックスを読み込む。不在・読み取り失敗・形式不正はいずれも空として扱う。"""
    try:
        payload = json.loads(_CREATION_TIME_INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if isinstance(value, dict)}


def _load_legacy_entries() -> dict[tuple[str, str], tuple[float, pathlib.Path]]:
    """旧形式のキャッシュを`(host, 相対パス)`から作成日時と実ファイルへの対応として読み込む。

    旧形式は`root`を保持しないため、現在の走査対象と一致するものだけを移行対象にできる。
    """
    entries: dict[tuple[str, str], tuple[float, pathlib.Path]] = {}
    try:
        candidates = [path for path in _CREATION_TIME_INDEX_PATH.parent.iterdir() if _LEGACY_CACHE_NAME_RE.match(path.name)]
    except OSError:
        return entries
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        host = payload.get("host")
        rel = payload.get("path")
        ctime = _entry_ctime(payload)
        if isinstance(host, str) and isinstance(rel, str) and ctime is not None:
            entries[(host, rel)] = (ctime, candidate)
    return entries


def _write_index(index: dict[str, typing.Any]) -> bool:
    """インデックスを原子的に保存する。失敗した場合は`False`を返す。

    一時ファイル名はリモートヘルパーの除去規則と一致する`index.json.<ランダム文字列>.tmp`とする。
    """
    content = json.dumps(index, ensure_ascii=False, indent=2) + "\n"
    temporary: pathlib.Path | None = None
    try:
        _CREATION_TIME_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = _CREATION_TIME_INDEX_PATH.with_name(f"{_CREATION_TIME_INDEX_PATH.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(_CREATION_TIME_INDEX_PATH)
    except OSError:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()
        return False
    return True


def update_creation_time_index(
    host: str,
    root: pathlib.Path,
    observed: dict[str, float],
    *,
    migrate_legacy: bool = True,
) -> dict[str, float]:
    """走査結果の観測時刻をインデックスへ反映し、確定した作成日時を相対パスごとに返す。

    初回観測時の値を作成日時として保持することで、編集で変動する値に依らず並び順を維持する。
    同一の`(host, root)`に属し今回の走査に現れなかったキーは回収し、
    別の`root`に属するキーは維持する（`root`ごとに走査対象が異なるため）。
    `migrate_legacy=True`の場合だけ、旧形式から`host`と相対パスが今回の走査と一致するものを取り込み、
    インデックスの書き込みに成功した場合に限り取り込んだファイルを削除する。
    ロックを取得できない場合はインデックスの更新を諦め、観測値をそのまま返す。
    """
    root_key = _root_key(root)
    resolved: dict[str, float] = {}
    with contextlib.ExitStack() as stack:
        if not _enter_index_lock(stack):
            return dict(observed)
        index = _load_index()
        legacy = _load_legacy_entries() if migrate_legacy else {}
        migrated: list[pathlib.Path] = []
        updated: dict[str, typing.Any] = {}
        for rel, observed_epoch in observed.items():
            key = _index_key(host, root_key, rel)
            cached = _entry_ctime(index.get(key, {}))
            if cached is None:
                legacy_entry = legacy.get((host, rel))
                if legacy_entry is not None:
                    cached, legacy_path = legacy_entry
                    migrated.append(legacy_path)
            creation = min(observed_epoch, cached) if cached is not None else observed_epoch
            resolved[rel] = creation
            updated[key] = {"host": host, "root": root_key, "path": rel, "ctime_epoch": creation}
        # インデックスを更新する経路は全て同じロックを保持するため、冒頭で読み込んだ内容へ直接反映する。
        for key, entry in list(index.items()):
            if key not in updated and entry.get("host") == host and entry.get("root") == root_key:
                del index[key]
        index.update(updated)
        if _write_index(index):
            for legacy_path in migrated:
                with contextlib.suppress(OSError):
                    legacy_path.unlink()
    return resolved


def cleanup_creation_time_temporaries() -> None:
    """作成日時インデックスの残存一時ファイルをロック下で除去する。

    対象は`index.json.<接尾辞>.tmp`と、
    旧実装が生成した`.<sha256 hexdigest>.json.<pid>.<スレッドID>.tmp`の2形式とする。
    書き込み途中のファイルを削除しないよう、除去はインデックスと同じロックの保持中に行う。
    ロックを取得できない場合は何もせずに返る。
    """
    directory = _CREATION_TIME_INDEX_PATH.parent
    if not directory.is_dir():
        return
    temporary_pattern = f"{_CREATION_TIME_INDEX_PATH.name}.*.tmp"
    with contextlib.ExitStack() as stack:
        if not _enter_index_lock(stack):
            return
        try:
            candidates = list(directory.iterdir())
        except OSError:
            return
        for candidate in candidates:
            if not candidate.match(temporary_pattern) and not _LEGACY_TEMPORARY_NAME_RE.match(candidate.name):
                continue
            with contextlib.suppress(OSError):
                candidate.unlink()


# --------------------------------------------------------------------------------------
# ローカル走査
# --------------------------------------------------------------------------------------


def is_target_path(path: pathlib.Path, root: pathlib.Path) -> bool:
    """`path`が対象接尾辞・`root`配下・非dotdirの全条件を満たすか判定する。

    読取・検索・変更監視の3経路が同一の対象集合を返すよう、当該判定を1箇所へ集約する。
    メイン`<stem>.md`と付属ファイル`<stem>.detail.md`・`<stem>.bugs.md`・レビュー指摘管理表を真とする
    （付属ファイルは一覧だけから除外し、読取・検索・監視の対象には含める）。
    リモート側`atk_serve_plans_remote_helper.py`の`_is_target_path`と同一基準を保つ
    （同ファイルはSSH越しに単独実行されるためモジュールを共有できず、意図的に重複させている）。
    `root`自身がドット配下（`~/.claude/plans`など）でも通るよう、判定は`root`からの相対パスに対して行う。
    シンボリックリンクを解決してから相対化するため、`root`外を指すリンクは対象外となる。
    """
    if path.suffix != ".md" and not path.name.endswith(_TARGET_TSV_SUFFIXES):
        return False
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return not any(part.startswith(".") for part in rel.parts)


def is_listed_path(path: pathlib.Path, root: pathlib.Path) -> bool:
    """`path`が計画一覧の対象（`is_target_path`が真、かつ付属計画ではない）かを判定する。"""
    return is_target_path(path, root) and not path.name.endswith(_LISTED_EXCLUDED_SUFFIXES)


class PlansEventHandler(watchdog.events.FileSystemEventHandler):
    """watchdogのイベントを受信してSSE購読者へ通知するハンドラ。

    watchdogコールバックはwatchdog側のスレッドで実行されるため、
    asyncioループへ`run_coroutine_threadsafe`でブリッジする。
    """

    def __init__(
        self,
        root: pathlib.Path,
        state: BroadcastState,
        source_id: str = "",
    ) -> None:
        super().__init__()
        self.root = root
        self.state = state
        self.source_id = source_id

    @typing.override
    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        """ファイルシステムイベントをフィルタリングして購読者へ通知する。"""
        if not isinstance(event, _WATCHED_EVENT_TYPES):
            return
        if event.is_directory:
            return
        # src_pathはwatchdog型定義上bytes|strだが実行時はstr。str変換でPath型エラーを回避する。
        src = pathlib.Path(str(event.src_path))
        # atomic-write保存では`FileMovedEvent(src_path="plan.md.tmp", dest_path="plan.md")`となるため、
        # src_pathとdest_pathの両方を確認する。
        if isinstance(event, watchdog.events.FileMovedEvent):
            dest = pathlib.Path(str(event.dest_path))
            if not (is_target_path(src, self.root) or is_target_path(dest, self.root)):
                return
        elif not is_target_path(src, self.root):
            return
        loop = self.state.loop
        if loop is None:
            # 起動直後にループ参照が未設定のイベントは取りこぼしてよい（直後のイベントで再通知される）。
            return
        asyncio.run_coroutine_threadsafe(schedule_broadcast(self.state), loop)


def _ctime_epoch(st: os.stat_result) -> float:
    """観測時点の作成日時候補をepoch秒で返す。

    `st_birthtime`（macOS・Windowsで実在し「作成時刻」を表す）を優先し、
    存在しないプラットフォームでは更新日時を用いる。
    初回観測時の値を保持する処理は`update_creation_time_index`が担う。
    編集で変動する`st_ctime`は用いない。
    """
    birthtime = getattr(st, "st_birthtime", None)
    return float(birthtime) if birthtime is not None else float(st.st_mtime)


def local_host_info(root: pathlib.Path) -> dict[str, str]:
    """ローカルホストの`host_info`エントリ（`root`・`home`・`os_type`・`os_name`）を組み立てる。

    `root`・`home`はクライアント側のパス結合と表記を統一するため常に`/`区切りへ正規化する。
    `home`はクライアント側のチルダ表記変換の基準パスとして使う。
    """
    home = str(pathlib.Path.home()).replace("\\", "/")
    return {
        "root": str(root).replace("\\", "/"),
        "home": home,
        "os_type": os.name,
        "os_name": os.name,
    }


def root_info(spec: RootSpec) -> dict[str, typing.Any]:
    """複数root APIへ返す保存元情報を組み立てる。"""
    info: dict[str, typing.Any] = {
        "source_id": spec.source_id,
        "portable_root": spec.portable_path,
    }
    if spec.warning is not None:
        info["warning"] = spec.warning
    return info


def root_warning(root: pathlib.Path) -> str | None:
    """rootを利用できない理由を返す。

    rootの不在は計画をまだ保存していない通常の状態であるため警告しない。
    非ディレクトリ以外の障害は走査時に判定する。
    """
    if root.exists() and not root.is_dir():
        return "rootがディレクトリではありません"
    return None


def root_status(warning: str | None) -> dict[str, str]:
    """rootの利用状態をAPI・SSE向けの小さな辞書へ変換する。"""
    if warning is None:
        return {"status": "ok", "message": ""}
    return {"status": "warning", "message": warning}


def scan_files(
    root: pathlib.Path,
    host: str,
    source_id: str = "",
    *,
    migrate_legacy_ctime: bool | None = None,
) -> tuple[list[FileEntry], str | None]:
    """`root`を走査し、一覧とroot単位の警告を返す。

    rootの非ディレクトリ・権限不足は呼び出し元が他rootの処理を継続できるよう、
    例外ではなく警告本文として返す。rootの不在は通常の状態として空の一覧だけを返す。
    rootは自動作成しない。
    """
    warning = root_warning(root)
    if warning is not None:
        return [], warning
    # 不在のrootに対する`rglob`は空を返して成功するため、走査へ進むと空の観測結果で
    # インデックスを更新し、同じ`(host, root)`に記録済みの作成日時を回収してしまう。
    if not root.is_dir():
        return [], None

    scanned: list[dict[str, typing.Any]] = []
    observed: dict[str, float] = {}
    warning = None
    try:
        for path in root.rglob("*"):
            try:
                if not path.is_file() or not is_listed_path(path, root):
                    continue
                st = path.stat()
            except OSError as error:
                warning = f"rootの走査に失敗しました: {error}"
                continue
            rel = path.relative_to(root).as_posix()
            observed[rel] = _ctime_epoch(st)
            scanned.append({"path": rel, "name": path.name, "mtime_epoch": st.st_mtime})
    except OSError as error:
        warning = f"rootの走査に失敗しました: {error}"

    # 走査後に一度だけインデックスを更新し、同じ`(host, root)`の不在エントリを回収する。
    resolved = update_creation_time_index(
        host,
        root,
        observed,
        migrate_legacy=(source_id in ("", LEGACY_SOURCE_ID) if migrate_legacy_ctime is None else migrate_legacy_ctime),
    )
    collected = [
        make_file_entry(host, {**item, "ctime_epoch": resolved[item["path"]], "source_id": source_id}) for item in scanned
    ]
    collected.sort(key=lambda entry: (entry.ctime_epoch, entry.path), reverse=True)
    return collected, warning


def list_files(root: pathlib.Path, host: str, source_id: str = "") -> list[FileEntry]:
    """`root`から一覧対象の計画ファイルを再帰的に探し、作成日時の降順で返す。"""
    entries, _ = scan_files(root, host, source_id)
    return entries


def search_files(root: pathlib.Path, query: str) -> set[str]:
    """本文へ検索語が部分一致する計画ファイルの相対パス集合を返す。"""
    needle = query.casefold()
    if not root.is_dir():
        return set()
    if not needle:
        try:
            return {
                path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and is_target_path(path, root)
            }
        except OSError:
            return set()
    matched: set[str] = set()
    try:
        for path in root.rglob("*"):
            if not path.is_file() or not is_target_path(path, root):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle in text.casefold():
                matched.add(path.relative_to(root).as_posix())
    except OSError:
        return matched
    return matched


def resolve_under_root(root: pathlib.Path, rel: str) -> pathlib.Path | None:
    """`rel`が`root`配下の対象ファイルを指す場合のみ絶対パスを返す。存在しない場合はNone。"""
    # シンボリックリンクを辿ってroot外へ出ないよう、resolve後のパスで範囲検査する。
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    if not target.is_file() or not is_target_path(target, root):
        return None
    return target


def read_markdown_css() -> str:
    """Markdown表示用のスタイルシートを配布物から読み込む。"""
    return (_STATIC_DIR / "markdown.css").read_text(encoding="utf-8")


def read_mermaid_bundle() -> str:
    """同梱したMermaidの単一ファイルbundleを読み込む。"""
    return (_STATIC_DIR / "vendor" / "mermaid.min.js").read_text(encoding="utf-8")


def read_pygments_css() -> str:
    """Pygmentsのスタイルシートを返す。

    pygmentsの基本ルール（`.codehilite { background: ...; color: ... }`）は除外し、
    トークン別カラールール（`.codehilite .k`等）のみを返す。
    背景と既定文字色はmarkdown.css側の`pre code`ルールへ委ね、
    `<pre>`の背景上に異色矩形が出現する事象を防ぐ。
    """
    raw = _PYGMENTS_FORMATTER.get_style_defs(f".{_PYGMENTS_CSS_CLASS}")
    base_selector = f".{_PYGMENTS_CSS_CLASS}"
    kept: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{base_selector} {{") or stripped.startswith(f"{base_selector}{{"):
            continue
        kept.append(line)
    return "\n".join(kept)


# --------------------------------------------------------------------------------------
# リモートホスト統合
# --------------------------------------------------------------------------------------


def _build_remote_command_argv(op: str, args: list[str]) -> list[str]:
    """SSH経由でリモートヘルパーを起動するargv要素列を返す。

    SSHは末尾の各要素を空白で連結してリモートシェルへ渡すため、
    シェルにより1単位として解釈すべき要素はあらかじめダブルクォートで囲んで返す。

    リモート起動コマンドはPOSIXシェル非依存とする。
    Windows OpenSSHの既定シェル`cmd.exe`では`bash -c`やheredoc展開が利用できないため、
    シェル組み込みコマンドへ依存しないこと。
    リモート側に`$HOME/dotfiles`が存在することを前提とし、ヘルパースクリプトは当該配下から読み込む。
    `~`はcmd.exeでは展開されないため、Pythonの`os.path.expanduser('~')`で展開する。
    クオートはPOSIXシェル/cmd.exe共通のダブルクォートのみを使い、
    `$`・`%`・`<`・`>`・`|`・`&`・`^`はコマンド本体に含めない。
    Windowsの既定ロケールはUTF-8とは限らないため、ヘルパー本体の読み込みはエンコーディングを明示する。
    """
    return [
        "uv",
        "run",
        "--no-project",
        "--with",
        '"watchdog>=6.0.0"',
        "--with",
        '"platformdirs>=4.0"',
        "python",
        "-c",
        f'"{REMOTE_BOOTSTRAP}"',
        op,
        *args,
    ]


class RemoteHelperError(Exception):
    """リモートヘルパーの実行が非0で終了したことを、失敗元の標準エラー出力とともに示す。

    本例外の文字列表現は利用者へ渡る警告本文と記録へそのまま引き継がれるため、失敗元の標準エラー出力を含める。
    SSHの接続が成立したうえでリモート側の実行が失敗する場合も本例外となるため、
    到達可否を判別していない語で原因を断定しない。
    """

    def __init__(self, returncode: int, stderr: bytes) -> None:
        super().__init__(f"リモートヘルパーの実行が終了コード{returncode}で失敗しました: {_stderr_excerpt(stderr)}")


def _stderr_excerpt(stderr: bytes) -> str:
    """失敗元の標準エラー出力を、警告本文へ埋め込む1行の文字列へ整える。

    復号できない列は置換し、末尾側を残して切り詰める（失敗の直接原因は出力の末尾に現れるため）。
    """
    text = " ".join(stderr.decode("utf-8", errors="replace").split())
    if not text:
        return "標準エラー出力はありません"
    if len(text) > STDERR_EXCERPT_MAX_CHARS:
        return f"...{text[-STDERR_EXCERPT_MAX_CHARS:]}"
    return text


async def default_ssh_runner(host: str, op: str, args: list[str]) -> str:
    """SSH経由でリモートヘルパーを単発実行し、stdoutをUTF-8文字列で返す。

    fallback経路（常駐watch経由RPCが利用できない場合）でのみ使う。
    `subprocess.run`はブロッキングのため`asyncio.to_thread`でラップする。
    非0終了は`RemoteHelperError`として送出し、失敗元の標準エラー出力を呼び出し元へ渡す。
    """
    cmd = ["ssh", *SSH_BASE_OPTIONS, host, *_build_remote_command_argv(op, args)]
    proc = await asyncio.to_thread(
        subprocess.run,
        cmd,
        capture_output=True,
        timeout=SSH_TIMEOUT_SEC,
        check=False,
    )
    # capture_output=Trueかつtext未指定のため`stdout`・`stderr`は実行時bytes固定。型注釈はAnyのため明示する。
    assert isinstance(proc.stdout, bytes)
    assert isinstance(proc.stderr, bytes)
    if proc.returncode != 0:
        raise RemoteHelperError(proc.returncode, proc.stderr)
    return proc.stdout.decode("utf-8")


def _decode_read_payload(payload: typing.Mapping[str, typing.Any]) -> tuple[str, float | None]:
    """`read`応答辞書（RPC・fallback共通）から`(本文, mtime_epoch)`を取り出す。

    `mtime_epoch`は応答に含まれない場合や数値でない場合に`None`を返す。
    その場合、呼び出し側はMarkdownキャッシュを安全側に倒してバイパスする。
    """
    data_b64 = str(payload["data"])
    text = base64.b64decode(data_b64).decode("utf-8", errors="replace")
    raw_mtime = payload.get("mtime_epoch")
    mtime = float(raw_mtime) if isinstance(raw_mtime, (int, float)) else None
    return text, mtime


async def fetch_remote_file(
    host: str,
    rel: str,
    ssh_runner: SshRunner,
    watcher: "RemoteWatcher | None" = None,
    *,
    source_id: str = "",
) -> tuple[str, float | None]:
    """リモートホストの指定ファイル本文と取得時点の`mtime_epoch`を返す。

    `watcher`が渡され、対応する常駐SSH接続が`connected`状態にあればRPC経由で読み取る。
    未接続・タイムアウト・例外などRPC不可状態では`ssh_runner`経由のfallbackへ切り替える。
    本文と`mtime_epoch`は同一読み取り処理から取り出すため、watch通知の遅延に左右されず整合する。
    """
    rel_b64 = base64.b64encode(rel.encode("utf-8")).decode("ascii")
    request_args: dict[str, str] = {"path": rel_b64}
    if source_id:
        request_args["source_id"] = source_id
    if watcher is not None and watcher.is_connected():
        try:
            response = await watcher.request("read", request_args)
        except Exception as error:  # noqa: BLE001
            # RPC失敗（タイムアウト・接続切断・応答エラー等）は警告のうえfallbackする。
            logger.warning("リモートRPC失敗 host=%s path=%s: %s（fallbackへ）", host, rel, error)
        else:
            if response.get("ok"):
                return _decode_read_payload(response)
            error_msg = response.get("error", "(no error message)")
            # `ok=False`は権限不足・パス不正など恒久的な失敗を含むため、fallbackで救済する。
            logger.warning("リモートRPCエラー host=%s path=%s: %s（fallbackへ）", host, rel, error_msg)
    args = [rel_b64]
    if source_id:
        source_b64 = base64.b64encode(source_id.encode("utf-8")).decode("ascii")
        args = [source_b64, rel_b64]
    raw = await ssh_runner(host, "read", args)
    return _decode_read_payload(json.loads(raw))


class RemoteSearchSuperseded(Exception):
    """後続の検索要求へ置き換えられ、実行されないまま打ち切られたことを示す。

    置き換えられた要求へ別の検索語の結果や空集合を返すと、
    呼び出し元は誤った一覧を検索結果として扱う。打ち切りを明示的な失敗として伝える。
    """


# コーディネーター経由で実行する検索本体。結果は一致した相対パス集合とする。
RemoteSearchResult = set[str] | set[tuple[str, str]]
RemoteSearchRunner = typing.Callable[[], typing.Awaitable[RemoteSearchResult]]


@dataclasses.dataclass
class _PendingSearch:
    """待機中の検索要求（実行本体と、結果を受け取るFuture）。"""

    run: RemoteSearchRunner
    future: asyncio.Future[RemoteSearchResult]


class RemoteSearchCoordinator:
    """SSHフォールバック経路の検索をホスト単位で直列化し、全ホスト合計でも有界化する。

    検索は利用者の入力ごとに発行されるため、素朴に実行するとSSHとリモートPythonの組が
    入力継続中に積み上がる。ホストごとに実行中1件・待機中1件へ制限し、
    3件目以降の到着時は待機中の要求を最新の1件へ置き換える。
    置き換えられた要求へは`RemoteSearchSuperseded`を返し、別の検索語の結果を共有しない。
    """

    def __init__(self, limit: int = DEFAULT_REMOTE_SEARCH_LIMIT) -> None:
        self._semaphore = asyncio.Semaphore(limit)
        # host -> 待機中の要求。実行中の要求はワーカーが保持するためここには現れない。
        self._pending: dict[str, _PendingSearch] = {}
        # host -> 稼働中のワーカータスク。キーの有無をホスト単位の実行中判定に使う。
        self._workers: dict[str, asyncio.Task[None]] = {}

    async def submit(self, host: str, run: RemoteSearchRunner) -> RemoteSearchResult:
        """検索本体をホストの実行枠へ載せ、自身の要求に対応する結果を返す。"""
        loop = asyncio.get_running_loop()
        request = _PendingSearch(run=run, future=loop.create_future())
        if host in self._workers:
            superseded = self._pending.get(host)
            self._pending[host] = request
            if superseded is not None and not superseded.future.done():
                superseded.future.set_exception(RemoteSearchSuperseded(f"リモート検索が後続要求へ置き換えられた: host={host}"))
        else:
            # 最初の要求は待機列を経由せず実行枠へ渡す。待機列へ置くと、
            # ワーカーの起動前に到着した後続要求が最初の要求を置き換える。
            self._workers[host] = asyncio.create_task(self._run_host(host, request))
        # 呼び出し元（HTTP要求）のキャンセルがワーカーへ波及しないよう遮蔽する。
        return await asyncio.shield(request.future)

    async def _run_host(self, host: str, request: _PendingSearch) -> None:
        """1ホスト分の要求を順に実行する。待機列が尽きた時点でホスト状態を削除して終了する。"""
        while True:
            async with self._semaphore:
                try:
                    result = await request.run()
                except asyncio.CancelledError:
                    if not request.future.done():
                        request.future.cancel()
                    raise
                except Exception as error:  # noqa: BLE001
                    # 例外は握り潰さず、要求元のFutureへそのまま転送する。
                    if not request.future.done():
                        request.future.set_exception(error)
                else:
                    if not request.future.done():
                        request.future.set_result(result)
            next_request = self._pending.pop(host, None)
            if next_request is None:
                # 削除と終了の間にawaitが無いため、この直後の`submit`は新しいワーカーを起動できる。
                self._workers.pop(host, None)
                return
            request = next_request


async def search_remote_files(
    host: str,
    query: str,
    ssh_runner: SshRunner,
    watcher: "RemoteWatcher | None" = None,
    coordinator: RemoteSearchCoordinator | None = None,
    *,
    source_id: str | None = None,
) -> RemoteSearchResult:
    """リモートホストで本文検索を実行し、一致した相対パス集合を返す。

    `coordinator`を渡した場合、SSHフォールバック経路だけが直列化と全体上限の対象になる。
    常駐SSH接続経由のRPCはプロセスを起動しないため対象外とする。
    """
    query_b64 = base64.b64encode(query.encode("utf-8")).decode("ascii")
    rpc_args: dict[str, str] = {"query": query_b64}
    if source_id:
        rpc_args["source_id"] = source_id
    if watcher is not None and watcher.is_connected():
        try:
            response = await watcher.request("search", rpc_args)
        except Exception as error:  # noqa: BLE001
            logger.warning("リモート検索RPC失敗 host=%s: %s（fallbackへ）", host, error)
        else:
            if response.get("ok"):
                matches = response.get("matches")
                if isinstance(matches, list):
                    return {
                        (str(item["source_id"]), str(item["path"]))
                        for item in matches
                        if isinstance(item, dict) and "source_id" in item and "path" in item
                    }
                if isinstance(response.get("paths"), list):
                    paths = {str(path) for path in response["paths"]}
                    if source_id:
                        return {(source_id, path) for path in paths}
                    return paths
            logger.warning("リモート検索RPCエラー host=%s: %s（fallbackへ）", host, response.get("error"))

    async def _run_ssh_search() -> RemoteSearchResult:
        args = [query_b64]
        if source_id:
            source_b64 = base64.b64encode(source_id.encode("utf-8")).decode("ascii")
            args = [source_b64, query_b64]
        raw = await ssh_runner(host, "search", args)
        payload = json.loads(raw)
        matches = payload.get("matches")
        if isinstance(matches, list):
            return {
                (str(item["source_id"]), str(item["path"]))
                for item in matches
                if isinstance(item, dict) and "source_id" in item and "path" in item
            }
        paths = payload.get("paths", [])
        if not isinstance(paths, list):
            return set()
        result = {str(path) for path in paths}
        if source_id:
            return {(source_id, path) for path in result}
        return result

    if coordinator is None:
        return await _run_ssh_search()
    return await coordinator.submit(host, _run_ssh_search)


def _decode_root_info(raw: typing.Any) -> dict[str, dict[str, typing.Any]]:
    """snapshotの新旧root情報をsource ID keyed形式へ正規化する。"""
    if isinstance(raw, dict):
        if "root" in raw or "portable_root" in raw or "source_id" in raw:
            return {str(raw.get("source_id", "")): dict(raw)}
        return {str(source_id): dict(info) for source_id, info in raw.items() if isinstance(info, dict)}
    if isinstance(raw, list):
        return {str(info.get("source_id", "")): dict(info) for info in raw if isinstance(info, dict) and "source_id" in info}
    return {}


def _decode_root_status(raw: typing.Any) -> dict[str, dict[str, str]]:
    """snapshotのroot状態をsource ID keyed形式へ正規化する。"""
    if not isinstance(raw, dict):
        return {}
    if "status" in raw:
        return {str(raw.get("source_id", "")): dict(raw)}
    return {str(source_id): dict(status) for source_id, status in raw.items() if isinstance(status, dict)}


def _is_listed_remote_path(path: str) -> bool:
    """リモートwatchイベントのパスが一覧対象かを判定する。"""
    return not pathlib.PurePosixPath(path).name.endswith(_LISTED_EXCLUDED_SUFFIXES)


class RemoteWatcher:
    """1ホスト分のwatch+RPC接続ライフサイクルを担うクラス。

    リモート監視はwatchdogによるpush方式を採用する。
    ポーリング方式は対象ファイル数が増えた場合や低リソースホストでのコストが懸念されるため、
    SSH越しに長時間watchプロセスを常駐させて差分イベントだけを配信する。

    `run()`の流れ:
      1. host_statusを"connecting"へ更新しSSE配信
      2. SSH経由でPython bootstrapを実行し、リモート側ヘルパーの`serve`を起動
      3. stdoutの行を読みつつ`_handle_event`でキャッシュ・SSE・RPC応答を処理
      4. snapshotを受信したら"connected"へ遷移し、以降は`request()`によるRPCも可能になる
      5. EOF・例外で"disconnected"へ遷移し、pending RPCを打ち切ってから指数バックオフで再接続
    """

    def __init__(self, host: str, state: BroadcastState) -> None:
        self.host = host
        self.state = state
        # 長時間維持された接続が途絶した後の再接続時にバックオフが最大値から始まらないよう、
        # snapshot受信（接続成功）時にリセットする。
        self._backoff = REMOTE_BACKOFF_INITIAL_SEC
        # RPC状態。接続未確立または接続切断中はNone。
        self._proc: _async_subprocess.Process | None = None
        # request id -> 応答待ちFuture。応答到着・タイムアウト・切断のいずれかで解決する。
        self._pending: dict[int, asyncio.Future[dict[str, typing.Any]]] = {}
        self._next_request_id = 1
        # stdinへの書き込みは複数タスクから発生し得るため`asyncio.Lock`で排他する。
        self._send_lock = asyncio.Lock()
        # snapshot受信後にTrueになり、接続切断時にFalseに戻る。
        self._connected = False
        self._stderr_task: asyncio.Task[None] | None = None

    def is_connected(self) -> bool:
        """RPCを送信可能な状態か（snapshot受信済みかつstdinが生存）を返す。"""
        if not self._connected:
            return False
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        return not proc.stdin.is_closing()

    async def request(
        self,
        op: str,
        args: dict[str, typing.Any],
        timeout: float = RPC_REQUEST_TIMEOUT_SEC,
    ) -> dict[str, typing.Any]:
        """常駐SSH接続経由でRPCリクエストを送信し、応答辞書を返す。

        接続未確立・切断中では`RuntimeError`を送出する。
        timeout時は対応するpendingエントリを除去して`TimeoutError`を送出する。
        """
        if not self.is_connected():
            raise RuntimeError(f"watch not connected: host={self.host}")
        proc = self._proc
        # 同時実行下では`is_connected`通過後に切断される可能性があるため、ここで再確認する。
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            raise RuntimeError(f"watch not connected: host={self.host}")
        loop = asyncio.get_running_loop()
        req_id = self._next_request_id
        self._next_request_id += 1
        fut: asyncio.Future[dict[str, typing.Any]] = loop.create_future()
        self._pending[req_id] = fut
        payload: dict[str, typing.Any] = {"id": req_id, "op": op, **args}
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            async with self._send_lock:
                proc.stdin.write(line.encode("utf-8"))
                await proc.stdin.drain()
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

    async def run(self) -> None:
        """無限ループで接続→ストリーム処理→バックオフ→再接続を繰り返す。

        `asyncio.CancelledError`は再送出してタスクを終了させる。
        それ以外の例外はwarningログに残し、`disconnected`遷移後にバックオフ再試行する。
        """
        while True:
            await self._set_status("connecting")
            proc: _async_subprocess.Process | None = None
            try:
                proc = await self._connect()
                self._proc = proc
                assert proc.stdout is not None
                await self._process_stream(_iter_stream_lines(proc.stdout))
                await self._set_status("disconnected")
            except asyncio.CancelledError:
                self._fail_pending(asyncio.CancelledError("watcher cancelled"))
                raise
            except Exception as error:  # noqa: BLE001
                # 接続失敗・JSON解析失敗・stat不能などをまとめて拾い、ホスト単位で再接続継続する。
                logger.warning("リモートwatch失敗 host=%s: %s", self.host, error)
                await self._set_status("disconnected")
            finally:
                self._fail_pending(ConnectionError(f"watch disconnected: host={self.host}"))
                await self._cancel_stderr_task()
                if proc is not None:
                    await _terminate_process(proc)
                self._proc = None
                self._connected = False
            # 指数バックオフ（上限・±20%ジッタ）。リトライ上限なし。
            jittered = self._backoff * random.uniform(*REMOTE_BACKOFF_JITTER_RANGE)
            await asyncio.sleep(jittered)
            self._backoff = min(self._backoff * 2, REMOTE_BACKOFF_MAX_SEC)

    async def _connect(self) -> _async_subprocess.Process:
        cmd = [
            "ssh",
            *SSH_BASE_OPTIONS,
            *SSH_WATCH_OPTIONS,
            self.host,
            *_build_remote_command_argv("serve", []),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # ヘルパーは初回snapshotで全エントリーを1行JSONとして出力するため、
            # asyncio既定の64KiB上限を超えると`readline()`が例外を送出する。
            limit=REMOTE_STREAM_LIMIT_BYTES,
        )
        # helper起動失敗（依存解決失敗など）はstdoutが空EOFとなり原因ログが残らないため、
        # stderrを常時読み取ってwarningへ転写する。
        assert proc.stderr is not None
        self._stderr_task = asyncio.create_task(_drain_stderr(self.host, proc.stderr))
        return proc

    async def _cancel_stderr_task(self) -> None:
        """stderr読取タスクを終了させる。切断・キャンセルのfinally経路から呼ぶ。"""
        task = self._stderr_task
        if task is None:
            return
        self._stderr_task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _process_stream(self, lines: LineSource) -> None:
        """行ストリームを受け取り、type別にハンドラへ振り分ける。"""
        async for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                logger.warning(
                    "リモートwatch JSON解析失敗 host=%s msg=%s pos=%d length=%d",
                    self.host,
                    error.msg,
                    error.pos,
                    len(line),
                )
                continue
            await self._handle_event(event)

    async def _handle_event(self, event: typing.Mapping[str, typing.Any]) -> None:
        kind = event.get("type")
        if kind == "snapshot":
            await self._handle_snapshot(event)
            return
        if kind == "upsert":
            entry = make_file_entry(self.host, event)
            async with self.state.lock:
                cached = self.state.remote_files.get(self.host, [])
                cached = [item for item in cached if (item.source_id, item.path) != (entry.source_id, entry.path)]
                if _is_listed_remote_path(entry.path):
                    cached.append(entry)
                self.state.remote_files[self.host] = cached
            await deliver_refresh(self.state)
            return
        if kind == "deleted":
            path = str(event.get("path", ""))
            source_id = str(event.get("source_id", event.get("source", "")))
            async with self.state.lock:
                cached = self.state.remote_files.get(self.host, [])
                self.state.remote_files[self.host] = [
                    item for item in cached if (item.source_id, item.path) != (source_id, path)
                ]
            await deliver_refresh(self.state)
            return
        if kind == "ping":
            return
        if kind == "response":
            self._resolve_response(event)
            return
        logger.warning("リモートwatch 未知のイベント host=%s type=%r", self.host, kind)

    async def _handle_snapshot(self, event: typing.Mapping[str, typing.Any]) -> None:
        """初回・再接続時のsnapshotをキャッシュへ反映し、接続確立を配信する。"""
        entries = [
            make_file_entry(self.host, item) for item in event.get("entries", []) if _is_listed_remote_path(str(item["path"]))
        ]
        host_info = event.get("host_info")
        has_root_info = "root_info" in event
        decoded_root_info = _decode_root_info(event.get("root_info"))
        has_root_status = "root_status" in event
        decoded_root_status = _decode_root_status(event.get("root_status"))
        async with self.state.lock:
            self.state.remote_files[self.host] = entries
            if isinstance(host_info, dict):
                self.state.host_info[self.host] = dict(host_info)
            if has_root_info:
                self.state.root_info[self.host] = decoded_root_info
            else:
                self.state.root_info.pop(self.host, None)
            if has_root_status:
                self.state.root_status[self.host] = decoded_root_status
            else:
                self.state.root_status.pop(self.host, None)
        await self._set_status("connected")
        self._connected = True
        # 接続成功時にバックオフをリセットし、次回切断後の再接続を初期値から始める。
        self._backoff = REMOTE_BACKOFF_INITIAL_SEC
        await deliver_refresh(self.state)
        if isinstance(host_info, dict):
            await deliver_host_info(self.state, self.host, dict(host_info))
        if has_root_info:
            await deliver_root_info(self.state, self.host, decoded_root_info)
        if has_root_status:
            await deliver_root_status(self.state, self.host, decoded_root_status)

    def _resolve_response(self, event: typing.Mapping[str, typing.Any]) -> None:
        """`type=response`イベントに対し、対応するpending Futureを解決する。

        対応Futureが既に取り消されている・タイムアウト後の遅延応答である場合は破棄する。
        """
        req_id = event.get("id")
        if not isinstance(req_id, int):
            logger.warning("リモートwatch 不正な応答id host=%s id=%r", self.host, req_id)
            return
        fut = self._pending.get(req_id)
        if fut is None or fut.done():
            return
        fut.set_result(dict(event))

    def _fail_pending(self, exc: BaseException) -> None:
        """切断・キャンセル時に全pending Futureを例外で解決する。"""
        if not self._pending:
            return
        pending = self._pending
        self._pending = {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(exc)

    async def _set_status(self, status: str) -> None:
        async with self.state.lock:
            previous = self.state.host_status.get(self.host)
            self.state.host_status[self.host] = status
            if status == "disconnected":
                # 接続喪失時は`host_info`のキー自体を削除する（`None`値保持ではない）。
                # 再接続成功時はsnapshot分岐で再登録される。
                self.state.host_info.pop(self.host, None)
                self.state.root_info.pop(self.host, None)
                self.state.root_status.pop(self.host, None)
        if previous != status:
            await deliver_host_status(self.state, self.host, status)
            if status == "disconnected":
                await deliver_host_info(self.state, self.host, None)
                await deliver_root_info(self.state, self.host, None)
                await deliver_root_status(self.state, self.host, {})


async def _iter_stream_lines(stream: asyncio.StreamReader) -> typing.AsyncIterator[str]:
    """`StreamReader`から1行ずつ取り出す非同期イテレータ。

    `readline()`はEOFで空bytesを返すため、その時点で打ち切る。
    snapshot行が`REMOTE_STREAM_LIMIT_BYTES`を超過した場合、`readline`は`ValueError`を送出する。
    明示捕捉してwarningを残して打ち切らないと`run`の広範な`except Exception`で見過ごされ、
    原因不明のリコネクトが続く。
    """
    while True:
        try:
            chunk = await stream.readline()
        except ValueError as error:
            logger.warning("リモートwatch snapshot行がlimit超過 limit=%d: %s", REMOTE_STREAM_LIMIT_BYTES, error)
            return
        if not chunk:
            return
        yield chunk.decode("utf-8", errors="replace")


async def _drain_stderr(host: str, stream: asyncio.StreamReader) -> None:
    """stderrを行単位で読み続けてwarningへ転写する（詳細は`_connect`のコメント参照）。"""
    try:
        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                logger.warning("リモートwatch stderr host=%s: %s", host, text)
    except Exception as error:  # noqa: BLE001
        # CancelledErrorはBaseException派生のため`Exception`で拾わず、通常経路で再送出される。
        logger.warning("リモートwatch stderr読取失敗 host=%s: %s", host, error)


async def _terminate_process(
    proc: _async_subprocess.Process,
    grace_timeout: float = TERMINATE_GRACE_TIMEOUT_SEC,
) -> None:
    """watch用subprocessを段階的に終了させる。

    serveヘルパーは`for raw in sys.stdin:`のEOFで停止経路に入るため、
    まずstdinをcloseして穏当な終了を試み、応答がなければ`terminate`、
    それでも応答がなければ`kill`へ降下する。
    """
    if proc.returncode is not None:
        return
    # 1) stdinへEOFを送ってhelperのreader_loopをbreakさせる。
    if proc.stdin is not None and not proc.stdin.is_closing():
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
            proc.stdin.close()
    if await _wait_with_timeout(proc, grace_timeout):
        return
    # 2) SIGTERM相当でhelperへ停止指示する。
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    if await _wait_with_timeout(proc, grace_timeout):
        return
    # 3) 最後にSIGKILL相当で強制終了させる。
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    await _wait_with_timeout(proc, grace_timeout)


async def _wait_with_timeout(proc: _async_subprocess.Process, timeout: float) -> bool:
    """`proc.wait()`を時間制限付きで実行し、終了済みならTrueを返す。

    `_terminate_process`はキャンセル経路からも呼ばれるため、
    `CancelledError`は吸収して段階的処理を継続する。
    """
    if proc.returncode is not None:
        return True
    with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    return proc.returncode is not None


def is_safe_remote_relpath(rel: str) -> bool:
    """SSHヘルパーへ渡す前に相対パスのトラバーサルを検証する。

    リモート側でも検証するが、サーバー側で先に拒否することで不要なSSH呼び出しを避け、
    ログにも危険な相対パスが残らないようにする。
    """
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    parts = pathlib.PurePosixPath(rel).parts
    if any(part in ("", "..") for part in parts):
        return False
    return rel.endswith(".md") or rel.endswith(_TARGET_TSV_SUFFIXES)


# --------------------------------------------------------------------------------------
# 画面が消費する処理
# --------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PlansContext:
    """計画ファイル画面のルートが共有するアプリ単位の依存。"""

    root: pathlib.Path
    roots: tuple[RootSpec, ...]
    hostname: str
    remote_hosts: list[str]
    allowed_remote_hosts: set[str]
    runner: SshRunner
    search_coordinator: RemoteSearchCoordinator
    renderer: markdown_it.MarkdownIt
    markdown_cache: MarkdownCache
    state: BroadcastState


def create_context(
    *,
    root: pathlib.Path | None = None,
    roots: typing.Iterable[RootSpec] | None = None,
    hostname: str | None = None,
    remote_hosts: typing.Iterable[str] | None = None,
    ssh_runner: SshRunner | None = None,
    remote_search_limit: int = DEFAULT_REMOTE_SEARCH_LIMIT,
) -> PlansContext:
    """計画ファイル画面の依存と初期接続状態を生成する。

    `root`を渡した場合は当該rootだけを対象とし、`roots`を渡した場合はそのroot群を使う。
    いずれも渡さない場合はprivate-notesと`~/.claude/plans`の2rootを解決する。
    `hostname`はローカル分のファイルエントリに付与する`host`ラベルとリモートホストとの一意性検査に使う。
    """
    # 前回の異常終了で残った作成日時インデックスの一時ファイルを起動時に除去する。
    cleanup_creation_time_temporaries()
    renderer = make_md_renderer()
    markdown_cache = MarkdownCache()
    state = BroadcastState()
    if roots is not None:
        root_specs = normalize_root_specs(roots)
    elif root is not None:
        root_specs = normalize_root_specs((explicit_root_spec(root),))
    else:
        root_specs = default_root_specs()
    if not root_specs:
        raise ValueError("計画ファイルのrootが1つも解決できません")
    resolved_root = root_specs[0].path
    resolved_hostname = hostname if hostname is not None else socket.gethostname()
    remote_host_list = list(remote_hosts) if remote_hosts else []
    if resolved_hostname in remote_host_list:
        # `remote_files`のキーが衝突しローカル/リモートが上書きし合うため、起動時に拒絶する。
        raise ValueError("ローカルホスト名がリモートホストの指定と重複しています")
    runner: SshRunner = ssh_runner if ssh_runner is not None else default_ssh_runner

    # 初期接続状態を設定する。ローカルは常にconnected、リモートはconnecting開始。
    state.host_status[resolved_hostname] = "connected"
    for host in remote_host_list:
        state.host_status[host] = "connecting"
    # ローカルホスト分の`host_info`は起動時に即座にセットする。
    # リモート分は接続確立時（初回snapshot受信）に`RemoteWatcher`側で追加する。
    state.host_info[resolved_hostname] = local_host_info(resolved_root)
    state.root_info[resolved_hostname] = {spec.source_id: root_info(spec) for spec in root_specs}
    state.root_status[resolved_hostname] = {
        spec.source_id: root_status(spec.warning or root_warning(spec.path)) for spec in root_specs
    }

    return PlansContext(
        root=resolved_root,
        roots=root_specs,
        hostname=resolved_hostname,
        remote_hosts=remote_host_list,
        allowed_remote_hosts=set(remote_host_list),
        runner=runner,
        search_coordinator=RemoteSearchCoordinator(remote_search_limit),
        renderer=renderer,
        markdown_cache=markdown_cache,
        state=state,
    )


def _scan_local_root(spec: RootSpec, host: str) -> tuple[list[FileEntry], str | None]:
    """root解決時の警告を保ったまま、利用可能なローカルrootだけを走査する。"""
    if spec.warning is not None:
        return [], spec.warning
    return scan_files(spec.path, host, spec.source_id, migrate_legacy_ctime=spec.migrate_legacy_ctime)


def _oldest_host_per_file(entries: list[FileEntry]) -> list[FileEntry]:
    """ホスト間で同期されるrootのエントリを、同じ相対パスごとに作成日時が最も古いホストの1件へ絞る。

    集約の対象は`NEW_SOURCE_ID`のroot（private-notes配下のplans）に限る。
    同rootは全環境で同期される場合があり、同一のファイルが各ホストのエントリとして重複する。
    更新は最初に作成したホストで行われるため、作成日時が最も古いエントリだけを残す。
    `ctime_epoch`が同値の場合は`(host, path)`の昇順で先頭を選び、選択を決定的にする。

    旧root（`~/.claude/plans`）と設定で明示したrootはホスト間で同期されない。
    これらは`source_id`と相対パスが同じでもホストごとに別のファイルであるため、集約せず全件を保持する。
    """
    kept: list[FileEntry] = []
    oldest: dict[str, FileEntry] = {}
    for entry in entries:
        if entry.source_id != NEW_SOURCE_ID:
            kept.append(entry)
            continue
        current = oldest.get(entry.path)
        if current is None or (entry.ctime_epoch, entry.host) < (current.ctime_epoch, current.host):
            oldest[entry.path] = entry
    return kept + list(oldest.values())


async def all_entries(context: PlansContext) -> list[FileEntry]:
    """ローカルとリモートの一覧を、同期で重複した分を1件へ絞ってから作成日時の降順で返す。"""
    # ローカル一覧はリモート集約と並列実行できるよう`asyncio.to_thread`経由で取得する。
    local_results = await asyncio.gather(
        *(asyncio.to_thread(_scan_local_root, spec, context.hostname) for spec in context.roots)
    )
    local_entries = [entry for entries, _ in local_results for entry in entries]
    local_status = {
        spec.source_id: root_status(warning) for spec, (_, warning) in zip(context.roots, local_results, strict=True)
    }
    async with context.state.lock:
        previous_status = context.state.root_status.get(context.hostname)
        context.state.root_status[context.hostname] = local_status
    if previous_status != local_status:
        await deliver_root_status(context.state, context.hostname, local_status)
    async with context.state.lock:
        remote_entries: list[FileEntry] = []
        for cached in context.state.remote_files.values():
            remote_entries.extend(cached)
    merged = _oldest_host_per_file(local_entries + remote_entries)
    merged.sort(key=lambda entry: (-entry.ctime_epoch, entry.host, entry.source_id, entry.path))
    return merged


def listed_plan_path(rel: str) -> str:
    """付属ファイルの検索一致を一覧で選択できる計画ファイル（メイン）へ接続する。"""
    suffix = next((suffix for suffix, _ in _PLAN_SUFFIX_LABELS if rel.endswith(suffix)), None)
    return rel if suffix is None else f"{rel[: -len(suffix)]}.md"


async def search_entries(context: PlansContext, query: str) -> list[FileEntry] | None:
    """本文検索に一致する一覧を返す。後続要求に置き換えられた場合は`None`を返す。"""
    entries = await all_entries(context)
    if not query:
        return entries
    local_results = await asyncio.gather(*(asyncio.to_thread(search_files, spec.path, query) for spec in context.roots))
    local_matches = {
        (spec.source_id, listed_plan_path(path))
        for spec, paths in zip(context.roots, local_results, strict=True)
        for path in paths
    }
    # 1ホストの打ち切りで他ホストの結果が未回収の例外にならないよう、全件を回収してから判定する。
    results = await asyncio.gather(
        *(_search_remote(context, host, query) for host in context.remote_hosts),
        return_exceptions=True,
    )
    remote_matches: dict[str, set[tuple[str, str]]] = {}
    for result in results:
        if isinstance(result, RemoteSearchSuperseded):
            return None
        if isinstance(result, BaseException):
            # `_search_remote`は`Exception`のみを捕捉するため、この分岐はキャンセル等に限られる。
            raise result
        matched_host, matched_paths = result
        remote_matches[matched_host] = {(source_id, listed_plan_path(path)) for source_id, path in matched_paths}
    return [
        entry
        for entry in entries
        if (entry.source_id, entry.path)
        in (local_matches if entry.host == context.hostname else remote_matches.get(entry.host, set()))
    ]


async def _search_remote(context: PlansContext, host: str, query: str) -> tuple[str, set[tuple[str, str]]]:
    """1台のリモートホストを本文検索する。"""
    try:
        raw_paths = await search_remote_files(
            host,
            query,
            context.runner,
            context.state.remote_watchers.get(host),
            coordinator=context.search_coordinator,
        )
    except RemoteSearchSuperseded:
        # 打ち切りは失敗として扱わず、別の検索語の結果で埋めないため呼び出し元へ伝える。
        raise
    except Exception as error:  # noqa: BLE001
        logger.warning("リモート本文検索失敗 host=%s: %s", host, error)
        raw_paths = set()
    source_ids = set(context.state.root_info.get(host, {}))
    fallback_source = next(iter(source_ids)) if len(source_ids) == 1 else ""
    paths: set[tuple[str, str]] = set()
    for value in raw_paths:
        if isinstance(value, tuple) and len(value) == 2:
            paths.add((str(value[0]), str(value[1])))
        else:
            paths.add((fallback_source, str(value)))
    return host, paths


def resolve_source_id(context: PlansContext, host: str, source_id: str, rel: str) -> str | None:
    """要求のsource IDを検証・補完する。確定できない場合は`None`を返す。"""
    if host == context.hostname:
        known = {spec.source_id for spec in context.roots}
        if source_id:
            return source_id if source_id in known else None
        candidates = [spec.source_id for spec in context.roots if resolve_under_root(spec.path, rel) is not None]
    else:
        known = set(context.state.root_info.get(host, {}))
        if source_id:
            return source_id if not known or source_id in known else None
        candidates = [entry.source_id for entry in context.state.remote_files.get(host, []) if entry.path == rel]
        if not candidates:
            candidates = list(known)
    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        return None
    # 単一rootを明示した構成ではsource IDが存在しない。
    return ""


def review_table_html(text: str) -> str:
    """JSON文字列7列のレビュー指摘管理表をHTML表へ変換し、不正入力は原文表示へ戻す。"""
    rows: list[list[str]] = []
    try:
        for line in text.splitlines():
            encoded_cells = line.split("\t")
            if len(encoded_cells) != len(_REVIEW_TABLE_HEADERS):
                raise ValueError("レビュー指摘管理表の列数が不正です")
            cells = [json.loads(cell) for cell in encoded_cells]
            if not all(isinstance(cell, str) for cell in cells):
                raise ValueError("レビュー指摘管理表のセルがJSON文字列ではありません")
            rows.append(cells)
    except (json.JSONDecodeError, ValueError):
        return f"<pre>{html_lib.escape(text)}</pre>\n"

    head = "".join(f"<th>{html_lib.escape(header)}</th>" for header in _REVIEW_TABLE_HEADERS)
    body = "".join("<tr>" + "".join(f"<td>{html_lib.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return f'<table class="review-table">\n<thead><tr>{head}</tr></thead>\n<tbody>{body}</tbody>\n</table>\n'


def is_review_table_path(rel: str) -> bool:
    """相対パスがレビュー指摘管理表かを判定する。"""
    return rel.endswith(_TARGET_TSV_SUFFIXES)


def _plan_paths(rel: str) -> tuple[tuple[str, str], ...]:
    """同じstemに属する計画ファイルの相対パスと表示名を返す。"""
    suffix = next((suffix for suffix, _ in _PLAN_SUFFIX_LABELS if rel.endswith(suffix)), None)
    if suffix is None:
        if not rel.endswith(".md"):
            return ()
        stem = rel[: -len(".md")]
    else:
        stem = rel[: -len(suffix)]
    return (
        (f"{stem}.md", "メイン"),
        *((f"{stem}{attached_suffix}", label) for attached_suffix, label in _PLAN_SUFFIX_LABELS),
    )


async def plan_links_html(context: PlansContext, host: str, source_id: str, rel: str) -> str:
    """同じstemで実在する他の計画だけを、表示応答の先頭へリンクとして付ける。"""
    plan_paths = _plan_paths(rel)
    if not plan_paths:
        return ""
    existing: list[tuple[str, str]] = []
    for plan_rel, label in plan_paths:
        if plan_rel == rel or await _plan_exists(context, host, source_id, plan_rel):
            existing.append((plan_rel, label))
    if len(existing) < 2:
        return ""
    parts: list[str] = []
    for plan_rel, label in existing:
        if plan_rel == rel:
            parts.append(html_lib.escape(label))
            continue
        escaped = html_lib.escape(plan_rel, quote=True)
        parts.append(f'<a href="#" data-plan-path="{escaped}">{html_lib.escape(label)}</a>')
    return f'<nav class="detail-link">{" | ".join(parts)}</nav>\n'


async def _plan_exists(context: PlansContext, host: str, source_id: str, plan_rel: str) -> bool:
    """付属計画の実在を判定する。

    ローカルはファイルシステムの存在確認、リモートはヘルパーのファイル取得の成否で判定する
    （付属計画は一覧から除外されるため、リモートでは一覧応答から実在を判定できない）。
    """
    if host == context.hostname:
        spec = next((item for item in context.roots if item.source_id == source_id), None)
        return spec is not None and resolve_under_root(spec.path, plan_rel) is not None
    if not is_safe_remote_relpath(plan_rel):
        return False
    watcher = context.state.remote_watchers.get(host)
    try:
        await fetch_remote_file(host, plan_rel, context.runner, watcher, source_id=source_id)
    except Exception as error:  # noqa: BLE001
        # 付属計画未作成は通常状態であり障害ではないため、記録レベルはdebugとする。
        logger.debug("付属計画の取得不可 host=%s path=%s: %s", host, plan_rel, error)
        return False
    return True


class PlanFileError(Exception):
    """計画ファイルの取得に失敗したことを、HTTPステータスとともに示す。"""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


async def resolve_text_and_mtime(
    context: PlansContext,
    host: str,
    source_id: str,
    rel: str,
) -> tuple[str, float | None]:
    """ファイル本文と`mtime_epoch`を取得する。取得できない場合は`PlanFileError`を送出する。"""
    if host == context.hostname:
        spec = next((item for item in context.roots if item.source_id == source_id), None)
        target = resolve_under_root(spec.path, rel) if spec is not None else None
        if target is None:
            raise PlanFileError(404, "not found")
        return await asyncio.to_thread(_read_with_mtime, target)
    if not is_safe_remote_relpath(rel):
        raise PlanFileError(400, "invalid path")
    watcher = context.state.remote_watchers.get(host)
    try:
        return await fetch_remote_file(host, rel, context.runner, watcher, source_id=source_id)
    except Exception as error:  # noqa: BLE001
        logger.warning("リモートファイル取得失敗 host=%s path=%s: %s", host, rel, error)
        raise PlanFileError(404, "not found") from error


def _read_with_mtime(path: pathlib.Path) -> tuple[str, float]:
    """ファイル本文と更新日時を連続して取得する。"""
    data = path.read_text(encoding="utf-8", errors="replace")
    return data, path.stat().st_mtime


async def render_file_html(context: PlansContext, host: str, source_id: str, rel: str) -> str:
    """計画ファイルの表示用HTMLを、付属計画リンクを先頭へ付けて返す。"""
    text, mtime = await resolve_text_and_mtime(context, host, source_id, rel)
    # `mtime`が取れた場合のみキャッシュを参照する。リモート応答にmtimeが欠落した場合は
    # 古い結果を返さないよう安全側に倒してバイパスする。
    cache_key: MarkdownCacheKey | None = None
    if mtime is not None:
        cache_key = (host, source_id, rel, mtime) if source_id else (host, rel, mtime)
    rendered: str | None = None
    if cache_key is not None:
        rendered = context.markdown_cache.get(cache_key)
    if rendered is None:
        rendered = review_table_html(text) if is_review_table_path(rel) else markdown_to_html(text, context.renderer)
        if cache_key is not None:
            context.markdown_cache.put(cache_key, rendered)
    # 付属計画リンクは応答組み立て層で付与する。本文変換とそのキャッシュへ混ぜると、
    # 付属計画の出現・消失のたびに本文HTMLキャッシュを無効化する必要が生じるため。
    return await plan_links_html(context, host, source_id, rel) + rendered


def start_remote_watchers(context: PlansContext) -> None:
    """設定済みリモートホストのwatchタスクを起動する。"""
    context.state.loop = asyncio.get_running_loop()
    for host in context.remote_hosts:
        watcher = RemoteWatcher(host, context.state)
        # 本文取得がwatch経路のRPCを利用できるよう参照を共有する。
        context.state.remote_watchers[host] = watcher
        context.state.remote_tasks.append(asyncio.create_task(watcher.run()))


async def stop_remote_watchers(context: PlansContext) -> None:
    """起動済みのwatchタスクをまとめて終了させる。"""
    for task in context.state.remote_tasks:
        task.cancel()
    for task in context.state.remote_tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    context.state.remote_tasks.clear()
