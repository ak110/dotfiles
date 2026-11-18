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

from agent_toolkit._agents_server import service_forward
from agent_toolkit._common import dotfiles_mode, process_tree, runtime_environment

from pytools._internal import claude_common, common, log_format, plugin_warmup, post_apply_cache, post_apply_outcome

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
    paths: list[Path] = []
    for command in commands:
        paths.extend((Path(command[0]), Path(command[1])))
        paths.append(Path(command[command.index("--project") + 1]) if "--project" in command else Path(command[-1]))
    # 更新実行IDはログの対応付け専用であり、毎回変わってもMCPの起動条件を変えない。
    startup_environment = sorted((name, value) for name, value in os.environ.items() if name != "UPDATE_DOTFILES_RUN_ID")
    key = post_apply_cache.fingerprint(paths, values=[repr(commands), repr(startup_environment)])
    checks = (
        _check_targets(commands, skip_startup=True)
        if post_apply_cache.matches("agents-server-startup", key)
        else _check_targets(commands)
    )
    outcomes = asyncio.run(checks)
    failures: list[str] = []
    dependencies_ready = True
    reclaim_errors = [outcome for outcome in outcomes if isinstance(outcome, process_tree.ReclaimError)]
    for command, outcome in zip(commands, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            logger.warning(log_format.format_status(_TAG, f"対象の確認に失敗: {command[-1]} / {outcome}"))
            failures.append(command[-1])
            continue
        dependency, startup = outcome
        dependency.report(target=log_format.home_short(Path(command[-1])), tag=_TAG)
        if not dependency.ok:
            dependencies_ready = False
        if startup is not None:
            (logger.info if startup.ok else logger.warning)(log_format.format_status(_TAG, startup.message))
            if not startup.ok:
                failures.append(command[-1])
    if reclaim_errors:
        # 回収不能は他の確認失敗と分け、post-applyが後続工程を開始しないよう例外で伝える。
        raise reclaim_errors[0]
    if failures:
        return post_apply_outcome.PostApplyOutcome(
            failure="準備後の起動確認に失敗: " + ", ".join(failures),
            next_action="`update-dotfiles logs`の起動段階・標準エラーを確認し、`update-dotfiles`を再実行する",
        )
    if dependencies_ready:
        post_apply_cache.save("agents-server-startup", key)
        plugin_warmup.cleanup_prepared(commands)
    return post_apply_outcome.PostApplyOutcome()


@dataclass(frozen=True)
class _StartupOutcome:
    ok: bool
    message: str


async def _check_targets(
    commands: Sequence[Sequence[str]],
    *,
    skip_startup: bool = False,
) -> list[tuple[plugin_warmup.CommandOutcome, _StartupOutcome | None] | BaseException]:
    """独立rootの依存確認を並列に進め、MCP起動確認は1件ずつ行い、1rootの失敗でも全対象を回収する。

    起動確認は管理サービスの世代を1件ずつ公開する。同じ世代の転送側は1つの管理サービスを共有するため、
    並列に進めると、ある確認の候補の世代を別の確認が先に公開する。
    """
    dependencies = await asyncio.gather(
        *(plugin_warmup.check_command([*command, "--check-dependencies"]) for command in commands), return_exceptions=True
    )
    outcomes: list[tuple[plugin_warmup.CommandOutcome, _StartupOutcome | None] | BaseException] = []
    for command, dependency in zip(commands, dependencies, strict=True):
        if isinstance(dependency, BaseException):
            outcomes.append(dependency)
        elif not dependency.ok:
            outcomes.append((dependency, None))
        elif skip_startup:
            outcomes.append((dependency, _StartupOutcome(True, "コード・起動条件が同じためMCP起動確認を省略")))
        else:
            try:
                outcomes.append((dependency, await _check_startup(command)))
            except process_tree.ReclaimError as error:
                outcomes.append(error)
    return outcomes


async def _check_startup(command: Sequence[str]) -> _StartupOutcome:
    """準備済みの公開stdioへ接続してホストの起動上限内にツール一覧を得てから、管理サービスの世代を公開する。

    常駐する管理サービスは、確認用の接続より先に、MCP SDKが所有しない子プロセスから公開を保留して起動する。
    WindowsのMCP SDKは起動したサーバーを離脱不可のKILL_ON_JOB_CLOSEのJobへ入れ、接続を閉じるとJobの全プロセスを止める。
    確認用の転送側が管理サービスを起動すると、サービスも同じJobに属して確認の終了とともに止まる
    （2026-11-18、受付先が終了した世代を指し、稼働中の会話の状態保存と送達が失敗した）。
    世代は、確認用の接続と子プロセスの終了後も生存することを確かめてから受付先として公開する。
    """
    # minimal導入と単なる対象解決ではMCP SDKを読み込まない。
    import anyio  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    from mcp import ClientSession  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    started = time.monotonic()
    phase = "service-start"
    root = _service_root(command)
    environment = dict(os.environ)
    if root is not None:
        environment[service_forward.DEFER_PUBLISH_ENV] = "1"
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        try:
            parameters = StdioServerParameters(command=command[0], args=list(command[1:]), env=environment)
            remaining = _STARTUP_TIMEOUT
            if root is not None:
                # 入力の無い転送側は、管理サービスをreadyにした後で接続せずに終了する。
                # anyioの期限は取消しを回収処理の待機にも届け続けるため、子の回収を待つ区間はasyncioの期限で区切る。
                async with asyncio.timeout(remaining):
                    service = await plugin_warmup.check_command(command, environment=environment)
                if not service.ok:
                    raise RuntimeError(
                        "管理サービスを起動できません: " + (service.error or common.format_cli_error(service.result))
                    )
                remaining -= time.monotonic() - started
                if remaining <= 0:
                    raise TimeoutError("管理サービスの起動だけで起動上限に達しました")
            phase = "process-start"
            with anyio.fail_after(remaining):
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
            if root is not None:
                phase = "publish"
                caller = runtime_environment.CallerContext(os.environ, Path.cwd())
                await asyncio.to_thread(service_forward.publish_service, caller, root, timeout=_STARTUP_TIMEOUT)
        except process_tree.ReclaimError:
            raise
        except Exception as exc:  # noqa: BLE001  # 公開transportの例外群とstderrをpost-applyへ伝える。
            errors.seek(0)
            return _StartupOutcome(
                False,
                f"MCP起動確認に失敗 (段階={phase}、{time.monotonic() - started:.1f}秒、対象={command[-1]}) / "
                + "".join(traceback.format_exception(exc))
                + errors.read(),
            )
    published = "・管理サービスの公開" if root is not None else ""
    return _StartupOutcome(
        True, f"MCPのinitialize・tools/list・終了{published}を確認 ({time.monotonic() - started:.1f}秒): {command[-1]}"
    )


def _service_root(command: Sequence[str]) -> Path | None:
    """起動コマンドが共有の管理サービスへ転送する配布rootを返す。管理サービスを持たない旧版のrootはNone。"""
    if "--project" not in command:
        return None
    root = Path(command[command.index("--project") + 1])
    return root if (root / "agent_toolkit" / "_agents_server" / "service.py").is_file() else None


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
