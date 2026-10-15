"""Codexの完成済み旧パッケージを整理するCLI。"""

import argparse
import io
import sys

import argcomplete

from pytools._internal import prune_codex_packages


def main(argv: list[str] | None = None) -> int:
    """実効CODEX_HOMEの選択中・使用中の版を保持して整理する。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="CODEX_HOME（未設定時は~/.codex）のCodex旧パッケージを整理する")
    parser.add_argument("--dry-run", action="store_true", help="保持理由と削除候補を表示し、パッケージを保持する")
    argcomplete.autocomplete(parser)
    args = parser.parse_args(argv)
    result = prune_codex_packages.prune(dry_run=args.dry_run)
    for path, reasons in result.retained.items():
        print(f"保持: {path} ({', '.join(reasons)})")
    for path in result.removed:
        print(f"{'削除候補' if args.dry_run else '削除'}: {path}")
    for message in result.deferred:
        print(f"整理を延期: {message}", file=sys.stderr)
    for message in result.failures:
        print(f"{message}。原因を解消し、`prune-codex-packages`を再実行する", file=sys.stderr)
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
