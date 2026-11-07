"""ワークアイテム一覧向けのプロセス内索引。

索引は、実パス、更新時刻（ナノ秒）およびファイルサイズが一致し、かつその解析結果が信用できる場合だけ再利用する。
解析結果が信用できるのは、解析した走査の開始時刻がファイルの更新時刻より2秒以上後である場合とする。
いずれかを満たさないファイルは次の走査で読み直し、走査対象から消えたファイルは索引から除く。

`st_mtime_ns`の単位はナノ秒でも、値の精度はファイルシステムが決める（ext3などは1秒、FATは2秒）。
秒単位の精度では、同じ秒の中で同じサイズのまま本文を書き換えると更新時刻もサイズも変わらないため、
更新時刻とサイズの一致だけでは書き換えを区別できない。
更新から2秒以内に読んだ解析結果は信用せずに次の走査で読み直し、書き換えが止まって2秒が過ぎた後に
読み直した解析結果から再利用する。追加で読み直すのは更新から2秒以内のファイルだけであり、
変更の無いファイルを読み直さない高速化は維持する。

索引は年月固定配置の実パスごとに保持し、保存されたstateに一致する項目だけを返す。
異なる状態を交互に表示しても、変更のないファイルの解析結果を共有する。
本文から導く値のうち計算の重いもの（終了項目の処理日時など）は、解析結果ごとの`derived`へ
呼び出し側が保存し、ファイルが変わらない限り再計算しない。

`cache_path`を渡すと、信用できる解析結果（frontmatterと本文の開始位置）をファイルへ保存し、次の起動で読み込む。
`atk serve`の起動直後の最初の一覧は全件の走査を待ち、数千件のfrontmatterの解析と本文の読込に数秒かかるためである。
読み込んだ解析結果も実パス・更新時刻・サイズの一致を確かめてから使い、本文は一覧の表示や検索で必要になった時点で読む。
本文を読んだ時点で無効化キーが変わっていれば、読んだ本文から本文部分を求め直す（frontmatterは次の走査で更新する）。
"""

import dataclasses
import datetime
import json
import os
import pathlib
import stat
import threading
import time
import typing

from agent_toolkit._atk.wi import constants as wi_constants
from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._atk.wi.layout import entry_state_from_metadata, iter_entry_paths
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.next_action import ActionableError

_RESCAN_SECONDS = 30.0
"""変更の通知が無くても全件を走査し直す間隔。通知の取りこぼしで一覧が古いまま残る期間の上限とする。"""

_TRUSTED_AGE_NS = 2_000_000_000
"""解析結果を信用するために必要な、走査開始時刻と更新時刻の差。更新時刻の精度が1秒または2秒（FAT）のファイルシステムでも書き換えを区別できる値とする。"""


_CACHE_VERSION = 1

_SAVE_INTERVAL_SEC = 60.0
"""保存した解析結果を書き直す最短の間隔。最初の保存は間隔を待たない。"""


def default_cache_path() -> pathlib.Path:
    """`atk serve`が解析結果を保存するパスを返す。"""
    return _state_paths.state_dir() / "serve" / "wi-entry-index.json"


class _ParsedFile:
    """状態ディレクトリによらず共通のファイル単位の解析結果。本文は必要になった時点で読む。"""

    __slots__ = (
        "_body_offset",
        "_lock",
        "_path",
        "_stamp",
        "_text",
        "_text_folded",
        "derived",
        "frontmatter_broken",
        "kind",
        "metadata",
    )

    def __init__(
        self,
        path: str,
        stamp: tuple[int, int],
        *,
        metadata: dict[str, typing.Any],
        body_offset: int,
        kind: str | None,
        frontmatter_broken: bool,
        text: str | None = None,
    ) -> None:
        self._path = path
        self._stamp = stamp
        self.metadata = metadata
        self._body_offset = body_offset
        self.kind = kind
        self.frontmatter_broken = frontmatter_broken
        self._text = text
        self._text_folded: str | None = None
        self._lock = threading.Lock()
        self.derived: dict[str, typing.Any] = {}

    @property
    def text(self) -> str:
        """本文全体。保存した解析結果から作った項目は最初の参照で読む。"""
        if self._text is None:
            with self._lock:
                if self._text is None:
                    self._text = self._read()
        return self._text

    @property
    def text_folded(self) -> str:
        """検索用に大文字小文字を畳み込んだ本文全体。"""
        if self._text_folded is None:
            self._text_folded = self.text.casefold()
        return self._text_folded

    @property
    def body(self) -> str:
        """frontmatterの後の本文。"""
        return self.text[self._body_offset :]

    @property
    def body_offset(self) -> int:
        """本文全体のうちfrontmatterの後の本文が始まる位置。"""
        return self._body_offset

    def _read(self) -> str:
        try:
            text = pathlib.Path(self._path).read_text(encoding="utf-8")
            current = os.stat(self._path)
        except OSError, UnicodeDecodeError:
            # 読めなくなった項目は次の走査が警告とともに外すため、ここでは空の本文として扱う。
            return ""
        if (current.st_mtime_ns, current.st_size) != self._stamp:
            # 走査の後に書き換わった本文では保存した本文の開始位置が合わないため、読んだ本文から求め直す。
            parsed = frontmatter.parse_frontmatter(text)
            body = parsed[1] if parsed is not None else text.removeprefix("\ufeff")
            self._body_offset = len(text) - len(body)
        return text


@dataclasses.dataclass(frozen=True)
class IndexedEntry:
    """一覧走査で得たエントリの解析済み情報。"""

    state: str
    path: pathlib.Path
    parsed: _ParsedFile
    updated_at: str

    @property
    def text(self) -> str:
        """本文全体。"""
        return self.parsed.text

    @property
    def text_folded(self) -> str:
        """検索用に大文字小文字を畳み込んだ本文全体。"""
        return self.parsed.text_folded

    @property
    def metadata(self) -> dict[str, typing.Any]:
        """frontmatterの値。"""
        return self.parsed.metadata

    @property
    def body(self) -> str:
        """frontmatterの後の本文。"""
        return self.parsed.body

    @property
    def kind(self) -> str | None:
        """正規化した種別。"""
        return self.parsed.kind

    @property
    def derived(self) -> dict[str, typing.Any]:
        """本文から導いた値の保存先。同じ解析結果を再利用する間は同じ辞書を共有する。"""
        return self.parsed.derived


@dataclasses.dataclass(frozen=True)
class _CacheEntry:
    """無効化キーと解析結果。"""

    mtime_ns: int
    size: int
    parsed: _ParsedFile
    trusted: bool
    """更新から2秒以上後に解析しており、無効化キーの一致だけで再利用できるか。"""
    updated_at: str
    """更新時刻のISO 8601表記。無効化キーが一致する間は同じ値になるため、解析結果とともに保持する。"""


def _json_round_trips(metadata: dict[str, typing.Any]) -> bool:
    """frontmatterの値をJSONで保存して読み戻しても同じ値になるかを返す（日時や非文字列のキーを含む項目は保存しない）。"""
    try:
        return json.loads(json.dumps(metadata, ensure_ascii=False)) == metadata
    except TypeError, ValueError:
        return False


class EntryIndex:
    """年月固定配置を走査し、変更されたMarkdownだけを解析する。"""

    def __init__(self, private_notes: pathlib.Path, *, cache_path: pathlib.Path | None = None) -> None:
        self._private_notes = private_notes
        self._cache_path = cache_path
        self._saved: dict[str, list[typing.Any]] = self._load_saved(cache_path) if cache_path is not None else {}
        self._saved_dirty = False
        self._saved_at: float | None = None
        # 実パスの文字列をキーとして保持する。`Path`をキーにすると比較のたびに正規化を伴う。
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._tracking = False
        self._changed = True
        self._last_full: tuple[float, list[IndexedEntry], list[dict[str, str]]] | None = None
        # 直近の全件走査で解析結果をまだ信用できなかった項目。変更の通知が無い間はこれだけを読み直す。
        self._pending: set[pathlib.Path] = set()

    def enable_change_tracking(self) -> None:
        """ファイル監視の変更通知を受ける前提で、変更の無い間の全件走査を省く。

        監視が動いていない構成（試験や単発の利用）では呼ばず、毎回全件を走査する。
        通知の取りこぼしに備え、変更の通知が無くても`_RESCAN_SECONDS`ごとに全件を走査し直す。
        """
        self._tracking = True

    def mark_changed(self) -> None:
        """保存先のMarkdownが変わったことを記録し、次の走査で全件を確かめさせる。"""
        self._changed = True

    def scan(
        self, states: typing.Iterable[str], *, stop: threading.Event | None = None
    ) -> tuple[list[IndexedEntry], list[dict[str, str]]]:
        """指定状態のエントリと、読み取りから除外したファイルの警告を返す。

        `stop`が設定されたら走査を打ち切り、索引を更新せずに空の結果を返す（停止要求を待たせないため）。
        """
        selected = set(states)
        with self._lock:
            reusable = self._last_full
            if (
                self._tracking
                and not self._changed
                and reusable is not None
                and time.monotonic() - reusable[0] < _RESCAN_SECONDS
            ):
                if self._pending:
                    # 書込直後の項目だけが未確定の間は、その項目だけを読み直す。
                    # 外部の追加や保存の直後は画面の再読込が続けて届き、毎回全件を走査すると1回ごとに秒単位かかる。
                    reusable = self._refresh_pending_locked(reusable)
                    self._last_full = reusable
                return [entry for entry in reusable[1] if entry.state in selected], list(reusable[2])
            # 走査中に届いた変更通知を取りこぼさないよう、走査の前に変更の記録を戻す。
            self._changed = False
            started = time.monotonic()
            entries, warnings = self._scan_locked(stop)
            if stop is not None and stop.is_set():
                self._changed = True
                return [], []
            self._last_full = (started, entries, warnings)
            return [entry for entry in entries if entry.state in selected], list(warnings)

    def _refresh_pending_locked(
        self, reusable: tuple[float, list[IndexedEntry], list[dict[str, str]]]
    ) -> tuple[float, list[IndexedEntry], list[dict[str, str]]]:
        """直近の全件走査の結果のうち、信用待ちの項目だけを読み直した結果を返す。

        全件走査の時刻は引き継ぎ、通知の取りこぼしに備えた定期の全件走査を遅らせない。
        """
        pending = self._pending
        names = {path.name for path in pending}
        refreshed: dict[pathlib.Path, IndexedEntry] = {}
        warnings = [warning for warning in reusable[2] if warning["filename"] not in names]
        scan_started_ns = time.time_ns()
        real_directories: dict[str, str] = {}
        still_pending: set[pathlib.Path] = set()
        for path in sorted(pending):
            indexed = self._index_path(path, self._cache, real_directories, scan_started_ns, warnings)
            if indexed is None:
                continue
            real_path, cached, entry = indexed
            self._cache[real_path] = cached
            if not cached.trusted:
                still_pending.add(path)
            if entry is not None:
                refreshed[path] = entry
        # 全件走査の並び順を保ち、読み直した項目だけを差し替える。消えた項目は除く。
        entries = [
            refreshed.get(entry.path, entry) for entry in reusable[1] if entry.path not in pending or entry.path in refreshed
        ]
        listed = {entry.path for entry in entries}
        entries.extend(entry for path, entry in sorted(refreshed.items()) if path not in listed)
        self._pending = still_pending
        return reusable[0], entries, warnings

    def _scan_locked(self, stop: threading.Event | None) -> tuple[list[IndexedEntry], list[dict[str, str]]]:
        result: list[IndexedEntry] = []
        warnings: list[dict[str, str]] = []
        # ファイルのstatより前に取得し、解析結果の信用判定を保守的にする。
        scan_started_ns = time.time_ns()
        real_directories: dict[str, str] = {}
        previous_cache = self._cache
        next_cache: dict[str, _CacheEntry] = {}
        pending: set[pathlib.Path] = set()
        for path in iter_entry_paths(self._private_notes):
            if stop is not None and stop.is_set():
                return [], []
            indexed = self._index_path(path, previous_cache, real_directories, scan_started_ns, warnings)
            if indexed is None:
                continue
            real_path, cached, entry = indexed
            next_cache[real_path] = cached
            if not cached.trusted:
                pending.add(path)
            if entry is not None:
                result.append(entry)
        self._cache = next_cache
        self._pending = pending
        if self._cache_path is not None:
            kept = {key: value for key, value in self._saved.items() if key in next_cache}
            self._saved_dirty |= len(kept) != len(self._saved)
            self._saved = kept
            self._save_if_due()
        return result, warnings

    @staticmethod
    def _load_saved(path: pathlib.Path) -> dict[str, list[typing.Any]]:
        """保存した解析結果を読む。不在・破損・版違いは空から始める（補助の記録であるため）。"""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except OSError, ValueError:
            return {}
        if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION:
            return {}
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return {}
        return {
            key: value
            for key, value in entries.items()
            if isinstance(value, list)
            and len(value) == 6
            and isinstance(value[0], int)
            and isinstance(value[1], int)
            and isinstance(value[2], dict)
            and isinstance(value[3], int)
            and (value[4] is None or isinstance(value[4], str))
            and isinstance(value[5], bool)
        }

    def _save_if_due(self) -> None:
        """解析結果が変わっていれば、最初は直ちに、以後は間隔を置いて保存する。失敗は一覧の取得を失敗させない。"""
        if self._cache_path is None or not self._saved_dirty:
            return
        if self._saved_at is not None and time.monotonic() - self._saved_at < _SAVE_INTERVAL_SEC:
            return
        self._saved_dirty = False
        self._saved_at = time.monotonic()
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(
                self._cache_path,
                json.dumps({"version": _CACHE_VERSION, "entries": self._saved}, ensure_ascii=False, separators=(",", ":")),
            )
        except OSError:
            # 保存できない場合も一覧は返せる。次の起動で全件を解析し直すだけである。
            self._saved_dirty = True

    def _index_path(
        self,
        path: pathlib.Path,
        previous_cache: dict[str, _CacheEntry],
        real_directories: dict[str, str],
        scan_started_ns: int,
        warnings: list[dict[str, str]],
    ) -> tuple[str, _CacheEntry, IndexedEntry | None] | None:
        """1件の解析結果を返す。読めないファイルは警告へ加えて`None`、状態が不正な項目は項目を`None`にする。"""
        state = "unknown"
        try:
            # 数千件の走査で1件ごとの`Path`の生成とシステムコールを減らすため、リンクでなければ`lstat`の結果を使う。
            file_stat = os.lstat(path)
            if stat.S_ISLNK(file_stat.st_mode):
                file_stat = path.stat()
                real_path = str(path.resolve())
            else:
                directory, name = os.path.split(os.fspath(path))
                if directory not in real_directories:
                    real_directories[directory] = os.path.realpath(directory)
                real_path = os.path.join(real_directories[directory], name)
        except FileNotFoundError:
            return None
        except OSError:
            warnings.append({"state": state, "filename": path.name, "reason": "ファイル情報を読み取れません"})
            return None
        cached = previous_cache.get(real_path)
        reused = (
            cached is not None
            and cached.trusted
            and (cached.mtime_ns, cached.size) == (file_stat.st_mtime_ns, file_stat.st_size)
        )
        saved = None if reused else self._saved.get(real_path)
        if saved is not None and (saved[0], saved[1]) == (file_stat.st_mtime_ns, file_stat.st_size):
            # 前回の起動で信用できた解析結果を使い、本文は必要になった時点で読む。
            cached = _CacheEntry(
                mtime_ns=file_stat.st_mtime_ns,
                size=file_stat.st_size,
                parsed=_ParsedFile(
                    os.fspath(path),
                    (file_stat.st_mtime_ns, file_stat.st_size),
                    metadata=saved[2],
                    body_offset=saved[3],
                    kind=saved[4],
                    frontmatter_broken=saved[5],
                ),
                trusted=True,
                updated_at=datetime.datetime.fromtimestamp(file_stat.st_mtime, tz=datetime.UTC).isoformat(),
            )
        elif not reused:
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return None
            except UnicodeDecodeError:
                warnings.append({"state": state, "filename": path.name, "reason": "UTF-8として読み取れません"})
                return None
            except OSError:
                warnings.append({"state": state, "filename": path.name, "reason": "ファイルを読み取れません"})
                return None
            parsed_frontmatter = frontmatter.parse_frontmatter(text)
            metadata = parsed_frontmatter[0] if parsed_frontmatter is not None else {}
            body = parsed_frontmatter[1] if parsed_frontmatter is not None else text.removeprefix("\ufeff")
            kind = wi_constants.normalized_wi_type(metadata.get("type"))
            parsed = _ParsedFile(
                os.fspath(path),
                (file_stat.st_mtime_ns, file_stat.st_size),
                metadata=metadata,
                body_offset=len(text) - len(body),
                kind=kind,
                frontmatter_broken=parsed_frontmatter is None,
                text=text,
            )
            cached = _CacheEntry(
                mtime_ns=file_stat.st_mtime_ns,
                size=file_stat.st_size,
                parsed=parsed,
                trusted=scan_started_ns - file_stat.st_mtime_ns >= _TRUSTED_AGE_NS,
                updated_at=datetime.datetime.fromtimestamp(file_stat.st_mtime, tz=datetime.UTC).isoformat(),
            )
            # 読み取りの間に書き換わったファイルは、読んだ本文と無効化キーが対応しないため今回は除く。
            # 解析結果を再利用したファイルは本文を読まないため、この確認を要さない。
            try:
                current_stat = path.stat()
            except FileNotFoundError:
                return None
            except OSError:
                warnings.append({"state": state, "filename": path.name, "reason": "ファイル情報を読み取れません"})
                return None
            if (current_stat.st_mtime_ns, current_stat.st_size) != (file_stat.st_mtime_ns, file_stat.st_size):
                return None
            if cached.trusted and self._cache_path is not None and _json_round_trips(metadata):
                self._saved[real_path] = [
                    file_stat.st_mtime_ns,
                    file_stat.st_size,
                    metadata,
                    parsed.body_offset,
                    kind,
                    parsed.frontmatter_broken,
                ]
                self._saved_dirty = True
        assert cached is not None
        try:
            state = "inbox" if cached.parsed.frontmatter_broken else entry_state_from_metadata(path, cached.parsed.metadata)
        except ActionableError as error:
            warnings.append({"state": state, "filename": path.name, "reason": str(error)})
            return real_path, cached, None
        return (
            real_path,
            cached,
            IndexedEntry(state=state, path=path, parsed=cached.parsed, updated_at=cached.updated_at),
        )
