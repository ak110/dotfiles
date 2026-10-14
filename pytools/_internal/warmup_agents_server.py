"""agent-toolkit pluginのuvプロジェクト環境を事前構築する。

Claude Code・Codexのhookと`agents_server_mcp.py`のMCPサーバーは、scripts/launch.pyが解決する
書き込み可能なデータ領域のuvプロジェクト環境で起動する。プロジェクト環境が未構築の初回実行では、
Python本体の解決・依存パッケージの取得・venv構築がhookの制限時間やMCPの起動上限に収まらず、
hook出力が破棄されるかMCPサーバーを利用できない状態でセッションが始まる。
`chezmoi apply`後処理で実際の参照先の版ディレクトリごとに1回、`agents_server_mcp.py`を依存検査付きで起動する。
scripts/launch.pyは起動時に提供されたデータ領域か、ユーザーの既定データ領域へ環境を構築する。
hookとMCPに同じデータ領域が提供される場合は同じ版の環境を共用する。
"""

from pathlib import Path

from pytools._internal import claude_common, plugin_warmup, post_apply_outcome

_TAG = "agent-toolkit warmup"
_SCRIPT_RELATIVE = Path("agent_toolkit") / "agents_server_mcp.py"
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH


def run() -> post_apply_outcome.PostApplyOutcome:
    """導入済みの版を解決し、起動時のデータ領域へuv環境を構築する。"""
    return plugin_warmup.run(_targets, tag=_TAG, arguments=("--check-dependencies",))


def _targets() -> list[Path]:
    """Claude CodeとCodexの実参照先を重複なく列挙する。

    Codexの版はプラグイン更新が失敗した場合も実際の参照先と一致させるため、
    `codex plugin list --json`の有効なエントリから解決する。
    """
    return plugin_warmup.agent_toolkit_targets(
        _INSTALLED_PLUGINS_PATH,
        claude_relative=_SCRIPT_RELATIVE,
        codex_relative=_SCRIPT_RELATIVE,
        tag=_TAG,
    )
