"""Claude Code固有の定数と`claude` CLIの呼び出し。

Claudeに依存しない汎用部品は`pytools._internal.common`が持つ。
"""

import logging
import subprocess
from pathlib import Path

from pytools._internal import common, log_format

logger = logging.getLogger(__name__)

CLAUDE_HOME = Path.home() / ".claude"
CLAUDE_CONFIG_PATH = Path.home() / ".claude.json"
SETTINGS_JSON_PATH = CLAUDE_HOME / "settings.json"
PLANS_DIR = CLAUDE_HOME / "plans"

# CLI 呼び出しを回避するための直接読み取り用
INSTALLED_PLUGINS_PATH = CLAUDE_HOME / "plugins" / "installed_plugins.json"

# marketplace.json の `name` と一致させる (.claude-plugin/marketplace.json を参照)
MARKETPLACE_NAME = "ak110-dotfiles"

CLAUDE_TIMEOUT = 30
PLUGIN_OPERATION_TIMEOUT = 300


def run_claude(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = CLAUDE_TIMEOUT,
) -> subprocess.CompletedProcess[str] | None:
    """`claude` CLIを呼び出す共通ヘルパー。

    タイムアウト・例外・非ゼロ終了を全て吸収して呼び出し元に返す。
    `cwd` を指定すると project scope など cwd 依存のサブコマンドに対応できる。
    `timeout`を省略した場合は30秒とし、長時間を要する操作だけ呼び出し元が上書きする。
    原因追跡のため、実行コマンドと戻り値を永続ログに残す。ユーザーの判断には使わないため、
    post-applyの画面へは出力しない。
    """
    claude = common.resolve_executable("claude", preferred_directories=(Path.home() / ".local" / "bin",))
    if claude is None:
        logger.warning(log_format.format_status("claude", "claudeコマンドが見つからないためスキップ"))
        return None
    logger.info(
        log_format.format_status(
            "claude",
            f"exec: {' '.join(args)}" + (f" (cwd={cwd})" if cwd is not None else ""),
        ),
        extra=log_format.LOG_ONLY,
    )
    result = common.run_subprocess([str(claude), *args], timeout=timeout, cwd=cwd, tag="claude")
    if result is None:
        return None
    logger.info(
        log_format.format_status("claude", f"exit {result.returncode}: {' '.join(args)}"),
        extra=log_format.LOG_ONLY,
    )
    return result
