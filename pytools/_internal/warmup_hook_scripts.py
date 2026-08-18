"""hookが起動するuvスクリプトの実行環境を事前構築する。

Claude Code・Codexのhookは`uv run --no-project --script`でPEP 723スクリプト
（`claude_hook.py`）を起動する。スクリプト環境が未構築の初回実行では、
Python本体の解決・依存パッケージの取得・venv構築がhookの制限時間内に収まらず、
hook出力が破棄される。`chezmoi apply`後処理で当該環境を事前に構築し、
初回hook実行時のコールドスタートを解消する。

ウォームアップ対象は「hook設定が実際に参照するパス」に限る。
`uv run --script`のvenvキャッシュはスクリプトパスに依存し得るため
（Linux・uv 0.12.3の実測で、依存メタデータが同一でもパスが異なるスクリプトは
venvを再構築した）、別パスにある同一内容のスクリプトを事前構築しても
hook初回実行時のvenv構築は残る。
"""

import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from pytools._internal import claude_common, install_codex_plugins, log_format

logger = logging.getLogger(__name__)

_TAG = "hook warmup"
_PLUGIN_ID = f"agent-toolkit@{claude_common.MARKETPLACE_NAME}"
_CODEX_PLUGIN_NAME = "agent-toolkit"
_HOOK_SCRIPT_RELATIVE = Path("scripts") / "claude_hook.py"
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH
# 低スペック環境ではPython本体の取得と依存パッケージの初回構築に分単位を要するため、余裕のある上限値とする。
_WARMUP_TIMEOUT = 600.0
_CODEX_LIST_TIMEOUT = 60.0


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    from pytools._internal.cli import setup_logging  # pylint: disable=import-outside-toplevel

    setup_logging()
    run()
    sys.exit(0)


def run() -> bool:
    """hookが参照する`claude_hook.py`のuvスクリプト環境を事前構築する。

    Returns:
        常にFalse。uvキャッシュのみへ作用し、観測可能な設定変更を行わないため。
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
    """ウォームアップ対象のスクリプトパスを重複なく列挙する。"""
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
    """配布settingsのhookが参照するdotfilesリポジトリ内のパスを返す。"""
    root = claude_common.find_dotfiles_root()
    if root is None:
        logger.info(log_format.format_status(_TAG, "dotfiles ルートが見つからずスキップ"))
        return None
    return root / _HOOK_SCRIPT_RELATIVE


def _claude_plugin_scripts() -> list[Path]:
    """Claude Codeプラグインhookが参照するインストール先のパスを返す。"""
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
    if entries is None:
        logger.info(log_format.format_status(_TAG, "Claude Code plugin が未導入のため除外"))
        return []
    if not isinstance(entries, list):
        logger.warning(log_format.format_status(_TAG, "Claude Code plugin一覧の構造が不正なため除外"))
        return []
    paths: list[Path] = []
    for entry in entries:
        install_path = entry.get("installPath") if isinstance(entry, dict) else None
        if isinstance(install_path, str):
            paths.append(Path(install_path) / _HOOK_SCRIPT_RELATIVE)
    return paths


def _codex_plugin_script() -> Path | None:
    """Codex hookが参照する有効版プラグインキャッシュ内のパスを返す。

    版はプラグイン更新が失敗した場合も実際の参照先と一致させるため、配布元manifestではなく
    `codex plugin list --json`の有効なエントリ（`pluginId`一致・`enabled`が真・
    文字列の`version`）から解決する。
    """
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
    return cache_root / version / _HOOK_SCRIPT_RELATIVE


def _enabled_version(installed: list[object]) -> str | None:
    """`codex plugin list --json`の一覧から有効版のバージョン文字列を返す。"""
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
    """1件のスクリプトへ`uv run`を実行して環境を構築する。

    `claude_hook.py`は引数なし実行でusageを表示して終了コード0で終わるため副作用は無い。
    個別の失敗は他の対象の構築を妨げないよう警告に留める。
    """
    started = time.monotonic()
    result = claude_common.run_subprocess(
        ["uv", "run", "--no-project", "--script", str(path)], timeout=_WARMUP_TIMEOUT, tag="uv"
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
