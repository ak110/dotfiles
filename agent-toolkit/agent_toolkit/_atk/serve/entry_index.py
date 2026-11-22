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
    return _state_paths.state_dir() / "serve" / "wi-entry-index.json"


class _ParsedFile:
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
        if self._text is None:
            with self._lock:
                if self._text is None:
                    self._text = self._read()
        return self._text

    @property
    def text_folded(self) -> str:
        if self._text_folded is None:
            self._text_folded = self.text.casefold()
        return self._text_folded

    @property
    def body(self) -> str:
        return self.text[self._body_offset :]

    @property
    def body_offset(self) -> int:
        return self._body_offset

    def _read(self) -> str:
        try:
            text = pathlib.Path(self._path).read_text(encoding="utf-8")
            current = os.stat(self._path)
        except OSError, UnicodeDecodeError:
            return ""
        if (current.st_mtime_ns, current.st_size) != self._stamp:
            parsed = frontmatter.parse_frontmatter(text)
            body = parsed[1] if parsed is not None else text.removeprefix("\ufeff")
            self._body_offset = len(text) - len(body)
        return text


@dataclasses.dataclass(frozen=True)
class IndexedEntry:
    state: str
    path: pathlib.Path
    parsed: _ParsedFile
    updated_at: str

    @property
    def text(self) -> str:
        return self.parsed.text

    @property
    def text_folded(self) -> str:
        return self.parsed.text_folded

    @property
    def metadata(self) -> dict[str, typing.Any]:
        return self.parsed.metadata

    @property
    def body(self) -> str:
        return self.parsed.body

    @property
    def kind(self) -> str | None:
        return self.parsed.kind

    @property
    def derived(self) -> dict[str, typing.Any]:
        return self.parsed.derived


@dataclasses.dataclass(frozen=True)
class _CacheEntry:
    mtime_ns: int
    size: int
    parsed: _ParsedFile
    trusted: bool
    """更新から2秒以上後に解析しており、無効化キーの一致だけで再利用できるか。"""
    updated_at: str
    """更新時刻のISO 8601表記。無効化キーが一致する間は同じ値になるため、解析結果とともに保持する。"""


def _warning(path: pathlib.Path, state: str, reason: str, next_action: str) -> dict[str, str]:
    return {
        "state": state,
        "filename": path.name,
        "path": os.fspath(path),
        "reason": reason,
        "next_action": next_action,
    }


def _unreadable_next_action(path: pathlib.Path) -> str:
    """ファイル情報または本文の読取に失敗したファイルに対する確認の操作を返す。"""
    return f"{path}が実在し、atk serveを実行するOSアカウントから読み取れる権限になっているかを確認する"


def _json_round_trips(metadata: dict[str, typing.Any]) -> bool:
    try:
        return json.loads(json.dumps(metadata, ensure_ascii=False)) == metadata
    except TypeError, ValueError:
        return False


class EntryIndex:
    def __init__(self, private_notes: pathlib.Path, *, cache_path: pathlib.Path | None = None) -> None:
        self._private_notes = private_notes
        self._cache_path = cache_path
        self._saved: dict[str, list[typing.Any]] = self._load_saved(cache_path) if cache_path is not None else {}
        self._saved_dirty = False
        self._saved_at: float | None = None

        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._tracking = False
        self._changed = True
        self._last_full: tuple[float, list[IndexedEntry], list[dict[str, str]]] | None = None

        self._pending: set[pathlib.Path] = set()

    def enable_change_tracking(self) -> None:
        """ファイル監視の変更通知を受ける前提で、変更の無い間の全件走査を省く。

        監視が動いていない構成（試験や単発の利用）では呼ばず、毎回全件を走査する。
        通知の取りこぼしに備え、変更の通知が無くても`_RESCAN_SECONDS`ごとに全件を走査し直す。
        """
        self._tracking = True

    def mark_changed(self) -> None:
        self._changed = True

    def scan(
        self, states: typing.Iterable[str], *, stop: threading.Event | None = None
    ) -> tuple[list[IndexedEntry], list[dict[str, str]]]:
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
                    reusable = self._refresh_pending_locked(reusable)
                    self._last_full = reusable
                return [entry for entry in reusable[1] if entry.state in selected], list(reusable[2])

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
            self._saved_dirty = True

    def _index_path(
        self,
        path: pathlib.Path,
        previous_cache: dict[str, _CacheEntry],
        real_directories: dict[str, str],
        scan_started_ns: int,
        warnings: list[dict[str, str]],
    ) -> tuple[str, _CacheEntry, IndexedEntry | None] | None:
        state = "unknown"
        try:
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
            warnings.append(_warning(path, state, "ファイル情報を読み取れません", _unreadable_next_action(path)))
            return None
        cached = previous_cache.get(real_path)
        reused = (
            cached is not None
            and cached.trusted
            and (cached.mtime_ns, cached.size) == (file_stat.st_mtime_ns, file_stat.st_size)
        )
        saved = None if reused else self._saved.get(real_path)
        if saved is not None and (saved[0], saved[1]) == (file_stat.st_mtime_ns, file_stat.st_size):
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
                warnings.append(
                    _warning(
                        path,
                        state,
                        "UTF-8として読み取れません",
                        f"{path}を別名で複製して元の内容を保全し、内容をUTF-8として保存し直す",
                    )
                )
                return None
            except OSError:
                warnings.append(_warning(path, state, "ファイルを読み取れません", _unreadable_next_action(path)))
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

            try:
                current_stat = path.stat()
            except FileNotFoundError:
                return None
            except OSError:
                warnings.append(_warning(path, state, "ファイル情報を読み取れません", _unreadable_next_action(path)))
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
            # 不正なstateの修正先は発生源（`wi/layout.py`）が持つ案内を引き継ぐ。
            warnings.append(_warning(path, state, str(error), error.next_action))
            return real_path, cached, None
        return (
            real_path,
            cached,
            IndexedEntry(state=state, path=path, parsed=cached.parsed, updated_at=cached.updated_at),
        )
