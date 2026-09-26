"""ワークアイテム一覧向けのプロセス内索引。

索引は、実パス、更新時刻（ナノ秒）及びファイルサイズが一致し、かつその解析結果が信用できる場合だけ再利用する。
解析結果が信用できるのは、解析した走査の開始時刻がファイルの更新時刻より2秒以上後である場合とする。
いずれかを満たさないファイルは次の走査で読み直し、走査対象から消えたファイルは索引から除く。

`st_mtime_ns`の単位はナノ秒でも、値の精度はファイルシステムが決める（ext3などは1秒、FATは2秒）。
秒単位の精度では、同じ秒の中で同じサイズのまま本文を書き換えると更新時刻もサイズも変わらないため、
更新時刻とサイズの一致だけでは書き換えを区別できない。
更新から2秒以内に読んだ解析結果は信用せずに次の走査で読み直し、書き換えが止まって2秒が過ぎた後に
読み直した解析結果から再利用する。追加で読み直すのは更新から2秒以内のファイルだけであり、
変更の無いファイルを読み直さない高速化は維持する。

索引は状態ディレクトリごとに保持し、走査した状態の分だけを差し替える。activeとadoptedのように
異なる状態を交互に走査しても、走査していない状態の索引は保持する。
本文から導く値のうち計算の重いもの（終端項目の処理日時など）は、解析結果ごとの`derived`へ
呼び出し側が保存し、ファイルが変わらない限り再計算しない。
"""

import dataclasses
import datetime
import os
import pathlib
import threading
import time
import typing

from agent_toolkit._atk.wi import common, frontmatter

_TRUSTED_AGE_NS = 2_000_000_000
"""解析結果を信用するために必要な、走査開始時刻と更新時刻の差。秒単位の精度とFATの2秒精度を覆う。"""


@dataclasses.dataclass(frozen=True)
class IndexedEntry:
    """一覧走査で得たエントリの解析済み情報。"""

    state: str
    path: pathlib.Path
    text: str
    text_folded: str
    metadata: dict[str, typing.Any]
    kind: str | None
    updated_at: str
    derived: dict[str, typing.Any]
    """本文から導いた値の保存先。同じ解析結果を再利用する間は同じ辞書を共有する。"""


@dataclasses.dataclass(frozen=True)
class _ParsedFile:
    """状態ディレクトリに依存しないファイル単位の解析結果。"""

    text: str
    text_folded: str
    metadata: dict[str, typing.Any]
    kind: str | None
    derived: dict[str, typing.Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class _CacheEntry:
    """無効化キーと解析結果。"""

    mtime_ns: int
    size: int
    parsed: _ParsedFile
    trusted: bool
    """更新時刻の精度の範囲外で解析したため、無効化キーの一致だけで再利用できるか。"""


class EntryIndex:
    """状態ディレクトリを走査し、変更されたMarkdownだけを解析する。"""

    def __init__(self, private_notes: pathlib.Path) -> None:
        self._private_notes = private_notes
        self._cache: dict[str, dict[pathlib.Path, _CacheEntry]] = {}
        self._lock = threading.Lock()

    def scan(self, states: typing.Iterable[str]) -> tuple[list[IndexedEntry], list[dict[str, str]]]:
        """指定状態のエントリと、読み取りから除外したファイルの警告を返す。"""
        with self._lock:
            return self._scan_locked(states)

    def _scan_locked(self, states: typing.Iterable[str]) -> tuple[list[IndexedEntry], list[dict[str, str]]]:
        result: list[IndexedEntry] = []
        warnings: list[dict[str, str]] = []
        # ファイルのstatより前に取得し、解析結果の信用判定を保守的にする。
        scan_started_ns = time.time_ns()
        for state in states:
            previous_cache = self._cache.get(state, {})
            next_cache: dict[pathlib.Path, _CacheEntry] = {}
            self._cache[state] = next_cache
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
                cached = previous_cache.get(real_path)
                if (
                    cached is None
                    or not cached.trusted
                    or (cached.mtime_ns, cached.size) != (file_stat.st_mtime_ns, file_stat.st_size)
                ):
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
                    parsed = _ParsedFile(text=text, text_folded=text.casefold(), metadata=metadata, kind=kind)
                    cached = _CacheEntry(
                        mtime_ns=file_stat.st_mtime_ns,
                        size=file_stat.st_size,
                        parsed=parsed,
                        trusted=scan_started_ns - file_stat.st_mtime_ns >= _TRUSTED_AGE_NS,
                    )
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
                        text_folded=cached.parsed.text_folded,
                        metadata=cached.parsed.metadata,
                        kind=cached.parsed.kind,
                        updated_at=datetime.datetime.fromtimestamp(file_stat.st_mtime, tz=datetime.UTC).isoformat(),
                        derived=cached.parsed.derived,
                    )
                )
        return result, warnings
