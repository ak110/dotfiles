"""Claude Code公式ネイティブバイナリを導入または更新する。"""

import contextlib
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from pytools._internal import claude_common, log_format, setup_cli_common

logger = logging.getLogger(__name__)

_PACKAGE = "@anthropic-ai/claude-code"
_HTTP_TIMEOUT = 30.0
_COMMAND_TIMEOUT = 300.0


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    from pytools._internal.cli import setup_logging  # pylint: disable=import-outside-toplevel

    setup_logging()
    run()
    sys.exit(0)


def run(client: httpx.Client | None = None) -> bool:
    """公式ネイティブ版を導入または更新し、確認後に旧npm版を移行する。"""
    launcher = Path.home() / ".local" / "bin" / ("claude.exe" if sys.platform == "win32" else "claude")
    if setup_cli_common.is_windows_cli_running("claude", _PACKAGE, [launcher]):
        logger.info(log_format.format_status("claude", "実行中のため導入と移行を次回へ延期"))
        return False
    if launcher.is_file():
        result = claude_common.run_subprocess([str(launcher), "update"], timeout=_COMMAND_TIMEOUT, tag="claude")
    else:
        result = _install_native(client)
    if result is None or result.returncode != 0:
        message = f"導入または更新に失敗: {claude_common.format_cli_error(result)}"
        logger.warning(log_format.format_status("claude", message))
        raise RuntimeError(message)
    verification = claude_common.run_subprocess([str(launcher), "--version"], timeout=30, tag="claude")
    if verification is None or verification.returncode != 0:
        logger.warning(
            log_format.format_status(
                "claude", f"正規版を確認できないため旧版を保持: {claude_common.format_cli_error(verification)}"
            )
        )
        raise RuntimeError("正規Claude Codeの確認に失敗")
    setup_cli_common.prepend_path(launcher.parent)
    setup_cli_common.migrate_npm_launchers("claude", _PACKAGE, launcher, launcher.parent.parent)
    return True


def _install_native(client: httpx.Client | None) -> subprocess.CompletedProcess[str] | None:
    owns_client = client is None
    active_client = client or httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True)
    suffix = ".ps1" if sys.platform == "win32" else ".sh"
    url = "https://claude.ai/install.ps1" if sys.platform == "win32" else "https://claude.ai/install.sh"
    temp_path: Path | None = None
    try:
        response = active_client.get(url)
        response.raise_for_status()
        with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as temp:
            temp.write(response.content)
            temp_path = Path(temp.name)
        command = (
            ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(temp_path)]
            if sys.platform == "win32"
            else ["bash", str(temp_path)]
        )
        return claude_common.run_subprocess(command, timeout=_COMMAND_TIMEOUT, tag="claude")
    except (httpx.HTTPError, OSError) as error:
        logger.warning(log_format.format_status("claude", f"公式インストーラーの取得に失敗: {error}"))
        raise RuntimeError("公式インストーラーの取得に失敗") from error
    finally:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink()
        if owns_client:
            active_client.close()


if __name__ == "__main__":
    main()
