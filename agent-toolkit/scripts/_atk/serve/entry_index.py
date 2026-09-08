"""ワークアイテム一覧向けのプロセス内索引。

索引は実パス、更新時刻（ナノ秒）及びファイルサイズが一致する間だけ解析結果を再利用する。
いずれかが変わったファイルは次の走査で読み直し、走査対象から消えたファイルは索引から除く。
"""

import dataclasses
import datetime
import os
import pathlib
import threading
import typing

from _atk.wi import common, frontmatter


@dataclasses.dataclass(frozen=True)
class IndexedEntry:
    """一覧走査で得たエントリの解析済み情報。"""

    state: str
    path: pathlib.Path
    text: str
    metadata: dict[str, typing.Any]
    kind: str | None
    updated_at: str


@dataclasses.dataclass(frozen=True)
class _ParsedFile:
    """状態ディレクトリに依存しないファイル単位の解析結果。"""

    text: str
    metadata: dict[str, typing.Any]
    kind: str | None


@dataclasses.dataclass(frozen=True)
class _CacheEntry:
    """無効化キーと解析結果。"""

    mtime_ns: int
    size: int
    parsed: _ParsedFile


class EntryIndex:
    """状態ディレクトリを走査し、変更されたMarkdownだけを解析する。"""

    def __init__(self, private_notes: pathlib.Path) -> None:
        self._private_notes = private_notes
        self._cache: dict[pathlib.Path, _CacheEntry] = {}
        self._lock = threading.Lock()

    def scan(self, states: typing.Iterable[str]) -> tuple[list[IndexedEntry], list[dict[str, str]]]:
        """指定状態のエントリと、読み取りから除外したファイルの警告を返す。"""
        with self._lock:
            return self._scan_locked(states)

    def _scan_locked(self, states: typing.Iterable[str]) -> tuple[list[IndexedEntry], list[dict[str, str]]]:
        result: list[IndexedEntry] = []
        warnings: list[dict[str, str]] = []
        next_cache: dict[pathlib.Path, _CacheEntry] = {}
        for state in states:
            directory = self._private_notes / state
            try:
                with os.scandir(directory) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name)
            except FileNotFoundError:
                continue
            for directory_entry in entries:
                path = pathlib.Path(directory_entry.path)
                if path.suffix != ".md":
                    continue
                try:
                    file_stat = directory_entry.stat()
                    real_path = path.resolve()
                except FileNotFoundError:
                    continue
                except OSError:
                    warnings.append({"filename": path.name, "reason": "ファイル情報を読み取れません"})
                    continue
                cached = self._cache.get(real_path)
                if cached is None or (cached.mtime_ns, cached.size) != (file_stat.st_mtime_ns, file_stat.st_size):
                    try:
                        text = path.read_text(encoding="utf-8")
                    except FileNotFoundError:
                        continue
                    except UnicodeDecodeError:
                        warnings.append({"filename": path.name, "reason": "UTF-8として読み取れません"})
                        continue
                    except OSError:
                        warnings.append({"filename": path.name, "reason": "ファイルを読み取れません"})
                        continue
                    parsed_frontmatter = frontmatter.parse_frontmatter(text)
                    metadata = parsed_frontmatter[0] if parsed_frontmatter is not None else {}
                    kind = common.entry_type_from_metadata(path, metadata) if parsed_frontmatter is not None else None
                    parsed = _ParsedFile(text=text, metadata=metadata, kind=kind)
                    cached = _CacheEntry(mtime_ns=file_stat.st_mtime_ns, size=file_stat.st_size, parsed=parsed)
                try:
                    current_stat = path.stat()
                except FileNotFoundError:
                    continue
                except OSError:
                    warnings.append({"filename": path.name, "reason": "ファイル情報を読み取れません"})
                    continue
                if (current_stat.st_mtime_ns, current_stat.st_size) != (file_stat.st_mtime_ns, file_stat.st_size):
                    continue
                next_cache[real_path] = cached
                result.append(
                    IndexedEntry(
                        state=state,
                        path=path,
                        text=cached.parsed.text,
                        metadata=cached.parsed.metadata,
                        kind=cached.parsed.kind,
                        updated_at=datetime.datetime.fromtimestamp(file_stat.st_mtime, tz=datetime.UTC).isoformat(),
                    )
                )
        self._cache = next_cache
        return result, warnings
