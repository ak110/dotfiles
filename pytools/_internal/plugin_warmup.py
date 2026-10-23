"""agent-toolkit pluginの有効版選択とuv環境ウォームアップを共有する。"""

import json
import logging
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from agent_toolkit._common import plugin_environment

from pytools._internal import claude_common, common, install_codex_plugins, log_format, post_apply_outcome

logger = logging.getLogger(__name__)

_WARMUP_TIMEOUT = 600.0
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
        [str(codex), "plugin", "list", "--json"],
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
        return post_apply_outcome.PostApplyOutcome()
    resolved = targets()
    if not resolved:
        logger.warning(
            log_format.format_status(
                tag,
                "対象スクリプトが見つからず環境構築を開始できない。`update-dotfiles`を再実行する",
            ),
        )
        return post_apply_outcome.PostApplyOutcome()
    startup_failures: list[str] = []
    for target in resolved:
        if (
            warmup(target, uv, tag=tag, arguments=arguments)
            and readiness is not None
            and not readiness([*_bootstrap(target, uv), str(target)])
        ):
            startup_failures.append(str(target))
    if startup_failures:
        return post_apply_outcome.PostApplyOutcome(
            failure="準備後の起動確認に失敗: " + ", ".join(startup_failures),
            next_action="`update-dotfiles logs`の起動段階・標準エラーを確認し、`update-dotfiles`を再実行する",
        )
    return post_apply_outcome.PostApplyOutcome()


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
    uv: Path,
    *,
    tag: str,
    arguments: Sequence[str] = (),
) -> bool:
    """Plugin rootのuvプロジェクトで対象スクリプトを1回起動し、依存環境を構築する。構築できた場合は真を返す。"""
    bootstrap = _bootstrap(path, uv)
    prepared = run_command([*bootstrap, "--prepare", str(path)], target=log_format.home_short(path), tag=tag)
    return prepared and run_command([*bootstrap, str(path), *arguments], target=log_format.home_short(path), tag=tag)


def _bootstrap(path: Path, uv: Path) -> list[str]:
    """依存準備と実起動が共用するlauncherを返す。"""
    root = next(parent.parent for parent in path.parents if parent.name == "agent_toolkit")
    launcher = Path(plugin_environment.__file__).resolve().parents[2] / "scripts" / "launch.py"
    return [
        str(uv),
        "run",
        "--no-config",
        "--no-project",
        "--python",
        "3.15",
        "python",
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
    elapsed = time.monotonic() - started
    if result is not None and result.returncode == 0:
        logger.info(log_format.format_status(tag, f"環境構築を確認 (exit 0、{elapsed:.1f}秒): {target}"))
        return True
    # 失敗時は構築コマンドの標準エラーと標準出力を必ず伝播させる。
    # 終了コードと経過時間だけの診断では、依存解決の失敗本文が永続ログにもtracebackにも現れず、
    # ユーザーと後続の調査主体が原因へ到達できない。
    if result is None:
        summary = f"環境構築に失敗 (exit codeなし、{elapsed:.1f}秒): {target}"
    else:
        summary = f"環境構築が異常終了 (exit {result.returncode}、{elapsed:.1f}秒): {target}"
    command = subprocess.list2cmdline(cmd) if sys.platform == "win32" else shlex.join(cmd)
    message = f"{summary} / {common.format_cli_error(result)}。手動で`{command}`を実行する"
    logger.warning(log_format.format_status(tag, message))
    return False
