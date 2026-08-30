"""dotfiles同梱のCodex pluginを自動導入・更新する。"""

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _LegacyLinkSnapshot:
    """legacy skillリンクの変更前状態を表す。"""

    path: Path
    kind: str
    target: Path


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
        if not isinstance(marketplace, dict) or not isinstance(plugin, dict):
            return None
        marketplace_name = marketplace["name"]
        plugin_name = plugin["name"]
        version = plugin["version"]
        entries = marketplace["plugins"]
        if not isinstance(entries, list):
            return None
        entry = next(
            (item for item in entries if isinstance(item, dict) and item.get("name") == plugin_name),
            None,
        )
        if not all(isinstance(value, str) for value in (marketplace_name, plugin_name, version)):
            return None
        if not isinstance(entry, dict) or entry.get("name") != plugin_name or not _valid_version_name(version):
            return None
        return marketplace_name, plugin_name, version
    except (OSError, json.JSONDecodeError, KeyError, IndexError, StopIteration, TypeError):
        return None


def _plugin_source(root: Path, marketplace_name: str, plugin_name: str) -> Path | None:
    """marketplaceのlocal sourceからagent-toolkit原本を解決する。"""
    try:
        marketplace = json.loads((root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        if not isinstance(marketplace, dict):
            return None
        if marketplace.get("name") != marketplace_name:
            return None
        entries = marketplace.get("plugins")
        if not isinstance(entries, list):
            return None
        entry = next(
            (item for item in entries if isinstance(item, dict) and item.get("name") == plugin_name),
            None,
        )
        if not isinstance(entry, dict):
            return None
        source = entry.get("source")
        if not isinstance(source, dict):
            return None
        if source.get("source") != "local" or not isinstance(source.get("path"), str):
            return None
        candidate = (root / source["path"]).resolve()
        candidate.relative_to(root.resolve())
        return candidate
    except (OSError, json.JSONDecodeError, KeyError, IndexError, StopIteration, TypeError, ValueError):
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


def _valid_version_name(value: str) -> bool:
    return value not in {"", ".", ".."} and _VERSION_PATTERN.fullmatch(value) is not None


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink() or setup_codex_links._is_link_like(path)  # pylint: disable=protected-access


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


def _legacy_link_snapshots(root: Path) -> tuple[_LegacyLinkSnapshot, ...]:
    skills = _codex_home() / "skills"
    if not _path_exists(skills):
        return ()
    if _is_link(skills) or not skills.is_dir():
        raise NotADirectoryError(f"Codex skills rootが通常ディレクトリではない: {skills}")
    source_root = (root / "agent-toolkit/skills").resolve()
    snapshots: list[_LegacyLinkSnapshot] = []
    for path in sorted(skills.iterdir(), key=lambda entry: entry.name):
        if not _is_link(path):
            continue
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(source_root)
        except (OSError, ValueError):
            continue
        if path.is_symlink():
            target = path.readlink()
            kind = "symlink"
        else:
            target = resolved
            kind = "junction"
        snapshots.append(_LegacyLinkSnapshot(path, kind, target))
    return tuple(snapshots)


def _restore_legacy_links(snapshots: tuple[_LegacyLinkSnapshot, ...]) -> None:
    errors: list[str] = []
    for snapshot in snapshots:
        path = snapshot.path
        try:
            if _path_exists(path):
                if path.is_symlink():
                    path.unlink()
                else:
                    path.rmdir()
            path.parent.mkdir(parents=True, exist_ok=True)
            if snapshot.kind == "symlink":
                path.symlink_to(snapshot.target, target_is_directory=True)
            else:
                setup_codex_links._create_link(path, snapshot.target)  # pylint: disable=protected-access
        except BaseException as error:  # noqa: BLE001 - 他のlegacy linkの復元を継続する
            errors.append(f"{path}: {error}")
    if errors:
        raise OSError(" / ".join(errors))


def _append_exception_message(error: BaseException, message: str) -> None:
    current = str(error)
    error.args = (f"{current}\n{message}" if current else message,)


def _verify_expected_state(plugin_id: str, version: str) -> None:
    after = _codex_json(["plugin", "list", "--json"])
    after_known, installed = _installed(after, plugin_id) if after is not None else (False, None)
    if not after_known or installed is None or installed.get("version") != version or installed.get("enabled") is not True:
        raise RuntimeError("Codex plugin更新後の状態が期待値と一致しない")


def _sync_local_plugin(
    root: Path,
    marketplace_name: str,
    plugin_name: str,
    version: str,
    current: dict[str, Any] | None,
    notices: list[post_apply_outcome.PostApplyNotice],
) -> bool:
    legacy_snapshots = _legacy_link_snapshots(root)
    plugin_id = f"{plugin_name}@{marketplace_name}"
    needs_plugin_add = current is None or current.get("enabled") is not True or current.get("version") != version
    if not needs_plugin_add and not legacy_snapshots:
        return False
    legacy_removal_started = False
    try:
        if needs_plugin_add:
            if not _command(["plugin", "add", plugin_id]):
                raise RuntimeError("Codex plugin addに失敗")
            _append_restart_notice_if_daemon_running(notices)
            _verify_expected_state(plugin_id, version)
        if legacy_snapshots:
            legacy_removal_started = True
            _remove_legacy_links(root)
    except BaseException as error:
        if legacy_removal_started:
            try:
                _restore_legacy_links(legacy_snapshots)
            except BaseException as restore_error:  # noqa: BLE001 - 元の失敗を保持する
                _append_exception_message(error, f"legacy skillリンク復元失敗: {restore_error}")
        raise
    return True


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


def _remove_legacy_links(root: Path) -> bool:
    changed = False
    skills = _codex_home() / "skills"
    if not _path_exists(skills):
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


def _append_notices_to_exception(error: Exception, notices: list[post_apply_outcome.PostApplyNotice]) -> None:
    unique_notices = tuple(dict.fromkeys(notices))
    if not unique_notices:
        return
    rendered = []
    for notice in unique_notices:
        rendered.append(notice.message)
        if notice.command is not None:
            rendered.append(notice.command)
    _append_exception_message(error, "post-apply案内:\n" + "\n".join(rendered))


def run() -> post_apply_outcome.PostApplyOutcome:
    """marketplaceを登録してagent-toolkitを導入・更新する。"""
    if shutil.which("codex") is None:
        logger.info(log_format.format_status("codex plugins", "codex CLIが見つからずスキップ"))
        return _outcome(False, [])

    notices: list[post_apply_outcome.PostApplyNotice] = []
    try:
        external_outcome = _install_external_plugins()
        changed = external_outcome.changed
        notices.extend(external_outcome.notices)
        root = claude_common.find_dotfiles_root()
        if root is None:
            return _outcome(changed, notices)
        target = _target(root)
        if target is None:
            logger.warning(log_format.format_status("codex plugins", "Codex plugin manifestが不正なためスキップ"))
            return _outcome(changed, notices)
        marketplace_name, plugin_name, version = target
        if _plugin_source(root, marketplace_name, plugin_name) is None:
            logger.warning(log_format.format_status("codex plugins", "Codex pluginのlocal sourceが不正なためスキップ"))
            return _outcome(changed, notices)
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
        local_changed = _sync_local_plugin(root, marketplace_name, plugin_name, version, current, notices)
        return _outcome(changed or local_changed, notices)
    except Exception as error:
        _append_notices_to_exception(error, notices)
        raise
