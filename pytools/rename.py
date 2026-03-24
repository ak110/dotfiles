"""正規表現でリネーム。"""

import argparse
import pathlib
import re


def _main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("-d", "--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("pattern", type=str)
    parser.add_argument("replacement", type=str)
    parser.add_argument("targets", nargs="*", type=pathlib.Path)
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--stem", action="store_true", help="replace stem only. (default)")
    g.add_argument("--name", action="store_true", help="replace name only.")
    g.add_argument("--fullpath", action="store_true", help="replace fullpath.")
    args = parser.parse_args()

    targets = args.targets
    if len(targets) <= 0:
        targets = list(pathlib.Path(".").glob("*"))

    flags = 0
    if args.ignore_case:
        flags |= re.IGNORECASE
    regex = re.compile(args.pattern, flags=flags)

    for src_path in targets:
        try:
            if args.fullpath:
                dst_path = pathlib.Path(regex.sub(args.replacement, str(src_path)))
                if src_path == dst_path:
                    continue
                print(f"{src_path} -> {dst_path}")
            elif args.name:
                dst_name = regex.sub(args.replacement, src_path.name).strip()
                dst_path = src_path.parent / dst_name
                if src_path == dst_path:
                    continue
                print(f"{src_path.name} -> {dst_name}")
            else:
                dst_stem = regex.sub(args.replacement, src_path.stem).strip()
                dst_path = src_path.parent / (dst_stem + src_path.suffix)
                if src_path == dst_path:
                    continue
                print(f"{src_path.stem} -> {dst_stem}")
            if not args.dry_run:
                src_path.rename(dst_path)
        except Exception as e:
            print(f"{src_path}: rename failed ({e})")


if __name__ == "__main__":
    _main()
