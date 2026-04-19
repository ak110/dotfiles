"""同名ファイルを検出して重複を削除する (C# deletehomonym の Python 移植)。

既定ではファイル名 (拡張子除外・小文字化) が MD5/SHA-1 形式の 16 進文字列の
ファイルだけを対象とする。ハッシュ名で同名のファイルが複数見つかった場合、
サイズが一致すれば新しい方を残し古い方を削除する。サイズが異なる場合は
ハッシュ衝突を意味するので警告のみ出す。
"""

import argparse
import io
import logging
import pathlib
import re

from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)

_HASH_RE = re.compile(r"^([0-9a-f]{32}|[0-9a-f]{40}|[0-9a-f]{64})$")


def _main() -> None:
    parser = argparse.ArgumentParser(description="同名ファイルの重複を削除する")
    parser.add_argument("-r", "--recursive", action="store_true")
    parser.add_argument("-n", "--no-hash-only", action="store_true", help="ハッシュ名以外も対象にする")
    parser.add_argument("-f", "--log-file", type=pathlib.Path, help="削除ログの追記先")
    parser.add_argument("-d", "--dry-run", action="store_true")
    parser.add_argument("pattern", type=str, help="検索 glob パターン (例: *.jpg)")
    parser.add_argument("paths", nargs="+", type=pathlib.Path)
    args = parser.parse_args()
    setup_logging()
    log_fp = args.log_file.open("a", encoding="utf-8") if args.log_file else None
    try:
        delete_homonym(
            args.paths,
            pattern=args.pattern,
            recursive=args.recursive,
            hash_only=not args.no_hash_only,
            dry_run=args.dry_run,
            log_fp=log_fp,
        )
    finally:
        if log_fp is not None:
            log_fp.close()


def delete_homonym(
    paths: list[pathlib.Path],
    *,
    pattern: str,
    recursive: bool = False,
    hash_only: bool = True,
    dry_run: bool = False,
    log_fp: io.TextIOBase | None = None,
) -> None:
    """重複ファイルを削除する。"""
    registry: dict[str, pathlib.Path] = {}
    for base in paths:
        base = base.resolve()
        logger.info("%s の処理中...", base)
        iterator = base.rglob(pattern) if recursive else base.glob(pattern)
        for file in iterator:
            if not file.is_file():
                continue
            name = file.stem.lower()
            if hash_only and not _HASH_RE.match(name):
                continue
            existing = registry.get(name)
            if existing is None:
                registry[name] = file
                continue
            existing_size = existing.stat().st_size
            current_size = file.stat().st_size
            if existing_size != current_size:
                if hash_only:
                    logger.warning("ハッシュ衝突の可能性: %s <> %s", existing, file)
                continue
            existing_mtime = existing.stat().st_mtime
            current_mtime = file.stat().st_mtime
            if existing_mtime <= current_mtime:
                _delete(file, kept=existing, dry_run=dry_run, log_fp=log_fp)
            else:
                _delete(existing, kept=file, dry_run=dry_run, log_fp=log_fp)
                registry[name] = file


def _delete(
    target: pathlib.Path,
    *,
    kept: pathlib.Path,
    dry_run: bool,
    log_fp: io.TextIOBase | None,
) -> None:
    logger.info("delete: %s by %s", target, kept)
    if log_fp is not None:
        log_fp.write(f'"{target}" by "{kept}"\n')
    if not dry_run:
        target.unlink(missing_ok=True)


if __name__ == "__main__":
    _main()
