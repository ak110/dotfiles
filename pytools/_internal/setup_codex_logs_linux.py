"""Codex診断ログDBをLinuxの共有メモリーへ配置する。

`chezmoi apply`後処理（`pytools.post_apply`）から呼ばれ、SSDへの継続的な
書き込みを避けるため`~/.codex/logs_2.sqlite`を`/dev/shm`へ移す。
"""

import logging
import os
import pathlib
import shutil
import sys

from pytools._internal import log_format

logger = logging.getLogger(__name__)

_DATABASE_NAMES = ("logs_2.sqlite", "logs_2.sqlite-wal", "logs_2.sqlite-shm")
_SHM_ROOT = pathlib.Path("/dev/shm")
# 診断ログDBは実測で121MBまで育つため、総容量と空き容量の双方へ余裕を持たせる。
_REQUIRED_BYTES = 256 * 1024 * 1024


def run(
    *,
    home_dir: pathlib.Path | None = None,
    shm_root: pathlib.Path = _SHM_ROOT,
) -> bool:
    """Codex診断ログDBを共有メモリーへ移し、元の場所へシンボリックリンクを作成する。

    共有メモリーの容量が不足する環境では新規リンクを作成しない。
    """
    if sys.platform != "linux":
        return False

    if not shm_root.is_dir():
        raise FileNotFoundError(f"共有メモリーディレクトリが見つからない: {shm_root}")

    codex_dir = (home_dir or pathlib.Path.home()) / ".codex"
    usage = shutil.disk_usage(shm_root)
    if usage.total < _REQUIRED_BYTES or usage.free < _REQUIRED_BYTES:
        existing_links = [
            codex_dir / database_name for database_name in _DATABASE_NAMES if (codex_dir / database_name).is_symlink()
        ]
        logger.warning(
            log_format.format_status(
                "codex-logs",
                f"共有メモリーの容量が不足するため新規リンクを作成しない: {shm_root} "
                f"(総容量{usage.total}バイト・空き{usage.free}バイト・必要{_REQUIRED_BYTES}バイト・"
                f"既存リンク{len(existing_links)}件)",
            )
        )
        return False

    codex_dir.mkdir(parents=True, exist_ok=True)
    changed = False
    for database_name in _DATABASE_NAMES:
        link_path = codex_dir / database_name
        target_path = shm_root / f"codex-{os.getuid()}-{database_name}"
        changed = _ensure_symlink(link_path, target_path) or changed
    return changed


def _ensure_symlink(link_path: pathlib.Path, target_path: pathlib.Path) -> bool:
    """診断ログを共有メモリーへ移し、元のパスをリンクにする。"""
    if link_path.is_symlink() and link_path.readlink() == target_path:
        return False

    if link_path.is_symlink():
        link_path.unlink()
    elif link_path.exists():
        _move_database(link_path, target_path)

    link_path.symlink_to(target_path)
    logger.info(log_format.format_status("codex-logs", f"共有メモリーへ配置: {link_path} -> {target_path}"))
    return True


def _move_database(source: pathlib.Path, destination: pathlib.Path) -> None:
    """既存DBを共有メモリーへ移す。"""
    if destination.exists():
        source.unlink()
    else:
        shutil.move(source, destination)
        destination.chmod(0o600)
