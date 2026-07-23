"""dotfiles同梱のCodex pluginを自動導入・更新する。"""

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)
CODEX_HOME = Path.home() / ".codex"
_TIMEOUT = 60.0


def _codex_json(args: list[str]) -> dict[str, Any] | None:
    result = claude_common.run_subprocess(["codex", *args], timeout=_TIMEOUT, tag="codex")
    if result is None or result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _command(args: list[str]) -> bool:
    result = claude_common.run_subprocess(["codex", *args], timeout=_TIMEOUT, tag="codex")
    return result is not None and result.returncode == 0


def _target(root: Path) -> tuple[str, str, str] | None:
    try:
        marketplace = json.loads((root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        plugin = json.loads((root / "agent-toolkit/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
        entry = marketplace["plugins"][0]
        return marketplace["name"], entry["name"], plugin["version"]
    except (OSError, json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None


def _marketplace_root(data: dict[str, Any], name: str) -> Path | None:
    for item in data.get("marketplaces", []):
        if item.get("name") == name and isinstance(item.get("root"), str):
            return Path(item["root"]).resolve()
    return None


def _installed(data: dict[str, Any], plugin_id: str) -> dict[str, Any] | None:
    return next((item for item in data.get("installed", []) if item.get("pluginId") == plugin_id), None)


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _unlink(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    else:
        path.rmdir()


def _remove_legacy_links(root: Path) -> bool:
    changed = False
    skills = CODEX_HOME / "skills"
    if not skills.exists():
        return False
    source_root = (root / "agent-toolkit/skills").resolve()
    for path in skills.iterdir():
        if not _is_link(path):
            continue
        try:
            target = path.resolve(strict=True)
            target.relative_to(source_root)
        except (OSError, ValueError):
            continue
        _unlink(path)
        changed = True
    return changed


def run() -> bool:
    """marketplaceを登録してagent-toolkitを導入・更新する。"""
    if shutil.which("codex") is None:
        logger.info(log_format.format_status("codex plugins", "codex CLIが見つからずスキップ"))
        return False
    root = claude_common.find_dotfiles_root()
    if root is None:
        return False
    target = _target(root)
    if target is None:
        logger.warning(log_format.format_status("codex plugins", "Codex plugin manifestが不正なためスキップ"))
        return False
    marketplace_name, plugin_name, version = target
    marketplace_data = _codex_json(["plugin", "marketplace", "list", "--json"])
    if marketplace_data is None:
        return False
    registered_root = _marketplace_root(marketplace_data, marketplace_name)
    changed = False
    if registered_root is None:
        if not _command(["plugin", "marketplace", "add", str(root)]):
            return False
        changed = True
    elif registered_root != root.resolve():
        logger.error(log_format.format_status("codex plugins", f"marketplace登録先が異なる: {registered_root}"))
        return False

    plugin_id = f"{plugin_name}@{marketplace_name}"
    before = _codex_json(["plugin", "list", "--json"])
    current = _installed(before, plugin_id) if before else None
    if current is None or current.get("version") != version or current.get("enabled") is not True:
        changed = True
    if not _command(["plugin", "add", plugin_id]):
        return False
    after = _codex_json(["plugin", "list", "--json"])
    installed = _installed(after, plugin_id) if after else None
    if installed is None or installed.get("version") != version or installed.get("enabled") is not True:
        return False
    return _remove_legacy_links(root) or changed
