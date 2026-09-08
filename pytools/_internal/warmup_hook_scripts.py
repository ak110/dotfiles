"""hookが起動するuvプロジェクトの実行環境を事前構築する。

Claude Code・Codexのhookはplugin rootのuvプロジェクトを明示して起動する。
プロジェクト環境が未構築の初回実行では、
Python本体の解決・依存パッケージの取得・venv構築がhookの制限時間内に収まらず、
hook出力が破棄される。`chezmoi apply`後処理で当該環境を事前に構築し、
初回hook実行時のコールドスタートを解消する。

ウォームアップ対象は「hook設定が実際に参照するパス」に限る。
配布先ごとに異なるplugin rootを`--project`へ渡すため、実際の参照先を事前構築する。
"""

import sys
from pathlib import Path

from pytools._internal import claude_common, plugin_warmup

_TAG = "hook warmup"
_PLUGIN_ID = f"agent-toolkit@{claude_common.MARKETPLACE_NAME}"
_PLUGIN_HOOK_SCRIPT_RELATIVE = Path("agent_toolkit") / "hook.py"
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH
# 低スペック環境ではPython本体の取得と依存パッケージの初回構築に分単位を要するため、余裕のある上限値とする。


def main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    from pytools._internal.cli import setup_logging  # pylint: disable=import-outside-toplevel

    setup_logging()
    run()
    sys.exit(0)


def run() -> bool:
    """hookが参照するuvスクリプト環境を事前構築する。

    Returns:
        常にFalse。uvキャッシュのみへ作用し、観測可能な設定変更を行わないため。
    """
    return plugin_warmup.run(_targets, tag=_TAG)


def _targets() -> list[Path]:
    """ウォームアップ対象のスクリプトパスを重複なく列挙する。"""
    return plugin_warmup.existing_targets([*_claude_plugin_scripts(), _codex_plugin_script()], tag=_TAG)


def _claude_plugin_scripts() -> list[Path]:
    """Claude Codeプラグインhookが参照するインストール先のパスを返す。"""
    return plugin_warmup.claude_plugin_scripts(
        _INSTALLED_PLUGINS_PATH,
        plugin_id=_PLUGIN_ID,
        relative_path=_PLUGIN_HOOK_SCRIPT_RELATIVE,
        tag=_TAG,
    )


def _codex_plugin_script() -> Path | None:
    """Codex hookが参照する有効版プラグインキャッシュ内のパスを返す。

    版はプラグイン更新が失敗した場合も実際の参照先と一致させるため、配布元manifestではなく
    `codex plugin list --json`の有効なエントリ（`pluginId`一致・`enabled`が真・
    文字列の`version`）から解決する。
    """
    return plugin_warmup.codex_plugin_script(
        plugin_id=_PLUGIN_ID,
        plugin_name="agent-toolkit",
        relative_path=_PLUGIN_HOOK_SCRIPT_RELATIVE,
        tag=_TAG,
    )


if __name__ == "__main__":
    main()
