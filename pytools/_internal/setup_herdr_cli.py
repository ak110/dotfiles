"""Herdr公式直接インストール版を導入または更新する。"""

import collections.abc
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
    with _curl_env_overrides() as env_overrides:
        return _run(client, env_overrides)


@contextlib.contextmanager
def _curl_env_overrides() -> collections.abc.Iterator[dict[str, str] | None]:
    """Windowsで失効確認先の不達を許すcurl設定を用意し、子プロセスへ渡す環境上書きを返す。

    curlは`-q`が無いと最初に`CURL_HOME`配下の`.curlrc`を読む。公式インストーラーに加えて
    `herdr update`自身もmanifestと配布物の取得で`-q`を付けずに`curl`を起動するため、
    分岐ごとに用意せずステップ全体の子プロセスへ同じ設定を渡す。
    一時ディレクトリは終了時（例外の送出時を含む）に除去し、親プロセスの環境は変えない。
    """
    if sys.platform != "win32":
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="herdr-curl-") as curl_home:
        (Path(curl_home) / ".curlrc").write_text("ssl-revoke-best-effort\n", encoding="ascii")
        yield {"CURL_HOME": curl_home}


def _run(client: httpx.Client | None, env_overrides: dict[str, str] | None) -> bool | post_apply_outcome.PostApplyOutcome:
    launcher = _launcher_path()
    update_deferred = False
    if launcher.is_file():
        result = claude_common.run_subprocess(
            [str(launcher), "update"], timeout=_COMMAND_TIMEOUT, tag=_TAG, env_overrides=env_overrides
        )
        update_error = result.stderr if result is not None and isinstance(result.stderr, str) else ""
        # 未知の更新失敗を隠さず、Herdrが離脱後の自己更新を明示した診断だけを保留対象にする。
        update_deferred = (
            result is not None
            and result.returncode != 0
            and "herdr update" in update_error
            and _DETACHED_UPDATE_MARKER in update_error
        )
    else:
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
    verification = claude_common.run_subprocess([str(launcher), "--version"], timeout=30, tag=_TAG, env_overrides=env_overrides)
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
