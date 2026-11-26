from __future__ import annotations

import ast
import contextlib
import functools
import hashlib
import json
import os
import pathlib
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from agent_toolkit._common.atomic_file import atomic_write

_INDEX_VERSION = 2
_RECENT_NS = 2_000_000_000
"""これより新しい更新時刻のファイルは、派生値を保存せず次回も解析する（ナノ秒）。"""
_PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[2]
_IMPORT_PREFIXES = tuple(f"{lazy}{keyword} agent_toolkit" for lazy in ("", "lazy ") for keyword in ("from", "import"))


def identity(stat: os.stat_result) -> list[int]:
    return [stat.st_ino, stat.st_mtime_ns, stat.st_size]


@functools.cache
def code_version(module: str, *, script_root: pathlib.Path | None = None) -> str:
    sources: dict[str, bytes] = {}
    pending = [module]
    while pending:
        name = pending.pop()
        if name in sources or (path := _module_file(name, script_root)) is None:
            continue
        sources[name] = path.read_bytes()
        parts = name.split(".")
        pending.extend(".".join(parts[:end]) for end in range(1, len(parts)))
        pending.extend(_imported_names(sources[name], include_scripts=script_root is not None))
    digest = hashlib.sha256()
    for name in sorted(sources):
        for value in (name.encode("utf-8"), sources[name]):
            digest.update(len(value).to_bytes(8, "big") + value)
    return digest.hexdigest()


def _module_file(name: str, script_root: pathlib.Path | None) -> pathlib.Path | None:
    if name == "agent_toolkit" or name.startswith("agent_toolkit."):
        base = _PACKAGE_ROOT.joinpath(*name.split("."))
    elif script_root is not None and "." not in name:
        base = script_root / name
    else:
        return None
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imported_names(source: bytes, *, include_scripts: bool) -> Iterator[str]:
    prefixes = ("from ", "import ", "lazy from ", "lazy import ") if include_scripts else _IMPORT_PREFIXES
    lines = source.decode("utf-8").splitlines()
    for index, line in enumerate(lines):
        statement = line.split("#", 1)[0].strip()
        if not statement.startswith(prefixes):
            continue
        following = (value.split("#", 1)[0].strip() for value in lines[index + 1 :])
        while ("(" in statement and ")" not in statement) or statement.endswith("\\"):
            statement = statement.removesuffix("\\") + " " + next(following, ")")
        try:
            node = ast.parse(statement.removeprefix("lazy ")).body[0]
        except SyntaxError:
            continue
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


@dataclass
class Checkpoint:
    every: int
    max_bytes: int
    digested: int = 0
    reused: int = 0
    bytes_saved: int = 0
    overflow: bool = False


def _temp_files(path: pathlib.Path) -> Iterator[pathlib.Path]:
    return path.parent.glob(f".{path.name}.*.tmp")


def _store(path: pathlib.Path, code: str, entries: dict[str, Any], checkpoint: Checkpoint | None) -> None:
    value = {"version": _INDEX_VERSION, "code": code, "entries": entries}
    if checkpoint is None:
        with contextlib.suppress(OSError):
            atomic_write(path, json.dumps(value, ensure_ascii=False))
        return
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8", errors="backslashreplace")
    if len(payload) > checkpoint.max_bytes:
        _remove_oversized_index(path, checkpoint, len(payload))
        return
    with contextlib.suppress(OSError):
        atomic_write(path, payload)
        checkpoint.bytes_saved = len(payload)


def _remove_oversized_index(path: pathlib.Path, checkpoint: Checkpoint, size: int) -> None:
    path.unlink(missing_ok=True)
    for temporary in _temp_files(path):
        temporary.unlink(missing_ok=True)
    checkpoint.overflow = True
    print(f"派生索引は容量上限を超えたため全件を読み直す: {size} > {checkpoint.max_bytes} bytes", file=sys.stderr)


def refreshed[T](
    index_path: pathlib.Path,
    code: str,
    listed: Iterable[tuple[str, pathlib.Path, list[int]]],
    derive: Callable[[str, pathlib.Path], T | None],
    encode: Callable[[T], list[Any]],
    decode: Callable[[list[Any]], T],
    *,
    checkpoint: Checkpoint | None = None,
) -> dict[str, T]:
    if checkpoint is not None:
        for temporary in _temp_files(index_path):
            temporary.unlink(missing_ok=True)
        listed = list(listed)
        if index_path.is_file() and (size := index_path.stat().st_size) > checkpoint.max_bytes:
            _remove_oversized_index(index_path, checkpoint, size)
            return {key: value for key, path, _ in listed if (value := derive(key, path)) is not None}
    saved = _load(index_path, code)
    remaining = (
        {key: saved[key] for key, _, current in listed if isinstance(saved.get(key), list) and saved[key][:3] == current}
        if checkpoint is not None
        else {}
    )
    if checkpoint is not None and index_path.is_file():
        checkpoint.bytes_saved = index_path.stat().st_size
    recent = time.time_ns() - _RECENT_NS
    values: dict[str, T] = {}
    kept: dict[str, list[Any]] = {}
    changed = False
    new_since_checkpoint = 0
    for key, path, current in listed:
        remaining.pop(key, None)
        value = _decoded(saved.get(key), current, decode)
        if value is None:
            changed = True
            if checkpoint is not None:
                checkpoint.digested += 1
                new_since_checkpoint += 1
            if (value := derive(key, path)) is None:
                continue
        elif checkpoint is not None:
            checkpoint.reused += 1
        values[key] = value
        if current[1] < recent:
            kept[key] = [*current, *encode(value)]
        if checkpoint is not None and new_since_checkpoint >= checkpoint.every:
            _store(index_path, code, remaining | kept, checkpoint)
            new_since_checkpoint = 0
            if checkpoint.overflow:
                break
    if (changed or saved.keys() != kept.keys()) and (checkpoint is None or not checkpoint.overflow):
        _store(index_path, code, kept, checkpoint)
    if checkpoint is not None and checkpoint.overflow:
        return {key: value for key, path, _ in listed if (value := derive(key, path)) is not None}
    return values


def _decoded[T](cached: object, current: list[int], decode: Callable[[list[Any]], T]) -> T | None:
    if not isinstance(cached, list) or cached[:3] != current:
        return None
    try:
        return decode(cached[3:])
    except TypeError, ValueError:
        return None


def _load(path: pathlib.Path, code: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_bytes())
    except OSError, ValueError:
        return {}
    if (
        not isinstance(payload, dict)
        or payload.get("version") != _INDEX_VERSION
        or payload.get("code") != code
        or not isinstance(payload.get("entries"), dict)
    ):
        return {}
    return payload["entries"]
