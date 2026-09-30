"""``agents_server_mcp.py``のuvプロジェクト環境を事前構築する。"""

import sys
from pathlib import Path

from pytools._internal import claude_common, plugin_warmup

_TAG = "agents_server warmup"
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

    初回MCP起動の成立条件であるため、個別の失敗は後処理全体へ伝播する。
    """
    return plugin_warmup.run(
        _targets,
        tag=_TAG,
        arguments=("--check-dependencies",),
        fail_on_error=True,
    )


def _targets() -> list[Path]:
    """Claude CodeとCodexの実参照先を重複なく列挙する。"""
    return plugin_warmup.agent_toolkit_targets(
        _INSTALLED_PLUGINS_PATH,
        claude_relative=_SCRIPT_RELATIVE,
        codex_relative=_SCRIPT_RELATIVE,
        tag=_TAG,
    )


if __name__ == "__main__":
    main()
