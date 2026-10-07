"""`atk info`の登録と実行。現在の実行文脈とplugin配布元の情報を読み取り専用で表示する。"""

import argparse
import json
import pathlib
import sys

from agent_toolkit._atk import config as _config_cmd
from agent_toolkit._common import state_paths as _state_paths


def build_parser(parser: argparse.ArgumentParser) -> None:
    """`atk info`は引数を持たない。登録表の形をそろえるために置く。"""
    del parser


def dispatch(args: argparse.Namespace) -> None:
    """現在の実行文脈とplugin配布元の情報を読み取り専用で表示する。"""
    del args
    plugin_root = pathlib.Path(__file__).resolve().parents[2]
    manifest = plugin_root / "plugin.json"
    try:
        version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError):
        version = "不明"
    config_file = _config_cmd._config_file_path()  # pylint: disable=protected-access
    print(f"作業ディレクトリ: {pathlib.Path.cwd()}")
    print(f"起動ファイル: {pathlib.Path(sys.argv[0]).resolve()}")
    print(f"plugin root: {plugin_root}")
    print(f"plugin version (plugin.json): {version}")
    print(f"設定ファイル: {config_file}{'' if config_file.is_file() else '（未作成）'}")
    print(f"状態ディレクトリ: {_state_paths.state_dir()}")
