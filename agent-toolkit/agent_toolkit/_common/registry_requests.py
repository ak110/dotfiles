"""session登録簿の直下に置かれる要求ファイルを列挙し、要求への応答と要求側の撤去を扱う。

登録簿は会話ごとの`<id>.json`を7日分保持し（2026-11-19の実測で1,605件）、effort要求と解放要求はまれにしか置かれない。
agents_serverの監視は1秒（自動再開待ちは0.1秒）ごとに要求を探すため、毎回の列挙は登録簿の件数に比例する仕事を常時生んでいた。
要求の置き場所と名前は、版の異なる書き手（CLI）と読み手（所有MCP）が共有する契約なので変えない。
要求の作成・改名・削除は直下の項目を変えてディレクトリの更新時刻を進めるため、更新時刻が前回と同じなら前回の一覧を使う。

応答は所有MCPが書き、要求と応答の削除は要求側のCLIが行う。
所有MCPが要求の存在を確かめてから応答を書くまでの間に要求側が両方を消すと、消した後に応答だけが残る（2026-11-19のCIで観測）。
応答の作成と要求側の撤去は、対象sessionの登録簿ロックの中で行う。
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from typing import Any

from agent_toolkit._common import atomic_file, session_registry

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


def respond(
    request: pathlib.Path,
    response_path: pathlib.Path,
    session_id: str,
    response: dict[str, Any],
    *,
    state_root: pathlib.Path | None,
) -> None:
    """要求が残っている場合だけ応答を原子的に書く。要求側が`withdraw`を終えた後には応答を作らない。"""
    with session_registry.registry_lock(session_id, state_root=state_root):
        if request.exists():
            atomic_file.atomic_write(response_path, json.dumps(response, ensure_ascii=False) + "\n")


def withdraw(request: pathlib.Path, response_path: pathlib.Path, session_id: str, *, state_root: pathlib.Path | None) -> None:
    """要求側が待機を終えた要求と応答を消す。"""
    with session_registry.registry_lock(session_id, state_root=state_root):
        request.unlink(missing_ok=True)
        response_path.unlink(missing_ok=True)
