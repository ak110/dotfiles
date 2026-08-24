"""``agents_server_mcp.py``のuvスクリプト環境を事前構築する。"""

# 配布先ごとの独立したPEP 723スクリプトを対象にするため、既存のhookウォームアップ処理と共通部分を重複させる。
# pylint: disable=duplicate-code

import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from pytools._internal import claude_common, install_codex_plugins, log_format

logger = logging.getLogger(__name__)

_TAG = "agents_server warmup"
_PLUGIN_ID = f"agent-toolkit@{claude_common.MARKETPLACE_NAME}"
_CODEX_PLUGIN_NAME = "agent-toolkit"
_SCRIPT_RELATIVE = Path("scripts") / "agents_server_mcp.py"
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH
_WARMUP_TIMEOUT = 600.0
_CODEX_LIST_TIMEOUT = 60.0


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    from pytools._internal.cli import setup_logging  # pylint: disable=import-outside-toplevel

    setup_logging()
    run()
    sys.exit(0)


def run() -> bool:
    """実際に参照される``agents_server_mcp.py``の環境を構築する。

    ウォームアップはキャッシュ構築だけを行うため、個別の失敗を後処理全体の失敗にしない。
    """
    if shutil.which("uv") is None:
        logger.info(log_format.format_status(_TAG, "uv CLI が見つからずスキップ"))
        return False
    targets = _targets()
    if not targets:
        logger.info(log_format.format_status(_TAG, "対象スクリプトが見つからずスキップ"))
        return False
    for target in targets:
        _warmup(target)
    return False


def _targets() -> list[Path]:
    """dotfiles・Claude Code・Codexの実参照先を重複なく列挙する。"""
    candidates = [_repository_script(), *_claude_plugin_scripts(), _codex_plugin_script()]
    targets: list[Path] = []
    for candidate in candidates:
        if candidate is None or candidate in targets:
            continue
        if not candidate.is_file():
            logger.info(log_format.format_status(_TAG, f"対象が存在しないため除外: {log_format.home_short(candidate)}"))
            continue
        targets.append(candidate)
    return targets


def _repository_script() -> Path | None:
    """配布元リポジトリ内のスクリプトを返す。"""
    root = claude_common.find_dotfiles_root()
    if root is None:
        logger.info(log_format.format_status(_TAG, "dotfiles ルートが見つからずスキップ"))
        return None
    return root / _SCRIPT_RELATIVE


def _claude_plugin_scripts() -> list[Path]:
    """Claude Codeプラグインのインストール先を返す。"""
    try:
        data = json.loads(_INSTALLED_PLUGINS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.info(log_format.format_status(_TAG, "Claude Code plugin一覧が存在しないため除外"))
        return []
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(log_format.format_status(_TAG, f"Claude Code plugin一覧を取得できないため除外: {e}"))
        return []
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        logger.warning(log_format.format_status(_TAG, "Claude Code plugin一覧の構造が不正なため除外"))
        return []
    entries = plugins.get(_PLUGIN_ID)
    if not isinstance(entries, list):
        logger.info(log_format.format_status(_TAG, "Claude Code plugin が未導入のため除外"))
        return []
    paths: list[Path] = []
    for entry in entries:
        install_path = entry.get("installPath") if isinstance(entry, dict) else None
        if isinstance(install_path, str):
            paths.append(Path(install_path) / _SCRIPT_RELATIVE)
    return paths


def _codex_plugin_script() -> Path | None:
    """Codexが参照する有効版プラグインキャッシュ内のスクリプトを返す。"""
    if shutil.which("codex") is None:
        logger.info(log_format.format_status(_TAG, "codex CLI が見つからないためCodex分を除外"))
        return None
    result = claude_common.run_subprocess(["codex", "plugin", "list", "--json"], timeout=_CODEX_LIST_TIMEOUT, tag="codex")
    if result is None or result.returncode != 0:
        logger.warning(log_format.format_status(_TAG, "Codex plugin一覧を取得できないためCodex分を除外"))
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning(log_format.format_status(_TAG, "Codex plugin一覧のJSONが不正なためCodex分を除外"))
        return None
    installed = data.get("installed") if isinstance(data, dict) else None
    version = _enabled_version(installed if isinstance(installed, list) else [])
    if version is None:
        logger.info(log_format.format_status(_TAG, "Codex plugin の有効版が無いためCodex分を除外"))
        return None
    codex_home = Path(os.environ.get("CODEX_HOME", install_codex_plugins.CODEX_HOME))
    cache_root = codex_home / "plugins" / "cache" / claude_common.MARKETPLACE_NAME / _CODEX_PLUGIN_NAME
    return cache_root / version / _SCRIPT_RELATIVE


def _enabled_version(installed: list[object]) -> str | None:
    """plugin一覧から有効なagent-toolkitの版を返す。"""
    for item in installed:
        if not isinstance(item, dict):
            continue
        if item.get("pluginId") != _PLUGIN_ID or item.get("enabled") is not True:
            continue
        version = item.get("version")
        if isinstance(version, str):
            return version
    return None


def _warmup(path: Path) -> None:
    """1件のスクリプトに``--help``を渡し、PEP 723環境を構築する。"""
    started = time.monotonic()
    result = claude_common.run_subprocess(
        ["uv", "run", "--no-project", "--script", str(path), "--help"],
        timeout=_WARMUP_TIMEOUT,
        tag="uv",
    )
    elapsed = time.monotonic() - started
    short = log_format.home_short(path)
    if result is None:
        logger.warning(log_format.format_status(_TAG, f"環境構築に失敗: {short}"))
        return
    if result.returncode != 0:
        logger.warning(log_format.format_status(_TAG, f"環境構築が異常終了 (exit {result.returncode}): {short}"))
        return
    logger.info(log_format.format_status(_TAG, f"環境構築を確認 ({elapsed:.1f}秒): {short}"))


if __name__ == "__main__":
    main()
