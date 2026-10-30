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
from dataclasses import dataclass
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
    commands = plugin_warmup.prepare_targets(_targets, tag=_TAG)
    if not commands:
        return post_apply_outcome.PostApplyOutcome()
    outcomes = asyncio.run(_check_targets(commands))
    failures: list[str] = []
    for command, outcome in zip(commands, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            logger.warning(log_format.format_status(_TAG, f"対象の確認に失敗: {command[-1]} / {outcome}"))
            failures.append(command[-1])
            continue
        dependency, startup = outcome
        dependency.report(target=log_format.home_short(Path(command[-1])), tag=_TAG)
        if startup is not None:
            (logger.info if startup.ok else logger.warning)(log_format.format_status(_TAG, startup.message))
            if not startup.ok:
                failures.append(command[-1])
    if failures:
        return post_apply_outcome.PostApplyOutcome(
            failure="準備後の起動確認に失敗: " + ", ".join(failures),
            next_action="`update-dotfiles logs`の起動段階・標準エラーを確認し、`update-dotfiles`を再実行する",
        )
    return post_apply_outcome.PostApplyOutcome()


@dataclass(frozen=True)
class _StartupOutcome:
    ok: bool
    message: str


async def _check_targets(
    commands: Sequence[Sequence[str]],
) -> list[tuple[plugin_warmup.CommandOutcome, _StartupOutcome | None] | BaseException]:
    """独立rootの確認を並列に進め、1rootの失敗でも全対象を回収する。"""

    async def check(command: Sequence[str]) -> tuple[plugin_warmup.CommandOutcome, _StartupOutcome | None]:
        dependency = await plugin_warmup.check_command([*command, "--check-dependencies"])
        startup = await _check_startup(command) if dependency.ok else None
        return dependency, startup

    return await asyncio.gather(*(check(command) for command in commands), return_exceptions=True)


async def _check_startup(command: Sequence[str]) -> _StartupOutcome:
    """準備済みの公開stdioへ接続し、ホストの起動上限内にツール一覧を得る。"""
    # minimal導入と単なる対象解決ではMCP SDKを読み込まない。
    import anyio  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    from mcp import ClientSession  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    started = time.monotonic()
    phase = "process-start"
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        try:
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

        except Exception as exc:  # noqa: BLE001  # 公開transportの例外群とstderrをpost-applyへ伝える。
            errors.seek(0)
            return _StartupOutcome(
                False,
                f"MCP起動確認に失敗 (段階={phase}、{time.monotonic() - started:.1f}秒、対象={command[-1]}) / "
                + "".join(traceback.format_exception(exc))
                + errors.read(),
            )
    return _StartupOutcome(True, f"MCPのinitialize・tools/list・終了を確認 ({time.monotonic() - started:.1f}秒): {command[-1]}")


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
