"""Claude Code agent-toolkit: ファイルロック・ログローテーションの共通ヘルパー。

`_session_state.py`のセッション状態排他ロックと`_stop_gate.py`の常時ログローテーションが
同一実装を個別に持っていたため、本モジュールへ集約する。
POSIX/NT両対応のロック取得・解放と、サイズ超過時の1世代ローテーションを提供する。
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import IO


def acquire_lock(fh: IO, *, blocking: bool = True) -> None:
    """ファイルハンドル`fh`へ排他ロックを取得する。

    POSIXは`fcntl.flock`、Windowsは`msvcrt.locking`を使う。
    `blocking=True`（既定）は取得できるまで待機する。
    `blocking=False`時は即時取得できない場合に`OSError`を送出する。
    """
    _acquire_lock_impl(fh, blocking=blocking)


def release_lock(fh: IO) -> None:
    """`acquire_lock`で取得したロックを解放する。解放失敗はベストエフォートで無視する。"""
    _release_lock_impl(fh)


def rotate_if_needed(path: Path, max_bytes: int, generations: int = 1) -> None:
    """`path`のサイズが`max_bytes`を超えた場合に世代ローテーションする。

    `generations=1`（現行唯一の対応値）は`path`のsuffixへ`.1`を付加したパスへリネームする
    （既存の`.1`ファイルは上書き）。ファイルが存在しない、サイズが上限未満の場合は何もしない。
    `generations`引数は将来の多世代対応に向けた拡張点として残すが、
    1以外の値は現行呼び出し元に存在しないため`NotImplementedError`とする。
    """
    if generations != 1:
        raise NotImplementedError("generations>1は未対応")
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= max_bytes:
        return
    path.replace(path.with_suffix(path.suffix + ".1"))


if os.name == "nt":
    import msvcrt  # type: ignore[import-not-found]  # pylint: disable=import-error

    def _acquire_lock_impl(fh: IO, *, blocking: bool) -> None:
        """Windows: バイト範囲ロックを取得する。

        `blocking=True`時、空ファイルでも`LK_LOCK`はブロッキング取得可能。
        `LK_LOCK`は最大10秒で再試行する仕様のため、長時間の競合に備えてOSError時はループで再試行する。
        `blocking=False`時は`LK_NBLCK`で即時判定し、取得不能なら`OSError`を送出する。
        """
        fh.seek(0)
        if not blocking:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            return
        while True:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
                return
            except OSError:
                continue

    def _release_lock_impl(fh: IO) -> None:
        """Windows: バイト範囲ロックを解放する。"""
        fh.seek(0)
        with contextlib.suppress(OSError):
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]

else:
    import fcntl

    def _acquire_lock_impl(fh: IO, *, blocking: bool) -> None:
        """POSIX: ファイル全体への排他ロックを取得する。"""
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(fh.fileno(), flags)

    def _release_lock_impl(fh: IO) -> None:
        """POSIX: ファイル全体への排他ロックを解放する。"""
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
