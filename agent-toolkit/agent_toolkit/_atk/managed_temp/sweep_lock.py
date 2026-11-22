"""一時root自体と旧版のロックで、日次・強制・空き不足時の掃引を直列化する。"""

from __future__ import annotations

import contextlib
import errno
import os
import pathlib
import sys
import typing

from agent_toolkit._atk.managed_temp import paths
from agent_toolkit._common import file_lock

if sys.platform == "win32":
    import _winapi  # pylint: disable=import-error  # Windows専用

    _FILE_ADD_FILE = 2
    _FILE_SHARE_READ_DELETE = 1 | 4
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
else:
    import fcntl  # pylint: disable=import-error  # POSIX専用


@contextlib.contextmanager
def _directory_lock(root: pathlib.Path) -> typing.Generator[bool]:
    if sys.platform == "win32":
        # FILE_ADD_FILEを要求し、共有はREADとDELETEだけを許す。子の読み書きやrootの列挙は妨げない。
        try:
            handle = _winapi.CreateFile(
                str(root), _FILE_ADD_FILE, _FILE_SHARE_READ_DELETE, 0, _winapi.OPEN_EXISTING, _FILE_FLAG_BACKUP_SEMANTICS, 0
            )
        except OSError as error:
            if error.winerror != 32:  # ERROR_SHARING_VIOLATION
                raise
            yield False
            return
        try:
            yield True
        finally:
            _winapi.CloseHandle(handle)
    else:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
            else:
                yield True
        finally:
            os.close(fd)


@contextlib.contextmanager
def sweep_access(temp_root: pathlib.Path, state_root: pathlib.Path) -> typing.Generator[bool]:
    """旧版のロックがあれば取得する。初回作成が満杯で失敗しても、一時rootの施錠は掃引終了まで保つ。"""
    path = persistent_lock_path(state_root)
    with contextlib.ExitStack() as stack:
        try:
            handle = path.open("r+b")
        except FileNotFoundError:
            # ロックが無い間だけ、作成する掃引と作成不能時の救済を同じディレクトリで排他する。
            if not stack.enter_context(_directory_lock(temp_root)):
                yield False
                return
            try:
                paths.prepare_sweep_directory(state_root)
                handle = path.open("a+b")
            except OSError as error:
                if error.errno != errno.ENOSPC:
                    raise
                try:
                    handle = path.open("r+b")
                except FileNotFoundError:
                    yield True
                    return
        with handle:
            try:
                file_lock.acquire_lock(handle, blocking=False)
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                yield False
                return
            try:
                yield True
            finally:
                file_lock.release_lock(handle)


def persistent_lock_path(state_root: pathlib.Path) -> pathlib.Path:
    """掃引と旧版が共用する永続ロックのパスを返す。"""
    return state_root / ".sweep/pressure.lock"
