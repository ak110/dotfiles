"""所有するディレクトリを記述子で固定して、走査と回収に必要な権限を補う。"""

from __future__ import annotations

import contextlib
import os
import pathlib
import stat
import typing

from agent_toolkit._atk.managed_temp.errors import ManagedTempError


@contextlib.contextmanager
def descriptor(
    path: pathlib.Path | str | int, flags: int = 0, mode: int = 0o777, *, dir_fd: int | None = None
) -> typing.Generator[int]:
    """獲得直後から記述子の寿命を閉じる。"""
    if isinstance(path, int):
        opened = os.dup(path)
    elif flags & os.O_CREAT:
        opened = os.open(path, flags, mode, dir_fd=dir_fd)
    else:
        opened = os.open(path, flags, dir_fd=dir_fd)
    try:
        yield opened
    finally:
        os.close(opened)


@contextlib.contextmanager
def directory(
    path: pathlib.Path | str,
    *,
    dir_fd: int | None = None,
    expected: os.stat_result | None = None,
    restore: bool = True,
    recover: bool = True,
    display_path: pathlib.Path | str | None = None,
) -> typing.Generator[int]:
    """非追跡で同一性と所有者を検証し、必要なowner modeを一時的に補う。"""
    before = os.stat(path, dir_fd=dir_fd, follow_symlinks=False) if expected is None else expected
    display = path if display_path is None else display_path
    if not stat.S_ISDIR(before.st_mode):
        raise ManagedTempError(f"通常ディレクトリではない: {display}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION)
    mode = stat.S_IMODE(before.st_mode)
    changed = recover and mode & 0o700 != 0o700
    if not changed:
        with descriptor(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd) as opened:
            _same_directory(opened, before, display)
            yield opened
        return
    _require_owner(before, display)
    # O_PATHは読取権限が無い実体も固定できる。procfsのfd参照は保持した実体だけを指す。
    if not hasattr(os, "O_PATH"):
        raise ManagedTempError(
            f"記述子による権限回復を利用できない: {path}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
        )
    with descriptor(path, os.O_PATH | os.O_NOFOLLOW, dir_fd=dir_fd) as held:
        _same_directory(held, before, display)
        held_path = f"/proc/self/fd/{held}"
        os.chmod(held_path, mode | 0o700)
        try:
            with descriptor(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd) as opened:
                _same_directory(opened, before, display)
                yield opened
        finally:
            if restore:
                os.chmod(held_path, mode)


def _same_directory(opened: int, before: os.stat_result, path: pathlib.Path | str) -> None:
    current = os.fstat(opened)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino, current.st_uid) != (
        before.st_dev,
        before.st_ino,
        before.st_uid,
    ):
        raise ManagedTempError(f"権限回復の対象が置換された: {path}", next_action=ManagedTempError.REPLACED_NEXT_ACTION)


def _require_owner(metadata: os.stat_result, path: pathlib.Path | str) -> None:
    if metadata.st_uid != os.geteuid():
        raise ManagedTempError(
            f"所有者が異なるため権限を回復できない: {path} (uid={metadata.st_uid})",
            next_action=f"所有者uid={metadata.st_uid}の権限で{path}を削除してから同じ回収操作を再実行する。できない場合はユーザーへ報告する",
        )


def prepare_tree(root: pathlib.Path) -> None:
    """撤去する所有木のディレクトリに権限を補い、gitの再判定と撤去に備える。"""
    if os.name != "posix":
        return
    _require_owner(root.lstat(), root)
    with directory(root, restore=False) as opened:
        _require_owner(os.fstat(opened), root)
        _prepare_children(opened, root)


def _prepare_children(opened: int, root: pathlib.Path) -> None:
    with os.scandir(opened) as children:
        for child in children:
            metadata = child.stat(follow_symlinks=False)
            _require_owner(metadata, root / child.name)
            if stat.S_ISDIR(metadata.st_mode):
                with directory(
                    child.name, dir_fd=opened, expected=metadata, restore=False, display_path=root / child.name
                ) as nested:
                    try:
                        _prepare_children(nested, root / child.name)
                    except OSError as error:
                        raise ManagedTempError(f"回収対象を走査できない: {root / child.name}: {error}") from error
