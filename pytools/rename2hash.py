# PYTHON_ARGCOMPLETE_OK
"""ファイルを内容の MD5 ハッシュ値へリネームする (C# rename2hash の Python 移植)。

互換性維持のため既存資産にならい MD5 を使用する。重複検出用途で
暗号学的衝突耐性は不要のため、ここでは問題にならない。
"""

import argparse
import contextlib
import hashlib
import io
import logging
import pathlib
import sys

from pytools._internal.cli import enable_completion, setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """ファイルをMD5ハッシュ名へリネームするエントリポイント。"""
    parser = argparse.ArgumentParser(description="ファイルを MD5 ハッシュ名へリネームする")
    parser.add_argument("-r", "--recursive", action="store_true")
    parser.add_argument("-f", "--log-file", type=pathlib.Path, help="リネームログの追記先")
    parser.add_argument("-d", "--dry-run", action="store_true")
    parser.add_argument("path", type=pathlib.Path)
    parser.add_argument("pattern", nargs="?", default="*.*")
    enable_completion(parser)
    args = parser.parse_args()
    setup_logging()
    with args.log_file.open("a", encoding="utf-8") if args.log_file else contextlib.nullcontext() as log_fp:
        failures = rename_to_hash(
            args.path,
            pattern=args.pattern,
            recursive=args.recursive,
            dry_run=args.dry_run,
            log_fp=log_fp,
        )
    sys.exit(1 if failures else 0)


def rename_to_hash(
    path: pathlib.Path,
    *,
    pattern: str = "*.*",
    recursive: bool = False,
    dry_run: bool = False,
    log_fp: io.TextIOBase | None = None,
) -> int:
    """ディレクトリ内のファイルを内容ハッシュの名前へリネームし、読み込みに失敗した件数を返す。"""
    failures = 0
    iterator = path.rglob(pattern) if recursive else path.glob(pattern)
    for file in iterator:
        if not file.is_file():
            continue
        try:
            digest = _md5_of_file(file)
        except OSError as e:
            failures += 1
            logger.warning(
                (
                    "%s: 読み込み失敗 (%s)。"
                    "このファイルを保持して続行します。"
                    "読み取り権限と使用中のアプリを確認して再実行してください。"
                ),
                file,
                e,
            )
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
    return failures


def _md5_of_file(path: pathlib.Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if __name__ == "__main__":
    main()
