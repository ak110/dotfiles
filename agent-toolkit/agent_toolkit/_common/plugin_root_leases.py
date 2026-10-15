"""保持した配布物の起動側と回収側で、排他とプロセスの使用記録を共有する。"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Generator
from pathlib import Path

import psutil

from agent_toolkit._common import file_lock, state_paths


def _key(path: Path) -> str:
    return hashlib.sha256(os.fsencode(os.path.normcase(os.path.abspath(path)))).hexdigest()


def _directory() -> Path:
    directory = state_paths.state_dir() / "plugin-root-leases"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


@contextlib.contextmanager
def plugin_root_lock(path: Path) -> Generator[None]:
    """同じ保持領域の使用登録と回収を直列にする。ロック実体は回収後も残す。"""
    with (_directory() / f"{_key(path)}.lock").open("a+", encoding="utf-8") as handle:
        file_lock.acquire_lock(handle)
        try:
            yield
        finally:
            file_lock.release_lock(handle)


def register_plugin_root_use(area: Path) -> None:
    """呼出元のPIDと作成時刻を記録する。呼出元は領域のロックを保持する。"""
    try:
        process = psutil.Process()
        record = {"pid": process.pid, "created_at": process.create_time(), "path": str(area)}
    except psutil.Error as error:
        raise OSError(f"配布物を使うプロセスのidentityを取得できない: {error}") from error
    destination = _directory() / f"{_key(area)}-{process.pid}.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(destination)


def plugin_root_in_use(area: Path) -> bool:
    """同じPIDと作成時刻の生存を調べ、取得不能な記録も保持する。ロック下で呼ぶ。"""
    for path in _directory().glob(f"{_key(area)}-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(record, dict)
                or record.get("path") != str(area)
                or not isinstance(record.get("pid"), int)
                or isinstance(record["pid"], bool)
                or record["pid"] <= 0
                or not isinstance(record.get("created_at"), (float, int))
                or isinstance(record["created_at"], bool)
            ):
                return True
            try:
                process = psutil.Process(record["pid"])
                if process.create_time() == record["created_at"] and process.status() != psutil.STATUS_ZOMBIE:
                    return True
            except psutil.NoSuchProcess:
                pass
            path.unlink()
        except OSError, ValueError, psutil.Error:
            return True
    return False
