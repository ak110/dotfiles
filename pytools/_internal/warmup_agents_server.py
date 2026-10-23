"""agent-toolkit pluginのuvプロジェクト環境を事前構築する。

Claude Code・Codexのhookと`agents_server_mcp.py`のMCPサーバーは、scripts/launch.pyが解決する
書き込み可能なデータ領域のuvプロジェクト環境で起動する。プロジェクト環境が未構築の初回実行では、
Python本体の解決・依存パッケージの取得・venv構築がhookの制限時間やMCPの起動上限に収まらず、
hook出力が破棄されるかMCPサーバーを利用できない状態でセッションが始まる。
`chezmoi apply`後処理で実際の参照先の版ディレクトリごとに1回、`agents_server_mcp.py`を依存検査付きで起動する。
scripts/launch.pyは起動時に提供されたデータ領域か、ユーザーの既定データ領域へ環境を構築する。
hookとMCPに同じデータ領域が提供される場合は同じ版の環境を共用する。
"""

import asyncio
import logging
import os
import tempfile
import time
import traceback
from collections.abc import Sequence
from pathlib import Path

from agent_toolkit._common import dotfiles_mode

from pytools._internal import claude_common, common, log_format, plugin_warmup, post_apply_outcome

_TAG = "agent-toolkit warmup"
_SCRIPT_RELATIVE = Path("agent_toolkit") / "agents_server_mcp.py"
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH
_STARTUP_TIMEOUT = 30.0
logger = logging.getLogger(__name__)


def run() -> post_apply_outcome.PostApplyOutcome:
    """導入済みの版を解決し、起動時のデータ領域へuv環境を構築する。"""
    if dotfiles_mode.is_minimal():
        return post_apply_outcome.PostApplyOutcome()
    return plugin_warmup.run(_targets, tag=_TAG, arguments=("--check-dependencies",), readiness=_check_startup)


def _check_startup(command: Sequence[str]) -> bool:
    """準備済みの公開stdioへ接続し、ホストの起動上限内にツール一覧を得る。"""
    # minimal導入と単なる対象解決ではMCP SDKを読み込まない。
    import anyio  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    from mcp import ClientSession  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    started = time.monotonic()
    phase = "process-start"
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:

        async def check() -> None:
            nonlocal phase
            parameters = StdioServerParameters(command=command[0], args=list(command[1:]), env=dict(os.environ))
            with anyio.fail_after(_STARTUP_TIMEOUT):
                async with stdio_client(parameters, errlog=errors) as streams:
                    read, write = streams
                    async with ClientSession(read, write) as client:
                        phase = "initialize"
                        await client.initialize()
                        phase = "tools/list"
                        tools = await client.list_tools()
                        if not tools.tools:
                            raise RuntimeError("MCPの公開ツールが0件")
                        phase = "close"

        try:
            asyncio.run(check())
        except Exception as exc:  # noqa: BLE001  # 公開transportの例外群とstderrをpost-applyへ伝える。
            errors.seek(0)
            logger.warning(
                log_format.format_status(
                    _TAG,
                    f"MCP起動確認に失敗 (段階={phase}、{time.monotonic() - started:.1f}秒、対象={command[-1]}) / "
                    + "".join(traceback.format_exception(exc))
                    + errors.read(),
                )
            )
            return False
    logger.info(
        log_format.format_status(
            _TAG, f"MCPのinitialize・tools/list・終了を確認 ({time.monotonic() - started:.1f}秒): {command[-1]}"
        )
    )
    return True


def _targets() -> list[Path]:
    """Claude CodeとCodexの実参照先を重複なく列挙する。

    Codexの版はプラグイン更新が失敗した場合も実際の参照先と一致させるため、
    `codex plugin list --json`の有効なエントリから解決する。
    """
    targets = plugin_warmup.agent_toolkit_targets(
        _INSTALLED_PLUGINS_PATH,
        claude_relative=_SCRIPT_RELATIVE,
        codex_relative=_SCRIPT_RELATIVE,
        tag=_TAG,
    )
    root = common.find_dotfiles_root()
    worktree_target = root / "agent-toolkit" / _SCRIPT_RELATIVE if root is not None else None
    return plugin_warmup.existing_targets([*targets, worktree_target], tag=_TAG)
