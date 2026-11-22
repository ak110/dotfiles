# PYTHON_ARGCOMPLETE_OK

import argparse
import datetime
import logging
import pathlib
import sys

from pytools._internal.cli import enable_completion, setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="更新日時に応じてファイルを再配置する")
    parser.add_argument("-d", "--dry-run", action="store_true")
    parser.add_argument("targets", nargs="+", type=pathlib.Path)
    enable_completion(parser)
    args = parser.parse_args()
    setup_logging()
    failures = 0
    for target in args.targets:
        if not target.is_dir():
            failures += 1
            logger.warning(
                ("%s はディレクトリではありません。スキップして続行します。存在するディレクトリを指定して再実行してください。"),
                target,
            )
            continue
        relocate(target, dry_run=args.dry_run)
    sys.exit(1 if failures else 0)


def relocate(target: pathlib.Path, *, dry_run: bool = False) -> None:
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
    main()
