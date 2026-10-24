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


# 実行中のplugin root。テストは配布形式ごとのmanifest配置を再現した一時ディレクトリへ差し替える。
_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]

# 配布形式ごとの正規manifest。Codex配布物はroot直下の`plugin.json`を持たず、
# Claude Code配布物とソース配置は全てを持つため、実在する最初のmanifestを使う。
_MANIFEST_CANDIDATES = (
    pathlib.Path(".codex-plugin/plugin.json"),
    pathlib.Path(".claude-plugin/plugin.json"),
    pathlib.Path("plugin.json"),
)


def _plugin_version(plugin_root: pathlib.Path) -> str | None:
    """Plugin root自身の正規manifestから版数を返す。どれからも得られなければNoneを返す。"""
    for relative in _MANIFEST_CANDIDATES:
        try:
            manifest = json.loads((plugin_root / relative).read_text(encoding="utf-8"))
        except OSError, ValueError:
            continue
        version = manifest.get("version") if isinstance(manifest, dict) else None
        if isinstance(version, str) and version:
            return version
    return None


def dispatch(args: argparse.Namespace) -> None:
    """現在の実行文脈とplugin配布元の情報を読み取り専用で表示する。"""
    del args
    plugin_root = _PLUGIN_ROOT
    version = _plugin_version(plugin_root) or "不明"
    config_file = _config_cmd._config_file_path()  # pylint: disable=protected-access
    print(f"作業ディレクトリ: {pathlib.Path.cwd()}")
    print(f"起動ファイル: {pathlib.Path(sys.argv[0]).resolve()}")
    print(f"plugin root: {plugin_root}")
    print(f"plugin version (plugin.json): {version}")
    print(f"設定ファイル: {config_file}{'' if config_file.is_file() else '（未作成）'}")
    print(f"状態ディレクトリ: {_state_paths.state_dir()}")
