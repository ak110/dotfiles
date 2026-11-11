"""セッション画面の表示モデルと共有設定。"""

import asyncio
import dataclasses
import datetime
import pathlib
import threading
import typing

from agent_toolkit._atk import session_record_format
from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._atk.serve import session_costs as _session_costs
from agent_toolkit._atk.serve import session_watch
from agent_toolkit._common import session_cost_slim

if typing.TYPE_CHECKING:
    from agent_toolkit._atk.serve.sessions_remote import RemoteSessionClient


# 一覧が返す最大件数。開始日時の新しい順に並べたうえで打ち切る。
MAX_LIST_ENTRIES = 2000


# 変更監視が動いている間に前回のローカル一覧を再利用する上限の秒数。
# watchdogはinotifyのキュー溢れや監視の追加失敗を通知しないため、取りこぼした変更もこの間隔で全件走査に反映する。
LOCAL_LIST_REUSE_SEC = 60.0


# 1件の記録から取得する最大バイト数。過大な記録の全文読み込みにより応答が滞る事態を避ける上限とする。
MAX_RECORD_BYTES = 64 * 1024 * 1024


# SSE購読者ごとの未配信通知の上限。超えた場合は一覧の再取得を促す1件へまとめる。
SUBSCRIBER_QUEUE_SIZE = 16


# SSH接続時に共通付与するオプション。鍵認証失敗時にパスワードプロンプトでハングしないようにする。
SSH_BASE_OPTIONS = ("-o", "BatchMode=yes")


SSH_WATCH_OPTIONS = (
    "-o",
    "ConnectTimeout=5",
    "-o",
    "ServerAliveInterval=10",
    "-o",
    "ServerAliveCountMax=3",
)


# 警告本文へ引き継ぐ標準エラー出力の最大文字数。値と選定理由は計画ファイル画面と共通とする。
STDERR_EXCERPT_MAX_CHARS = _atk_serve_remote.STDERR_EXCERPT_MAX_CHARS


# リモートヘルパーの操作ごとの上限秒。常駐接続のRPCと単発SSHで同じ値を使う。
# `list`は全記録を走査し、常駐ヘルパーの最初の要求と単発SSHでは所要時間が記録量に比例する。
# 値の根拠（初回走査の観測値）は`docs/development/design-serve.md`のセッション画面の節に置く。
# `read`は記録1件の読み取りで、記録量に比例しない。
OPERATION_TIMEOUT_SEC = {"list": 300.0, "read": 30.0}


# RPCが上限を超えても単発SSHへ切り替えない操作。単発SSHは同じ走査を索引の無い新しいプロセスで
# 最初からやり直すため、走査を続けている常駐ヘルパーより先に終わらず、常駐ヘルパーとCPUを奪い合う。
_NO_FALLBACK_ON_RPC_TIMEOUT = frozenset({"list"})


# 常駐SSH接続のstdout用bufferのフロー制御閾値（バイト）。JSONの行長はchunkからの復元で扱う。
REMOTE_STREAM_LIMIT_BYTES = 128 * 1024 * 1024


# 再接続のバックオフ。
BACKOFF_INITIAL_SEC = 1.0


BACKOFF_MAX_SEC = 30.0


# リモート側で実行する短いPython bootstrap。組み立ての制約は`_atk_serve_remote`が定める。
REMOTE_BOOTSTRAP = _atk_serve_remote.remote_bootstrap("atk_serve_sessions_remote_helper.py")


SshRunner = typing.Callable[[str, str, list[str]], typing.Awaitable[str]]


@dataclasses.dataclass(frozen=True, slots=True)
class SessionSummary:
    """一覧の1件。記録が持たない項目は`None`とする。"""

    engine: str
    host: str
    cwd: str | None
    first_user_message: str | None
    session_id: str
    path: str
    started_at: str | None
    updated_at: str | None
    size: int | None
    warning: str | None = None
    parent_path: str | None = None

    def to_json(self) -> dict[str, typing.Any]:
        """JSON応答向けの辞書へ変換する。"""
        return dataclasses.asdict(self)


def _isoformat(epoch: float | None) -> str | None:
    """epoch秒をローカルタイムゾーン付きのISO 8601表記へ変換する。"""
    if epoch is None:
        return None
    tzinfo = datetime.datetime.now().astimezone().tzinfo
    return datetime.datetime.fromtimestamp(epoch, tz=tzinfo).isoformat()


def build_detail(
    engine: str,
    records: list[dict[str, typing.Any]],
    *,
    broken_lines: int,
    subagents: list[dict[str, typing.Any]] | None,
    subagents_unavailable: bool,
) -> dict[str, typing.Any]:
    """解析済みの記録から詳細応答を組み立てる。

    `subagents_unavailable`は、サブエージェント記録の有無そのものを判定できなかったことを表す。
    サブエージェントが無いこと（`subagents`が`null`）と区別して画面へ示すために持たせる。
    イベントは件数で切り詰めず全件を返す。記録の読み込み量は`MAX_RECORD_BYTES`が抑え、応答量は件数より
    イベント本文の長さで決まるためである。画面は先頭から段階的に描画して初期表示の負荷を抑える。
    """
    events, totals = (
        session_record_format.claude_events(records) if engine == "claude" else session_record_format.codex_events(records)
    )
    return {
        "engine": engine,
        "events": [event.to_json() for event in events],
        "usage": totals,
        "subagents": subagents,
        "subagents_unavailable": subagents_unavailable,
        "broken_lines": broken_lines,
    }


def _started_at(engine: str, records: list[dict[str, typing.Any]]) -> str | None:
    """記録の先頭から開始時刻を取り出す。持たない場合は`None`を返す。"""
    if engine == "codex":
        started = session_record_format.codex_metadata(records).get("started_at")
        if isinstance(started, str):
            return started
    for record in records:
        timestamp = record.get("timestamp")
        if isinstance(timestamp, str):
            return timestamp
    return None


@dataclasses.dataclass(frozen=True)
class SessionsContext:
    """セッション画面のルートが共有するアプリ単位の依存。"""

    hostname: str
    claude_home: pathlib.Path
    codex_home: pathlib.Path
    remote_hosts: tuple[str, ...]
    runner: SshRunner
    state: SessionsState
    # agents_serverのsession登録簿を置く状態ディレクトリ。登録簿の委譲元を一覧の親子付けに使う。
    state_dir: pathlib.Path | None = None


@dataclasses.dataclass(slots=True)
class _LocalListCache:
    """再利用するローカル一覧と、その変更を記録している監視の対応。"""

    tracker: session_watch.RecordChangeTracker
    entries: list[SessionSummary]
    # 走査を始めた時刻（`time.monotonic`）。走査中の変更は次の`take_list_changes`で受け取る。
    scanned_at: float


@dataclasses.dataclass(slots=True)
class SessionsState:
    """SSE購読者とリモート接続状態を保持する。"""

    subscribers: set[asyncio.Queue[str]] = dataclasses.field(default_factory=set)
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    # ホスト名 -> "connected"|"connecting"|"disconnected"。
    host_status: dict[str, str] = dataclasses.field(default_factory=dict)
    clients: dict[str, RemoteSessionClient] = dataclasses.field(default_factory=dict)
    tasks: list[asyncio.Task[None]] = dataclasses.field(default_factory=list)
    # ローカルの記録rootの変更監視。`start_local_watch`が生成する。
    record_watch: session_watch.RecordWatch | None = None
    # サーバーの停止要求。スレッドで動くローカル走査が反復の途中で参照して打ち切る。
    stop_requested: threading.Event = dataclasses.field(default_factory=threading.Event)
    # ローカルの記録単位の解析結果。要求ごとの走査で変更のない記録を読み直さないために保持する。
    record_index: session_watch.RecordSummaryIndex = dataclasses.field(default_factory=session_watch.RecordSummaryIndex)
    # ローカルの記録の家族ごとの推定費用。家族の記録が変わらない間は算出し直さない。
    family_costs: _session_costs.FamilyCostCache = dataclasses.field(
        default_factory=lambda: _session_costs.FamilyCostCache(None)
    )
    # 記録ごとの縮約記録。家族の記録が変わった時に、変わった記録の追記分だけを解析する（`session_cost_slim`）。
    slim_records: session_cost_slim.SlimRecordStore = dataclasses.field(
        default_factory=lambda: session_cost_slim.SlimRecordStore(None)
    )
    # 一覧の記録の家族の推定費用を背景で先に求める。serveの変更監視とともに`start_local_watch`が生成する。
    cost_warmer: _session_costs.FamilyCostWarmer | None = None
    # 前回のローカル一覧。変更監視が一覧を変え得る変更を観測していない間だけ再利用する。
    local_list: _LocalListCache | None = None
    # 前回の起動で最後に作ったローカル一覧の保存先と、起動時に読み込んだ一覧。
    # 起動後の最初の一覧は記録数千件の全件の走査に負荷時で数秒かかるため、保存した一覧を返して背景で走査し直す。
    local_list_path: pathlib.Path | None = None
    saved_local_list: list[SessionSummary] | None = None
    # ローカル一覧の全件の走査を1つずつ実行する。要求の中の走査と背景の再走査が記録単位の索引を同時に更新しないためである。
    local_scan_lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    # 再利用の上限時間を過ぎた後の背景の再走査が動いているか。同時に1つだけ動かす。
    local_rescan_running: bool = False
    # `local_rescan_running`の確認と設定だけを守る。走査中も保持する`local_scan_lock`を使うと、
    # 背景の走査の間に届いた要求が走査の終わりまで待たされるためである。
    local_rescan_flag_lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    # 背景の走査の間に一覧の集合を変え得る変更が届いたか。走査の後にもう1回走査する。
    local_rescan_again: bool = False
    # 背景の走査の結果で一覧が変わったときに`refresh`を配信するイベントループ。変更監視の開始時に保持する。
    watch_loop: asyncio.AbstractEventLoop | None = None
    # 進行中の一覧の取得（ローカルの走査とリモートの取得）。同時に届いた一覧の要求はこの1回の結果を共有する。
    list_task: asyncio.Future[tuple[list[SessionSummary], list[dict[str, str]]]] | None = None


class SessionNotFoundError(Exception):
    """指定されたセッション記録へ到達できないことを示す。"""
