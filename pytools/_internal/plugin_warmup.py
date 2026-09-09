"""agent-toolkit pluginの有効版選択とuv環境ウォームアップを共有する。"""

import json
import logging
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from pytools._internal import claude_common, install_codex_plugins, log_format

logger = logging.getLogger(__name__)

_WARMUP_TIMEOUT = 600.0
_CODEX_LIST_TIMEOUT = 60.0


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
    """Codexが参照する有効版pluginキャッシュ内の入口を返す。"""
    codex = claude_common.resolve_executable("codex")
    if codex is None:
        logger.info(log_format.format_status(tag, "codex CLI が見つからないためCodex分を除外"))
        return None
    result = claude_common.run_subprocess(
        [str(codex), "plugin", "list", "--json"],
        timeout=_CODEX_LIST_TIMEOUT,
        tag="codex",
    )
    if result is None or result.returncode != 0:
        logger.warning(log_format.format_status(tag, "Codex plugin一覧を取得できないためCodex分を除外"))
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning(log_format.format_status(tag, "Codex plugin一覧のJSONが不正なためCodex分を除外"))
        return None
    installed = data.get("installed") if isinstance(data, dict) else None
    version = enabled_version(installed if isinstance(installed, list) else [], plugin_id)
    if version is None:
        logger.info(log_format.format_status(tag, "Codex plugin の有効版が無いためCodex分を除外"))
        return None
    codex_home = Path(os.environ.get("CODEX_HOME", install_codex_plugins.CODEX_HOME))
    cache_root = codex_home / "plugins" / "cache" / claude_common.MARKETPLACE_NAME / plugin_name
    return cache_root / version / relative_path


def run(targets: Callable[[], list[Path]], *, tag: str, arguments: Sequence[str] = ()) -> bool:
    """実在する入口群のuv環境を構築し、設定変更なしを表すFalseを返す。"""
    uv = claude_common.resolve_executable("uv", preferred_directories=(Path.home() / ".local" / "bin",))
    if uv is None:
        logger.info(log_format.format_status(tag, "uv CLI が見つからずスキップ"))
        return False
    resolved = targets()
    if not resolved:
        logger.info(log_format.format_status(tag, "対象スクリプトが見つからずスキップ"))
        return False
    for target in resolved:
        warmup(target, uv, tag=tag, arguments=arguments)
    return False


def existing_targets(candidates: Sequence[Path | None], *, tag: str) -> list[Path]:
    """候補から実在する入口を重複なく列挙する。"""
    targets: list[Path] = []
    for candidate in candidates:
        if candidate is None or candidate in targets:
            continue
        if not candidate.is_file():
            logger.info(log_format.format_status(tag, f"対象が存在しないため除外: {log_format.home_short(candidate)}"))
            continue
        targets.append(candidate)
    return targets


def claude_plugin_scripts(
    installed_plugins_path: Path,
    *,
    plugin_id: str,
    relative_path: Path,
    tag: str,
) -> list[Path]:
    """Claude Codeのplugin一覧から指定した入口パスを返す。"""
    try:
        data = json.loads(installed_plugins_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.info(log_format.format_status(tag, "Claude Code plugin一覧が存在しないため除外"))
        return []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(log_format.format_status(tag, f"Claude Code plugin一覧を取得できないため除外: {exc}"))
        return []
    plugins = data.get("plugins") if isinstance(data, dict) else None
    entries = plugins.get(plugin_id) if isinstance(plugins, dict) else None
    if not isinstance(entries, list):
        logger.info(log_format.format_status(tag, "Claude Code plugin が未導入のため除外"))
        return []
    return [
        Path(install_path) / relative_path
        for entry in entries
        if isinstance(entry, dict)
        if isinstance(install_path := entry.get("installPath"), str)
    ]


def warmup(path: Path, uv: Path, *, tag: str, arguments: Sequence[str] = ()) -> None:
    """Plugin rootのuvプロジェクトで入口を1回起動し、依存環境を構築する。"""
    started = time.monotonic()
    result = claude_common.run_subprocess(
        [
            str(uv),
            "run",
            "--project",
            str(path.parents[1]),
            "--locked",
            "--no-default-groups",
            str(path),
            *arguments,
        ],
        timeout=_WARMUP_TIMEOUT,
        tag="uv",
    )
    elapsed = time.monotonic() - started
    short = log_format.home_short(path)
    if result is None:
        logger.warning(log_format.format_status(tag, f"環境構築に失敗: {short}"))
        return
    if result.returncode != 0:
        logger.warning(log_format.format_status(tag, f"環境構築が異常終了 (exit {result.returncode}): {short}"))
        return
    logger.info(log_format.format_status(tag, f"環境構築を確認 ({elapsed:.1f}秒): {short}"))
