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

from pytools._internal import winutils
from pytools._internal.cli import enable_completion, setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    """ファイル・ディレクトリの更新日時を変更するエントリポイント。"""
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
    sys.exit(0)


def _print_targets(targets: list[pathlib.Path]) -> None:
    """対象一覧と現在 mtime を表示する。

    シンボリックリンクとジャンクションは `touch_path` がリンク自身を処理するため、
    表示側も同じ判定を行う（参照先消失時の挙動を実処理と揃える）。
    `lstat` を使い、リンクの場合はリンク自身の mtime を表示する。
    """
    print("対象ファイル：")
    for target in targets:
        if _is_link(target) or target.exists():
            mtime = datetime.datetime.fromtimestamp(target.lstat().st_mtime)
            print(f"{target.name}: {mtime}")
        else:
            print(f"{target.name}: (存在しません)")
    print()


def touch_path(path: pathlib.Path, time: datetime.datetime) -> None:
    """対象パスの mtime/atime を `time` に設定する。

    ディレクトリ指定時は配下を再帰的に処理する。シンボリックリンクとジャンクションは
    追跡せず、どのOSでもリンク自身の時刻を更新する。存在しないパスは警告ログのみで継続する。
    `pathlib` には任意時刻を指定する API が無いため `os.utime` を使う。
    """
    print(f"{path} . . .")
    timestamp = time.timestamp()
    if _is_link(path):
        _touch_link(path, timestamp)
    elif path.is_file():
        os.utime(path, (timestamp, timestamp))
    elif path.is_dir():
        os.utime(path, (timestamp, timestamp))
        for child in path.iterdir():
            touch_path(child, time)
    else:
        logger.warning(
            ("対象が存在しません: %s。スキップして続行します。更新が必要なら存在するパスを指定して再実行してください。"), path
        )


def _is_link(path: pathlib.Path) -> bool:
    """シンボリックリンクまたはWindowsのディレクトリジャンクションかを返す。"""
    return path.is_symlink() or path.is_junction()


def _touch_link(path: pathlib.Path, timestamp: float) -> None:
    """リンク自身の atime/mtime を設定する。"""
    if os.utime in os.supports_follow_symlinks:
        os.utime(path, (timestamp, timestamp), follow_symlinks=False)
    else:
        winutils.set_link_times(str(path), timestamp)


def _wait_keypress() -> None:
    """Windows でキー入力待ちを行う。msvcrt 未導入環境では no-op。"""
    try:
        msvcrt = winutils.import_msvcrt()
    except ImportError:
        return
    print("終了するには何かキーを押してください . . .")
    msvcrt.getch()


if __name__ == "__main__":
    main()
