"""pyfltr MCPサーバーの`uvx`ツール環境を事前構築する。

Claude CodeとCodexはagent-toolkit pluginのMCP定義から`uvx --from <要求指定> pyfltr mcp`を起動する。
ツール環境が未構築だと初回起動でパッケージの取得と環境構築が起動時間に加わり、
MCPクライアントの起動上限を超えるとpyfltr MCPを利用できない状態でセッションが始まる。
MCP定義の起動形の末尾`mcp`を`--version`へ置き換えて1回起動し、MCP起動が再利用する同じツール環境を構築する。
要求指定はMCP定義から読み、本モジュールへ書き写さない。
"""

import json
import logging
import sys
from pathlib import Path

from pytools._internal import claude_common, log_format, plugin_warmup

logger = logging.getLogger(__name__)

_TAG = "pyfltr MCP warmup"
_SERVER_NAME = "pyfltr"
# Claude Codeは`.mcp.json`、Codexは`.codex-plugin/plugin.json`の`mcpServers`が指す`.mcp.codex.json`を読む。
_CLAUDE_MCP_RELATIVE = Path(".mcp.json")
_CODEX_MCP_RELATIVE = Path(".mcp.codex.json")
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    from pytools._internal.cli import setup_logging  # pylint: disable=import-outside-toplevel

    setup_logging()
    run()
    sys.exit(0)


def run() -> bool:
    """Claude CodeとCodexのMCP定義が指すpyfltrのツール環境を構築する。

    初回MCP起動の成立条件であるため、個別の失敗は後処理全体へ伝播する。

    Returns:
        常にFalse。uvのキャッシュだけへ作用し、設定を変更しないため。
    """
    uvx = claude_common.resolve_executable("uvx", preferred_directories=(Path.home() / ".local" / "bin",))
    if uvx is None:
        message = "uvx CLI が見つからず環境構築を開始できない"
        logger.warning(log_format.format_status(_TAG, message))
        raise RuntimeError(message)
    definitions = plugin_warmup.agent_toolkit_targets(
        _INSTALLED_PLUGINS_PATH,
        claude_relative=_CLAUDE_MCP_RELATIVE,
        codex_relative=_CODEX_MCP_RELATIVE,
        tag=_TAG,
    )
    if not definitions:
        message = "MCP定義が見つからず環境構築を開始できない"
        logger.warning(log_format.format_status(_TAG, message))
        raise RuntimeError(message)
    # 同じ起動形は同じツール環境を使うため、定義ファイルをまとめて1回だけ起動する。
    launches: dict[tuple[str, ...], list[Path]] = {}
    for definition in definitions:
        launches.setdefault(_warmup_arguments(definition), []).append(definition)
    for arguments, sources in launches.items():
        target = f"uvx {' '.join(arguments)} ({', '.join(log_format.home_short(path) for path in sources)})"
        plugin_warmup.run_command([str(uvx), *arguments], target=target, tag=_TAG, fail_on_error=True)
    return False


def _warmup_arguments(definition: Path) -> tuple[str, ...]:
    """MCP定義の`pyfltr`の起動形から、末尾の`mcp`を`--version`へ置き換えた`uvx`の引数列を返す。"""
    short = log_format.home_short(definition)
    try:
        data = json.loads(definition.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"MCP定義を読めない: {short}: {exc}") from exc
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    server = servers.get(_SERVER_NAME) if isinstance(servers, dict) else None
    if not isinstance(server, dict):
        raise RuntimeError(f"MCP定義に{_SERVER_NAME}が無い: {short}")
    command = server.get("command")
    args = server.get("args")
    if (
        command != "uvx"
        or not isinstance(args, list)
        or len(args) < 2
        or not all(isinstance(arg, str) for arg in args)
        or args[-1] != "mcp"
    ):
        raise RuntimeError(f"{_SERVER_NAME}の起動形が`uvx ... mcp`ではない: {short}: {command} {args}")
    return (*args[:-1], "--version")


if __name__ == "__main__":
    main()
