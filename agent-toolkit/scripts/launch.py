"""標準ライブラリだけで起動し、配布root外の依存環境から指定スクリプトを実行する。"""

import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_toolkit._common import next_action, plugin_environment  # noqa: E402  # pylint: disable=wrong-import-position,import-error


def main() -> int:
    """CLI・hook・MCP・スキルスクリプトのargvと終了状態をuvへそのまま渡す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[1], help="配布rootの絶対パス")
    parser.add_argument("--persist-claude-env", action="store_true", help="SessionStartのBash環境ファイルへCLIのPATHを追記する")
    parser.add_argument("target", type=Path, help="root内のスクリプトの相対パスまたは絶対パス")
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="対象スクリプトへそのまま渡す引数")
    options = parser.parse_args()
    root = options.project.resolve()
    target = (root / options.target).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        parser.error(
            next_action.with_next_action(
                "targetは指定された配布root内の実在するスクリプトである必要がある",
                "配布root内のスクリプトのパスをtargetへ指定して再実行してください。",
            )
        )
    try:
        if options.persist_claude_env:
            plugin_environment.persist_claude_environment(root, os.environ)
        environment = plugin_environment.launch_environment(root, os.environ)
    except OSError as exc:
        next_action.report(
            f"起動環境を準備できないため、agent-toolkitを起動できません: {exc}",
            next_action="配布rootの読取権限と、CLAUDE_ENV_FILEが指定されていればその書込権限を確かめてください。",
        )
        return 1
    uv = shutil.which("uv")
    if uv is None:
        next_action.report(
            "uvが見つからないため、agent-toolkitを起動できません。",
            next_action="uvを導入し、同じコマンドを再実行してください。",
        )
        return 127
    arguments = [uv, "run", "--project", str(root), "--locked", "--no-default-groups", str(target), *options.arguments]
    os.execve(uv, arguments, environment)
    return 1


if __name__ == "__main__":
    sys.exit(main())
