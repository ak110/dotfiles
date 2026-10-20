"""長い相対木の受入試験で共有する実ファイル入力。"""

from __future__ import annotations

import contextlib
import os
import pathlib

from agent_toolkit._atk.managed_temp import permissions


def create_long_posix_tree(root: pathlib.Path, *, leaf: str = "payload", timestamp: int | None = None) -> str:
    """相対名で到達でき、連結長がPATH_MAXを超える木を作成する。"""
    parts = []
    with contextlib.ExitStack() as stack:
        descriptor = stack.enter_context(permissions.descriptor(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for index in range(24):
            name = f"{index:02d}-" + "x" * 197
            os.mkdir(name, mode=0o700, dir_fd=descriptor)
            child = stack.enter_context(
                permissions.descriptor(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            )
            if timestamp is not None:
                os.utime(descriptor, ns=(timestamp, timestamp))
            descriptor = child
            parts.append(name)
        payload = stack.enter_context(permissions.descriptor(leaf, os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=descriptor))
        if timestamp is not None:
            os.utime(payload, ns=(timestamp, timestamp))
            os.utime(descriptor, ns=(timestamp, timestamp))
    return os.path.join(*parts)
