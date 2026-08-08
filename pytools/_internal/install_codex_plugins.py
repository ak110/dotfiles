"""dotfiles同梱のCodex pluginを自動導入・更新する。"""

import json
import logging
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pytools._internal import claude_common, log_format, post_apply_outcome

logger = logging.getLogger(__name__)
CODEX_HOME = Path.home() / ".codex"
_TIMEOUT = 60.0
_CODEX_PLUGIN_RESTART_NOTICE = post_apply_outcome.PostApplyNotice(
    message=(
        "Codex pluginを更新しました。実行中のCodexセッションを終了してから、"
        "利用中のCodexアプリケーション又はCLIを再起動してください。"
    ),
)

# dotfiles自身以外のマーケットプレイスから導入するプラグイン。
# (マーケットプレイス名, 登録ソース, プラグイン識別子) の組で保持する。
_EXTERNAL_PLUGINS: tuple[tuple[str, str, str], ...] = (("compact-plus", "u-ichi/compact-plus", "compact-plus@compact-plus"),)


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


def _marketplace_entry(data: dict[str, Any], marketplace_name: str) -> dict[str, Any] | None:
    return next((item for item in data.get("marketplaces", []) if item.get("name") == marketplace_name), None)


def _marketplace_source_matches(entry: dict[str, Any], expected_source: str) -> bool:
    source_data = entry.get("marketplaceSource")
    if not isinstance(source_data, dict) or source_data.get("sourceType") != "git":
        return False
    source = source_data.get("source")
    expected = _canonical_github_https_source(expected_source, allow_shorthand=True)
    actual = _canonical_github_https_source(source, allow_shorthand=False) if isinstance(source, str) else None
    return expected is not None and actual == expected


def _canonical_github_https_source(source: str, *, allow_shorthand: bool) -> str | None:
    """安全なGitHub HTTPS取得元を正規URLへ変換する。"""
    value = source.strip().rstrip("/")
    if "://" in value:
        parsed = urlparse(value)
        try:
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.params
            or parsed.query
            or parsed.fragment
            or not parsed.path.endswith(".git")
        ):
            return None
        repo = parsed.path.removeprefix("/").removesuffix(".git")
    elif allow_shorthand:
        repo = value.removesuffix(".git")
    else:
        return None
    parts = repo.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    owner, name = (part.lower() for part in parts)
    return f"https://github.com/{owner}/{name}.git"


def _outcome(
    changed: bool,
    notices: list[post_apply_outcome.PostApplyNotice] | tuple[post_apply_outcome.PostApplyNotice, ...],
) -> post_apply_outcome.PostApplyOutcome:
    """案内を初出順で重複排除した結果を返す。"""
    return post_apply_outcome.PostApplyOutcome(changed=changed, notices=tuple(dict.fromkeys(notices)))


def _install_external_plugins() -> post_apply_outcome.PostApplyOutcome:
    """外部マーケットプレイスを登録し、未導入のプラグインを導入する。"""
    changed = False
    notices: list[post_apply_outcome.PostApplyNotice] = []
    for marketplace_name, source, plugin_id in _EXTERNAL_PLUGINS:
        marketplace_data = _codex_json(["plugin", "marketplace", "list", "--json"])
        if marketplace_data is None:
            logger.warning(log_format.format_status(plugin_id, "marketplace一覧の取得に失敗したためスキップ"))
            continue
        marketplace = _marketplace_entry(marketplace_data, marketplace_name)
        if marketplace is None:
            if not _command(["plugin", "marketplace", "add", source]):
                logger.warning(log_format.format_status(plugin_id, "marketplace登録に失敗したためスキップ"))
                continue
            changed = True
            marketplace_data = _codex_json(["plugin", "marketplace", "list", "--json"])
            marketplace = _marketplace_entry(marketplace_data, marketplace_name) if marketplace_data is not None else None
        if marketplace is None or not _marketplace_source_matches(marketplace, source):
            logger.error(log_format.format_status(plugin_id, f"marketplace取得元が一致しないためスキップ: 期待値 {source}"))
            continue

        installed_data = _codex_json(["plugin", "list", "--json"])
        if installed_data is None:
            logger.warning(log_format.format_status(plugin_id, "plugin一覧の取得に失敗したためスキップ"))
            continue
        if _installed(installed_data, plugin_id) is not None:
            continue
        if not _command(["plugin", "add", plugin_id]):
            logger.warning(log_format.format_status(plugin_id, "plugin導入に失敗したため続行"))
            continue
        changed = True
        notices.append(_CODEX_PLUGIN_RESTART_NOTICE)
    return _outcome(changed, notices)


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
            target = path.resolve(strict=False)
            target.relative_to(source_root)
        except (OSError, ValueError):
            continue
        _unlink(path)
        changed = True
    return changed


def run() -> post_apply_outcome.PostApplyOutcome:
    """marketplaceを登録してagent-toolkitを導入・更新する。"""
    if shutil.which("codex") is None:
        logger.info(log_format.format_status("codex plugins", "codex CLIが見つからずスキップ"))
        return _outcome(False, [])
    external_outcome = _install_external_plugins()
    changed = external_outcome.changed
    notices = list(external_outcome.notices)
    root = claude_common.find_dotfiles_root()
    if root is None:
        return _outcome(changed, notices)
    target = _target(root)
    if target is None:
        logger.warning(log_format.format_status("codex plugins", "Codex plugin manifestが不正なためスキップ"))
        return _outcome(changed, notices)
    marketplace_name, plugin_name, version = target
    marketplace_data = _codex_json(["plugin", "marketplace", "list", "--json"])
    if marketplace_data is None:
        return _outcome(changed, notices)
    registered_root = _marketplace_root(marketplace_data, marketplace_name)
    if registered_root is None:
        if not _command(["plugin", "marketplace", "add", str(root)]):
            return _outcome(changed, notices)
        changed = True
    elif registered_root != root.resolve():
        logger.error(log_format.format_status("codex plugins", f"marketplace登録先が異なる: {registered_root}"))
        return _outcome(changed, notices)

    plugin_id = f"{plugin_name}@{marketplace_name}"
    before = _codex_json(["plugin", "list", "--json"])
    current = _installed(before, plugin_id) if before else None
    if current is not None and current.get("version") == version and current.get("enabled") is True:
        legacy_changed = _remove_legacy_links(root)
        return _outcome(changed or legacy_changed, notices)
    if not _command(["plugin", "add", plugin_id]):
        return _outcome(changed, notices)
    changed = True
    notices.append(_CODEX_PLUGIN_RESTART_NOTICE)
    after = _codex_json(["plugin", "list", "--json"])
    installed = _installed(after, plugin_id) if after else None
    if installed is None or installed.get("version") != version or installed.get("enabled") is not True:
        return _outcome(changed, notices)
    legacy_changed = _remove_legacy_links(root)
    return _outcome(changed or legacy_changed, notices)
