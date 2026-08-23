"""旧Codex User scope MCP登録を明示的な導入経路から移行する。"""

from __future__ import annotations

import json
import logging
import shutil
from typing import Any

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

_CODEX_NAME = "codex"
_CODEX_COMMAND = "codex"
_CODEX_ARGS = ["mcp-server"]
_LEGACY_TIMEOUT = 7_200_000
_ALLOWED_FIELDS = frozenset({"type", "command", "args", "timeout", "env"})
_CLAUDE_CONFIG_PATH = claude_common.CLAUDE_CONFIG_PATH


def _load_user_codex() -> dict[str, Any] | None:
    """User scope設定のcodex定義を読み取る。"""
    try:
        data = json.loads(_CLAUDE_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    value = servers.get(_CODEX_NAME)
    return value if isinstance(value, dict) else None


def is_legacy_definition(value: object) -> bool:
    """過去installerが生成した完全一致の定義であるかを返す。

    受理するフィールドの集合は移行元の実生成物を基準とする。旧installerが使う
    `claude mcp add`は`-e`を指定しない場合も`env`を空dictとして書き込むため、
    値を持たない`env`だけを受理し、値を持つ`env`は利用者が加えた設定として保持する。
    """
    if not isinstance(value, dict) or not set(value).issubset(_ALLOWED_FIELDS):
        return False
    if value.get("type") not in (None, "stdio"):
        return False
    if value.get("command") != _CODEX_COMMAND or value.get("args") != _CODEX_ARGS:
        return False
    env = value.get("env")
    if env is not None and env != {}:
        return False
    timeout = value.get("timeout")
    return timeout is None or timeout == _LEGACY_TIMEOUT


def run() -> bool:
    """完全一致するUser scope旧定義だけを`claude mcp remove --scope user`で削除する。"""
    if shutil.which("claude") is None:
        logger.info(log_format.format_status("legacy-codex-mcp", "claude CLI 未検出のためスキップ"))
        return False
    current = _load_user_codex()
    if not is_legacy_definition(current):
        if current is not None:
            logger.warning(
                "User scopeのcodex MCP定義は旧installerの完全一致ではないため保持します。"
                " 必要なら `claude mcp remove --scope user codex` を手動実行してください。"
            )
        return False
    result = claude_common.run_claude(["mcp", "remove", "--scope", "user", _CODEX_NAME])
    if result is None or result.returncode != 0:
        detail = claude_common.format_cli_error(result)
        logger.warning(log_format.format_status("legacy-codex-mcp", f"移行に失敗 (続行): {detail}"))
        return False
    logger.info(log_format.format_status("legacy-codex-mcp", "旧User scope登録を削除しました"))
    return True
