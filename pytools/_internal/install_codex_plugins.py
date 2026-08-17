"""dotfiles同梱のCodex pluginを自動導入・更新する。"""

import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pytools._internal import claude_common, log_format, post_apply_outcome, setup_codex_links

logger = logging.getLogger(__name__)
CODEX_HOME = Path.home() / ".codex"
_TIMEOUT = 60.0
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9._+-]+\Z")
_CODEX_PLUGIN_RESTART_NOTICE = post_apply_outcome.PostApplyNotice(
    message=(
        "Codex pluginを更新しました。実行中のCodexセッションを終了してから、"
        "次のコマンドでapp-server daemonを再起動してください。"
    ),
    command="codex app-server daemon restart",
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


def _installed(data: dict[str, Any], plugin_id: str) -> tuple[bool, dict[str, Any] | None]:
    installed = data.get("installed")
    if not isinstance(installed, list):
        return False, None
    plugin = None
    for item in installed:
        if not isinstance(item, dict) or not isinstance(item.get("pluginId"), str):
            return False, None
        if item["pluginId"] != plugin_id:
            continue
        if not isinstance(item.get("version"), str) or not isinstance(item.get("enabled"), bool):
            return False, None
        if plugin is None:
            plugin = item
    return True, plugin


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


def _append_restart_notice_if_daemon_running(notices: list[post_apply_outcome.PostApplyNotice]) -> None:
    """稼働中のCodex daemonがある場合だけ再起動案内を追加する。"""
    if _command(["app-server", "daemon", "version"]):
        notices.append(_CODEX_PLUGIN_RESTART_NOTICE)


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", CODEX_HOME))


def _cache_root(marketplace_name: str, plugin_name: str) -> Path:
    return _codex_home() / "plugins" / "cache" / marketplace_name / plugin_name


def _versions_path(marketplace_name: str, plugin_name: str) -> Path:
    return _codex_home() / "plugins" / "cache-compat" / marketplace_name / plugin_name / "versions"


def _valid_version_name(value: str) -> bool:
    return value not in {"", ".", ".."} and _VERSION_PATTERN.fullmatch(value) is not None


def _read_versions(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return set()
    versions: set[str] = set()
    for value in lines:
        if _valid_version_name(value):
            versions.add(value)
        else:
            logger.warning(log_format.format_status("codex plugins", f"不正な互換version名を無視: {value!r}"))
    return versions


def _cache_versions(cache_root: Path) -> set[str]:
    try:
        entries = tuple(cache_root.iterdir())
    except FileNotFoundError:
        return set()
    versions: set[str] = set()
    for entry in entries:
        if not _valid_version_name(entry.name):
            logger.warning(log_format.format_status("codex plugins", f"不正なcache version名を無視: {entry.name!r}"))
            continue
        if entry.is_dir() or setup_codex_links._is_link_like(entry):  # pylint: disable=protected-access
            versions.add(entry.name)
    return versions


def _write_versions(path: Path, versions: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{version}\n" for version in sorted(versions))
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary_path = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _save_cache_versions(marketplace_name: str, plugin_name: str) -> set[str]:
    cache_root = _cache_root(marketplace_name, plugin_name)
    versions_path = _versions_path(marketplace_name, plugin_name)
    versions = _read_versions(versions_path) | _cache_versions(cache_root)
    _write_versions(versions_path, versions)
    return versions


def _restore_cache_links(marketplace_name: str, plugin_name: str, current_version: str) -> bool:
    versions_path = _versions_path(marketplace_name, plugin_name)
    if not versions_path.exists():
        return False
    versions = _read_versions(versions_path) - {current_version}
    if not versions:
        return False
    cache_root = _cache_root(marketplace_name, plugin_name)
    target = cache_root / current_version
    _require_cache_target(target)
    changed = False
    for version in sorted(versions):
        try:
            changed = setup_codex_links.sync_directory_link(cache_root / version, target) or changed
        except FileExistsError as error:
            logger.error(log_format.format_status("codex plugins", f"通常エントリと互換バージョン名が競合: {error.args[0]}"))
            raise
    return changed


def _require_cache_target(target: Path) -> None:
    if not target.is_dir() or setup_codex_links._is_link_like(target):  # pylint: disable=protected-access
        raise FileNotFoundError(f"現行plugin cache実体が存在しない: {target}")


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
        state_known, installed = _installed(installed_data, plugin_id)
        if not state_known:
            logger.warning(log_format.format_status(plugin_id, "plugin一覧の構造が不正なためスキップ"))
            continue
        if installed is not None:
            continue
        if not _command(["plugin", "add", plugin_id]):
            logger.warning(log_format.format_status(plugin_id, "plugin導入に失敗したため続行"))
            continue
        changed = True
        _append_restart_notice_if_daemon_running(notices)
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
    skills = _codex_home() / "skills"
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
    if before is None:
        logger.error(log_format.format_status("codex plugins", "更新前のplugin状態を取得できないため中止"))
        return _outcome(changed, notices)
    state_known, current = _installed(before, plugin_id)
    if not state_known:
        logger.error(log_format.format_status("codex plugins", "更新前のplugin状態を取得できないため中止"))
        return _outcome(changed, notices)
    if current is not None and current.get("version") == version and current.get("enabled") is True:
        cache_changed = _restore_cache_links(marketplace_name, plugin_name, version)
        legacy_changed = _remove_legacy_links(root)
        return _outcome(changed or cache_changed or legacy_changed, notices)
    if current is not None:
        _save_cache_versions(marketplace_name, plugin_name)
    if not _command(["plugin", "add", plugin_id]):
        return _outcome(changed, notices)
    changed = True
    _append_restart_notice_if_daemon_running(notices)
    after = _codex_json(["plugin", "list", "--json"])
    after_known, installed = _installed(after, plugin_id) if after is not None else (False, None)
    if not after_known or installed is None or installed.get("version") != version or installed.get("enabled") is not True:
        raise RuntimeError("Codex plugin更新後の状態が期待値と一致しない")
    if current is not None:
        _require_cache_target(_cache_root(marketplace_name, plugin_name) / version)
    cache_changed = _restore_cache_links(marketplace_name, plugin_name, version)
    legacy_changed = _remove_legacy_links(root)
    return _outcome(changed or cache_changed or legacy_changed, notices)
