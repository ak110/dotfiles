"""Codex CLI公式スタンドアローン版を導入または更新する。"""

import contextlib
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from pytools._internal import claude_common, log_format, setup_cli_common

logger = logging.getLogger(__name__)

_PACKAGE = "@openai/codex"
_HTTP_TIMEOUT = 30.0
_HTTP_RETRY_ATTEMPTS = 3
_HTTP_RETRY_BASE_DELAY = 0.25
_HTTP_RETRY_JITTER = 0.25
_COMMAND_TIMEOUT = 300.0


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    from pytools._internal.cli import setup_logging  # pylint: disable=import-outside-toplevel

    setup_logging()
    run()
    sys.exit(0)


def run(client: httpx.Client | None = None) -> bool:
    """公式スタンドアローン版を導入または更新し、確認後に旧npm版とmise版を移行する。"""
    if setup_cli_common.is_windows_cli_running("codex", _PACKAGE):
        logger.info(log_format.format_status("codex", "実行中のため導入と移行を次回へ延期"))
        return False
    result = _run_installer(client)
    if result is None or result.returncode != 0:
        message = f"導入または更新に失敗: {claude_common.format_cli_error(result)}"
        logger.warning(log_format.format_status("codex", message))
        raise RuntimeError(message)
    launcher = _find_standalone_launcher()
    if launcher is None:
        message = f"管理対象のCodexが見つからないため旧版を保持: {_standalone_root() / 'current'}"
        logger.warning(log_format.format_status("codex", message))
        raise RuntimeError("管理対象Codexの確認に失敗")
    verification = claude_common.run_subprocess([str(launcher), "--version"], timeout=30, tag="codex")
    if verification is None or verification.returncode != 0:
        logger.warning(
            log_format.format_status(
                "codex", f"正規版を確認できないため旧版を保持: {claude_common.format_cli_error(verification)}"
            )
        )
        raise RuntimeError("正規Codexの確認に失敗")
    setup_cli_common.prepend_path(_visible_bin_dir())
    failures, removed = _remove_mise_versions()
    migrated = False
    try:
        migrated = setup_cli_common.migrate_npm_launchers("codex", _PACKAGE, launcher, _standalone_root())
    except RuntimeError as error:
        failures.append(str(error))
    # 旧版の除去でmise shimが実体を失うため、除去を伴った場合だけ最後にshimを再生成する。
    if removed or migrated:
        failures.extend(_reshim_mise())
    if failures:
        message = " / ".join(failures)
        logger.warning(log_format.format_status("codex", message))
        raise RuntimeError(message)
    return True


def _run_installer(client: httpx.Client | None) -> subprocess.CompletedProcess[str] | None:
    """公式インストーラーを取得して非対話で実行する。

    公式インストーラーは未導入なら新規導入、導入済みなら更新として同じ処理経路を使うため、
    導入済み判定による分岐を設けない。
    """
    executable = _find_powershell() if sys.platform == "win32" else "sh"
    owns_client = client is None
    active_client = client or httpx.Client(timeout=_HTTP_TIMEOUT, follow_redirects=True)
    suffix = ".ps1" if sys.platform == "win32" else ".sh"
    url = "https://chatgpt.com/codex/install.ps1" if sys.platform == "win32" else "https://chatgpt.com/codex/install.sh"
    temp_path: Path | None = None
    try:
        response = _get_installer(active_client, url)
        with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as temp:
            temp_path = Path(temp.name)
            temp.write(response.content)
        command = (
            [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(temp_path)]
            if sys.platform == "win32"
            else [executable, str(temp_path)]
        )
        # 公式マニュアルはスクリプト化した導入と更新に`CODEX_NON_INTERACTIVE=1`を使うと定める。
        return claude_common.run_subprocess(
            command, timeout=_COMMAND_TIMEOUT, tag="codex", env_overrides={"CODEX_NON_INTERACTIVE": "1"}
        )
    except (httpx.HTTPError, OSError) as error:
        logger.warning(log_format.format_status("codex", f"公式インストーラーの取得に失敗: {error}"))
        raise RuntimeError("公式インストーラーの取得に失敗") from error
    finally:
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink()
        if owns_client:
            active_client.close()


def _find_powershell() -> str:
    """PowerShell 7を優先し、利用可能なPowerShell実行ファイルを返す。"""
    for name in ("pwsh", "powershell"):
        executable = shutil.which(name)
        if executable is not None:
            return executable
    raise RuntimeError("Codexの公式インストーラーを実行できるPowerShellが見つからない")


def _get_installer(client: httpx.Client, url: str) -> httpx.Response:
    """一時的なHTTPエラーを有限回再試行して公式インストーラーを取得する。"""
    for attempt in range(_HTTP_RETRY_ATTEMPTS):
        retry_error: httpx.HTTPError
        try:
            response = client.get(url)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            status_code = error.response.status_code
            if status_code != 429 and status_code < 500:
                raise
            retry_error = error
        except (httpx.NetworkError, httpx.TimeoutException) as error:
            retry_error = error
        if attempt == _HTTP_RETRY_ATTEMPTS - 1:
            raise retry_error
        delay = _HTTP_RETRY_BASE_DELAY * (2**attempt) + random.uniform(0, _HTTP_RETRY_JITTER)
        time.sleep(delay)
    raise AssertionError("到達不能")


def _codex_home() -> Path:
    """公式インストーラーが使うCodexのホームディレクトリを返す。"""
    value = os.environ.get("CODEX_HOME")
    return Path(value) if value else Path.home() / ".codex"


def _standalone_root() -> Path:
    """スタンドアローン版のパッケージキャッシュのルートを返す。"""
    return _codex_home() / "packages" / "standalone"


def _find_standalone_launcher() -> Path | None:
    """管理対象のスタンドアローン版ランチャーを返す。

    公式インストーラーが導入済み版の判定に使う`current/bin/codex`と`current/codex`を同じ順で確認する。
    """
    current = _standalone_root() / "current"
    name = "codex.exe" if sys.platform == "win32" else "codex"
    return next((candidate for candidate in (current / "bin" / name, current / name) if candidate.is_file()), None)


def _visible_bin_dir() -> Path:
    """公式インストーラーが可視コマンドを置くディレクトリを返す。"""
    value = os.environ.get("CODEX_INSTALL_DIR")
    if value:
        return Path(value)
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "Programs" / "OpenAI" / "Codex" / "bin"
    return Path.home() / ".local" / "bin"


def _find_mise() -> Path | None:
    """PATH上のmiseの絶対パスを返す。"""
    mise_name = shutil.which("mise")
    return Path(mise_name).absolute() if mise_name is not None else None


def _remove_mise_versions() -> tuple[list[str], bool]:
    """mise管理版が実在する場合だけ全版を除去し、失敗の説明と除去有無を返す。"""
    mise = _find_mise()
    if mise is None:
        return [], False
    target = f"npm:{_PACKAGE}"
    listing = claude_common.run_subprocess(
        [str(mise), "ls", "--json", target], timeout=claude_common.CLAUDE_TIMEOUT, tag="mise"
    )
    if listing is None or listing.returncode != 0:
        return [f"mise版の一覧取得に失敗: {claude_common.format_cli_error(listing)}"], False
    try:
        listed = json.loads(listing.stdout or "")
    except json.JSONDecodeError as error:
        return [f"mise版の一覧解析に失敗: {error}"], False
    if not isinstance(listed, list):
        return ["mise版の一覧が配列ではない"], False
    # `mise ls`はmise.toml定義済みで未導入の版も列挙する。
    # 未導入時に`uninstall`を呼ぶと警告が出るため、導入済みの要素だけを数える。
    if not any(isinstance(entry, dict) and entry.get("installed") for entry in listed):
        return [], False
    removal = claude_common.run_subprocess(
        [str(mise), "uninstall", "--all", "--yes", target], timeout=_COMMAND_TIMEOUT, tag="mise"
    )
    if removal is None or removal.returncode != 0:
        return [f"mise版の削除に失敗: {claude_common.format_cli_error(removal)}"], False
    return [], True


def _reshim_mise() -> list[str]:
    """mise管理のshimを再生成し、失敗の説明を返す。"""
    mise = _find_mise()
    if mise is None:
        return []
    reshim = claude_common.run_subprocess([str(mise), "reshim"], timeout=claude_common.CLAUDE_TIMEOUT, tag="mise")
    if reshim is None or reshim.returncode != 0:
        return [f"mise reshimに失敗: {claude_common.format_cli_error(reshim)}"]
    return []


if __name__ == "__main__":
    main()
