"""プロセス間の排他ファイルロック。

POSIXの`fcntl.flock`とWindowsの`msvcrt.locking`の差異を吸収し、
`with`構文で保持できる単一の入口を提供する。
`agent-toolkit/scripts/_file_lock.py`は同等の機構を持つが別配布物のため参照しない。
"""

import contextlib
import os
import pathlib
import typing


@contextlib.contextmanager
def exclusive_file_lock(path: pathlib.Path) -> typing.Iterator[None]:
    """`path`をロックファイルとしてプロセス間の排他ロックを保持する。

    ロックファイルの親ディレクトリが無ければ作成する。
    ロックは`with`ブロックを抜けるまで保持し、ブロック内の例外送出時も解放する。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        _acquire(handle)
        try:
            yield
        finally:
            _release(handle)


if os.name == "nt":
    import msvcrt  # type: ignore[import-not-found]  # pylint: disable=import-error

    def _acquire(handle: typing.IO[typing.Any]) -> None:
        """Windows: 先頭1バイトのバイト範囲ロックを取得する。

        `LK_LOCK`は10秒間の再試行後に`OSError`を送出する仕様のため、
        長時間の競合でも待機し続けるようループで再試行する。
        """
        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
                return
            except OSError:
                continue

    def _release(handle: typing.IO[typing.Any]) -> None:
        """Windows: バイト範囲ロックを解放する。"""
        handle.seek(0)
        with contextlib.suppress(OSError):
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]

else:
    import fcntl

    def _acquire(handle: typing.IO[typing.Any]) -> None:
        """POSIX: ファイル全体への排他ロックを取得する。"""
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _release(handle: typing.IO[typing.Any]) -> None:
        """POSIX: ファイル全体への排他ロックを解放する。"""
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
