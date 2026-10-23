"""Antigravity向け配布rootを原本から生成する。"""

import argparse
import sys

from pytools._internal import antigravity_plugin, common


def main(argv: list[str] | None = None) -> int:
    """通常同期または非変更検査を実行する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="生成物の差分だけを検査する")
    parser.add_argument(
        "--plugin",
        action="append",
        choices=antigravity_plugin.PLUGIN_NAMES,
        help="対象プラグイン。繰り返し指定でき、省略時はtoolkitとbasicsの両方を同期する",
    )
    args = parser.parse_args(argv)
    root = common.find_dotfiles_root()
    if root is None:
        parser.error("dotfilesの作業ツリーを解決できません")
    plugins = tuple(dict.fromkeys(args.plugin or antigravity_plugin.PLUGIN_NAMES))
    if args.check:
        diagnostics = tuple(message for name in plugins for message in antigravity_plugin.check_diagnostics(root, name))
        for diagnostic in diagnostics:
            print(diagnostic, file=sys.stderr)
        return 1 if diagnostics else 0
    for name in plugins:
        antigravity_plugin.sync(root, name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
