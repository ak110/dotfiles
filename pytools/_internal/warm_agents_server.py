"""``agents_server_mcp.py``のuvプロジェクト環境を事前構築する。"""

import sys
from pathlib import Path

from pytools._internal import claude_common, plugin_warmup

_TAG = "agents_server warmup"
_PLUGIN_ID = f"agent-toolkit@{claude_common.MARKETPLACE_NAME}"
_SCRIPT_RELATIVE = Path("agent_toolkit") / "agents_server_mcp.py"
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    from pytools._internal.cli import setup_logging  # pylint: disable=import-outside-toplevel

    setup_logging()
    run()
    sys.exit(0)


def run() -> bool:
    """実際に参照される``agents_server_mcp.py``の環境を構築する。

    ウォームアップはキャッシュ構築だけを行うため、個別の失敗を後処理全体の失敗にしない。
    """
    return plugin_warmup.run(_targets, tag=_TAG, arguments=("--check-dependencies",))


def _targets() -> list[Path]:
    """Claude CodeとCodexの実参照先を重複なく列挙する。"""
    return plugin_warmup.existing_targets([*_claude_plugin_scripts(), _codex_plugin_script()], tag=_TAG)


def _claude_plugin_scripts() -> list[Path]:
    """Claude Codeプラグインのインストール先を返す。"""
    return plugin_warmup.claude_plugin_scripts(
        _INSTALLED_PLUGINS_PATH,
        plugin_id=_PLUGIN_ID,
        relative_path=_SCRIPT_RELATIVE,
        tag=_TAG,
    )


def _codex_plugin_script() -> Path | None:
    """Codexが参照する有効版プラグインキャッシュ内のスクリプトを返す。"""
    return plugin_warmup.codex_plugin_script(
        plugin_id=_PLUGIN_ID,
        plugin_name="agent-toolkit",
        relative_path=_SCRIPT_RELATIVE,
        tag=_TAG,
    )


if __name__ == "__main__":
    main()
