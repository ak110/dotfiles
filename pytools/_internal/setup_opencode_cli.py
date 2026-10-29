"""未導入のOpenCode CLIを、両OSで公開されているnpm導入手段で配置する。"""

import logging
import shutil
from pathlib import Path

from agent_toolkit._common import dotfiles_mode

from pytools._internal import common, log_format, post_apply_outcome, setup_mise

logger = logging.getLogger(__name__)

# 導入後の確認で`opencode`が見つからない場合に、PATHへ加える配置先として示す。
_SHIM_DIRECTORY_HINT = r"miseのshimディレクトリ（Linuxは`~/.local/share/mise/shims`、Windowsは`%LOCALAPPDATA%\mise\shims`）"


def find_launcher() -> Path | None:
    """Mise shimを含むPATH上の`opencode`を返す。

    OpenCodeはmise管理のnodeに付属するnpmで導入するため、nodeの導入先がPATHに無いホスト
    （Windowsの既定、`mise activate`を使わないLinux）ではmise shimからだけ起動できる。
    公式導入版を探す`common.resolve_executable`はshimを除外するため、ここでは使わない。
    """
    found = shutil.which("opencode")
    return Path(found) if found else None


def run() -> post_apply_outcome.PostApplyOutcome:
    """既存の導入を保持し、取得・導入の失敗は警告として返す。"""
    if dotfiles_mode.is_minimal():
        return post_apply_outcome.PostApplyOutcome()
    # 有効なnodeの版に導入されていない古いshimも見つかるため、起動できることまで確かめて導入済みと判定する。
    launcher = find_launcher()
    current = common.run_subprocess([str(launcher), "--version"], timeout=30, tag="opencode") if launcher else None
    if current is not None and current.returncode == 0:
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
    # mise管理のnpmで導入した実行ファイルのshimは、`mise reshim`を実行するまで生成されない。
    # post-applyの`mise セットアップ`工程のreshimはこの導入より前に終わるため、ここで生成する。
    mise = setup_mise.find_mise_binary()
    if mise is not None:
        common.run_subprocess([str(mise), "reshim"], timeout=common.COMMAND_TIMEOUT, tag="opencode")
    launcher = find_launcher()
    if launcher is None:
        logger.warning(
            log_format.format_status(
                "opencode",
                f"導入後の確認に失敗: PATH上で`opencode`が見つからない。{_SHIM_DIRECTORY_HINT}か、"
                "`npm prefix --global`が示す導入先の実行ファイル配置先をPATHへ加えて`update-dotfiles`を再実行する",
            )
        )
        return post_apply_outcome.PostApplyOutcome()
    verification = common.run_subprocess([str(launcher), "--version"], timeout=30, tag="opencode")
    if verification is None or verification.returncode != 0:
        logger.warning(
            log_format.format_status(
                "opencode",
                f"導入後の確認で`{launcher} --version`が失敗: {common.format_cli_error(verification)}。"
                "表示された原因を解消して`update-dotfiles`を再実行する",
            )
        )
        return post_apply_outcome.PostApplyOutcome()
    return post_apply_outcome.PostApplyOutcome(changed=True)
