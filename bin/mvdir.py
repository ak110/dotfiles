#!/usr/bin/env python3
"""ディレクトリをマージするスクリプト。"""
import argparse
import pathlib
import logging

logger = logging.getLogger(__name__)


def _main():
    try:
        import better_exceptions

        better_exceptions.hook()
    except BaseException:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("src_dir", type=pathlib.Path)
    parser.add_argument("dest_dir", type=pathlib.Path)
    args = parser.parse_args()
    logging.basicConfig(format="%(message)s", level="DEBUG")
    _move_dir(args.src_dir, args.dest_dir)


def _move_dir(src_dir, dest_dir):
    dest_dir.mkdir(parents=True, exist_ok=True)
    for p in src_dir.iterdir():
        try:
            if p.is_dir():
                _move_dir(p, dest_dir / p.name)
            else:
                p.rename(dest_dir / p.name)
            logger.info(p)
        except BaseException:
            logger.warning(f"Error: {p}", exc_info=True)
    src_dir.rmdir()


if __name__ == "__main__":
    _main()
