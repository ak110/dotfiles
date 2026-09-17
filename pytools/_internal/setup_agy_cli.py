"""Antigravity CLI（`agy`）を導入する。

更新はAntigravity CLI自身が実行中に自動で行うため、本ステップは未導入の場合だけ公式インストーラーを実行する。
バイナリ本体の取得先へ到達できない環境があるため、失敗は警告1行に留めて`False`を返す。
例外で終えると、当該環境の`update-dotfiles`が毎回終了コード1になり、他のステップの失敗が埋もれる。
"""

import logging
import os
import sys
from pathlib import Path

import httpx

from pytools._internal import claude_common, log_format, setup_cli_common

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT = 300.0
_TAG = "agy"
_POSIX_INSTALLER_URL = "https://antigravity.google/cli/install.sh"
_WINDOWS_INSTALLER_URL = "https://antigravity.google/cli/install.ps1"


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    from pytools._internal.cli import setup_logging  # pylint: disable=import-outside-toplevel

    setup_logging()
    run()
    sys.exit(0)


def _launcher_path() -> Path:
    """Antigravity CLIのランチャーの位置を返す。"""
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "agy" / "bin" / "agy.exe"
    return Path.home() / ".local" / "bin" / "agy"


def run(client: httpx.Client | None = None) -> bool:
    """未導入の場合だけ公式インストーラーでAntigravity CLIを導入する。"""
    launcher = _launcher_path()
    if launcher.is_file():
        logger.info(log_format.format_status(_TAG, "導入済みのため実行しない（更新はAntigravity CLIが自動で行う）"))
        return False
    result, reason = setup_cli_common.run_official_installer(
        client,
        posix_url=_POSIX_INSTALLER_URL,
        windows_url=_WINDOWS_INSTALLER_URL,
        tag=_TAG,
        timeout=_COMMAND_TIMEOUT,
    )
    if result is None or result.returncode != 0:
        detail = reason or claude_common.format_cli_error(result)
        logger.warning(log_format.format_status(_TAG, f"導入に失敗: {detail}"))
        return False
    verification = claude_common.run_subprocess([str(launcher), "--version"], timeout=30, tag=_TAG)
    if verification is None or verification.returncode != 0:
        logger.warning(log_format.format_status(_TAG, f"導入後の確認に失敗: {claude_common.format_cli_error(verification)}"))
        return False
    setup_cli_common.prepend_path(launcher.parent)
    return True


if __name__ == "__main__":
    main()
