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

索引は年月固定配置の実パスごとに保持し、保存されたstateで結果を絞り込む。
異なる状態を交互に表示しても、変更のないファイルの解析結果を共有する。
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

from agent_toolkit._atk.wi import constants as wi_constants
from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._atk.wi.layout import entry_state_from_metadata, iter_entry_paths
from agent_toolkit._common.next_action import ActionableError

_TRUSTED_AGE_NS = 2_000_000_000
"""解析結果を信用するために必要な、走査開始時刻と更新時刻の差。更新時刻の精度が1秒または2秒（FAT）のファイルシステムでも書き換えを区別できる値とする。"""


@dataclasses.dataclass(frozen=True)
class IndexedEntry:
    """一覧走査で得たエントリの解析済み情報。"""

    state: str
    path: pathlib.Path
    text: str
    text_folded: str
    metadata: dict[str, typing.Any]
    body: str
    kind: str | None
    updated_at: str
    derived: dict[str, typing.Any]
    """本文から導いた値の保存先。同じ解析結果を再利用する間は同じ辞書を共有する。"""


@dataclasses.dataclass(frozen=True)
class _ParsedFile:
    """状態ディレクトリによらず共通のファイル単位の解析結果。"""

    text: str
    text_folded: str
    metadata: dict[str, typing.Any]
    body: str
    kind: str | None
    frontmatter_broken: bool
    derived: dict[str, typing.Any] = dataclasses.field(default_factory=dict)


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


class EntryIndex:
    """年月固定配置を走査し、変更されたMarkdownだけを解析する。"""

    def __init__(self, private_notes: pathlib.Path) -> None:
        self._private_notes = private_notes
        # 実パスの文字列をキーとして保持する。`Path`をキーにすると比較のたびに正規化を伴う。
        self._cache: dict[str, _CacheEntry] = {}
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
        real_directories: dict[pathlib.Path, str] = {}
        selected_states = set(states)
        previous_cache = self._cache
        next_cache: dict[str, _CacheEntry] = {}
        for path in iter_entry_paths(self._private_notes):
            state = "unknown"
            try:
                file_stat = path.stat()
                if path.is_symlink():
                    real_path = str(path.resolve())
                else:
                    if path.parent not in real_directories:
                        real_directories[path.parent] = str(path.parent.resolve())
                    real_path = os.path.join(real_directories[path.parent], path.name)
            except FileNotFoundError:
                continue
            except OSError:
                warnings.append({"state": state, "filename": path.name, "reason": "ファイル情報を読み取れません"})
                continue
            cached = previous_cache.get(real_path)
            reused = (
                cached is not None
                and cached.trusted
                and (cached.mtime_ns, cached.size) == (file_stat.st_mtime_ns, file_stat.st_size)
            )
            if not reused:
                try:
                    text = path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    continue
                except UnicodeDecodeError:
                    warnings.append({"state": state, "filename": path.name, "reason": "UTF-8として読み取れません"})
                    continue
                except OSError:
                    warnings.append({"state": state, "filename": path.name, "reason": "ファイルを読み取れません"})
                    continue
                parsed_frontmatter = frontmatter.parse_frontmatter(text)
                metadata = parsed_frontmatter[0] if parsed_frontmatter is not None else {}
                body = parsed_frontmatter[1] if parsed_frontmatter is not None else text.removeprefix("\ufeff")
                kind = wi_constants.normalized_wi_type(metadata.get("type"))
                parsed = _ParsedFile(
                    text=text,
                    text_folded=text.casefold(),
                    metadata=metadata,
                    body=body,
                    kind=kind,
                    frontmatter_broken=parsed_frontmatter is None,
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
                    continue
                except OSError:
                    warnings.append({"state": state, "filename": path.name, "reason": "ファイル情報を読み取れません"})
                    continue
                if (current_stat.st_mtime_ns, current_stat.st_size) != (file_stat.st_mtime_ns, file_stat.st_size):
                    continue
            assert cached is not None
            next_cache[real_path] = cached
            try:
                state = "inbox" if cached.parsed.frontmatter_broken else entry_state_from_metadata(path, cached.parsed.metadata)
            except ActionableError as error:
                warnings.append({"state": state, "filename": path.name, "reason": str(error)})
                continue
            if state not in selected_states:
                continue
            result.append(
                IndexedEntry(
                    state=state,
                    path=path,
                    text=cached.parsed.text,
                    text_folded=cached.parsed.text_folded,
                    metadata=cached.parsed.metadata,
                    body=cached.parsed.body,
                    kind=cached.parsed.kind,
                    updated_at=cached.updated_at,
                    derived=cached.parsed.derived,
                )
            )
        self._cache = next_cache
        return result, warnings
