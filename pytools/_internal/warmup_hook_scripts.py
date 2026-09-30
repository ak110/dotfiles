"""hookが起動するuvプロジェクトの実行環境を事前構築する。

Claude Code・Codexのhookはplugin rootのuvプロジェクトを明示して起動する。
プロジェクト環境が未構築の初回実行では、
Python本体の解決・依存パッケージの取得・venv構築がhookの制限時間内に収まらず、
hook出力が破棄される。`chezmoi apply`後処理でhookが使うuv環境を事前に構築し、
初回hook実行時のコールドスタートを解消する。

ウォームアップ対象は「hook設定が実際に参照するパス」に限る。
配布先ごとに異なるplugin rootを`--project`へ渡すため、実際の参照先を事前構築する。
"""

import sys
from pathlib import Path

from pytools._internal import claude_common, plugin_warmup

_TAG = "hook warmup"
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
    """ウォームアップ対象のスクリプトパスを重複なく列挙する。

    Codexの版はプラグイン更新が失敗した場合も実際の参照先と一致させるため、
    `codex plugin list --json`の有効なエントリから解決する。
    """
    return plugin_warmup.agent_toolkit_targets(
        _INSTALLED_PLUGINS_PATH,
        claude_relative=_PLUGIN_HOOK_SCRIPT_RELATIVE,
        codex_relative=_PLUGIN_HOOK_SCRIPT_RELATIVE,
        tag=_TAG,
    )


if __name__ == "__main__":
    main()
