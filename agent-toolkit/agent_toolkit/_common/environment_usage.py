import contextlib
import ctypes
import ctypes.wintypes
import os
import sys
from collections.abc import Generator
from pathlib import Path
from typing import BinaryIO


@contextlib.contextmanager
def lock(environment: Path, *, exclusive: bool = False) -> Generator[BinaryIO]:
    """起動は共有、回収は即時の排他を取り、使用中の環境を削除させない。

    ロックは環境の外へ置き、回収・再作成で排他対象のinodeを変えない。
    POSIXのexecではハンドルを継承して、Pythonの置換後も使用中保護を保つ。
    """
    path = environment.with_name(environment.name + ".usage-lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            _windows_lock(handle, exclusive=exclusive)
        else:
            import fcntl  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

            fcntl.flock(handle.fileno(), (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            os.set_inheritable(handle.fileno(), True)

        yield handle


def _windows_lock(handle: BinaryIO, *, exclusive: bool) -> None:
    """LockFileExの共有ロックを使い、複数の起動を直列化しない。"""
    if sys.platform != "win32":
        raise OSError("Windows専用のファイルロックです")
    import msvcrt  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

    class Overlapped(ctypes.Structure):
        """同期ファイルハンドルで使う、offset 0のOVERLAPPED。"""

        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", ctypes.wintypes.DWORD),
            ("OffsetHigh", ctypes.wintypes.DWORD),
            ("hEvent", ctypes.wintypes.HANDLE),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    acquire = kernel.LockFileEx
    acquire.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(Overlapped),
    ]
    acquire.restype = ctypes.wintypes.BOOL
    overlapped = Overlapped()
    if not acquire(msvcrt.get_osfhandle(handle.fileno()), 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(overlapped)):
        raise ctypes.WinError(ctypes.get_last_error())
