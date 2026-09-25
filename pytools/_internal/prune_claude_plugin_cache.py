"""Claude Codeのplugin cacheに残る旧版ディレクトリを、後継版の導入から猶予期間の経過後に削除する。

Claude Codeは`plugin install`と`plugin update`で`<cache>/<marketplace>/<name>/<version>/`を新設するだけで、
旧版を削除しない。一方、起動中のClaude Codeセッションは起動時に解決した版ディレクトリを
`hooks.json`と`.mcp.json`の`${CLAUDE_PLUGIN_ROOT}`から参照し続けるため、旧版を即時に削除すると
そのセッションのhookとMCPが失敗する。そこで後継版の導入から`GRACE_DAYS`日が経過した版だけを削除する。

導入時刻には各版の`.claude-plugin/plugin.json`のmtimeを用いる。版ディレクトリ自体のmtimeは
`uv run`が`.venv`などを作成するたびに更新され、導入時刻を表さないためである。
`plugin.json`を欠く版だけは版ディレクトリのmtimeで代替する。
"""

import json
import logging
import shutil
import time
from pathlib import Path

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

_TAG = "plugin cache"
_INSTALLED_PLUGINS_PATH = claude_common.INSTALLED_PLUGINS_PATH

# 後継版の導入から旧版を削除するまでの猶予日数。
# 起動中のセッションが旧版を参照し続ける期間を見込んだ値であり、変更する場合はこの定数だけを変える。
GRACE_DAYS = 7


def run() -> bool:
    """現行版以外の版ディレクトリのうち、後継版の導入から猶予期間を過ぎたものを削除する。

    Returns:
        1件以上削除した場合True。`installed_plugins.json`を解釈できない場合は何もせずFalse。
    """
    current_paths = _current_install_paths()
    if current_paths is None:
        return False
    cache_root = _INSTALLED_PLUGINS_PATH.parent / "cache"
    try:
        cache_resolved = cache_root.resolve()
    except OSError as error:
        logger.warning(log_format.format_status(_TAG, f"cacheディレクトリを解決できないためスキップ: {error}"))
        return False
    deadline = time.time() - GRACE_DAYS * 86400
    removed = 0
    for (marketplace, name), installed in sorted(current_paths.items()):
        plugin_dir = cache_root / marketplace / name
        for version_dir in _expired_versions(plugin_dir, installed, deadline):
            if _remove_version_dir(version_dir, cache_resolved):
                removed += 1
    return removed > 0


def _current_install_paths() -> dict[tuple[str, str], set[Path]] | None:
    """`installed_plugins.json`から`(marketplace, name)`ごとの現行`installPath`の集合を返す。"""
    try:
        data = json.loads(_INSTALLED_PLUGINS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as error:
        logger.warning(log_format.format_status(_TAG, f"plugin一覧の読み込みに失敗したため旧版削除をスキップ: {error}"))
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        logger.warning(log_format.format_status(_TAG, "plugin一覧の構造が想定と異なるため旧版削除をスキップ"))
        return None
    result: dict[tuple[str, str], set[Path]] = {}
    for key, entries in plugins.items():
        if not isinstance(key, str) or "@" not in key or not isinstance(entries, list):
            continue
        name, marketplace = key.rsplit("@", 1)
        if (
            not name
            or not marketplace
            or any(part in ("", ".", "..") or "/" in part or "\\" in part for part in (name, marketplace))
        ):
            continue
        paths = result.setdefault((marketplace, name), set())
        for entry in entries:
            install_path = entry.get("installPath") if isinstance(entry, dict) else None
            if isinstance(install_path, str) and install_path:
                paths.add(_resolve(Path(install_path).expanduser()))
    return result


def _expired_versions(plugin_dir: Path, installed: set[Path], deadline: float) -> list[Path]:
    """後継版の導入時刻が`deadline`以前である非現行の版ディレクトリを返す。"""
    try:
        candidates = [child for child in plugin_dir.iterdir() if not _is_link_like(child) and child.is_dir()]
    except OSError:
        return []
    timed = sorted(((_installed_at(child), child) for child in candidates), key=lambda item: (item[0], item[1].name))
    expired: list[Path] = []
    for index, (_, version_dir) in enumerate(timed[:-1]):
        if _resolve(version_dir) in installed:
            continue
        successor_installed_at = timed[index + 1][0]
        if successor_installed_at <= deadline:
            expired.append(version_dir)
    return expired


def _installed_at(version_dir: Path) -> float:
    """版ディレクトリの導入時刻を返す。`plugin.json`を欠く場合は版ディレクトリのmtimeで代替する。"""
    try:
        return (version_dir / ".claude-plugin" / "plugin.json").stat().st_mtime
    except OSError:
        pass
    try:
        return version_dir.stat().st_mtime
    except OSError:
        # 時刻を得られない版は最新とみなし、後継の判定にも削除の対象にも使わない。
        return float("inf")


def _remove_version_dir(version_dir: Path, cache_resolved: Path) -> bool:
    """cache配下にあることを確認してから版ディレクトリを削除する。"""
    try:
        version_dir.resolve().relative_to(cache_resolved)
    except (OSError, ValueError):
        logger.warning(log_format.format_status(log_format.home_short(version_dir), "cache配下ではないため削除をスキップ"))
        return False
    try:
        shutil.rmtree(version_dir)
    except OSError as error:
        logger.warning(log_format.format_status(log_format.home_short(version_dir), f"旧版の削除に失敗: {error}"))
        return False
    logger.info(log_format.format_status(log_format.home_short(version_dir), "旧版を削除"))
    return True


def _is_link_like(path: Path) -> bool:
    """シンボリックリンクまたはWindowsのディレクトリジャンクションかを返す。"""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _resolve(path: Path) -> Path:
    """比較用に実体パスへ解決する。解決できない場合は入力をそのまま返す。"""
    try:
        return path.resolve()
    except OSError:
        return path
