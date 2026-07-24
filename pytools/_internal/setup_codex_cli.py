"""Codex CLIを現在有効なNode環境のnpm global導入へ統一する。"""

import logging
import shutil
import sys
from pathlib import Path

from pytools._internal import claude_common, log_format, setup_cli_common

logger = logging.getLogger(__name__)

_PACKAGE = "@openai/codex"
_TIMEOUT = 300.0


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    from pytools._internal.cli import setup_logging  # pylint: disable=import-outside-toplevel

    setup_logging()
    run()
    sys.exit(0)


def run() -> bool:
    """正規Codexを導入し、確認後に旧npm版とmise版を移行する。"""
    if setup_cli_common.is_windows_cli_running("codex", _PACKAGE):
        logger.info(log_format.format_status("codex", "実行中のため導入と移行を次回へ延期"))
        return False
    npm_name = shutil.which("npm")
    if npm_name is None:
        message = "npmが見つからないためCodexを導入できない"
        logger.warning(log_format.format_status("codex", message))
        raise RuntimeError(message)
    npm = Path(npm_name).absolute()
    prefix = _npm_prefix(npm)
    if prefix is None:
        raise RuntimeError("npm prefixの取得に失敗")
    install = claude_common.run_subprocess(
        [str(npm), "install", "--global", f"{_PACKAGE}@latest"], timeout=_TIMEOUT, tag="codex"
    )
    if install is None or install.returncode != 0:
        message = f"npm導入に失敗: {claude_common.format_cli_error(install)}"
        logger.warning(log_format.format_status("codex", message))
        raise RuntimeError(message)
    launcher = prefix / ("codex.cmd" if sys.platform == "win32" else "bin/codex")
    verification = claude_common.run_subprocess([str(launcher), "--version"], timeout=30, tag="codex")
    if verification is None or verification.returncode != 0:
        logger.warning(
            log_format.format_status(
                "codex", f"正規版を確認できないため旧版を保持: {claude_common.format_cli_error(verification)}"
            )
        )
        raise RuntimeError("正規Codexの確認に失敗")
    setup_cli_common.prepend_path(launcher.parent)
    failures: list[str] = []
    mise_name = shutil.which("mise")
    if mise_name is not None:
        removal = claude_common.run_subprocess(
            [str(Path(mise_name).absolute()), "uninstall", "--all", "--yes", "npm:@openai/codex"],
            timeout=_TIMEOUT,
            tag="mise",
        )
        if removal is None or removal.returncode != 0:
            failures.append(f"mise版の削除に失敗: {claude_common.format_cli_error(removal)}")
        try:
            setup_cli_common.migrate_npm_launchers("codex", _PACKAGE, launcher, prefix)
        except RuntimeError as error:
            failures.append(str(error))
        reshim = claude_common.run_subprocess(
            [str(Path(mise_name).absolute()), "reshim"], timeout=claude_common.CLAUDE_TIMEOUT, tag="mise"
        )
        if reshim is None or reshim.returncode != 0:
            failures.append(f"mise reshimに失敗: {claude_common.format_cli_error(reshim)}")
    else:
        setup_cli_common.migrate_npm_launchers("codex", _PACKAGE, launcher, prefix)
    if failures:
        message = " / ".join(failures)
        logger.warning(log_format.format_status("codex", message))
        raise RuntimeError(message)
    return True


def _npm_prefix(npm: Path) -> Path | None:
    result = claude_common.run_subprocess([str(npm), "prefix", "--global"], timeout=claude_common.CLAUDE_TIMEOUT, tag="npm")
    value = (result.stdout or "").strip() if result is not None and result.returncode == 0 else ""
    if not value:
        logger.warning(log_format.format_status("codex", f"npm prefixの取得に失敗: {claude_common.format_cli_error(result)}"))
        return None
    return Path(value)


if __name__ == "__main__":
    main()
