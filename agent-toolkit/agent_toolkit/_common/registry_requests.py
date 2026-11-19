"""session登録簿の直下に置かれる要求ファイルを、ディレクトリが変わった時だけ列挙し直す。

登録簿は会話ごとの`<id>.json`を7日分保持し（2026-11-19の実測で1,605件）、effort要求と解放要求はまれにしか置かれない。
agents_serverの監視は1秒（自動再開待ちは0.1秒）ごとに要求を探すため、毎回の列挙は登録簿の件数に比例する仕事を常時生んでいた。
要求の置き場所と名前は、版の異なる書き手（CLI）と読み手（所有MCP）が共有する契約なので変えない。
要求の作成・改名・削除は直下の項目を変えてディレクトリの更新時刻を進めるため、更新時刻が前回と同じなら前回の一覧を使う。
"""

from __future__ import annotations

import os
import pathlib
import time

EFFORT_REQUEST_SUFFIX = ".effort-request"
RELEASE_REQUEST_SUFFIX = ".release-request"
_SUFFIXES = (EFFORT_REQUEST_SUFFIX, RELEASE_REQUEST_SUFFIX)

# 時刻の刻みが粗いファイルシステム（ext3は1秒、Windowsは最大15.6ミリ秒）では、列挙の直後に同じ刻みの中で置かれた要求が
# 更新時刻を変えない。刻みより十分古い更新時刻の一覧だけを再利用し、それより新しい間は毎回列挙し直す。
_TRUSTED_AGE_NS = 2_000_000_000

_LISTINGS: dict[str, tuple[tuple[int, int], tuple[str, ...]]] = {}


def request_paths(directory: pathlib.Path, suffix: str) -> list[pathlib.Path]:
    """`directory`直下の`suffix`で終わる要求ファイルを名前順に返す。ディレクトリが無い・読めない場合は空を返す。"""
    started = time.time_ns()
    try:
        status = os.stat(directory)
    except OSError:
        return []
    signature = (status.st_ino, status.st_mtime_ns)
    key = os.fspath(directory)
    cached = _LISTINGS.get(key)
    if cached is not None and cached[0] == signature:
        names = cached[1]
    else:
        try:
            with os.scandir(directory) as entries:
                names = tuple(sorted(entry.name for entry in entries if entry.name.endswith(_SUFFIXES)))
        except OSError:
            return []
        if status.st_mtime_ns < started - _TRUSTED_AGE_NS:
            _LISTINGS[key] = (signature, names)
        else:
            _LISTINGS.pop(key, None)
    return [directory / name for name in names if name.endswith(suffix)]
