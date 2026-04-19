"""ファイルを更新日時に基づいてサフィックス付きディレクトリへ再配置する。

`<dir>` 配下のファイルを、それぞれの最終更新日時 `YYYYMM` に応じて
`<dir>-YYYYMM/` へ移動する。元 C# 実装 (DateRelocator.cs) の Python 移植。
"""

import argparse
import datetime
import logging
import pathlib

from pytools._internal.cli import setup_logging

logger = logging.getLogger(__name__)


def _main() -> None:
    parser = argparse.ArgumentParser(description="更新日時に応じてファイルを再配置する")
    parser.add_argument("-d", "--dry-run", action="store_true")
    parser.add_argument("targets", nargs="+", type=pathlib.Path)
    args = parser.parse_args()
    setup_logging()
    for target in args.targets:
        if not target.is_dir():
            logger.warning("%s はディレクトリではありません", target)
            continue
        relocate(target, dry_run=args.dry_run)


def relocate(target: pathlib.Path, *, dry_run: bool = False) -> None:
    """`target` 配下の全ファイルを日付サフィックスディレクトリへ再配置する。"""
    target = target.resolve()
    for file in target.rglob("*"):
        if not file.is_file():
            continue
        mtime = datetime.datetime.fromtimestamp(file.stat().st_mtime)
        new_root = target.with_name(f"{target.name}-{mtime:%Y%m}")
        rel = file.relative_to(target)
        new_file = new_root / rel
        if new_file.exists():
            continue
        logger.info("%s -> %s", file, new_file)
        if not dry_run:
            new_file.parent.mkdir(parents=True, exist_ok=True)
            file.rename(new_file)


if __name__ == "__main__":
    _main()
