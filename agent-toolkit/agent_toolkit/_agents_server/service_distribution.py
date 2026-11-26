from __future__ import annotations

import hashlib
import json
import os
import pathlib
import stat

from agent_toolkit._common import file_lock
from agent_toolkit._common.atomic_file import atomic_write

SCOPE_ENV = "AGENT_TOOLKIT_AGENTS_SERVICE_SCOPE"
UPDATE_ENV = "AGENT_TOOLKIT_AGENTS_SERVICE_UPDATE"
_MARKER = ".agent-toolkit-plugin-root.json"
_EXCLUDED = {".venv", "__pycache__", ".git", ".pytest_cache", ".ruff_cache", "node_modules"}


def source_root(root: pathlib.Path) -> pathlib.Path:
    root = root.resolve()
    seen: set[pathlib.Path] = set()
    while root not in seen:
        seen.add(root)
        try:
            record = json.loads((root.parent / _MARKER).read_bytes())
        except FileNotFoundError:
            return root
        source = pathlib.Path(record["source"])
        if record.get("root_name") != root.name or not source.is_absolute():
            raise ValueError("保持配布物の複製元が不正")
        root = source.resolve()
    raise ValueError("保持配布物の複製元が循環")


def identity(root: pathlib.Path) -> tuple[str, str]:
    origin = os.path.normcase(str(source_root(root)))
    return origin, _content_digest(root)


def _content_digest(root: pathlib.Path) -> str:
    contents = hashlib.sha256()
    for directory, folders, files in os.walk(root, onerror=_raise):
        folder = pathlib.Path(directory)
        folders[:] = sorted(name for name in folders if name not in _EXCLUDED)
        if any((folder / name).is_symlink() or (folder / name).is_junction() for name in folders):
            raise ValueError("配布物のフォルダーにリンクがあるため内容を確定できない")
        for name in sorted(files):
            path = folder / name
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError("配布物の通常ファイル以外の内容は比較できない")
            contents.update(os.fsencode(path.relative_to(root).as_posix()))
            contents.update(b"\0")
            with path.open("rb") as stream:
                contents.update(hashlib.file_digest(stream, "sha256").digest())
    return contents.hexdigest()


def _raise(error: OSError) -> None:
    raise error


def _published(directory: pathlib.Path) -> dict[str, str]:
    try:
        data = json.loads((directory / "published-distributions.json").read_bytes())
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or not all(isinstance(value, str) for value in data.values()):
        raise ValueError("公開済み配布物の記録が不正")
    return data


def isolated_scope(directory: pathlib.Path, root: pathlib.Path) -> str | None:
    source = os.path.normcase(str(root.resolve()))
    try:
        source = os.path.normcase(str(source_root(root)))
        expected = _published(directory).get(source)
        if expected is not None and expected == _content_digest(root):
            return None
    except OSError, ValueError, TypeError, KeyError:
        pass
    return f"distribution:{source}"


def publish(directory: pathlib.Path, approved: tuple[str, str]) -> None:
    source, digest = approved
    with (directory / "published-distributions.lock").open("a+", encoding="utf-8") as handle:
        file_lock.acquire_lock(handle)
        try:
            entries = _published(directory)
            entries[source] = digest
            atomic_write(directory / "published-distributions.json", json.dumps(entries, ensure_ascii=False))
        finally:
            file_lock.release_lock(handle)
