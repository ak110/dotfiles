"""正規表現でディレクトリ削除。"""

import argparse
import pathlib
import re
import shutil


def _main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("-d", "--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("patterns", nargs="*", type=str)
    parser.add_argument("target", type=pathlib.Path)
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--name", action="store_true", help="replace name only. (default)")
    g.add_argument("--fullpath", action="store_true", help="replace fullpath.")
    args = parser.parse_args()

    flags = 0
    if args.ignore_case:
        flags |= re.IGNORECASE
    regex_list = [re.compile(pattern, flags=flags) for pattern in args.patterns]

    for path in args.target.rglob("*"):
        if not path.is_dir():
            continue
        try:
            if args.fullpath:
                if not _match(regex_list, str(path)):
                    continue
            else:
                if not _match(regex_list, path.name):
                    continue
            print(f"{path}")
            if not args.dry_run:
                shutil.rmtree(path)
        except Exception as e:
            print(f"{path}: rmtree failed ({e})")


def _match(regex_list: list[re.Pattern], value: str) -> bool:
    for regex in regex_list:
        if regex.search(value):
            return True
    return False


if __name__ == "__main__":
    _main()
