"""agent-toolkit pluginの有効版選択とuv環境ウォームアップを共有する。"""

import asyncio
import contextlib
import datetime
import json
import logging
import os
import shlex
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import psutil
from agent_toolkit._atk.managed_temp import sweep
from agent_toolkit._common import plugin_environment, plugin_environment_cleanup, process_tree, session_review

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
    except json.JSONDecodeError as error:
        logger.warning(
            log_format.format_status(
                tag,
                f"Codex plugin一覧のJSON解析に失敗したためCodex分を除外: {error}。"
                f"実行CLI: {codex}、marketplace: {plugin_id.rpartition('@')[2]}、終了状態: {result.returncode}。"
                f"{shlex.join(['codex', *install_codex_plugins.plugin_list_args(plugin_id.rpartition('@')[2])])} "
                "で一覧の応答と導入状態を確認し、不整合を修復してから`update-dotfiles`を再実行する",
            ),
        )
        return None
    installed = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(installed, list):
        logger.warning(
            log_format.format_status(
                tag,
                f"Codex plugin一覧のinstalledが配列ではないためCodex分を除外。実行CLI: {codex}、"
                f"marketplace: {plugin_id.rpartition('@')[2]}、終了状態: {result.returncode}。"
                f"{shlex.join(['codex', *install_codex_plugins.plugin_list_args(plugin_id.rpartition('@')[2])])} "
                "で応答形式と導入状態を確認し、不整合を修復してから`update-dotfiles`を再実行する",
            )
        )
        return None
    version = enabled_version(installed, plugin_id)
    if version is None:
        logger.info(
            log_format.format_status(tag, "Codex plugin の有効版が無いためCodex分を除外。未導入のため対応不要"),
        )
        return None
    codex_home = Path(os.environ.get("CODEX_HOME", install_codex_plugins.CODEX_HOME))
    if version in {"", ".", ".."} or "/" in version or "\\" in version:
        logger.warning(
            log_format.format_status(
                tag,
                f"Codex pluginの版がキャッシュの単一ディレクトリ名ではありません: plugin={plugin_id}。"
                "plugin一覧の版と導入状態を修復してから`update-dotfiles`を再実行する",
            )
        )
        return None
    cache_root = codex_home / "plugins" / "cache" / claude_common.MARKETPLACE_NAME / plugin_name
    candidate = cache_root / version / relative_path
    if not candidate.is_file():
        logger.warning(
            log_format.format_status(
                tag,
                f"Codex配布物が欠損: plugin={plugin_id}、有効版={version}、期待相対パス={relative_path}、"
                f"解決先={candidate}。既存のplugin導入・更新経路で配布物を回復してから`update-dotfiles`を再実行する",
            )
        )
    return candidate


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
        logger.info(
            log_format.format_status(
                tag,
                "準備対象なし。候補解決時の診断を参照し、未導入・無効なら対応不要。"
                "一覧の取得失敗や配布物欠損は、その原因を修復してから`update-dotfiles`を再実行する",
            ),
        )
        return None
    python = _resolve_python(uv, tag)
    if python is None:
        return None
    return resolved, python


@dataclass(frozen=True)
class PreparedTargets:
    """準備できたrootの起動コマンドと、共有環境を特定できなかった登録済みの旧形式の対象。"""

    commands: list[list[str]]
    legacy: tuple[Path, ...] = ()


def prepare_targets(targets: Callable[[], list[Path]], *, tag: str) -> PreparedTargets:
    """依存keyごとに一度準備し、成功したrootの起動コマンドを列挙順に、旧形式の対象とともに返す。"""
    resolved = resolve_targets(targets, tag=tag)
    if resolved is None:
        return PreparedTargets([])
    paths, python = resolved
    prepared: dict[Path, bool] = {}
    commands: list[list[str]] = []
    legacy: list[Path] = []
    for target in paths:
        bootstrap = _bootstrap(target, python)
        key = _environment_key(Path(bootstrap[-1]), python, target=target, tag=tag)
        if key is None:
            legacy.append(target)
            continue
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
    return PreparedTargets(commands, tuple(legacy))


def cleanup_prepared(prepared: PreparedTargets) -> None:
    """実際に準備した全有効rootの依存環境を保持し、full更新後の旧資源を回収する。

    旧形式の対象が登録されている間は、その版のhookとMCPが使う共有環境を特定できないため、共有環境を回収しない。
    """
    commands = prepared.commands
    try:
        required = [
            plugin_environment.project_environment(
                Path(command[command.index("--project") + 1]), os.environ, python_executable=Path(command[0])
            )
            for command in commands
        ]
        # 同じ依存keyを使う全rootの版を、prepareを省略したrootも含めて記録する。
        for command, target in zip(commands, required, strict=True):
            if target.is_dir():
                with plugin_environment.prepare_lock(target):
                    plugin_environment.record_source(Path(command[command.index("--project") + 1]), target)
        if prepared.legacy:
            logger.info(
                "旧形式の版が登録されているため共有環境の回収を延期: %s。登録が新しい版へ移った後のupdate-dotfilesで回収します",
                ", ".join(log_format.home_short(target) for target in prepared.legacy),
            )
        else:
            rows = plugin_environment_cleanup.cleanup(required, os.environ)
            for row in rows:
                logger.info("共有依存環境: %s", json.dumps(row, ensure_ascii=False), extra=log_format.LOG_ONLY)
            counts = Counter(row["status"] for row in rows)
            action = "。保留が続く場合は update-dotfiles logs で理由を確認してください" if counts["deferred"] else ""
            logger.info(
                "共有依存環境: 保持%d件、削除%d件、保留%d件%s",
                counts["retained"],
                counts["deleted"],
                counts["deferred"],
                action,
            )
    except (OSError, ValueError, IndexError, psutil.Error) as error:
        logger.warning(
            "共有環境の回収を完了できませんでした: %s: %s。共有環境のアクセス権と配布元のJSONを確認してください。"
            "原因を修復した後にupdate-dotfilesを再実行すると回収を再試行します",
            plugin_environment.runtime_data(os.environ) / "environments",
            error,
        )
    try:
        session_review.sweep(now=time.time())
    except (OSError, ValueError, KeyError, TypeError) as error:
        logger.warning(
            "完了済み振り返りの回収を保留: %s: %s。表示したディレクトリ内のJSONとアクセス権を確認し、"
            "破損した記録は原本から復元してください。原因の解消後にupdate-dotfilesを再実行すると回収を再試行します",
            session_review.directory(),
            error,
        )
    result = sweep.sweep_managed_temp(now=datetime.datetime.now(datetime.UTC), force=True)
    for target, error in result.failed:
        logger.warning("一時資源の回収を保留: %s: %s", target, error, extra=log_format.LOG_ONLY)
    if result.failed:
        logger.warning(
            "一時資源%d件の回収を保留した。update-dotfiles logs で対象と理由を確認し、"
            "使用中の場合は利用終了後に、権限不足の場合は削除権限を確認してから update-dotfiles を再実行してください",
            len(result.failed),
        )
    for target in result.deleted:
        logger.info("一時資源を回収: %s", target, extra=log_format.LOG_ONLY)
    if result.deleted:
        logger.info("一時資源を%d件回収した", len(result.deleted))


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
    candidates: list[Path] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(install_path := entry.get("installPath"), str):
            continue
        candidate = Path(install_path) / relative_path
        if not candidate.is_file():
            logger.warning(
                log_format.format_status(
                    tag,
                    f"Claude Code配布物が欠損: plugin={plugin_id}、版={entry.get('version', '不明')}、"
                    f"期待相対パス={relative_path}、解決先={candidate}。"
                    "既存のplugin導入・更新経路で配布物を回復してから`update-dotfiles`を再実行する",
                ),
            )
        candidates.append(candidate)
    return candidates


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
    key = _environment_key(Path(bootstrap[-1]), python, target=path, tag=tag)
    if key is None:
        return False
    if prepared is None or key not in prepared:
        if not run_command([*bootstrap, "--prepare", str(path)], target=log_format.home_short(path), tag=tag):
            return False
        if prepared is not None:
            prepared.add(key)
    return run_command([*bootstrap, str(path), *arguments], target=log_format.home_short(path), tag=tag)


def _environment_key(root: Path, python: Path, *, target: Path, tag: str) -> Path | None:
    """配布rootの共有依存環境を返す。依存の固定内容を持たない旧形式の配布rootはNone。

    旧形式（配布rootにuvのプロジェクトを置いた版）は、その版自身の起動部分だけが準備できる。
    更新に失敗して有効版が旧形式のまま残った場合に、更新後の起動部分で準備せず再更新を案内する。
    """
    try:
        return plugin_environment.project_environment(root, os.environ, python_executable=python)
    except FileNotFoundError:
        logger.warning(
            log_format.format_status(
                tag,
                f"依存の固定内容（{plugin_environment.dependency_lock(root)}）が無い旧形式の配布rootのため準備を省略: "
                f"{log_format.home_short(target)}。`update-dotfiles`を再実行してプラグインを更新する",
            )
        )
        return None


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


async def check_command(command: Sequence[str], *, environment: Mapping[str, str] | None = None) -> CommandOutcome:
    """依存確認を非同期に待ち、取消し・時間切れでも所有する子孫を回収する。"""
    started = time.monotonic()
    result = None
    error = ""
    try:
        process = await asyncio.create_subprocess_exec(
            *command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment
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
                if residual:
                    raise process_tree.ReclaimError(
                        [process_tree.describe(item) for item in residual], residual, context="依存確認"
                    )
                try:
                    await asyncio.wait_for(process.communicate(), timeout=process_tree.RECLAIM_TIMEOUT_SECONDS)
                except TimeoutError as reap_error:
                    # 子を止めた後もパイプが閉じないのは、列挙外の子孫が出力を保持しているためである。
                    raise process_tree.ReclaimError(
                        [f"{process.pid}: 終了後も出力のパイプが閉じない"], descendants, context="依存確認"
                    ) from reap_error
    except (OSError, TimeoutError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    return CommandOutcome(list(command), result, time.monotonic() - started, error)
