"""pstack-claudeを無効状態で導入し、導入済みなら更新する。

`poteto-mode`コマンド（`pytools.agent_launcher`）が起動単位でだけ有効にするため、
通常のセッションへは影響しないようuser scopeでは無効にしておく。
導入済みの有効・無効の設定はユーザーの選択として保持する。
"""

import json
import logging

from agent_toolkit._common import dotfiles_mode

from pytools._internal import claude_common, common

logger = logging.getLogger(__name__)
PLUGIN = "pstack@pstack-claude"
MARKETPLACE = "pstack-claude"
SOURCE = "michael-denyer/pstack-claude"


def _list_json(args: list[str]) -> list[object] | None:
    result = claude_common.run_claude(args, timeout=claude_common.PLUGIN_OPERATION_TIMEOUT)
    if result is None or result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    entries = data.get("marketplaces") if isinstance(data, dict) else data
    return entries if isinstance(entries, list) else None


def _command(args: list[str]) -> bool:
    result = claude_common.run_claude(args, timeout=claude_common.PLUGIN_OPERATION_TIMEOUT)
    if result is None or result.returncode != 0:
        logger.warning(
            "pstack-claude: `claude %s`に失敗: %s。update-dotfilesで再試行する", " ".join(args), common.format_cli_error(result)
        )
        return False
    return True


def run() -> bool:
    """pstack-claudeを導入または更新し、変更があればTrueを返す。失敗しても他の処理へ伝播させない。"""
    if dotfiles_mode.is_minimal():
        return False
    plugins = _list_json(["plugin", "list", "--json"])
    marketplaces = _list_json(["plugin", "marketplace", "list", "--json"])
    if plugins is None or marketplaces is None:
        logger.warning("pstack-claude: 導入状態を取得できず保留する。update-dotfilesで再試行する")
        return False
    installed = any(isinstance(entry, dict) and entry.get("id") == PLUGIN for entry in plugins)
    registered = any(isinstance(entry, dict) and entry.get("name") == MARKETPLACE for entry in marketplaces)
    if not (
        _command(["plugin", "marketplace", "update", MARKETPLACE])
        if registered
        else _command(["plugin", "marketplace", "add", SOURCE])
    ):
        return False
    if installed:
        return _command(["plugin", "update", PLUGIN, "--scope=user"])
    return _command(["plugin", "install", PLUGIN, "--scope=user"]) and _command(["plugin", "disable", PLUGIN, "--scope=user"])
