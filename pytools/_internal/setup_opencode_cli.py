"""未導入のOpenCode CLIを、両OSで公開されているnpm導入手段で配置する。"""

import logging
import shutil

from agent_toolkit._common import dotfiles_mode

from pytools._internal import common, log_format, post_apply_outcome

logger = logging.getLogger(__name__)


def run() -> post_apply_outcome.PostApplyOutcome:
    """既存の導入を保持し、取得・導入の失敗は警告として返す。"""
    if dotfiles_mode.is_minimal():
        return post_apply_outcome.PostApplyOutcome()
    if common.resolve_executable("opencode") is not None:
        logger.info(log_format.format_status("opencode", "導入済みのため実行しない。対応不要"))
        return post_apply_outcome.PostApplyOutcome()
    npm = shutil.which("npm")
    if npm is None:
        logger.warning(
            log_format.format_status("opencode", "npmが見つからない。Node.jsの導入を確認して`update-dotfiles`を再実行する")
        )
        return post_apply_outcome.PostApplyOutcome()
    result = common.run_subprocess([str(npm), "install", "--global", "opencode-ai"], timeout=300, tag="opencode")
    if result is None or result.returncode != 0:
        logger.warning(
            log_format.format_status(
                "opencode", f"導入に失敗: {common.format_cli_error(result)}。`update-dotfiles`を再実行する"
            )
        )
        return post_apply_outcome.PostApplyOutcome()
    launcher = common.resolve_executable("opencode")
    verification = (
        common.run_subprocess([str(launcher), "--version"], timeout=30, tag="opencode") if launcher is not None else None
    )
    if verification is None or verification.returncode != 0:
        logger.warning(
            log_format.format_status(
                "opencode",
                f"導入後の確認に失敗: {common.format_cli_error(verification)}。"
                "npmのグローバル導入先をPATHへ加えて`update-dotfiles`を再実行する",
            )
        )
        return post_apply_outcome.PostApplyOutcome()
    return post_apply_outcome.PostApplyOutcome(changed=True)
