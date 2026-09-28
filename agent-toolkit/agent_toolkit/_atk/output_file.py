"""CLIの標準出力を指定ファイルへ保存する共通処理。"""

from __future__ import annotations

import argparse
import contextlib
import pathlib
from collections.abc import Callable, Iterator


def add_output_file_arg(parser: argparse.ArgumentParser) -> None:
    """標準出力の保存先を受け取る共通オプションを追加する。"""
    parser.add_argument(
        "--output-file",
        metavar="PATH",
        type=pathlib.Path,
        default=None,
        help="標準出力を指定した絶対パスのファイルへ保存し、保存先パスと保存した行数を表示する。",
    )
    parser.set_defaults(subparser=parser)


@contextlib.contextmanager
def redirect(path: pathlib.Path, *, after_save: Callable[[pathlib.Path], None] | None = None) -> Iterator[None]:
    """標準出力をUTF-8ファイルへ保存し、離脱時に保存先、行数および指定された内訳を報告する。"""
    resolved = path.resolve(strict=False)
    stream = resolved.open("w", encoding="utf-8", newline="")
    try:
        with stream, contextlib.redirect_stdout(stream):
            yield
    finally:
        if not stream.closed:
            stream.flush()
            stream.close()
        with resolved.open(encoding="utf-8", newline="") as saved_stream:
            line_count = sum(1 for _line in saved_stream)
        print(f"保存先: {resolved}")
        print(f"行数: {line_count}")
        if after_save is not None:
            after_save(resolved)
