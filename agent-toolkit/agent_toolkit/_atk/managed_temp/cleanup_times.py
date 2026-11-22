from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import pathlib
from collections.abc import Callable

from agent_toolkit._atk.managed_temp import paths


def update_time(metadata: os.stat_result, now_ns: int) -> int:
    stamp = metadata.st_mtime_ns if metadata.st_mtime_ns <= now_ns else metadata.st_ctime_ns
    return min(stamp, now_ns)


def _children(path: pathlib.Path) -> dict[str, int]:
    with os.scandir(path) as children:
        return {child.name: child.inode() for child in children}


def _signature(children: dict[str, int]) -> str:
    digest = hashlib.sha256()
    for name, inode in sorted(children.items()):
        digest.update(os.fsencode(name) + b"\0" + str(inode).encode() + b"\0")
    return digest.hexdigest()


class CleanupTimes:
    def __init__(self, state_root: pathlib.Path, cutoff_ns: int) -> None:
        self._state_root = state_root
        self._records: dict[int, dict[str, dict[str, int]]] = {}

        with contextlib.suppress(OSError):
            for record in (state_root / ".sweep/cache-parents").iterdir():
                try:
                    physical, identity, signature, logical = record.name.split("_")
                    mtime, latest = int(physical), int(logical)
                except ValueError:
                    continue
                if max(mtime, latest) < cutoff_ns:
                    with contextlib.suppress(OSError):
                        record.unlink(missing_ok=True)
                else:
                    self._add(mtime, identity, signature, latest)

    def _add(self, mtime: int, identity: str, signature: str, latest: int) -> None:
        generation = self._records.setdefault(mtime, {}).setdefault(identity, {})
        generation[signature] = max(latest, generation.get(signature, latest))

    @staticmethod
    def _identity(path: pathlib.Path, metadata: os.stat_result) -> str:
        key = f"{metadata.st_dev}:{metadata.st_ino}:{metadata.st_ctime_ns}:".encode() + os.fsencode(path)
        return hashlib.sha256(key).hexdigest()

    def activity(
        self, path: pathlib.Path, metadata: os.stat_result, now_ns: int, *, children: Callable[[], dict[str, int]] | None = None
    ) -> int:
        generation = self._records.get(metadata.st_mtime_ns)
        if generation and os.name == "nt" and not metadata.st_ino:
            # WindowsのDirEntry.statはデバイス・inodeを返さない。
            metadata = path.lstat()
            generation = self._records.get(metadata.st_mtime_ns)
        fallback = update_time(metadata, now_ns)
        if not generation or not (matches := generation.get(self._identity(path, metadata))):
            return fallback

        signature = _signature(_children(path) if children is None else children())
        return matches.get(signature, fallback)

    def observe_parent(self, path: pathlib.Path, *, last_use_ns: int, now_ns: int) -> Callable[[set[str]], None]:
        before = path.lstat()
        expected = _children(path)
        prior = self.activity(path, before, now_ns, children=lambda: expected)
        latest = max(prior, last_use_ns)

        def finish(removed: set[str]) -> None:
            after = path.lstat()
            if not os.path.samestat(before, after):
                return
            for name in removed:
                expected.pop(name, None)
            signature = _signature(expected)
            identity = self._identity(path, after)
            mtime = after.st_mtime_ns
            try:
                directory = paths.prepare_sweep_directory(self._state_root) / "cache-parents"
                directory.mkdir(exist_ok=True)
                (directory / f"{mtime}_{identity}_{signature}_{latest}").touch()
            except OSError as error:
                if error.errno != errno.ENOSPC:
                    raise

                return
            self._add(mtime, identity, signature, latest)

        return finish
