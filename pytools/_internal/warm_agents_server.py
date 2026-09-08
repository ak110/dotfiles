"""``agents_server_mcp.py``のuvプロジェクト環境を事前構築する。"""

# 配布先ごとの独立したpluginプロジェクトを対象にするため、既存のhookウォームアップ処理と共通部分を重複させる。

import json
import logging
import sys
from pathlib import Path

from pytools._internal import claude_common, log_format, plugin_warmup

logger = logging.getLogger(__name__)

_TAG = "agents_server warmup"
_PLUGIN_ID = f"agent-toolkit@{claude_common.MARKETPLACE_NAME}"
_SCRIPT_RELATIVE = Path("agent_toolkit") / "agents_server_mcp.py"
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH


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
    return plugin_warmup.run(_targets, tag=_TAG, arguments=("--check-dependencies",))


def _targets() -> list[Path]:
    """Claude CodeとCodexの実参照先を重複なく列挙する。"""
    candidates = [*_claude_plugin_scripts(), _codex_plugin_script()]
    targets: list[Path] = []
    for candidate in candidates:
        if candidate is None or candidate in targets:
            continue
        if not candidate.is_file():
            logger.info(log_format.format_status(_TAG, f"対象が存在しないため除外: {log_format.home_short(candidate)}"))
            continue
        targets.append(candidate)
    return targets


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
    return plugin_warmup.codex_plugin_script(
        plugin_id=_PLUGIN_ID,
        plugin_name="agent-toolkit",
        relative_path=_SCRIPT_RELATIVE,
        tag=_TAG,
    )


if __name__ == "__main__":
    main()
