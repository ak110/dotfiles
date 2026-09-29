"""CLIの標準出力を指定ファイルへ保存する共通処理と、エージェント環境での長い出力の自動退避。"""

from __future__ import annotations

import argparse
import contextlib
import io
import pathlib
import sys
from collections.abc import Callable, Iterator

from agent_toolkit._atk import outcome as _outcome

AUTO_SAVE_THRESHOLD_BYTES = 16 * 1024
"""エージェント環境で標準出力を自動退避するUTF-8のバイト数の閾値。

Codexのシェル出力上限への対策としてPreToolUseフックが大きな読取を遮断する閾値
（`_hooks/pretooluse/large_reads.py`の`_DEFAULT_BYTE_THRESHOLD`）と同じ値とし、
エージェントが1回のツール結果として受け取れる量に合わせる。
"""

_AUTO_SAVE_FILE_NAME = "output.txt"


def add_output_file_arg(parser: argparse.ArgumentParser) -> None:
    """標準出力の保存先を受け取る共通オプションを追加する。"""
    parser.add_argument(
        "--output-file",
        metavar="PATH",
        type=pathlib.Path,
        default=None,
        help="標準出力を指定した絶対パスのファイルへ保存し、保存先パスと保存した行数を表示する。"
        "エージェント環境で本オプションを省略した場合、標準出力がUTF-8で16384バイトを超えると"
        "全量を新しい管理対象一時領域へ自動で保存し、同じ形式で保存先と行数だけを表示する。",
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
        _report_saved(resolved, after_save)


@contextlib.contextmanager
def auto_save(
    create_directory: Callable[[], pathlib.Path],
    *,
    after_save: Callable[[pathlib.Path], None] | None = None,
) -> Iterator[None]:
    """標準出力を受け取り、閾値を超える場合だけ全量を新しいファイルへ保存して要約行を書く。

    エージェントが長い出力の保存先を毎回組み立てずに済むよう、`--output-file`の指定時と同じ要約行へ置き換える。
    保存先のディレクトリは実行ごとに`create_directory`が新しく作成する。作成または書き込みに失敗した場合は
    出力を失わないよう、警告を標準エラーへ書いて全量を標準出力へ書く。
    サブコマンドが`SystemExit`や例外で終わる場合も、それまでの出力を同じ規則で書いてから元の終了を伝える。
    """
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            yield
    finally:
        text = buffer.getvalue()
        if len(text.encode("utf-8")) <= AUTO_SAVE_THRESHOLD_BYTES:
            sys.stdout.write(text)
        else:
            try:
                saved = create_directory() / _AUTO_SAVE_FILE_NAME
                with saved.open("x", encoding="utf-8", newline="") as stream:
                    stream.write(text)
            except Exception as error:  # noqa: BLE001  # 保存の失敗で本来の出力を失わない
                _outcome.report_warning(f"長い出力を自動で保存できなかったため全量を表示する: {error}")
                sys.stdout.write(text)
            else:
                _report_saved(saved.resolve(), after_save)


def _report_saved(resolved: pathlib.Path, after_save: Callable[[pathlib.Path], None] | None) -> None:
    """保存先、行数および指定された内訳を標準出力へ書く。"""
    with resolved.open(encoding="utf-8", newline="") as saved_stream:
        line_count = sum(1 for _line in saved_stream)
    print(f"保存先: {resolved}")
    print(f"行数: {line_count}")
    if after_save is not None:
        after_save(resolved)
