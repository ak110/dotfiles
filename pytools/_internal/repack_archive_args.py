"""repack-archiveの引数を、設定モデルと展開処理の読み込み前に解釈する。"""

import argparse
import pathlib

from pytools._internal.cli import enable_completion


def parse_args() -> argparse.Namespace:
    """既存の引数と対象パスを解釈する。"""
    parser = argparse.ArgumentParser(description="アーカイブ・PDF を gv 向けに前処理する")
    parser.add_argument("-c", "--config", type=pathlib.Path, help="YAML 設定ファイル")
    parser.add_argument(
        "-b", "--backup-dir", type=pathlib.Path, help="バックアップ先 (省略時: 対象ファイルのあるディレクトリ/bk)"
    )
    parser.add_argument("--no-trash", action="store_true", help="バックアップをゴミ箱送りしない")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("targets", nargs="+", type=pathlib.Path)
    enable_completion(parser)
    return parser.parse_args()
