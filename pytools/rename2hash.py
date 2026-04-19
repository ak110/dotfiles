"""ファイルを内容の MD5 ハッシュ値へリネームする (C# rename2hash の Python 移植)。

互換性維持のため既存資産にならい MD5 を使用する。重複検出用途で
暗号学的衝突耐性は不要のため、ここでは問題にならない。
"""

import argparse
import hashlib
import io
import logging
import pathlib

from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)


def _main() -> None:
    parser = argparse.ArgumentParser(description="ファイルを MD5 ハッシュ名へリネームする")
    parser.add_argument("-r", "--recursive", action="store_true")
    parser.add_argument("-f", "--log-file", type=pathlib.Path, help="リネームログの追記先")
    parser.add_argument("-d", "--dry-run", action="store_true")
    parser.add_argument("path", type=pathlib.Path)
    parser.add_argument("pattern", nargs="?", default="*.*")
    args = parser.parse_args()
    setup_logging()
    log_fp = args.log_file.open("a", encoding="utf-8") if args.log_file else None
    try:
        rename_to_hash(
            args.path,
            pattern=args.pattern,
            recursive=args.recursive,
            dry_run=args.dry_run,
            log_fp=log_fp,
        )
    finally:
        if log_fp is not None:
            log_fp.close()


def rename_to_hash(
    path: pathlib.Path,
    *,
    pattern: str = "*.*",
    recursive: bool = False,
    dry_run: bool = False,
    log_fp: io.TextIOBase | None = None,
) -> None:
    """ディレクトリ内のファイルを内容ハッシュの名前へリネームする。"""
    iterator = path.rglob(pattern) if recursive else path.glob(pattern)
    for file in iterator:
        if not file.is_file():
            continue
        try:
            digest = _md5_of_file(file)
        except OSError as e:
            logger.warning("%s: 読み込み失敗 (%s)", file, e)
            continue
        new_name = f"{digest}{file.suffix}"
        new_path = file.with_name(new_name)
        if file.name.lower() == new_name.lower():
            continue
        if new_path.exists():
            # 既存のものと同内容ならこちらが重複しているだけ。上書き的に置き換え。
            logger.info("%s -> %s (既存を置換)", file.name, new_name)
            if not dry_run:
                new_path.unlink()
                file.rename(new_path)
        else:
            logger.info("%s -> %s", file.name, new_name)
            if not dry_run:
                file.rename(new_path)
        if log_fp is not None:
            log_fp.write(f"{file}\t{new_path}\n")


def _md5_of_file(path: pathlib.Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    _main()
