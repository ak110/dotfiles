# PYTHON_ARGCOMPLETE_OK
"""実行中プロセスの実行ファイルパスを検索する (C# psgrep の Python 移植)。

元 C# 実装は grep 部分が未実装だったため、ここで本来意図されていた機能を
`psutil` でクロスプラットフォームに新規実装する。
"""

import argparse
import fnmatch
import logging
import re
import sys

import psutil

from pytools._internal.cli import enable_completion, setup_logging

logger = logging.getLogger(__name__)


def _main() -> None:
    parser = argparse.ArgumentParser(description="プロセスの実行ファイルパスを検索する")
    parser.add_argument("pattern", type=str, help="検索パターン")
    parser.add_argument("--regex", action="store_true", help="正規表現マッチ (既定は部分一致/ワイルドカード)")
    parser.add_argument("-i", "--ignore-case", action="store_true")
    enable_completion(parser)
    args = parser.parse_args()
    setup_logging()
    found = grep(args.pattern, regex=args.regex, ignore_case=args.ignore_case)
    for pid, exe in found:
        print(f"{pid}\t{exe}")
    sys.exit(0 if found else 1)


def grep(pattern: str, *, regex: bool = False, ignore_case: bool = False) -> list[tuple[int, str]]:
    """検索パターンにマッチするプロセスの (pid, exe) リストを返す。"""
    flags = re.IGNORECASE if ignore_case else 0
    compiled = re.compile(pattern, flags=flags) if regex else None
    needle = pattern.lower() if ignore_case else pattern
    results: list[tuple[int, str]] = []
    for proc in psutil.process_iter(["pid", "exe", "name"]):
        try:
            exe = proc.info.get("exe") or proc.info.get("name") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if not exe:
            continue
        if compiled is not None:
            if compiled.search(exe):
                results.append((proc.info["pid"], exe))
        else:
            haystack = exe.lower() if ignore_case else exe
            if fnmatch.fnmatch(haystack, f"*{needle}*") or needle in haystack:
                results.append((proc.info["pid"], exe))
    return results


if __name__ == "__main__":
    _main()
