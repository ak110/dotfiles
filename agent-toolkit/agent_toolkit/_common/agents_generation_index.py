from __future__ import annotations

import contextlib
import json
import os
import pathlib
from collections.abc import Generator
from typing import Any

lazy import psutil

from agent_toolkit._common import file_lock, private_directory
from agent_toolkit._common.atomic_file import atomic_write
lazy from agent_toolkit._common import process_identity

REPOSITORY_READER_MINIMUM = (2, 311, 0)
_WINDOWS_EPOCH = 116444736000000000


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def reads_separated_repositories(version: str) -> bool:
    return version_tuple(version) >= REPOSITORY_READER_MINIMUM


@contextlib.contextmanager
def locked_index(directory: pathlib.Path, *, write: bool = True) -> Generator[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True, mode=private_directory.MODE)
    with (directory / "generations.lock").open("a+", encoding="utf-8") as handle:
        file_lock.acquire_lock(handle)
        try:
            path = directory / "generations.json"
            try:
                data = json.loads(path.read_bytes())
            except FileNotFoundError:
                data = {"schema": 1, "next_epoch": 1, "current": None, "generations": {}}
            if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("generations"), dict):
                raise ValueError(f"管理サービスの世代索引が不正: {path}")
            yield data
            if write:
                save_locked_index(directory, data)
        finally:
            file_lock.release_lock(handle)


@contextlib.contextmanager
def repository_reader_boundary(directory: pathlib.Path) -> Generator[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True, mode=private_directory.MODE)
    with (directory / "startup.lock").open("a+", encoding="utf-8") as startup:
        file_lock.acquire_lock(startup)
        try:
            with locked_index(directory, write=False) as data:
                yield data
        finally:
            file_lock.release_lock(startup)


def save_locked_index(directory: pathlib.Path, data: dict[str, Any]) -> None:
    atomic_write(directory / "generations.json", json.dumps(data, ensure_ascii=False))


def generation_process_alive(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        pid, started = map(int, value.split(":"))
        process = psutil.Process(pid)
        if process.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
            return False
        if os.name == "nt":
            # FILETIMEとpsutilのfloat秒で丸めが異なる。書込許可でなく生存側に倒す判定に限る。pyfltr: allow-comment
            return abs(process.create_time() - (started - _WINDOWS_EPOCH) / 10_000_000) <= 0.00001
        actual = process_identity.start_id(process)
        return actual is not None and int(actual.rsplit(":", 1)[-1]) == started
    except ValueError, psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True
