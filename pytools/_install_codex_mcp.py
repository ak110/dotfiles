"""codex MCPサーバーを user scope に自動登録する。

`chezmoi apply` 後処理 (`pytools.post_apply`) から呼ばれる。
`claude mcp add --scope=user codex codex mcp-server` を冪等に実行する。

前提条件を満たさない場合は完全にスキップする
(dotfiles apply 全体を失敗させないための安全側動作)。

前提条件:

1. `claude` CLI が PATH にある

冪等動作:

- `claude mcp list` の出力に `codex:` 行があれば既登録としてスキップ
- 無ければ `claude mcp add --scope user codex codex mcp-server` で登録
"""

import json
import logging
import shutil
from pathlib import Path

from pytools import _install_claude_plugins, _log_format

logger = logging.getLogger(__name__)

_CODEX_NAME = "codex"
_CODEX_COMMAND = "codex"
_CODEX_ARGS = ("mcp-server",)

# Claude Code設定ファイルのパス (CLI呼び出しを回避するための直接読み取り用)
_CLAUDE_CONFIG_PATH = Path.home() / ".claude.json"


def _is_codex_registered_from_file() -> bool | None:
    """.claude.jsonを直接読み取り、codex MCPサーバーの登録状態を判定する。

    Returns:
        True: mcpServersにcodexキーが存在する（登録済み）。
        False: mcpServersは存在するがcodexキーが無い（未登録）。
        None: 読み取り失敗（CLIフォールバックが必要）。
    """
    try:
        data = json.loads(_CLAUDE_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    mcp_servers = data.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        return None
    return _CODEX_NAME in mcp_servers


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    logging.basicConfig(format="%(message)s", level="INFO")
    run()


def run() -> bool:
    """Codex MCPサーバーを user scope に登録する。

    Returns:
        新たに登録した場合 True。既登録・CLI不在などでスキップした場合 False。
    """
    if shutil.which("claude") is None:
        logger.info(_log_format.format_status("codex-mcp", "claude CLI 未検出のためスキップ"))
        return False

    # ファイル直接読み取りを先に試み、登録済みならCLI呼び出しを省略する
    file_check = _is_codex_registered_from_file()
    if file_check is True:
        logger.info(_log_format.format_status("codex-mcp", "登録済み"))
        return False
    if file_check is None and _is_codex_registered():
        logger.info(_log_format.format_status("codex-mcp", "登録済み"))
        return False

    args = ["mcp", "add", "--scope", "user", _CODEX_NAME, _CODEX_COMMAND, *_CODEX_ARGS]
    result = _install_claude_plugins._run_claude(args)  # pylint: disable=protected-access
    if result is None or result.returncode != 0:
        # タイムアウトで list が失敗 → 未登録と誤判定 → add が "already exists" で
        # 失敗するケースがある。この場合は実際には登録済みなので成功扱いにする
        stderr = result.stderr.strip() if result else ""
        if result is not None and "already exists" in result.stderr:
            logger.info(_log_format.format_status("codex-mcp", "登録済み (add が already exists を返却)"))
            return False
        logger.info(_log_format.format_status("codex-mcp", f"登録に失敗 (続行): {stderr}"))
        return False
    logger.info(_log_format.format_status("codex-mcp", "user scope に登録しました"))
    return True


def _is_codex_registered() -> bool:
    """`claude mcp list` の出力に codex サーバーが含まれているか判定する。"""
    result = _install_claude_plugins._run_claude(["mcp", "list"])  # pylint: disable=protected-access
    if result is None or result.returncode != 0:
        # list が失敗した場合は未登録扱いにし、後続の add 試行で改めて判定する
        # (add は登録済みの場合に非ゼロ終了するため冪等性が保たれる)
        return False
    # 出力の各行は `<name>: <command/url> - <status>` 形式。先頭の name が codex かで判定する
    return any(line.strip().startswith(f"{_CODEX_NAME}:") for line in result.stdout.splitlines())


if __name__ == "__main__":
    _main()
