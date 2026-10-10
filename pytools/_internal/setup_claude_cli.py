"""Claude Code公式ネイティブバイナリを導入または更新する。"""

# 他の処理では不要な依存を、使用時まで遅延する。旧Pythonでは通常のimportとなる。
__lazy_modules__ = {"httpx"}

import logging
import subprocess
import sys
from pathlib import Path

import httpx

from pytools._internal import common, log_format, post_apply_outcome, setup_cli_common

logger = logging.getLogger(__name__)

_PACKAGE = "@anthropic-ai/claude-code"
_HTTP_TIMEOUT = 30.0
_COMMAND_TIMEOUT = 300.0


def run(client: httpx.Client | None = None) -> post_apply_outcome.PostApplyOutcome:
    """公式ネイティブ版を導入または更新し、確認後に旧npm版を移行する。

    取得・導入・更新と確認の失敗は警告を出力してスキップと数え、旧npm版の撤去の失敗は失敗と数える。
    """
    launcher = Path.home() / ".local" / "bin" / ("claude.exe" if sys.platform == "win32" else "claude")
    if setup_cli_common.is_windows_cli_running("claude", _PACKAGE, [launcher]):
        logger.info(log_format.format_status("claude", "実行中のため導入と移行を次回へ延期"))
        return post_apply_outcome.PostApplyOutcome()
    if launcher.is_file():
        result = common.run_subprocess([str(launcher), "update"], timeout=_COMMAND_TIMEOUT, tag="claude")
    else:
        result = _install_native(client)
    if result is None or result.returncode != 0:
        logger.warning(log_format.format_status("claude", f"導入または更新に失敗: {common.format_cli_error(result)}"))
        return post_apply_outcome.PostApplyOutcome()
    verification = common.run_subprocess([str(launcher), "--version"], timeout=30, tag="claude")
    if verification is None or verification.returncode != 0:
        logger.warning(
            log_format.format_status("claude", f"正規版を確認できないため旧版を保持: {common.format_cli_error(verification)}")
        )
        return post_apply_outcome.PostApplyOutcome()
    setup_cli_common.prepend_path(launcher.parent)
    try:
        setup_cli_common.migrate_npm_launchers("claude", _PACKAGE, launcher, launcher.parent.parent)
    except RuntimeError as error:
        return post_apply_outcome.PostApplyOutcome(changed=True, failure=str(error))
    return post_apply_outcome.PostApplyOutcome(changed=True)


def _install_native(client: httpx.Client | None) -> subprocess.CompletedProcess[str] | None:
    result, reason = setup_cli_common.run_official_installer(
        client,
        posix_url="https://claude.ai/install.sh",
        windows_url="https://claude.ai/install.ps1",
        tag="claude",
        timeout=_COMMAND_TIMEOUT,
        http_timeout=_HTTP_TIMEOUT,
    )
    if reason:
        logger.warning(log_format.format_status("claude", reason))
        return None
    return result
