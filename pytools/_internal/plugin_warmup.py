"""agent-toolkit pluginの有効版選択とuv環境ウォームアップを共有する。"""

import asyncio
import contextlib
import json
import logging
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import psutil
from agent_toolkit._common import plugin_environment, process_tree

from pytools._internal import claude_common, common, install_codex_plugins, log_format, post_apply_outcome

logger = logging.getLogger(__name__)

_WARMUP_TIMEOUT = 660.0
_PYTHON_FIND_TIMEOUT = 60.0
_CODEX_LIST_TIMEOUT = 60.0
_AGENT_TOOLKIT_ID = f"agent-toolkit@{claude_common.MARKETPLACE_NAME}"


def enabled_version(installed: list[object], plugin_id: str) -> str | None:
    """plugin一覧から指定したpluginの有効版を返す。"""
    for item in installed:
        if not isinstance(item, dict):
            continue
        if item.get("pluginId") != plugin_id or item.get("enabled") is not True:
            continue
        version = item.get("version")
        if isinstance(version, str):
            return version
    return None


def codex_plugin_script(*, plugin_id: str, plugin_name: str, relative_path: Path, tag: str) -> Path | None:
    """Codexが参照する有効版pluginキャッシュ内の起動スクリプトのパスを返す。"""
    codex = common.resolve_executable("codex")
    if codex is None:
        logger.info(
            log_format.format_status(tag, "codex CLI が見つからないためCodex分を除外。未導入のため対応不要"),
        )
        return None
    result = common.run_subprocess(
        [str(codex), *install_codex_plugins.plugin_list_args(plugin_id.rpartition("@")[2])],
        timeout=_CODEX_LIST_TIMEOUT,
        tag="codex",
    )
    if result is None or result.returncode != 0:
        logger.warning(
            log_format.format_status(
                tag,
                "Codex plugin一覧を取得できないためCodex分を除外。`update-dotfiles`を再実行する",
            ),
        )
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning(
            log_format.format_status(
                tag,
                "Codex plugin一覧のJSONが不正なためCodex分を除外。`update-dotfiles`を再実行する",
            ),
        )
        return None
    installed = data.get("installed") if isinstance(data, dict) else None
    version = enabled_version(installed if isinstance(installed, list) else [], plugin_id)
    if version is None:
        logger.info(
            log_format.format_status(tag, "Codex plugin の有効版が無いためCodex分を除外。未導入のため対応不要"),
        )
        return None
    codex_home = Path(os.environ.get("CODEX_HOME", install_codex_plugins.CODEX_HOME))
    cache_root = codex_home / "plugins" / "cache" / claude_common.MARKETPLACE_NAME / plugin_name
    return cache_root / version / relative_path


def run(
    targets: Callable[[], list[Path]],
    *,
    tag: str,
    arguments: Sequence[str] = (),
    readiness: Callable[[Sequence[str]], bool] | None = None,
) -> post_apply_outcome.PostApplyOutcome:
    """実在する起動スクリプト群のuv環境を構築する。

    uv環境の構築は導入に当たるため、構築できない場合は警告だけを出力してスキップと数える。
    uvのキャッシュだけへ作用し設定を変えないため、変更なしを返す。
    """
    resolved = resolve_targets(targets, tag=tag)
    if resolved is None:
        return post_apply_outcome.PostApplyOutcome()
    paths, python = resolved
    startup_failures: list[str] = []
    prepared: set[Path] = set()
    for target in paths:
        if (
            warmup(target, python, tag=tag, arguments=arguments, prepared=prepared)
            and readiness is not None
            and not readiness([*_bootstrap(target, python), str(target)])
        ):
            startup_failures.append(str(target))
    if startup_failures:
        return post_apply_outcome.PostApplyOutcome(
            failure="準備後の起動確認に失敗: " + ", ".join(startup_failures),
            next_action="`update-dotfiles logs`の起動段階・標準エラーを確認し、`update-dotfiles`を再実行する",
        )
    return post_apply_outcome.PostApplyOutcome()


def resolve_targets(targets: Callable[[], list[Path]], *, tag: str) -> tuple[list[Path], Path] | None:
    """有効な対象と全対象で共用するPythonを解決する。"""
    uv = common.resolve_executable("uv", preferred_directories=(Path.home() / ".local" / "bin",))
    if uv is None:
        logger.warning(
            log_format.format_status(
                tag,
                "uv CLI が見つからず環境構築を開始できない。"
                "https://docs.astral.sh/uv/getting-started/installation/ "
                "の手順でuvを導入して`update-dotfiles`を再実行する",
            ),
        )
        return None
    resolved = targets()
    if not resolved:
        logger.warning(
            log_format.format_status(
                tag,
                "対象スクリプトが見つからず環境構築を開始できない。`update-dotfiles`を再実行する",
            ),
        )
        return None
    python = _resolve_python(uv, tag)
    if python is None:
        return None
    return resolved, python


def prepare_targets(targets: Callable[[], list[Path]], *, tag: str) -> list[list[str]]:
    """依存keyごとに一度準備し、成功したrootの起動コマンドを列挙順に返す。"""
    resolved = resolve_targets(targets, tag=tag)
    if resolved is None:
        return []
    paths, python = resolved
    prepared: dict[Path, bool] = {}
    commands: list[list[str]] = []
    for target in paths:
        bootstrap = _bootstrap(target, python)
        key = plugin_environment.project_environment(Path(bootstrap[-1]), os.environ, python_executable=python)
        if key not in prepared:
            prepared[key] = run_command([*bootstrap, "--prepare", str(target)], target=log_format.home_short(target), tag=tag)
        if prepared[key]:
            commands.append([*bootstrap, str(target)])
        else:
            logger.warning(
                log_format.format_status(
                    tag, f"共有依存環境を準備できず対象の確認を省略: {target}。`update-dotfiles`を再実行する"
                )
            )
    return commands


def existing_targets(candidates: Sequence[Path | None], *, tag: str) -> list[Path]:
    """候補から実在する起動スクリプトのパスを重複なく列挙する。"""
    targets: list[Path] = []
    for candidate in candidates:
        if candidate is None or candidate in targets:
            continue
        if not candidate.is_file():
            logger.info(
                log_format.format_status(
                    tag,
                    f"対象が存在しないため除外: {log_format.home_short(candidate)}。`update-dotfiles`を再実行する",
                ),
            )
            continue
        targets.append(candidate)
    return targets


def agent_toolkit_targets(
    installed_plugins_path: Path,
    *,
    claude_relative: Path,
    codex_relative: Path,
    tag: str,
) -> list[Path]:
    """Claude CodeとCodexが実際に参照するagent-toolkitの版の中のファイルを重複なく列挙する。"""
    claude = claude_plugin_scripts(
        installed_plugins_path,
        plugin_id=_AGENT_TOOLKIT_ID,
        relative_path=claude_relative,
        tag=tag,
    )
    codex = codex_plugin_script(
        plugin_id=_AGENT_TOOLKIT_ID,
        plugin_name="agent-toolkit",
        relative_path=codex_relative,
        tag=tag,
    )
    return existing_targets([*claude, codex], tag=tag)


def claude_plugin_scripts(
    installed_plugins_path: Path,
    *,
    plugin_id: str,
    relative_path: Path,
    tag: str,
) -> list[Path]:
    """Claude Codeのplugin一覧から指定した起動スクリプトのパスを返す。"""
    try:
        data = json.loads(installed_plugins_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.info(
            log_format.format_status(tag, "Claude Code plugin一覧が存在しないため除外。未導入のため対応不要"),
        )
        return []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            log_format.format_status(
                tag,
                f"Claude Code plugin一覧を取得できないため除外: {exc}。`update-dotfiles`を再実行する",
            ),
        )
        return []
    plugins = data.get("plugins") if isinstance(data, dict) else None
    entries = plugins.get(plugin_id) if isinstance(plugins, dict) else None
    if not isinstance(entries, list):
        logger.info(log_format.format_status(tag, "Claude Code plugin が未導入のため除外。未導入のため対応不要"))
        return []
    return [
        Path(install_path) / relative_path
        for entry in entries
        if isinstance(entry, dict)
        if isinstance(install_path := entry.get("installPath"), str)
    ]


def warmup(
    path: Path,
    python: Path,
    *,
    tag: str,
    arguments: Sequence[str] = (),
    prepared: set[Path] | None = None,
) -> bool:
    """同じ依存環境は一度だけ準備し、各対象の実起動確認は必ず実行する。"""
    bootstrap = _bootstrap(path, python)
    root = Path(bootstrap[-1])
    key = plugin_environment.project_environment(root, os.environ, python_executable=python)
    if prepared is None or key not in prepared:
        if not run_command([*bootstrap, "--prepare", str(path)], target=log_format.home_short(path), tag=tag):
            return False
        if prepared is not None:
            prepared.add(key)
    return run_command([*bootstrap, str(path), *arguments], target=log_format.home_short(path), tag=tag)


def _resolve_python(uv: Path, tag: str) -> Path | None:
    """warmup全体で共有するPython実行ファイルを一度だけ解決する。"""
    result = common.run_subprocess(
        [str(uv), "python", "find", "--no-config", "--no-project", "3.15"],
        timeout=_PYTHON_FIND_TIMEOUT,
        tag=Path(uv).stem,
    )
    if result is not None and result.returncode == 0:
        path = Path(result.stdout.strip())
        if path.is_file():
            return path.resolve()
    logger.warning(
        log_format.format_status(
            tag,
            "agent-toolkit用Pythonを解決できないため環境構築を除外: "
            f"{common.format_cli_error(result)}。`update-dotfiles`を再実行する",
        )
    )
    return None


def _bootstrap(path: Path, python: Path) -> list[str]:
    """解決済みPythonで依存準備と実起動が共用するlauncherを返す。"""
    root = next(parent.parent for parent in path.parents if parent.name == "agent_toolkit")
    launcher = Path(plugin_environment.__file__).resolve().parents[2] / "scripts" / "launch.py"
    return [
        str(python),
        str(launcher),
        "--project",
        str(root),
    ]


def run_command(cmd: Sequence[str], *, target: str, tag: str) -> bool:
    """環境を構築するコマンドを上限時間付きで1回実行し、対象・終了コード・所要時間を記録する。

    失敗は警告として記録し、構築できた場合は真を返す。
    """
    started = time.monotonic()
    result = common.run_subprocess(list(cmd), timeout=_WARMUP_TIMEOUT, tag=Path(cmd[0]).stem)
    return CommandOutcome(list(cmd), result, time.monotonic() - started).report(target=target, tag=tag)


@dataclass(frozen=True)
class CommandOutcome:
    """並列実行中の診断を保持し、親workerで対象順に表示する。"""

    command: list[str]
    result: subprocess.CompletedProcess[str] | None
    elapsed: float
    error: str = ""

    @property
    def ok(self) -> bool:
        """コマンドが正常終了したかを返す。"""
        return self.result is not None and self.result.returncode == 0

    def report(self, *, target: str, tag: str) -> bool:
        """標準出力・標準エラーを対象と対応付け、成功と失敗の診断を表示する。"""
        if self.ok:
            logger.info(log_format.format_status(tag, f"環境構築を確認 (exit 0、{self.elapsed:.1f}秒): {target}"))
            return True
        summary = (
            f"環境構築に失敗 (exit codeなし、{self.elapsed:.1f}秒): {target}"
            if self.result is None
            else f"環境構築が異常終了 (exit {self.result.returncode}、{self.elapsed:.1f}秒): {target}"
        )
        command = subprocess.list2cmdline(self.command) if sys.platform == "win32" else shlex.join(self.command)
        message = f"{summary} / {self.error or common.format_cli_error(self.result)}。手動で`{command}`を実行する"
        logger.warning(log_format.format_status(tag, message))
        return False


async def check_command(command: Sequence[str]) -> CommandOutcome:
    """依存確認を非同期に待ち、取消し・時間切れでも所有する子孫を回収する。"""
    started = time.monotonic()
    result = None
    error = ""
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=_WARMUP_TIMEOUT)
            assert process.returncode is not None
            result = subprocess.CompletedProcess(
                list(command),
                process.returncode,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
            )
        finally:
            if process.returncode is None:
                descendants = process_tree.collect_descendants(process.pid)
                with contextlib.suppress(psutil.NoSuchProcess):
                    descendants.insert(0, psutil.Process(process.pid))
                # 回収の待機だけを退避し、ログはthread-local捕捉のある親workerで出力する。
                residual = await asyncio.to_thread(process_tree.reclaim_processes, descendants)
                await asyncio.wait_for(process.communicate(), timeout=process_tree.RECLAIM_TIMEOUT_SECONDS)
                if residual:
                    raise RuntimeError("依存確認の子孫プロセスが残存: " + " / ".join(map(process_tree.describe, residual)))
    except (OSError, TimeoutError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    return CommandOutcome(list(command), result, time.monotonic() - started, error)
