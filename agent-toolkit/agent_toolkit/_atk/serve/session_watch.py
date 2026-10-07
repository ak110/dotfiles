"""セッション記録の一覧判定と変更監視を、サーバーとリモートヘルパーで共有する。

サーバー側`sessions.py`と、SSH先で単独実行する`atk_serve_sessions_remote_helper.py`の双方が読み込む。
ヘルパーはリモートホストのPython 3.10以上で標準ライブラリだけを前提に動くため、本モジュールは
標準ライブラリだけをimportし、watchdogは監視の開始時に遅延importする。

一覧に載せる記録は、ユーザー発話の記録行を1件以上持つものに限る。
発話を持たない記録（Codexを起動しただけで入力しなかった記録など）は、選んでも本文がほぼ空になるためである。
"""

import dataclasses
import json
import os
import pathlib
import threading
import time
import typing

from agent_toolkit._atk.serve import session_delegations
from agent_toolkit._common.runtime_inserted import is_runtime_generated, is_runtime_inserted_text

RECORD_SUFFIX = ".jsonl"
CODEX_ROLLOUT_PREFIX = "rollout-"
# サブエージェントの記録は同じディレクトリの`*.meta.json`で親子関係を確定するため、その作成と削除も一覧を変える。
_CLAUDE_META_SUFFIX = ".meta.json"
# 変更をまとめて通知する時間窓（秒）。書き込みのたびに通知すると、進行中のセッションで通知が連続する。
DEFAULT_DEBOUNCE_SEC = 0.5

# 通知の種類。`refresh`は一覧の再取得を促し、`record`は記録1件の更新を示す。
REFRESH_TYPE = "refresh"
RECORD_TYPE = "record"


def _as_text(value: typing.Any) -> str | None:
    """記録の本文欄を表示用の文字列へ正規化する。取り出せない場合は`None`を返す。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [
            block["text"] for block in value if isinstance(block, dict) and isinstance(block.get("text"), str) and block["text"]
        ]
        return "\n".join(parts) if parts else None
    return None


def _first_line(value: typing.Any) -> str | None:
    """本文として解釈できる値の先頭1行を返す。"""
    text = _as_text(value)
    if text is None:
        return None
    lines = text.splitlines()
    return lines[0] if lines else None


def _is_user_record(record: dict[str, typing.Any], engine: str) -> bool:
    """記録行がユーザー発話かを返す。Claude Codeは`type`、Codexは`payload.role`で判定する。"""
    if engine == "claude":
        return record.get("type") == "user"
    payload = record.get("payload")
    return isinstance(payload, dict) and payload.get("role") == "user"


SummaryFields = tuple[str | None, str | None, str | None, bool | None, str | None]
"""`summary_fields`が返す作業ディレクトリ、最初の発話、開始日時、発話の有無およびCodexの親threadの識別子。"""

# ユーザー発話の記録行は、Claude Codeでは`"type":"user"`、Codexでは`"role":"user"`を持つ。
# この文字列を含まない行はユーザー発話になり得ないため、JSONとして解析しない。
_USER_MARKER = '"user"'


def _codex_parent_thread_id(payload: dict[str, typing.Any]) -> str | None:
    """Codexの`session_meta`の`source`から、`spawn_agent`系で起動した親threadの識別子を取り出す。"""
    source = payload.get("source")
    subagent = source.get("subagent") if isinstance(source, dict) else None
    spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
    parent = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
    return parent if isinstance(parent, str) and parent else None


class _SummaryScan:
    """記録の行を順に受け取り、`summary_fields`の値を求める。

    作業ディレクトリ・開始日時（Codexでは最初の`session_meta`も）の識別項目が揃うまでは全行を解析し、
    揃った後は表示できる発話が見つかるまで`"user"`を含む行だけを解析する。
    """

    def __init__(self, engine: str) -> None:
        self.engine = engine
        self.parent_thread_id: str | None = None
        self.meta_seen = False
        self.cwd: str | None = None
        self.first_user_message: str | None = None
        self.first_user_seen = False
        self.first_visible_user_seen = False
        self.started_at: str | None = None
        self.first_timestamp: str | None = None

    def _identified(self) -> bool:
        return self.cwd is not None and self.started_at is not None and (self.engine == "claude" or self.meta_seen)

    @property
    def done(self) -> bool:
        """以後の行が値を変えないか。"""
        return self.first_visible_user_seen and self._identified()

    def wants(self, line: str) -> bool:
        """行をJSONとして解析する必要があるかを返す。"""
        if self.done:
            return False
        return not self._identified() or _USER_MARKER in line

    def feed(self, record: typing.Any) -> None:
        """`wants`が真を返した行を解析した値を受け取る。"""
        if not isinstance(record, dict):
            return
        if self.first_timestamp is None and isinstance(record.get("timestamp"), str):
            self.first_timestamp = record["timestamp"]
        if self.engine == "claude":
            if self.started_at is None:
                self.started_at = self.first_timestamp
            if self.cwd is None and isinstance(record.get("cwd"), str):
                self.cwd = record["cwd"]
            if not self.first_visible_user_seen and _is_user_record(record, self.engine):
                self.first_user_seen = True
                message = record.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                text = _as_text(content)
                if not is_runtime_generated(record) and not (text and is_runtime_inserted_text(text)):
                    self.first_visible_user_seen = True
                    self.first_user_message = _first_line(content)
            return
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return
        is_meta = record.get("type") == "session_meta"
        if is_meta and not self.meta_seen:
            self.meta_seen = True
            self.parent_thread_id = _codex_parent_thread_id(payload)
        if self.started_at is None and is_meta and isinstance(payload.get("timestamp"), str):
            self.started_at = payload["timestamp"]
        if self.cwd is None and is_meta and isinstance(payload.get("cwd"), str):
            self.cwd = payload["cwd"]
        if not self.first_visible_user_seen and _is_user_record(record, self.engine):
            self.first_user_seen = True
            content = payload.get("content")
            text = _as_text(content)
            if not (text and is_runtime_inserted_text(text)):
                self.first_visible_user_seen = True
                self.first_user_message = _first_line(content)

    def fields(self, *, readable: bool) -> SummaryFields:
        """求めた値を返す。記録を読み取れなかった場合は発話の有無を`None`とする。"""
        has_user = self.first_user_seen if readable else None
        return self.cwd, self.first_user_message, self.started_at or self.first_timestamp, has_user, self.parent_thread_id


@dataclasses.dataclass(frozen=True)
class RecordScan:
    """記録1件の1回の走査で求めた、一覧が使う値。"""

    fields: SummaryFields
    delegated_ids: frozenset[str]
    """子セッションIDの集合。`scan_record`へ`delegations=False`を渡した場合は求めずに空集合とする。"""


def scan_record(path: pathlib.Path, engine: str, *, delegations: bool = True) -> RecordScan:
    """記録を1回だけ開き、一覧が記録1件ごとに求める値を1つの走査で求める。

    一覧の記録単位の値を加えるときは、記録を別に開く関数を足さずにこの走査へ加える。
    行は要約と子セッションIDのどちらかが結果を変え得る場合だけJSONとして解析する。
    文字コードは要約側が不正なバイトを置換して読み、子セッションIDの側はUTF-8として復号できる行だけを使う。
    記録を読み取れない場合は、要約の発話の有無を`None`、子セッションIDを空集合とする。
    """
    summary = _SummaryScan(engine)
    collector = session_delegations.DelegationCollector(engine) if delegations else None
    readable = True
    try:
        with path.open("rb") as stream:
            for raw in stream:
                if collector is None and summary.done:
                    break
                try:
                    line = raw.decode("utf-8")
                    decodable = True
                except UnicodeDecodeError:
                    line = raw.decode("utf-8", errors="replace")
                    decodable = False
                summary_wants = summary.wants(line)
                delegation_wants = collector is not None and decodable and collector.wants(line)
                if not summary_wants and not delegation_wants:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if summary_wants:
                    summary.feed(record)
                if delegation_wants and collector is not None:
                    collector.feed(record)
    except OSError:
        readable = False
    delegated = collector.children if collector is not None and readable else frozenset()
    return RecordScan(fields=summary.fields(readable=readable), delegated_ids=delegated)


def summary_fields(path: pathlib.Path, engine: str) -> SummaryFields:
    """一覧の識別に使う作業ディレクトリ、最初の発話、開始日時、発話の有無および親threadを先頭から取得する。

    発話の有無は、ユーザー発話の記録行を1件でも持てば`True`とする。
    最初の発話が本文を持たない形式でも`True`とし、`first_user_message`がnullであることとは区別する。
    記録を読み取れない場合は判定できないため`None`を返す。
    親threadはCodex自身のサブエージェントの記録だけが持ち、最初の`session_meta`行の値を使う。
    2行目以降の`session_meta`行は親thread側のメタデータの写しであり、記録自身を表さないためである。
    """
    return scan_record(path, engine, delegations=False).fields


def has_user_message(path: pathlib.Path, engine: str) -> bool | None:
    """記録がユーザー発話の記録行を持つかを返す。読み取れない場合は`None`を返す。

    `"user"`を含まない行はユーザー発話になり得ないため、JSONとして解析しない。
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if _USER_MARKER not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and _is_user_record(record, engine):
                    return True
    except OSError:
        return None
    return False


_TRUSTED_AGE_NS = 2_000_000_000
"""解析結果を信用するために必要な、解析の開始時刻と更新時刻の差。秒単位とFATの2秒の時刻精度を覆う。"""


@dataclasses.dataclass(frozen=True)
class _IndexedRecord:
    """記録1件の無効化キーと解析結果。"""

    mtime_ns: int
    size: int
    trusted: bool
    """更新時刻の精度の範囲外で解析したため、無効化キーの一致だけで再利用できるか。"""
    fields: SummaryFields
    delegated_ids: frozenset[str]
    """子セッションIDの集合。`fields`と同じ走査で求める。"""


class RecordSummaryIndex:
    """セッション一覧が記録1件ごとに求める値を、記録の実パスごとに保持する索引。

    一覧は要求のたびに全記録を巡回するため、記録単位の解析結果を再利用しないと、変更のない記録まで
    毎回全行を読み直す。再利用の条件はワークアイテム一覧の`EntryIndex`と同じく、更新時刻（ナノ秒）と
    サイズが一致し、解析を始めた時点が更新時刻より2秒以上後であることとする。秒精度のファイルシステムで
    同じ秒に同じサイズのまま追記された記録を、更新時刻とサイズだけでは区別できないためである。
    `begin_scan`から`finish_scan`までの走査で参照しなかった記録は破棄し、保持件数を走査対象の記録数に
    追従させる。固定の上限を置くと、巡回する記録数が上限を超えた時点でLRUが全件を順に破棄し、再利用が止まる。
    走査が停止要求などで途中で終わった場合は破棄せずに残し、次の走査の終わりにまとめて破棄する。
    サーバーとリモートヘルパーの常駐モードが1プロセスに1つ持ち、複数スレッドから使えるようロックで保護する。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, _IndexedRecord] = {}
        self._seen: set[str] = set()

    def begin_scan(self) -> None:
        """走査の開始を記録し、走査で参照した記録の集合を空にする。"""
        with self._lock:
            self._seen = set()

    def finish_scan(self) -> None:
        """走査で参照しなかった記録の解析結果を破棄する。"""
        with self._lock:
            self._records = {key: value for key, value in self._records.items() if key in self._seen}

    def summary(self, path: pathlib.Path, engine: str) -> tuple[SummaryFields, os.stat_result | OSError]:
        """記録の`summary_fields`の値と、無効化の判定に使った`stat`の結果を返す。

        `stat`できない記録は保持せずに解析し、`stat`の結果の代わりにその例外を返す。
        """
        analysis_started_ns = time.time_ns()
        try:
            st = path.stat()
        except OSError as error:
            return scan_record(path, engine, delegations=False).fields, error
        key = str(path)
        with self._lock:
            self._seen.add(key)
            cached = self._records.get(key)
        if cached is not None and cached.trusted and (cached.mtime_ns, cached.size) == (st.st_mtime_ns, st.st_size):
            return cached.fields, st
        scan = scan_record(path, engine)
        record = _IndexedRecord(
            mtime_ns=st.st_mtime_ns,
            size=st.st_size,
            trusted=analysis_started_ns - st.st_mtime_ns >= _TRUSTED_AGE_NS,
            fields=scan.fields,
            delegated_ids=scan.delegated_ids,
        )
        with self._lock:
            self._records[key] = record
        return scan.fields, st

    def delegated_ids(self, path: pathlib.Path, engine: str) -> frozenset[str]:
        """記録が起動した子セッションIDの集合を返す。

        `summary`が要約と同じ走査で求めて保持した集合を、記録を開かずに返す。
        保持していない記録（`stat`できなかった記録など）だけ記録を読む。
        """
        with self._lock:
            cached = self._records.get(str(path))
        if cached is not None:
            return cached.delegated_ids
        return scan_record(path, engine).delegated_ids


class RecordChangeTracker:
    """記録の変更を、一覧の再取得と記録1件の更新の2種類の通知へ振り分ける。

    一覧の再取得を促すのは、一覧に載る項目の集合か表示する値が変わり得る変更に限る。
    一覧に載っている記録への追記では記録1件の更新だけを通知する。
    一覧の応答は大きく、進行中のセッションの追記ごとに再取得させると転送量が増えるためである。

    `listed`は記録ごとの発話の有無（一覧に載るか）を保持する。一覧の取得時に`prime`で初期化し、
    未知の記録は変更の観測時に判定する。発話は追記で消えないため、一度`True`となった記録は再判定しない。
    watchdogのスレッドから呼ばれるため、状態はロックで保護する。
    """

    def __init__(
        self,
        roots: typing.Sequence[tuple[pathlib.Path, str]],
        on_flush: typing.Callable[[bool, list[tuple[str, str]]], None],
        *,
        debounce_sec: float = DEFAULT_DEBOUNCE_SEC,
    ) -> None:
        self.roots = list(roots)
        self._on_flush = on_flush
        self._debounce_sec = debounce_sec
        self._lock = threading.Lock()
        self._listed: dict[str, bool] = {}
        self._pending_refresh = False
        self._pending_records: dict[tuple[str, str], None] = {}
        self._timer: threading.Timer | None = None
        self._stopped = False

    def prime(self, path: str, listed: bool) -> None:
        """一覧の取得で判定した発話の有無を保持する。"""
        with self._lock:
            self._listed[path] = listed

    def engine_of(self, path: pathlib.Path) -> str | None:
        """記録のパスが属するrootの実行系を返す。対象外のパスは`None`を返す。"""
        for root, engine in self.roots:
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if engine == "claude":
                if path.name.endswith(RECORD_SUFFIX) or path.name.endswith(_CLAUDE_META_SUFFIX):
                    return engine
                return None
            if path.name.startswith(CODEX_ROLLOUT_PREFIX) and path.name.endswith(RECORD_SUFFIX) and relative.parts:
                return engine
            return None
        return None

    def record_changed(self, path: pathlib.Path) -> None:
        """記録の作成または追記を反映する。"""
        engine = self.engine_of(path)
        if engine is None:
            return
        if not path.name.endswith(RECORD_SUFFIX):
            # サブエージェントのmetadataは親子関係を変えるため、一覧の再取得を促す。
            self._schedule(refresh=True)
            return
        key = str(path)
        with self._lock:
            known = self._listed.get(key)
        if known:
            self._schedule(record=(engine, key))
            return
        listed = has_user_message(path, engine)
        if listed is None:
            return
        with self._lock:
            self._listed[key] = listed
        # 未判定の記録は、一覧に載っていたかを確定できないため、発話があれば一覧の再取得を促す。
        self._schedule(refresh=listed, record=(engine, key))

    def record_removed(self, path: pathlib.Path) -> None:
        """記録の削除を反映する。一覧に載っていないと分かっている記録では通知しない。"""
        engine = self.engine_of(path)
        if engine is None:
            return
        key = str(path)
        with self._lock:
            known = self._listed.pop(key, None)
        if known is False:
            return
        self._schedule(refresh=True)

    def structure_changed(self) -> None:
        """ディレクトリの作成など、記録の集合が変わり得る変更を反映する。"""
        self._schedule(refresh=True)

    def _schedule(self, *, refresh: bool = False, record: tuple[str, str] | None = None) -> None:
        with self._lock:
            if self._stopped or (not refresh and record is None):
                return
            self._pending_refresh = self._pending_refresh or refresh
            if record is not None:
                self._pending_records[record] = None
            if self._timer is not None:
                return
            self._timer = threading.Timer(self._debounce_sec, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        """保留中の通知を1回にまとめて送る。"""
        with self._lock:
            self._timer = None
            if self._stopped:
                return
            refresh = self._pending_refresh
            records = list(self._pending_records)
            self._pending_refresh = False
            self._pending_records = {}
        if refresh or records:
            self._on_flush(refresh, records)

    def stop(self) -> None:
        """保留中の通知を破棄し、以後の通知を止める。"""
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class RecordWatch:
    """記録のrootをwatchdogで監視し、`RecordChangeTracker`へ変更を渡す。

    rootが起動時に存在しない場合は、存在する最も近い祖先を非再帰で監視し、
    ディレクトリの作成を契機に監視の登録をやり直す。監視の開始後に作成したrootの記録も検知するためである。
    登録をやり直すたびに、どの未作成rootの祖先でもなくなった非再帰の監視を外す。
    残すと祖先直下の無関係なディレクトリ作成が一覧の再取得通知になるためである。
    """

    def __init__(self, tracker: RecordChangeTracker) -> None:
        self.tracker = tracker
        self._observer: typing.Any = None
        self._handler: typing.Any = None
        self._event_types: typing.Any = None
        self._lock = threading.Lock()
        self._watches: dict[str, typing.Any] = {}
        self._recursive: set[str] = set()

    def start(self) -> None:
        """監視を開始する。watchdogはここで読み込む。"""
        import watchdog.events  # pylint: disable=import-outside-toplevel
        import watchdog.observers  # pylint: disable=import-outside-toplevel

        watch = self

        class Handler(watchdog.events.FileSystemEventHandler):
            """watchdogのイベントを記録の変更へ変換する。"""

            # リモートホストのPython 3.10でも読み込むため、3.12で導入された`typing.override`は付けない。
            def on_any_event(self, event: typing.Any) -> None:
                watch.handle(event)

        self._handler = Handler()
        self._event_types = watchdog.events
        self._observer = watchdog.observers.Observer()
        self._schedule_roots()
        self._observer.start()

    def stop(self) -> None:
        """監視スレッドを終了させる。"""
        self.tracker.stop()
        observer = self._observer
        if observer is None:
            return
        observer.stop()
        observer.join()
        self._observer = None

    def _schedule_roots(self) -> None:
        """各rootを再帰で、存在しないrootは最も近い祖先を非再帰で監視へ登録し、不要になった祖先の監視を外す。"""
        with self._lock:
            ancestors: set[str] = set()
            for root, _engine in self.tracker.roots:
                key = str(root)
                if key in self._recursive:
                    continue
                if root.is_dir():
                    self._add_watch(key, recursive=True)
                    self._recursive.add(key)
                    continue
                ancestor = root.parent
                while not ancestor.is_dir() and ancestor != ancestor.parent:
                    ancestor = ancestor.parent
                if ancestor.is_dir():
                    self._add_watch(str(ancestor), recursive=False)
                    ancestors.add(str(ancestor))
            for path, watch in list(self._watches.items()):
                if watch.is_recursive or path in self._recursive or path in ancestors:
                    continue
                self._observer.unschedule(watch)
                del self._watches[path]

    def _add_watch(self, path: str, *, recursive: bool) -> None:
        existing = self._watches.get(path)
        if existing is not None:
            if not recursive or existing.is_recursive:
                return
            self._observer.unschedule(existing)
        try:
            self._watches[path] = self._observer.schedule(self._handler, path, recursive=recursive)
        except OSError:
            self._watches.pop(path, None)

    def _in_watched_root(self, path: pathlib.Path) -> bool:
        with self._lock:
            recursive = list(self._recursive)
        return any(path.is_relative_to(root) for root in recursive)

    def handle(self, event: typing.Any) -> None:
        """1件のイベントを記録の変更へ変換する。"""
        events = self._event_types
        if isinstance(event, (events.FileOpenedEvent, events.FileClosedNoWriteEvent)):
            return
        if event.is_directory:
            # root配下のディレクトリの作成は、その中の記録の作成イベントで扱う。
            # rootの外（祖先の監視）で作成したディレクトリだけが、監視の登録をやり直す契機となる。
            if isinstance(event, (events.DirCreatedEvent, events.DirMovedEvent)) and not self._in_watched_root(
                pathlib.Path(str(event.src_path))
            ):
                self._schedule_roots()
                self.tracker.structure_changed()
            return
        src = pathlib.Path(str(event.src_path))
        if isinstance(event, events.FileMovedEvent):
            self.tracker.record_removed(src)
            self.tracker.record_changed(pathlib.Path(str(event.dest_path)))
            return
        if isinstance(event, events.FileDeletedEvent):
            self.tracker.record_removed(src)
            return
        self.tracker.record_changed(src)
