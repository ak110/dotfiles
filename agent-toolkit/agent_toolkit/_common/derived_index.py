from __future__ import annotations

import ast
import contextlib
import functools
import hashlib
import json
import os
import pathlib
import time
from collections.abc import Callable, Iterable, Iterator
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
def code_version(module: str) -> str:
    sources: dict[str, bytes] = {}
    pending = [module]
    while pending:
        name = pending.pop()
        if name in sources or (path := _module_file(name)) is None:
            continue
        sources[name] = path.read_bytes()
        parts = name.split(".")
        pending.extend(".".join(parts[:end]) for end in range(1, len(parts)))
        pending.extend(_imported_names(sources[name]))
    digest = hashlib.sha256()
    for name in sorted(sources):
        for value in (name.encode("utf-8"), sources[name]):
            digest.update(len(value).to_bytes(8, "big") + value)
    return digest.hexdigest()


def _module_file(name: str) -> pathlib.Path | None:
    if name != "agent_toolkit" and not name.startswith("agent_toolkit."):
        return None
    base = _PACKAGE_ROOT.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imported_names(source: bytes) -> Iterator[str]:
    lines = source.decode("utf-8").splitlines()
    for index, line in enumerate(lines):
        statement = line.split("#", 1)[0].strip()
        if not statement.startswith(_IMPORT_PREFIXES):
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


def refreshed[T](
    index_path: pathlib.Path,
    code: str,
    listed: Iterable[tuple[str, pathlib.Path, list[int]]],
    derive: Callable[[str, pathlib.Path], T | None],
    encode: Callable[[T], list[Any]],
    decode: Callable[[list[Any]], T],
) -> dict[str, T]:
    saved = _load(index_path, code)
    recent = time.time_ns() - _RECENT_NS
    values: dict[str, T] = {}
    kept: dict[str, list[Any]] = {}
    changed = False
    for key, path, current in listed:
        value = _decoded(saved.get(key), current, decode)
        if value is None:
            changed = True
            if (value := derive(key, path)) is None:
                continue
        values[key] = value
        if current[1] < recent:
            kept[key] = [*current, *encode(value)]
    if changed or saved.keys() != kept.keys():
        with contextlib.suppress(OSError):
            atomic_write(index_path, json.dumps({"version": _INDEX_VERSION, "code": code, "entries": kept}, ensure_ascii=False))
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
