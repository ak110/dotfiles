"""Codex設定に残るClaude MCP登録を削除する。"""

import logging

from pytools._internal import common, post_apply_outcome

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT = 30
_NOT_FOUND_ERROR = "Error: No MCP server named 'claude' found."


def run() -> post_apply_outcome.PostApplyOutcome:
    """Claude MCP登録が存在すれば削除し、変更の有無を返す。"""
    codex = common.resolve_executable("codex")
    if codex is None:
        logger.warning("codexコマンドが見つからないためClaude MCP登録を確認できません。手動で確認してください。")
        return post_apply_outcome.PostApplyOutcome()
    result = common.run_subprocess(
        [str(codex), "mcp", "get", "claude", "--json"],
        timeout=_COMMAND_TIMEOUT,
        tag="codex",
    )
    if result is None:
        return post_apply_outcome.PostApplyOutcome(failure=f"Claude MCP登録の取得に失敗: {common.format_cli_error(result)}")
    # 標準エラーは実行環境がNode.jsの警告などを付加し得る通路のため、完全一致では判定しない。
    # 終了コード1の条件は、無関係な失敗を未登録と誤判定しないために併せて維持する。
    if result.returncode == 1 and _NOT_FOUND_ERROR in (result.stderr or ""):
        return post_apply_outcome.PostApplyOutcome()
    if result.returncode != 0:
        return post_apply_outcome.PostApplyOutcome(failure=f"Claude MCP登録の取得に失敗: {common.format_cli_error(result)}")

    removal = common.run_subprocess(
        [str(codex), "mcp", "remove", "claude"],
        timeout=_COMMAND_TIMEOUT,
        tag="codex",
    )
    if removal is None or removal.returncode != 0:
        return post_apply_outcome.PostApplyOutcome(failure=f"Claude MCP登録の削除に失敗: {common.format_cli_error(removal)}")
    return post_apply_outcome.PostApplyOutcome(changed=True)
