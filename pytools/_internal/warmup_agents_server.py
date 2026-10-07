"""agent-toolkit pluginのuvプロジェクト環境を事前構築する。

Claude Code・Codexのhookと`agents_server_mcp.py`のMCPサーバーは、同じplugin rootのuvプロジェクトを
`uv run --project`で起動する。プロジェクト環境が未構築の初回実行では、
Python本体の解決・依存パッケージの取得・venv構築がhookの制限時間やMCPの起動上限に収まらず、
hook出力が破棄されるかMCPサーバーを利用できない状態でセッションが始まる。
`chezmoi apply`後処理で実際の参照先の版ディレクトリごとに1回、`agents_server_mcp.py`を依存検査付きで起動して環境を構築する。
hook（`agent_toolkit/hook.py`）は同じ版ディレクトリの同じ環境を使うため、別に起動しない。
"""

from pathlib import Path

from pytools._internal import claude_common, plugin_warmup, post_apply_outcome

_TAG = "agent-toolkit warmup"
_SCRIPT_RELATIVE = Path("agent_toolkit") / "agents_server_mcp.py"
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH


def run() -> post_apply_outcome.PostApplyOutcome:
    """Claude CodeとCodexが実際に参照する版ディレクトリのuv環境を構築する。"""
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
