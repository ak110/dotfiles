# PYTHON_ARGCOMPLETE_OK
"""ファイル・ディレクトリの更新日時を変更するCLI。

複数の対象を引数で受け取り、指定日時または現在日時に mtime/atime を一括設定する。
ディレクトリ指定時は配下を再帰的に処理する。
"""

import argparse
import datetime
import logging
import os
import pathlib
import sys
import typing

from pytools._internal.cli import enable_completion, setup_logging

logger = logging.getLogger(__name__)


def _main() -> None:
    parser = argparse.ArgumentParser(description="ファイル・ディレクトリの更新日時を変更する。")
    parser.add_argument("targets", nargs="+", type=pathlib.Path, help="対象ファイルまたはディレクトリ")
    parser.add_argument(
        "-t",
        "--time",
        type=str,
        default=None,
        help="設定する日時 (ISO 8601 形式)。省略時は対話入力",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    enable_completion(parser)
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    _print_targets(args.targets)

    interactive = args.time is None
    if interactive:
        try:
            time_string = input("日時を入力してください。空の場合は現在日時を設定します。: ").strip()
        except EOFError:
            time_string = ""
    else:
        time_string = args.time

    target_time = datetime.datetime.fromisoformat(time_string) if time_string else datetime.datetime.now()

    for target in args.targets:
        touch_path(target, target_time)

    # Windowsの「送る」経由実行ではコマンドプロンプトが自動で閉じるため、
    # 結果を確認できるようキー入力待ちで停止する (C# 版踏襲)。
    if interactive and sys.platform == "win32":
        _wait_keypress()


def _print_targets(targets: list[pathlib.Path]) -> None:
    """対象一覧と現在 mtime を表示する。

    シンボリックリンクは `touch_path` がリンク自身を処理するため、
    表示側も `is_symlink()` を含めて判定する（参照先消失時の挙動を実処理と揃える）。
    `lstat` を使い、リンクの場合は参照先ではなくリンク自身の mtime を表示する。
    """
    print("対象ファイル：")
    for target in targets:
        if target.is_symlink() or target.exists():
            mtime = datetime.datetime.fromtimestamp(target.lstat().st_mtime)
            print(f"{target.name}: {mtime}")
        else:
            print(f"{target.name}: (存在しません)")
    print()


def touch_path(path: pathlib.Path, time: datetime.datetime) -> None:
    """対象パスの mtime/atime を `time` に設定する。

    ディレクトリ指定時は配下を再帰的に処理する。シンボリックリンクは追跡せず、
    リンク自身の時刻を更新する。存在しないパスは警告ログのみで継続する。
    `pathlib` には任意時刻を指定する API が無いため `os.utime` を使う。
    """
    print(f"{path} . . .")
    timestamp = time.timestamp()
    if path.is_symlink():
        os.utime(path, (timestamp, timestamp), follow_symlinks=False)
    elif path.is_file():
        os.utime(path, (timestamp, timestamp))
    elif path.is_dir():
        os.utime(path, (timestamp, timestamp))
        for child in path.iterdir():
            touch_path(child, time)
    else:
        logger.warning("対象が存在しません: %s", path)


def _wait_keypress() -> None:
    """Windows でキー入力待ちを行う。msvcrt 未導入環境では no-op。

    `msvcrt` は Windows 専用の標準モジュールで、Linux 上の型チェッカが属性アクセスを
    `reportAttributeAccessIssue` 等として誤検出するため、`importlib` 経由で `Any` 型として扱う。
    """
    import importlib  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    try:
        msvcrt: typing.Any = importlib.import_module("msvcrt")
    except ImportError:
        return
    print("終了するには何かキーを押してください . . .")
    msvcrt.getch()


if __name__ == "__main__":
    _main()
