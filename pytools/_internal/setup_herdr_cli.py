"""Herdr公式直接インストール版を導入または更新する。"""

import logging
import os
import sys
from pathlib import Path

import httpx

from pytools._internal import claude_common, log_format, setup_cli_common

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT = 300.0
_TAG = "herdr"
_POSIX_INSTALLER_URL = "https://herdr.dev/install.sh"
_WINDOWS_INSTALLER_URL = "https://herdr.dev/install.ps1"


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    from pytools._internal.cli import setup_logging  # pylint: disable=import-outside-toplevel

    setup_logging()
    run()
    sys.exit(0)


def _launcher_path() -> Path:
    """公式直接インストール版Herdrの安定したランチャー位置を返す。"""
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "Programs" / "Herdr" / "bin" / "herdr.exe"
    return Path.home() / ".local" / "bin" / "herdr"


def run(client: httpx.Client | None = None) -> bool:
    """公式直接インストール版Herdrを導入または更新し、実行可能な状態を確認する。"""
    launcher = _launcher_path()
    if launcher.is_file():
        result = claude_common.run_subprocess([str(launcher), "update"], timeout=_COMMAND_TIMEOUT, tag=_TAG)
    else:
        result, reason = setup_cli_common.run_official_installer(
            client,
            posix_url=_POSIX_INSTALLER_URL,
            windows_url=_WINDOWS_INSTALLER_URL,
            tag=_TAG,
            timeout=_COMMAND_TIMEOUT,
        )
        if reason:
            logger.warning(log_format.format_status(_TAG, reason))
            raise RuntimeError("公式インストーラーの取得に失敗")
    if result is None or result.returncode != 0:
        message = f"導入または更新に失敗: {claude_common.format_cli_error(result)}"
        logger.warning(log_format.format_status(_TAG, message))
        raise RuntimeError(message)
    verification = claude_common.run_subprocess([str(launcher), "--version"], timeout=30, tag=_TAG)
    if verification is None or verification.returncode != 0:
        message = f"導入または更新後の確認に失敗: {claude_common.format_cli_error(verification)}"
        logger.warning(log_format.format_status(_TAG, message))
        raise RuntimeError(message)
    setup_cli_common.prepend_path(launcher.parent)
    return True


if __name__ == "__main__":
    main()
