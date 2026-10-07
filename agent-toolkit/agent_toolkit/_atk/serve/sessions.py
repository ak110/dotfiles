"""`atk serve`のセッション画面の処理本体。

Claude CodeとCodexの保存済み記録を共通の表示モデルへ正規化し、一覧と詳細を返す。
保存先の規約は`agent-toolkit/skills/writing-standards/references/session-records.md`が定める。
リモートホスト側で実行するヘルパーは`atk_serve_sessions_remote_helper.py`とする。

記録が持たない情報は0や空文字列で補わず、`None`（JSONのnull）として返す。
閲覧は読み取り専用とし、記録を変更しない。
"""

import asyncio
import asyncio.subprocess as _async_subprocess
import base64
import contextlib
import dataclasses
import datetime
import json
import logging
import pathlib
import socket
import threading
import typing

from agent_toolkit._atk import session_record_format
from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._atk.serve import session_parents, session_watch
from agent_toolkit._common import host_homes as _host_homes
from agent_toolkit._common import session_launchers
from agent_toolkit._common import state_paths as _state_paths

logger = logging.getLogger(__name__)

# 一覧が返す最大件数。開始日時の新しい順に並べたうえで打ち切る。
MAX_LIST_ENTRIES = 2000
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
# 最初からやり直すため、走査を続けている常駐ヘルパーより先に終わらず、そのCPUを奪い合う。
_NO_FALLBACK_ON_RPC_TIMEOUT = frozenset({"list"})
# 常駐SSH接続のstdout用StreamReader上限（バイト）。一覧・本文は1行JSONで届くため大きく取る。
REMOTE_STREAM_LIMIT_BYTES = 128 * 1024 * 1024
# 再接続のバックオフ。
BACKOFF_INITIAL_SEC = 1.0
BACKOFF_MAX_SEC = 30.0

# リモート側で実行する短いPython bootstrap。組み立ての制約は`_atk_serve_remote`が定める。
REMOTE_BOOTSTRAP = _atk_serve_remote.remote_bootstrap("atk_serve_sessions_remote_helper.py")

SshRunner = typing.Callable[[str, str, list[str]], typing.Awaitable[str]]


# --------------------------------------------------------------------------------------
# 表示モデル
# --------------------------------------------------------------------------------------


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


# --------------------------------------------------------------------------------------
# Claude Codeの記録
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# Codexの記録
# --------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------
# 記録の読み込み
# --------------------------------------------------------------------------------------


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


# --------------------------------------------------------------------------------------
# ローカルの保存先
# --------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class SessionsContext:
    """セッション画面のルートが共有するアプリ単位の依存。"""

    hostname: str
    claude_home: pathlib.Path
    codex_home: pathlib.Path
    remote_hosts: tuple[str, ...]
    runner: SshRunner
    state: "SessionsState"
    # agents_serverのsession登録簿を置く状態ディレクトリ。登録簿の委譲元を一覧の親子付けに使う。
    state_dir: pathlib.Path | None = None


@dataclasses.dataclass(slots=True)
class SessionsState:
    """SSE購読者とリモート接続状態を保持する。"""

    subscribers: set[asyncio.Queue[str]] = dataclasses.field(default_factory=set)
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    # ホスト名 -> "connected"|"connecting"|"disconnected"。
    host_status: dict[str, str] = dataclasses.field(default_factory=dict)
    clients: dict[str, "RemoteSessionClient"] = dataclasses.field(default_factory=dict)
    tasks: list[asyncio.Task[None]] = dataclasses.field(default_factory=list)
    # ローカルの記録rootの変更監視。`start_local_watch`が生成する。
    record_watch: session_watch.RecordWatch | None = None
    # サーバーの停止要求。スレッドで動くローカル走査が反復の途中で参照して打ち切る。
    stop_requested: threading.Event = dataclasses.field(default_factory=threading.Event)
    # ローカルの記録単位の解析結果。要求ごとの走査で変更のない記録を読み直さないために保持する。
    record_index: session_watch.RecordSummaryIndex = dataclasses.field(default_factory=session_watch.RecordSummaryIndex)
    # 進行中の一覧の取得（ローカルの走査とリモートの取得）。同時に届いた一覧の要求はこの1回の結果を共有する。
    list_task: "asyncio.Future[tuple[list[SessionSummary], list[dict[str, str]]]] | None" = None


def create_context(
    *,
    hostname: str | None = None,
    claude_home: pathlib.Path | None = None,
    codex_home: pathlib.Path | None = None,
    remote_hosts: typing.Iterable[str] | None = None,
    ssh_runner: SshRunner | None = None,
    state_dir: pathlib.Path | None = None,
) -> SessionsContext:
    """セッション画面の依存と初期接続状態を生成する。"""
    resolved_hostname = hostname if hostname is not None else socket.gethostname()
    hosts = tuple(remote_hosts or ())
    if resolved_hostname in hosts:
        raise ValueError("ローカルホスト名がリモートホストの指定と重複しています")
    state = SessionsState()
    state.host_status[resolved_hostname] = "connected"
    for host in hosts:
        state.host_status[host] = "connecting"
    return SessionsContext(
        hostname=resolved_hostname,
        claude_home=claude_home if claude_home is not None else _host_homes.claude_config_dir(),
        codex_home=codex_home if codex_home is not None else _host_homes.codex_home(),
        remote_hosts=hosts,
        runner=ssh_runner if ssh_runner is not None else default_ssh_runner,
        state=state,
        state_dir=state_dir if state_dir is not None else _state_paths.state_dir(),
    )


def _local_entry(
    index: session_watch.RecordSummaryIndex, path: pathlib.Path, engine: str, session_id: str, host: str
) -> tuple[SessionSummary, bool | None, str | None]:
    """ローカルの記録1件を一覧の項目へ変換し、ユーザー発話の有無とCodexの親threadの識別子とともに返す。"""
    (cwd, first_user_message, started_at, has_user, parent_thread_id), st = index.summary(path, engine)
    if isinstance(st, OSError):
        summary = SessionSummary(
            engine=engine,
            host=host,
            cwd=cwd,
            first_user_message=first_user_message,
            session_id=session_id,
            path=str(path),
            started_at=started_at,
            updated_at=None,
            size=None,
            warning=f"記録の情報を取得できません: {st}",
        )
        return summary, has_user, parent_thread_id
    summary = SessionSummary(
        engine=engine,
        host=host,
        cwd=cwd,
        first_user_message=first_user_message,
        session_id=session_id,
        path=str(path),
        started_at=started_at,
        updated_at=_isoformat(st.st_mtime),
        size=st.st_size,
    )
    return summary, has_user, parent_thread_id


def list_local_sessions(context: SessionsContext) -> list[SessionSummary]:
    """ローカルの保存済みセッションを開始日時の新しい順に返す。

    Claude Codeはセッション本体と、metadataで親子を確定できるサブエージェントを含める。
    Codexは`<CODEX_HOME>/sessions/<年>/<月>/<日>/rollout-*<thread-id>.jsonl`を対象とする。
    ユーザー発話の記録行を持たない記録は、件数上限による切り詰めより前に除外する。
    変更監視が動いている場合は、判定した発話の有無を監視側の判定へ引き継ぐ。
    サーバーの停止要求を受けた場合は記録1件ごとの確認で`ServeStopping`を送出して打ち切る。
    """
    stop = context.state.stop_requested
    index = context.state.record_index
    index.begin_scan()
    collected: list[tuple[SessionSummary, bool | None, str | None]] = []
    for path in session_record_format.claude_session_records(context.claude_home):
        _atk_serve_remote.raise_if_stopping(stop)
        collected.append(_local_entry(index, path, "claude", path.stem, context.hostname))
        for agent_id, child_path, parent_path in session_record_format.claude_subagent_records(path):
            child, child_has_user, child_thread = _local_entry(index, child_path, "claude", agent_id, context.hostname)
            collected.append((dataclasses.replace(child, parent_path=parent_path), child_has_user, child_thread))
    for path in session_record_format.codex_session_records(context.codex_home):
        _atk_serve_remote.raise_if_stopping(stop)
        collected.append(_local_entry(index, path, "codex", session_record_format.codex_session_id(path), context.hostname))
    watch = context.state.record_watch
    if watch is not None:
        for entry, has_user, _ in collected:
            if has_user is not None:
                watch.tracker.prime(entry.path, has_user)
    # 読めずに判定できなかった記録（`None`）は警告とともに一覧へ残す。
    kept = [(entry, parent_thread_id) for entry, has_user, parent_thread_id in collected if has_user is not False]
    links = session_parents.resolve_parent_paths(
        [
            session_parents.ParentSource(
                path=entry.path,
                engine=entry.engine,
                session_id=entry.session_id,
                subagent_parent_path=entry.parent_path,
                codex_parent_thread_id=parent_thread_id,
            )
            for entry, parent_thread_id in kept
        ],
        delegated_ids=lambda path, engine: index.delegated_ids(pathlib.Path(path), engine),
        launcher_of=session_launchers.launcher_reader(context.state_dir),
    )
    index.finish_scan()
    entries = [dataclasses.replace(entry, parent_path=links.get(entry.path)) for entry, _ in kept]
    entries.sort(key=lambda entry: entry.started_at or "", reverse=True)
    return session_parents.limit_with_ancestors(
        entries, MAX_LIST_ENTRIES, path_of=lambda entry: entry.path, parent_of=lambda entry: entry.parent_path
    )


def is_local_record_path(context: SessionsContext, raw: str) -> bool:
    """読み取り要求のパスがいずれかの保存先の配下を指すかを検証する。"""
    if not raw or ".." in pathlib.PurePosixPath(raw).parts or not raw.endswith(session_record_format.RECORD_SUFFIX):
        return False
    try:
        target = pathlib.Path(raw).resolve()
    except OSError:
        return False
    for root in (context.claude_home / "projects", context.codex_home / "sessions"):
        try:
            target.relative_to(root.resolve())
        except (ValueError, OSError):
            continue
        return True
    return False


class SessionNotFoundError(Exception):
    """指定されたセッション記録へ到達できないことを示す。"""


def read_local_detail(context: SessionsContext, engine: str, raw_path: str) -> dict[str, typing.Any]:
    """ローカルの記録を読み、詳細応答を組み立てる。"""
    if engine not in {"claude", "codex"} or not is_local_record_path(context, raw_path):
        raise SessionNotFoundError(raw_path)
    path = pathlib.Path(raw_path)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise SessionNotFoundError(raw_path) from error
    if len(data) > MAX_RECORD_BYTES:
        raise SessionNotFoundError(f"{raw_path}（記録が大きすぎます）")
    records, broken = session_record_format.parse_records(data.decode("utf-8", errors="replace"))
    subagents = session_record_format.claude_subagents(path) if engine == "claude" else None
    # ローカルの記録は保存先を直接読むため、サブエージェント記録の有無を常に判定できる。
    detail = build_detail(engine, records, broken_lines=broken, subagents=subagents, subagents_unavailable=False)
    detail["session_id"] = path.stem if engine == "claude" else session_record_format.codex_session_id(path)
    detail["host"] = context.hostname
    detail["path"] = raw_path
    detail["started_at"] = _started_at(engine, records)
    detail["project"] = _detail_project(engine, records, path)
    return detail


def _detail_project(engine: str, records: list[dict[str, typing.Any]], path: pathlib.PurePath) -> str | None:
    """記録から作業ディレクトリを取り出す。持たない場合はディレクトリ名で代替する。"""
    if engine == "codex":
        cwd = session_record_format.codex_metadata(records).get("cwd")
        return cwd if isinstance(cwd, str) else None
    for record in records:
        cwd = record.get("cwd")
        if isinstance(cwd, str) and cwd:
            return cwd
    return path.parent.name


# --------------------------------------------------------------------------------------
# リモートホスト統合
# --------------------------------------------------------------------------------------


# 単発SSHの失敗の表現と標準エラー出力の整形は計画ファイル画面と共通の契約とする。
RemoteHelperError = _atk_serve_remote.RemoteHelperError
_stderr_excerpt = _atk_serve_remote.stderr_excerpt


async def default_ssh_runner(host: str, op: str, args: list[str]) -> str:
    """SSH経由でこの画面のリモートヘルパーを単発実行し、stdoutをUTF-8文字列で返す。

    `list`には常駐接続と同じ接続確立の打ち切りと生存確認を付け、上限が長くても
    到達できないホストの失敗を接続確立の段階で数秒のうちに返す。
    """
    ssh_options = (*SSH_BASE_OPTIONS, *SSH_WATCH_OPTIONS) if op == "list" else SSH_BASE_OPTIONS
    return await _atk_serve_remote.run_remote_helper(
        REMOTE_BOOTSTRAP, host, op, args, ssh_options=ssh_options, timeout=OPERATION_TIMEOUT_SEC[op]
    )


class RemoteSessionClient:
    """1ホスト分の常駐SSH接続とRPCを担う。

    `run()`は接続を維持し、切断時はバックオフして再接続する。
    RPCを送れない状態では呼び出し元が単発SSHへ切り替える。
    """

    def __init__(self, host: str, state: SessionsState) -> None:
        self.host = host
        self.state = state
        self._proc: _async_subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[dict[str, typing.Any]]] = {}
        self._next_request_id = 1
        self._send_lock = asyncio.Lock()
        self._connected = False
        self._backoff = BACKOFF_INITIAL_SEC
        self._stderr_task: asyncio.Task[None] | None = None

    def is_connected(self) -> bool:
        """RPCを送信可能な状態かを返す。"""
        if not self._connected:
            return False
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        return not proc.stdin.is_closing()

    async def request(self, op: str, args: dict[str, typing.Any] | None = None) -> dict[str, typing.Any]:
        """常駐SSH接続経由でRPCリクエストを送信し、応答辞書を返す。

        応答が操作ごとの上限（`OPERATION_TIMEOUT_SEC`）までに届かない場合は、操作名と上限秒数を含む`TimeoutError`を送出する。
        """
        proc = self._proc
        if not self.is_connected() or proc is None or proc.stdin is None or proc.stdin.is_closing():
            raise RuntimeError(f"session helper not connected: host={self.host}")
        loop = asyncio.get_running_loop()
        req_id = self._next_request_id
        self._next_request_id += 1
        future: asyncio.Future[dict[str, typing.Any]] = loop.create_future()
        self._pending[req_id] = future
        line = json.dumps({"id": req_id, "op": op, **(args or {})}, ensure_ascii=False) + "\n"
        try:
            async with self._send_lock:
                proc.stdin.write(line.encode("utf-8"))
                await proc.stdin.drain()
            return await _atk_serve_remote.wait_rpc_response(future, op=op, timeout=OPERATION_TIMEOUT_SEC[op])
        finally:
            self._pending.pop(req_id, None)

    async def run(self) -> None:
        """接続→応答処理→バックオフ→再接続を繰り返す。"""
        while True:
            await self._set_status("connecting")
            proc: _async_subprocess.Process | None = None
            try:
                proc = await self._connect()
                self._proc = proc
                assert proc.stdout is not None
                await self._process_stream(proc.stdout)
                await self._set_status("disconnected")
            except asyncio.CancelledError:
                self._fail_pending(asyncio.CancelledError("session client cancelled"))
                raise
            except Exception as error:  # noqa: BLE001
                logger.warning("セッション記録の常駐接続に失敗 host=%s: %s", self.host, error)
                await self._set_status("disconnected")
            finally:
                self._fail_pending(ConnectionError(f"session helper disconnected: host={self.host}"))
                await _atk_serve_remote.stop_resident_helper(proc, self._stderr_task)
                self._stderr_task = None
                self._proc = None
                self._connected = False
            _atk_serve_remote.raise_if_cancelling()
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, BACKOFF_MAX_SEC)

    async def _connect(self) -> _async_subprocess.Process:
        proc, self._stderr_task = await _atk_serve_remote.start_resident_helper(
            REMOTE_BOOTSTRAP,
            self.host,
            ssh_options=(*SSH_BASE_OPTIONS, *SSH_WATCH_OPTIONS),
            stream_limit=REMOTE_STREAM_LIMIT_BYTES,
            logger=logger,
            label="セッション記録の常駐接続",
        )
        return proc

    async def _process_stream(self, stream: asyncio.StreamReader) -> None:
        """行ストリームを読み、`ready`で接続確立、`response`でRPCを解決し、変更通知をSSEへ中継する。"""
        while True:
            try:
                chunk = await stream.readline()
            except ValueError as error:
                logger.warning("セッション記録の応答行が上限を超過 host=%s: %s", self.host, error)
                return
            if not chunk:
                return
            line = chunk.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                logger.warning("セッション記録の応答を解析できません host=%s: %s", self.host, error)
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "ready":
                self._connected = True
                self._backoff = BACKOFF_INITIAL_SEC
                await self._set_status("connected")
                continue
            if event.get("type") == "response":
                self._resolve_response(event)
                continue
            await _relay_remote_notification(self.state, self.host, event)

    def _resolve_response(self, event: dict[str, typing.Any]) -> None:
        req_id = event.get("id")
        if not isinstance(req_id, int):
            return
        future = self._pending.get(req_id)
        if future is None or future.done():
            return
        future.set_result(dict(event))

    def _fail_pending(self, exc: BaseException) -> None:
        if not self._pending:
            return
        pending = self._pending
        self._pending = {}
        for future in pending.values():
            if not future.done():
                future.set_exception(exc)

    async def _set_status(self, status: str) -> None:
        async with self.state.lock:
            self.state.host_status[self.host] = status


def is_safe_remote_record_path(raw: str) -> bool:
    """リモートへ渡す前に記録のパスを検証する。

    上位ディレクトリへの参照と対象外の接尾辞を拒否する。
    リモート側でも同じ検証を行うが、サーバー側で先に拒否することで不要なSSH呼び出しを避ける。
    """
    if not raw or not raw.endswith(session_record_format.RECORD_SUFFIX):
        return False
    path = pathlib.PureWindowsPath(raw) if "\\" in raw else pathlib.PurePosixPath(raw)
    return path.is_absolute() and ".." not in path.parts


async def _remote_call(context: SessionsContext, host: str, op: str, args: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """常駐RPCを優先し、未接続・失敗と、`_NO_FALLBACK_ON_RPC_TIMEOUT`以外の期限超過では単発SSHへ切り替える。"""
    client = context.state.clients.get(host)
    if client is not None and client.is_connected():
        try:
            response = await client.request(op, args)
        except Exception as error:  # noqa: BLE001
            if isinstance(error, TimeoutError) and op in _NO_FALLBACK_ON_RPC_TIMEOUT:
                raise
            logger.warning("セッション記録のRPCに失敗 host=%s op=%s: %s（単発SSHへ）", host, op, error)
        else:
            if response.get("ok"):
                return response
            logger.warning(
                "セッション記録のRPCがエラーを返した host=%s op=%s: %s（単発SSHへ）",
                host,
                op,
                response.get("error"),
            )
    argv = [str(args["path"])] if op == "read" else []
    raw = await context.runner(host, op, argv)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"リモート応答の形式が不正です: host={host}")
    return payload


async def _remote_sessions(context: SessionsContext, host: str) -> tuple[list[SessionSummary], dict[str, str] | None]:
    """1台のリモートホストの一覧を取得する。取得できない場合は失敗の内容を警告として返す。"""
    try:
        payload = await _remote_call(context, host, "list", {})
    except Exception as error:  # noqa: BLE001
        logger.warning("リモートのセッション一覧を取得できません host=%s: %s", host, error)
        return [], {"host": host, "reason": f"記録を取得できません: {error}"}
    entries: list[SessionSummary] = []
    for item in payload.get("entries", []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        entries.append(
            SessionSummary(
                engine=str(item.get("engine", "")),
                host=host,
                cwd=item.get("cwd") if isinstance(item.get("cwd"), str) else None,
                first_user_message=(
                    item.get("first_user_message") if isinstance(item.get("first_user_message"), str) else None
                ),
                session_id=str(item.get("session_id", "")),
                path=str(item["path"]),
                started_at=item.get("started_at") if isinstance(item.get("started_at"), str) else None,
                updated_at=_isoformat(item["updated_at"]) if isinstance(item.get("updated_at"), (int, float)) else None,
                size=item.get("size") if isinstance(item.get("size"), int) else None,
                warning=item.get("warning") if isinstance(item.get("warning"), str) else None,
                parent_path=item.get("parent_path") if isinstance(item.get("parent_path"), str) else None,
            )
        )
    return entries, None


def _retrieve_exception(task: "asyncio.Future[typing.Any]") -> None:
    """待つ要求が残らずに終わった取得の例外を回収し、未回収の例外としてログへ出力しない。"""
    if not task.cancelled():
        task.exception()


async def list_sessions(context: SessionsContext) -> tuple[list[SessionSummary], list[dict[str, str]]]:
    """ローカルと設定済みリモートホストの一覧を、到達できないホストの警告とともに返す。

    進行中の取得があればその結果を共有し、無ければ新しく取得する。同時に届いた要求ごとに取得すると、
    ローカルの重い走査がスレッドで重なってGILを奪い合い、他の画面の要求も待たされる。
    リモートホストへの要求も常駐接続の上で順に処理されるため、後の要求ほど待たされる。
    共有する取得は要求のキャンセルで止めない（`asyncio.shield`）。1つの要求の打ち切りが、
    同じ結果を待つ他の要求を失敗させないためである。ローカルの走査はサーバーの停止要求
    （`stop_requested`）で打ち切る。
    """
    state = context.state
    task = state.list_task
    if task is None or task.done():
        task = asyncio.ensure_future(_collect_sessions(context))
        task.add_done_callback(_retrieve_exception)
        state.list_task = task
    entries, warnings = await asyncio.shield(task)
    return list(entries), list(warnings)


async def _collect_sessions(context: SessionsContext) -> tuple[list[SessionSummary], list[dict[str, str]]]:
    """ローカルと設定済みリモートホストの一覧を取得する。

    到達できないホストがある場合も、他のホストとローカルの一覧は返す。
    """
    local_entries, remote_results = await asyncio.gather(
        asyncio.to_thread(list_local_sessions, context),
        asyncio.gather(*(_remote_sessions(context, host) for host in context.remote_hosts)),
    )
    entries: list[SessionSummary] = list(local_entries)
    warnings: list[dict[str, str]] = []
    for remote_entries, warning in remote_results:
        entries.extend(remote_entries)
        if warning is not None:
            warnings.append(warning)
    entries.sort(key=lambda entry: entry.started_at or "", reverse=True)
    # 親の参照は同じホストの記録を指すため、ホストとパスの組で祖先を戻す。
    limited = session_parents.limit_with_ancestors(
        entries,
        MAX_LIST_ENTRIES,
        path_of=lambda entry: json.dumps([entry.host, entry.path]),
        parent_of=lambda entry: json.dumps([entry.host, entry.parent_path]) if entry.parent_path else None,
    )
    return limited, warnings


def _remote_subagents(engine: str, payload: dict[str, typing.Any]) -> tuple[list[dict[str, typing.Any]] | None, bool]:
    """リモートの読み取り応答から、サブエージェント一覧と判定不能かどうかを返す。

    リモートホストのdotfilesが古く、サブエージェント一覧を返さない版のヘルパーが動いている場合は、
    読み取り自体が成功したままその欄だけが欠ける。サブエージェントが無い場合と区別するため、
    欄が無い応答は判定不能として扱う。Codexの記録はサブエージェントを持たないため判定不能としない。
    """
    if engine != "claude":
        return None, False
    found = payload.get("subagents")
    if not isinstance(found, list):
        return None, True
    # ローカルと同じく、0件は`None`で表して「サブエージェントが無い」ことを示す。
    return found or None, False


async def session_detail(context: SessionsContext, engine: str, host: str, path: str) -> dict[str, typing.Any]:
    """指定ホストの記録1件を共通の表示モデルへ正規化して返す。"""
    if engine not in {"claude", "codex"}:
        raise SessionNotFoundError(f"未知の実行系です: {engine}")
    if host == context.hostname:
        return await asyncio.to_thread(read_local_detail, context, engine, path)
    if host not in context.remote_hosts:
        raise SessionNotFoundError(f"未知のホストです: {host}")
    if not is_safe_remote_record_path(path):
        raise SessionNotFoundError(path)
    path_b64 = base64.b64encode(path.encode("utf-8")).decode("ascii")
    try:
        payload = await _remote_call(context, host, "read", {"path": path_b64})
    except Exception as error:  # noqa: BLE001
        logger.warning("リモートのセッション記録を取得できません host=%s path=%s: %s", host, path, error)
        raise SessionNotFoundError(path) from error
    text = base64.b64decode(str(payload["data"])).decode("utf-8", errors="replace")
    records, broken = session_record_format.parse_records(text)
    subagents, subagents_unavailable = _remote_subagents(engine, payload)
    detail = build_detail(
        engine, records, broken_lines=broken, subagents=subagents, subagents_unavailable=subagents_unavailable
    )
    pure_path = pathlib.PureWindowsPath(path) if "\\" in path else pathlib.PurePosixPath(path)
    detail["session_id"] = pure_path.stem
    detail["host"] = host
    detail["path"] = path
    detail["started_at"] = _started_at(engine, records)
    detail["project"] = _detail_project(engine, records, pure_path)
    return detail


async def host_status(context: SessionsContext) -> dict[str, str]:
    """ホストごとの接続状態を返す。"""
    async with context.state.lock:
        return dict(context.state.host_status)


def start_remote_clients(context: SessionsContext) -> None:
    """設定済みリモートホストの常駐接続を開始する。"""
    for host in context.remote_hosts:
        client = RemoteSessionClient(host, context.state)
        context.state.clients[host] = client
        context.state.tasks.append(asyncio.create_task(client.run()))


async def stop_remote_clients(context: SessionsContext) -> None:
    """常駐接続をまとめて終了させる。"""
    for task in context.state.tasks:
        task.cancel()
    for task in context.state.tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    context.state.tasks.clear()


async def subscribe(state: SessionsState) -> asyncio.Queue[str]:
    """SSE購読キューを生成して登録し返す。"""
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
    async with state.lock:
        state.subscribers.add(queue)
    return queue


async def unsubscribe(state: SessionsState, queue: asyncio.Queue[str]) -> None:
    """購読キューを解除する。存在しない場合はエラーにしない。"""
    async with state.lock:
        state.subscribers.discard(queue)


async def deliver_refresh(state: SessionsState) -> None:
    """全購読者へ一覧の再取得を促す通知を配信する。

    未配信の通知は破棄して一覧の再取得の1件だけを残す。
    一覧の再取得は選択中の記録の再取得も伴うため、破棄した通知の内容を含み、同じ通知も重ねない。
    """
    payload = json.dumps({"type": "refresh"}, ensure_ascii=False)
    async with state.lock:
        targets = list(state.subscribers)
    for queue in targets:
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(payload)


async def deliver_record(state: SessionsState, host: str, engine: str, path: str) -> None:
    """全購読者へ記録1件の更新を配信する。

    一覧の再取得を促す通知と異なり、選択中の記録を開いている購読者だけが詳細を再取得する。
    キューが満杯の場合は未配信の通知を破棄し、一覧の再取得を促す通知へ置き換える。
    一覧の再取得は選択中の記録の再取得も伴うため、破棄した更新を補える。
    """
    payload = json.dumps(
        {"type": session_watch.RECORD_TYPE, "host": host, "engine": engine, "path": path},
        ensure_ascii=False,
    )
    async with state.lock:
        targets = list(state.subscribers)
    for queue in targets:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(json.dumps({"type": "refresh"}, ensure_ascii=False))


async def _relay_remote_notification(state: SessionsState, host: str, event: dict[str, typing.Any]) -> None:
    """リモートヘルパーの変更通知をSSEへ中継する。未知の型は無視する。"""
    kind = event.get("type")
    if kind == session_watch.REFRESH_TYPE:
        await deliver_refresh(state)
        return
    if kind == session_watch.RECORD_TYPE:
        engine = event.get("engine")
        path = event.get("path")
        if isinstance(engine, str) and isinstance(path, str):
            await deliver_record(state, host, engine, path)


def start_local_watch(context: SessionsContext) -> None:
    """ローカルの記録rootの変更監視を開始する。

    監視は一覧の再取得と記録1件の更新の2種類の通知を、イベントループ上の購読者へ中継する。
    """
    loop = asyncio.get_running_loop()

    def on_flush(refresh: bool, records: list[tuple[str, str]]) -> None:
        async def deliver() -> None:
            if refresh:
                await deliver_refresh(context.state)
            for engine, path in records:
                await deliver_record(context.state, context.hostname, engine, path)

        with contextlib.suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(deliver(), loop)

    tracker = session_watch.RecordChangeTracker(
        [(context.claude_home / "projects", "claude"), (context.codex_home / "sessions", "codex")],
        on_flush,
    )
    watch = session_watch.RecordWatch(tracker)
    try:
        watch.start()
    except OSError as error:
        logger.warning("セッション記録の監視を開始できません: %s", error)
        return
    context.state.record_watch = watch


def stop_local_watch(context: SessionsContext) -> None:
    """ローカルの記録rootの変更監視を終了させる。"""
    watch = context.state.record_watch
    if watch is None:
        return
    context.state.record_watch = None
    watch.stop()
