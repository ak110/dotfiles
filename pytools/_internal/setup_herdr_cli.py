"""Herdr公式直接インストール版を導入または更新する。"""

import contextlib
import logging
import os
import sys
import tempfile
from pathlib import Path

import httpx

from pytools._internal import claude_common, log_format, post_apply_outcome, setup_cli_common

logger = logging.getLogger(__name__)

_COMMAND_TIMEOUT = 300.0
_TAG = "herdr"
_POSIX_INSTALLER_URL = "https://herdr.dev/install.sh"
_WINDOWS_INSTALLER_URL = "https://herdr.dev/install.ps1"
_DETACHED_UPDATE_MARKER = "outside herdr after detaching from the session"


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


def run(client: httpx.Client | None = None) -> bool | post_apply_outcome.PostApplyOutcome:
    """公式直接インストール版Herdrを導入または更新し、実行可能な状態を確認する。"""
    launcher = _launcher_path()
    update_deferred = False
    if launcher.is_file():
        result = claude_common.run_subprocess([str(launcher), "update"], timeout=_COMMAND_TIMEOUT, tag=_TAG)
        update_error = result.stderr if result is not None and isinstance(result.stderr, str) else ""
        # 未知の更新失敗を隠さず、Herdrが離脱後の自己更新を明示した診断だけを保留対象にする。
        update_deferred = (
            result is not None
            and result.returncode != 0
            and "herdr update" in update_error
            and _DETACHED_UPDATE_MARKER in update_error
        )
    else:
        env_overrides: dict[str, str] | None = None
        with contextlib.ExitStack() as stack:
            if sys.platform == "win32":
                # curlはCURL_HOMEを最初に調べ、公式インストーラーは`-q`を付けない。
                # この子プロセスだけに失効確認先の不達を許す設定を渡し、終了時に除去する。
                curl_home = stack.enter_context(tempfile.TemporaryDirectory(prefix="herdr-curl-"))
                (Path(curl_home) / ".curlrc").write_text("ssl-revoke-best-effort\n", encoding="ascii")
                env_overrides = {"CURL_HOME": curl_home}
            result, reason = setup_cli_common.run_official_installer(
                client,
                posix_url=_POSIX_INSTALLER_URL,
                windows_url=_WINDOWS_INSTALLER_URL,
                tag=_TAG,
                timeout=_COMMAND_TIMEOUT,
                env_overrides=env_overrides,
            )
        if reason:
            logger.warning(log_format.format_status(_TAG, reason))
            raise RuntimeError("公式インストーラーの取得に失敗")
    if result is None or (result.returncode != 0 and not update_deferred):
        message = f"導入または更新に失敗: {claude_common.format_cli_error(result)}"
        logger.warning(log_format.format_status(_TAG, message))
        raise RuntimeError(message)
    verification = claude_common.run_subprocess([str(launcher), "--version"], timeout=30, tag=_TAG)
    if verification is None or verification.returncode != 0:
        message = f"導入または更新後の確認に失敗: {claude_common.format_cli_error(verification)}"
        logger.warning(log_format.format_status(_TAG, message))
        raise RuntimeError(message)
    setup_cli_common.prepend_path(launcher.parent)
    if update_deferred:
        return post_apply_outcome.PostApplyOutcome(
            changed=False,
            notices=(
                post_apply_outcome.PostApplyNotice(
                    message="Herdrセッション内では自己更新できないため、更新を保留しました。",
                    command="herdr update",
                ),
            ),
        )
    return True


if __name__ == "__main__":
    main()
