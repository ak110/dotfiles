"""post-applyの成功済み処理を、入力と配置状態の内容一致で再利用する。"""

import hashlib
import json
import os
import stat
from collections.abc import Iterable
from pathlib import Path

import platformdirs

from pytools._internal import common

# plugin実行時や開発時の生成物は配布内容の変更に含めない。
_IGNORED = frozenset(
    {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".hypothesis", ".orphaned_at"}
)


def fingerprint(paths: Iterable[Path], *, values: Iterable[str] = ()) -> str:
    """パスの順序、相対名、内容、modeを境界付きでハッシュ化する。"""
    digest = hashlib.sha256()

    def add(data: bytes) -> None:
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)

    def read_error(error: OSError) -> None:
        raise error

    for value in values:
        add(b"value")
        add(value.encode())
    for root in paths:
        add(b"path")
        if not root.exists() and not root.is_symlink():
            add(b"missing")
            continue
        files = [root]
        if root.is_dir():
            files = []
            for directory, names, filenames in os.walk(root, onerror=read_error):
                names[:] = sorted(name for name in names if name not in _IGNORED)
                files.extend(Path(directory) / name for name in names if (Path(directory) / name).is_symlink())
                files.extend(Path(directory) / name for name in sorted(filenames) if name not in _IGNORED)
        add(b"present")
        add(str(len(files)).encode())
        for path in sorted(files):
            add(str(path.relative_to(root) if path != root else Path(".")).encode())
            add(str(stat.S_IMODE(path.lstat().st_mode)).encode())
            add(os.readlink(path).encode() if path.is_symlink() else path.read_bytes())
    return digest.hexdigest()


def record_path(name: str) -> Path:
    """処理ごとの固定サイズの成功記録の保存先を返す。"""
    return Path(platformdirs.user_cache_dir("dotfiles", appauthor=False)) / "post-apply" / f"{name}.json"


def matches(name: str, key: str) -> bool:
    """読めない記録は未確認として扱う。"""
    record = common.load_json_dict(record_path(name))
    return record is not None and record.get("key") == key


def save(name: str, key: str) -> None:
    """成功後の入力・出力の内容を記録する。保存できなければ次回も処理する。"""
    common.atomic_write_text(record_path(name), json.dumps({"key": key}), tag="post-apply cache")
