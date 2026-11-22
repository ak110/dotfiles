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
import contextlib
import logging
import os
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_toolkit._agents_server import service_forward
from agent_toolkit._atk import config_repositories, config_settings
from agent_toolkit._common import dotfiles_mode, process_tree, runtime_environment, windows_security

from pytools._internal import (
    claude_common,
    common,
    install_codex_plugins,
    log_format,
    plugin_warmup,
    post_apply_cache,
    post_apply_outcome,
)

_TAG = "agent-toolkit warmup"
_SCRIPT_RELATIVE = Path("agent_toolkit") / "agents_server_mcp.py"
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH
_STARTUP_TIMEOUT = 30.0
_UNCHANGED = "コード・起動条件が同じためMCP起動確認を省略"
_REPO_ID = "github.com/ak110/dotfiles"
_ELEVATED = (
    "管理者権限（昇格）で実行中のため、非昇格の会話と共有する管理サービスの起動と公開を省略"
    "（非昇格の会話の転送側は昇格したプロセスの管理サービスとの接続を認証できない）"
)
_ELEVATED_NOTICE = post_apply_outcome.PostApplyNotice(
    message="管理者権限（昇格）で実行したため、agents_serverの管理サービスを起動・公開していません。"
    "非昇格の端末で`update-dotfiles`を再実行してください（新しい会話の開始時にも起動します）。",
    command="update-dotfiles",
)
logger = logging.getLogger(__name__)


def run() -> post_apply_outcome.PostApplyOutcome:
    """導入済みの版を解決し、起動時のデータ領域へuv環境を構築する。"""
    if dotfiles_mode.is_minimal():
        return post_apply_outcome.PostApplyOutcome()
    checkout = common.find_dotfiles_root()
    candidates: list[Path] = []

    def targets() -> list[Path]:
        candidates.extend(_targets())
        return candidates

    prepared = plugin_warmup.prepare_targets(targets, tag=_TAG)
    if not prepared.commands:
        return post_apply_outcome.PostApplyOutcome()
    try:
        registered = _registered_checkout()
    except (OSError, ValueError) as error:
        _warn_skip(f"{config_settings.config_file_path()} のリポジトリ登録を読めません: {error}", None, checkout)
        return post_apply_outcome.PostApplyOutcome()
    checkout_skip = _checkout_skip(checkout, registered)
    if checkout_skip is not None:
        _warn_skip(checkout_skip, registered, checkout)
        return post_apply_outcome.PostApplyOutcome()
    approved = _approved_scripts(registered, candidates)
    commands = []
    for command in prepared.commands:
        try:
            script = Path(command[-1]).resolve(strict=True)
            project = Path(command[command.index("--project") + 1]).resolve(strict=True)
            if script not in approved or script != project / _SCRIPT_RELATIVE:
                raise ValueError("起動対象が登録済みの主checkoutまたは通常のプラグイン配布先と一致しません")
        except (OSError, ValueError, IndexError) as error:
            _warn_skip(f"{command[-1]}: {error}", registered, checkout)
            continue
        commands.append(command)
    if not commands:
        return post_apply_outcome.PostApplyOutcome()
    paths: list[Path] = []
    for command in commands:
        paths.extend((Path(command[0]), Path(command[1])))
        paths.append(Path(command[command.index("--project") + 1]) if "--project" in command else Path(command[-1]))
    # 更新のログ、診断、後続の整理に使う値は、管理サービスの起動条件に含めない。
    update_markers = {
        "DOTFILES_PYTOOLS_INSTALL_DETAIL",
        "UPDATE_DOTFILES_RUN_ID",
        "DOTFILES_PRUNE_READY_FILE",
        "UPDATE_DOTFILES_BOOTSTRAP_REPORT",
        "UPDATE_DOTFILES_BOOTSTRAP_FAILURES",
    }
    startup_environment = sorted((name, value) for name, value in os.environ.items() if name not in update_markers)
    key = post_apply_cache.fingerprint(paths, values=[repr(commands), repr(startup_environment)])
    # 昇格したプロセスが起動した管理サービスが受付先になると、同じユーザーの非昇格の会話は接続を認証できない。
    # 起動確認を省略した回は起動条件を記録せず、次の非昇格の実行で起動確認と公開を行う。
    elevated = windows_security.is_elevated_above_user_processes()
    if elevated:
        checks = _check_targets(commands, skip_startup=_ELEVATED)
    elif post_apply_cache.matches("agents-server-startup", key):
        checks = _check_targets(commands, skip_startup=_UNCHANGED)
    else:
        checks = _check_targets(commands)
    outcomes = asyncio.run(checks)
    failures: list[str] = []
    inherited_job_targets: list[str] = []
    dependencies_ready = True
    reclaim_errors = [outcome for outcome in outcomes if isinstance(outcome, process_tree.ReclaimError)]
    for command, outcome in zip(commands, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            logger.error("%s の確認に失敗", command[-1], exc_info=outcome, extra=log_format.LOG_ONLY)
            failures.append(f"{command[-1]}: {_exception_summary(outcome)}")
            continue
        dependency, startup = outcome
        dependency.report(target=log_format.home_short(Path(command[-1])), tag=_TAG)
        if not dependency.ok:
            dependencies_ready = False
        if startup is not None:
            if startup.inherited_job:
                inherited_job_targets.append(command[-1])
            if startup.ok:
                logger.info(log_format.format_status(_TAG, startup.message), extra=log_format.LOG_ONLY)
            else:
                failures.append(startup.message)
    if inherited_job_targets:
        logger.warning("%s: %s", ", ".join(inherited_job_targets), service_forward.INHERITED_JOB_WARNING)
    if reclaim_errors:
        # 回収不能は他の確認失敗と分け、post-applyが後続工程を開始しないよう例外で伝える。
        raise reclaim_errors[0]
    if failures:
        return post_apply_outcome.PostApplyOutcome(
            failure="; ".join(failures),
            next_action=post_apply_outcome.CHECK_UPDATE_LOG,
        )
    if dependencies_ready:
        if not elevated:
            post_apply_cache.save("agents-server-startup", key)
        plugin_warmup.cleanup_prepared(prepared, approved_commands=commands)
    if elevated:
        return post_apply_outcome.PostApplyOutcome(deferred=True, notices=(_ELEVATED_NOTICE,))
    return post_apply_outcome.PostApplyOutcome()


@dataclass(frozen=True)
class _StartupOutcome:
    ok: bool
    message: str
    inherited_job: bool = False


def _exception_summary(error: BaseException) -> str:
    """MCPのタスク群の外側の例外ではなく、原因の例外を1行で返す。"""
    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    reason = str(error).partition("\n")[0][:240]
    return f"{type(error).__name__}: {reason}" if reason else type(error).__name__


async def _check_targets(
    commands: Sequence[Sequence[str]],
    *,
    skip_startup: str | None = None,
) -> list[tuple[plugin_warmup.CommandOutcome, _StartupOutcome | None] | BaseException]:
    """独立rootの依存確認を並列に進め、MCP起動確認は1件ずつ行い、1rootの失敗でも全対象を回収する。

    起動確認は管理サービスの世代を1件ずつ公開する。同じ世代の転送側は1つの管理サービスを共有するため、
    並列に進めると、ある確認の候補の世代を別の確認が先に公開する。
    `skip_startup`を指定すると起動確認を行わず、その理由を確認結果として返す。
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
        elif skip_startup is not None:
            outcomes.append((dependency, _StartupOutcome(True, skip_startup)))
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
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    if root is not None:
        environment[service_forward.DEFER_PUBLISH_ENV] = "1"
    inherited_job = False
    service_output = ""
    with contextlib.ExitStack() as capture:
        errors = None
        try:
            parameters = StdioServerParameters(command=command[0], args=list(command[1:]), env=environment)
            remaining = _STARTUP_TIMEOUT
            if root is not None:
                # 入力の無い転送側は、管理サービスをreadyにした後で接続せずに終了する。
                # 常駐サービス自身のstderrは世代別ログへ向く。起動元だけのパイプを終了まで読む。
                service = await plugin_warmup.check_command(command, environment=environment, timeout=remaining)
                service_output = (service.result.stderr if service.result is not None else service.error).replace("\r\n", "\n")
                if service.result is None:
                    raise RuntimeError(service_output.partition("\n")[0])
                if not service.ok:
                    if service.result.stdout:
                        service_output += f"\nstdout: {service.result.stdout}"
                    raise RuntimeError(f"管理サービスを起動できません (exit {service.result.returncode})")
                inherited_job = service_forward.INHERITED_JOB_WARNING in service_output
                remaining -= time.monotonic() - started
                if remaining <= 0:
                    raise TimeoutError("管理サービスの起動だけで起動上限に達しました")
            phase = "process-start"
            errors = capture.enter_context(tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace"))
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
        except Exception as exc:  # noqa: BLE001  # 公開transportの例外群とstderrをpost-applyへ伝える。
            output = service_output
            if errors is not None:
                errors.seek(0)
                output += "\n" + errors.read()
            message = f"MCP起動確認に失敗 (段階={phase}、対象={command[-1]}): {_exception_summary(exc)}"
            if phase != "publish" and (
                reason := plugin_warmup.output_summary(output.replace(service_forward.INHERITED_JOB_WARNING, ""))
            ):
                message += f" / 子の出力: {reason}"
            logger.error(
                "%s (%.1f秒)\n子の出力:\n%s",
                message,
                time.monotonic() - started,
                output,
                exc_info=True,
                extra=log_format.LOG_ONLY,
            )
            if isinstance(exc, process_tree.ReclaimError):
                raise
            return _StartupOutcome(False, message, inherited_job)
    published = "・管理サービスの公開" if root is not None else ""
    return _StartupOutcome(
        True,
        f"MCPのinitialize・tools/list・終了{published}を確認 ({time.monotonic() - started:.1f}秒): {command[-1]}",
        inherited_job,
    )


def _service_root(command: Sequence[str]) -> Path | None:
    """起動コマンドが共有の管理サービスへ転送する配布rootを返す。管理サービスを持たない旧版のrootはNone。"""
    if "--project" not in command:
        return None
    root = Path(command[command.index("--project") + 1])
    return root if (root / "agent_toolkit" / "_agents_server" / "service.py").is_file() else None


def _registered_checkout() -> Path | None:
    """登録ファイルを読み取るだけにし、更新試験で移行や保存を起こさない。"""
    raw = config_settings.load_config_strict().get("repositories", "{}")
    if not isinstance(raw, str):
        raise ValueError("repositoriesの値がJSON文字列ではありません")
    path = config_repositories.parse(raw, check_paths=False).get(_REPO_ID, {}).get("path")
    return Path(path).resolve(strict=True) if isinstance(path, str) and path else None


def _checkout_skip(checkout: Path | None, registered: Path | None) -> str | None:
    """登録を優先し、登録がない時だけ既存のGit判定で主checkoutかを確かめる。"""
    if checkout is None:
        return "起動元のcheckoutを確認できません"
    if registered is not None:
        return None if checkout == registered else "更新元が登録先と一致しません"
    try:
        location = config_repositories.locate(checkout)
    except (OSError, ValueError) as error:
        return f"起動元 {checkout} のGit作業ツリーを確認できません: {error}"
    if location is not None and checkout == location.worktree == location.common_dir.parent:
        return None
    return "未登録の更新元をGitの主checkoutとも確認できません"


def _warn_skip(reason: str, registered: Path | None, checkout: Path | None) -> None:
    logger.warning(
        "共有管理サービスの起動と公開を省略: %s。登録先: %s、更新元: %s。"
        "atk config repos add または atk config repos set の --path で登録を主checkoutに合わせ、通常の更新を行ってください。"
        "linked worktreeを登録したまま通常の更新で復旧することには対応していません",
        reason,
        registered if registered is not None else "なし・未確認",
        checkout if checkout is not None else "未確認",
    )


def _approved_scripts(registered: Path | None, installed: Sequence[Path]) -> set[Path]:
    """登録先と、導入一覧に載るバージョン別キャッシュの実体だけを許可する。"""
    marketplace = Path("plugins", "cache", claude_common.MARKETPLACE_NAME, "agent-toolkit")
    homes = (claude_common.CLAUDE_HOME, Path(os.environ.get("CODEX_HOME", install_codex_plugins.CODEX_HOME)))
    caches = {(home / marketplace).resolve() for home in homes}
    approved = {registered / "agent-toolkit" / _SCRIPT_RELATIVE} if registered is not None else set()
    for script in installed:
        try:
            project = script.parents[1].resolve(strict=True)
            physical = script.resolve(strict=True)
            if project.parent in caches and physical == project / _SCRIPT_RELATIVE:
                approved.add(physical)
        except OSError:
            # 起動コマンドと許可集合が一致しない理由は起動直前に表示する。
            continue
    return approved


def _targets() -> list[Path]:
    """Claude CodeとCodexの実参照先を重複なく列挙する。"""
    installed = plugin_warmup.agent_toolkit_targets(
        _INSTALLED_PLUGINS_PATH,
        claude_relative=_SCRIPT_RELATIVE,
        codex_relative=_SCRIPT_RELATIVE,
        tag=_TAG,
    )
    checkout = common.find_dotfiles_root()
    worktree_target = checkout / "agent-toolkit" / _SCRIPT_RELATIVE if checkout is not None else None
    return plugin_warmup.existing_targets([*installed, worktree_target], tag=_TAG)
