"""Codex設定に残るClaude MCP登録を削除する。"""

from pytools._internal import claude_common

_COMMAND_TIMEOUT = 30
_NOT_FOUND_ERROR = "Error: No MCP server named 'claude' found."


def run() -> bool:
    """Claude MCP登録が存在すれば削除し、変更の有無を返す。"""
    result = claude_common.run_subprocess(
        ["codex", "mcp", "get", "claude", "--json"],
        timeout=_COMMAND_TIMEOUT,
        tag="codex",
    )
    if result is None:
        raise RuntimeError(f"Claude MCP登録の取得に失敗: {claude_common.format_cli_error(result)}")
    # 標準エラーは実行環境がNode.jsの警告などを付加し得る通路のため、完全一致では判定しない。
    # 終了コード1の条件は、無関係な失敗を未登録と誤判定しないために併せて維持する。
    if result.returncode == 1 and _NOT_FOUND_ERROR in (result.stderr or ""):
        return False
    if result.returncode != 0:
        raise RuntimeError(f"Claude MCP登録の取得に失敗: {claude_common.format_cli_error(result)}")

    removal = claude_common.run_subprocess(
        ["codex", "mcp", "remove", "claude"],
        timeout=_COMMAND_TIMEOUT,
        tag="codex",
    )
    if removal is None or removal.returncode != 0:
        raise RuntimeError(f"Claude MCP登録の削除に失敗: {claude_common.format_cli_error(removal)}")
    return True
