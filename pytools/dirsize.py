# PYTHON_ARGCOMPLETE_OK
"""ディレクトリサイズを集計表示する (C# dirsize の Python 移植)。

再帰的に配下のファイルサイズを合計し、サブディレクトリごとに大きさを表示する。
`-r DEPTH` で深度制限、`-a` で 1 MiB 未満も表示、`-e` で進捗を stderr に出す。
"""

import argparse
import logging
import pathlib
import sys

from pytools._internal.cli import enable_completion

logger = logging.getLogger(__name__)

_MIB = 1024 * 1024


def _main() -> None:
    parser = argparse.ArgumentParser(description="ディレクトリサイズを集計する")
    parser.add_argument("-r", "--recursive", nargs="?", const=1, type=int, default=0, help="再帰深度 (既定 0: 直下のみ)")
    parser.add_argument("-e", "--progress", action="store_true", help="進捗を stderr に出す")
    parser.add_argument("-a", "--all", action="store_true", help="1 MiB 未満も表示する")
    parser.add_argument("target", nargs="?", type=pathlib.Path, default=pathlib.Path.cwd())
    enable_completion(parser)
    args = parser.parse_args()
    total = _dir_size(args.target, depth=args.recursive, progress=args.progress, show_all=args.all, ply=0)
    print()
    print(f"{total // _MIB:8d}MiB\t.")


def _dir_size(path: pathlib.Path, *, depth: int, progress: bool, show_all: bool, ply: int) -> int:
    total = 0
    subsizes: list[tuple[str, int]] = []
    if progress and ply <= 2:
        print(f"{path} 検索中 . . .", file=sys.stderr)
    try:
        for entry in path.iterdir():
            try:
                if entry.is_file():
                    total += entry.stat().st_size
                elif entry.is_dir():
                    sub_total = _dir_size(entry, depth=depth - 1, progress=progress, show_all=show_all, ply=ply + 1)
                    subsizes.append((entry.name, sub_total))
                    total += sub_total
            except OSError:
                continue
    except OSError:
        return total
    if subsizes and depth >= 0 and (show_all or total >= _MIB):
        subsizes.sort(key=lambda x: x[1])
        print()
        print(f"{path}:")
        for name, size in subsizes:
            print(f"{size // _MIB:8d}MiB\t{name}")
    if progress and depth >= -1:
        print(f"{path} => {total // _MIB} MiB", file=sys.stderr)
    return total


if __name__ == "__main__":
    _main()
