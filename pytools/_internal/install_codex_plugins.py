"""dotfiles同梱のCodex pluginを自動導入・更新する。"""

import json
import logging
import os
import re
import shutil
import sys
import tempfile
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
class _CacheSnapshot:
    """cache rootの変更前状態を表す。"""

    root_present: bool
    entries: tuple[Path, ...]


@dataclass(frozen=True)
class _FileSnapshot:
    """通常ファイルの存在とbytesを表す。"""

    present: bool
    content: bytes | None


@dataclass(frozen=True)
class _LegacyLinkSnapshot:
    """legacy skillリンクの変更前状態を表す。"""

    path: Path
    kind: str
    target: Path
    target_is_directory: bool


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


def _cache_root(marketplace_name: str, plugin_name: str) -> Path:
    return _codex_home() / "plugins" / "cache" / marketplace_name / plugin_name


def _versions_path(marketplace_name: str, plugin_name: str) -> Path:
    return _codex_home() / "plugins" / "cache-compat" / marketplace_name / plugin_name / "versions"


def _valid_version_name(value: str) -> bool:
    return value not in {"", ".", ".."} and _VERSION_PATTERN.fullmatch(value) is not None


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink() or setup_codex_links._is_link_like(path)  # pylint: disable=protected-access


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


def _cache_entries(cache_root: Path) -> tuple[Path, ...]:
    if not _path_exists(cache_root):
        return ()
    if setup_codex_links._is_link_like(cache_root) or not cache_root.is_dir():  # pylint: disable=protected-access
        raise NotADirectoryError(f"plugin cache rootが通常ディレクトリではない: {cache_root}")
    return tuple(sorted(cache_root.iterdir(), key=lambda entry: entry.name))


def _cache_versions(cache_root: Path) -> set[str]:
    versions: set[str] = set()
    for entry in _cache_entries(cache_root):
        if not _valid_version_name(entry.name):
            logger.warning(log_format.format_status("codex plugins", f"不正なcache version名を無視: {entry.name!r}"))
            continue
        if entry.is_dir() or setup_codex_links._is_link_like(entry):  # pylint: disable=protected-access
            versions.add(entry.name)
    return versions


def _write_versions(path: Path, versions: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"{version}\n" for version in sorted(versions))
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _ledger_snapshot(path: Path) -> _FileSnapshot:
    if not _path_exists(path):
        return _FileSnapshot(False, None)
    if setup_codex_links._is_link_like(path) or not path.is_file():  # pylint: disable=protected-access
        raise OSError(f"version台帳が通常ファイルではない: {path}")
    return _FileSnapshot(True, path.read_bytes())


def _ledger_needs_write(versions: set[str], snapshot: _FileSnapshot) -> bool:
    expected = "".join(f"{version}\n" for version in sorted(versions)).encode()
    return not snapshot.present or snapshot.content != expected


def _restore_ledger(path: Path, snapshot: _FileSnapshot) -> None:
    if snapshot.present:
        assert snapshot.content is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(snapshot.content)
    elif _path_exists(path):
        _remove_entry(path)


def _source_entries(source: Path, plugin_name: str) -> tuple[Path, ...]:
    if setup_codex_links._is_link_like(source) or not source.is_dir():  # pylint: disable=protected-access
        raise FileNotFoundError(f"agent-toolkit原本が通常ディレクトリではない: {source}")
    manifest_path = source / ".codex-plugin/plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FileNotFoundError(f"agent-toolkit原本のplugin manifestを読み取れない: {manifest_path}") from error
    if not isinstance(manifest, dict) or manifest.get("name") != plugin_name:
        raise ValueError(f"agent-toolkit原本のplugin名が一致しない: {manifest_path}")
    entries = tuple(sorted(source.iterdir(), key=lambda item: item.name))
    for entry in entries:
        if setup_codex_links._is_link_like(entry) or not (entry.is_dir() or entry.is_file()):  # pylint: disable=protected-access
            raise OSError(f"agent-toolkit原本の直下資源が通常ファイル／通常ディレクトリではない: {entry}")
    return entries


def _has_plugin_structure(version_path: Path, plugin_name: str) -> bool:
    manifest_path = version_path / ".codex-plugin/plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(manifest, dict) and manifest.get("name") == plugin_name


def _validate_existing_versions(cache_root: Path, versions: set[str], plugin_name: str) -> None:
    for version in sorted(versions):
        path = cache_root / version
        if not _path_exists(path):
            continue
        if setup_codex_links._is_link_like(path):  # pylint: disable=protected-access
            continue
        if not path.is_dir():
            raise FileExistsError(f"通常versionエントリを置換しない: {path}")
        if not _has_plugin_structure(path, plugin_name):
            raise FileExistsError(f"plugin構造を確認できない通常versionエントリを置換しない: {path}")


def _version_is_source_connected(version_path: Path, source_entries: tuple[Path, ...], plugin_name: str) -> bool:
    if not _path_exists(version_path) or setup_codex_links._is_link_like(version_path):  # pylint: disable=protected-access
        return False
    if not version_path.is_dir() or not _has_plugin_structure(version_path, plugin_name):
        return False
    try:
        actual_names = {entry.name for entry in version_path.iterdir()}
    except OSError:
        return False
    expected_names = {entry.name for entry in source_entries}
    if actual_names != expected_names:
        return False
    for source_entry in source_entries:
        destination = version_path / source_entry.name
        try:
            if sys.platform == "win32" and source_entry.is_file():
                if not destination.is_file() or setup_codex_links._is_link_like(destination):  # pylint: disable=protected-access
                    return False
                if destination.read_bytes() != source_entry.read_bytes():
                    return False
                continue
            if not destination.is_symlink() and not setup_codex_links._is_link_like(destination):  # pylint: disable=protected-access
                return False
            if sys.platform != "win32" and destination.readlink().is_absolute():
                return False
            if destination.resolve(strict=False) != source_entry.resolve():
                return False
        except (OSError, RuntimeError, ValueError):
            return False
    return True


def _prepare_source_versions(
    source_entries: tuple[Path, ...],
    versions: set[str],
    stage_root: Path,
    cache_root: Path,
) -> Path:
    prepared_root = stage_root / "versions"
    prepared_root.mkdir(parents=True, exist_ok=True)
    for version in sorted(versions):
        prepared_version = prepared_root / version
        prepared_version.mkdir()
        final_version = cache_root / version
        for source_entry in source_entries:
            destination = prepared_version / source_entry.name
            if sys.platform == "win32" and source_entry.is_dir():
                setup_codex_links.sync_directory_link(destination, source_entry)
            elif sys.platform == "win32":
                shutil.copy2(source_entry, destination)
            else:
                relative_target = Path(os.path.relpath(source_entry, start=final_version.resolve()))
                destination.symlink_to(relative_target, target_is_directory=source_entry.is_dir())
    return prepared_root


def _remove_entry(path: Path) -> None:
    if _is_link(path):
        _unlink(path)
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _clear_cache_root(cache_root: Path) -> None:
    if not _path_exists(cache_root):
        return
    if setup_codex_links._is_link_like(cache_root) or not cache_root.is_dir():  # pylint: disable=protected-access
        _remove_entry(cache_root)
        return
    for entry in tuple(cache_root.iterdir()):
        _remove_entry(entry)


def _move_cache_to_backup(cache_root: Path, backup_root: Path) -> None:
    entries = _cache_entries(cache_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    try:
        for entry in entries:
            destination = backup_root / entry.name
            entry.rename(destination)
            moved.append(destination)
    except BaseException as error:
        restore_errors: list[str] = []
        try:
            cache_root.mkdir(parents=True, exist_ok=True)
        except BaseException as restore_error:  # noqa: BLE001 - 各entryの復元を継続する
            restore_errors.append(f"cache root再作成: {restore_error}")
        for backup_entry in reversed(moved):
            try:
                backup_entry.rename(cache_root / backup_entry.name)
            except BaseException as restore_error:  # noqa: BLE001 - 他entryの復元を継続する
                restore_errors.append(f"{backup_entry.name}: {restore_error}")
        if restore_errors:
            _append_exception_message(error, "cache退避巻き戻し失敗: " + " / ".join(restore_errors))
        raise


def _install_prepared_versions(prepared_root: Path, cache_root: Path) -> None:
    _clear_cache_root(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    for prepared_version in sorted(prepared_root.iterdir(), key=lambda item: item.name):
        prepared_version.rename(cache_root / prepared_version.name)


def _restore_unmanaged_cache_entries(backup_root: Path, cache_root: Path, versions: set[str]) -> None:
    if not backup_root.exists():
        return
    cache_root.mkdir(parents=True, exist_ok=True)
    for entry in sorted(backup_root.iterdir(), key=lambda item: item.name):
        if entry.name in versions:
            continue
        destination = cache_root / entry.name
        if _path_exists(destination):
            raise FileExistsError(f"cache復元先が競合する: {destination}")
        entry.rename(destination)


def _restore_cache_snapshot(cache_root: Path, snapshot: _CacheSnapshot, backup_root: Path) -> None:
    errors: list[str] = []
    if not snapshot.root_present:
        try:
            current_entries = _cache_entries(cache_root)
        except BaseException as error:  # noqa: BLE001 - cache root自体の除去も試みる
            errors.append(f"現行cache列挙: {error}")
            current_entries = ()
        for entry in current_entries:
            try:
                _remove_entry(entry)
            except BaseException as error:  # noqa: BLE001 - 他entryの除去を継続する
                errors.append(f"entry除去 {entry.name}: {error}")
        try:
            if _path_exists(cache_root):
                _remove_entry(cache_root)
        except BaseException as error:  # noqa: BLE001 - 他の管理状態の復元を継続する
            errors.append(f"cache root除去: {error}")
    else:
        try:
            cache_root.mkdir(parents=True, exist_ok=True)
        except BaseException as error:  # noqa: BLE001 - 各entryの復元を継続する
            errors.append(f"cache root再作成: {error}")
        snapshot_names = {entry.name for entry in snapshot.entries}
        try:
            current_entries = _cache_entries(cache_root)
        except BaseException as error:  # noqa: BLE001 - 退避済みentryの復元を試みる
            errors.append(f"現行cache列挙: {error}")
            current_entries = ()
        for entry in current_entries:
            if entry.name in snapshot_names:
                continue
            try:
                _remove_entry(entry)
            except BaseException as error:  # noqa: BLE001 - 他entryの復元を継続する
                errors.append(f"追加entry除去 {entry.name}: {error}")
        for snapshot_entry in snapshot.entries:
            backup_entry = backup_root / snapshot_entry.name
            destination = cache_root / snapshot_entry.name
            try:
                if _path_exists(backup_entry):
                    if _path_exists(destination):
                        _remove_entry(destination)
                    backup_entry.rename(destination)
                elif not _path_exists(destination):
                    errors.append(f"entry復元 {snapshot_entry.name}: 退避元と復元先の両方に存在しない")
            except BaseException as error:  # noqa: BLE001 - 他entryの復元を継続する
                errors.append(f"entry復元 {snapshot_entry.name}: {error}")
    if errors:
        raise OSError(" / ".join(errors))


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
        snapshots.append(_LegacyLinkSnapshot(path, kind, target, True))
    return tuple(snapshots)


def _restore_legacy_links(snapshots: tuple[_LegacyLinkSnapshot, ...]) -> None:
    errors: list[str] = []
    for snapshot in snapshots:
        try:
            path = snapshot.path
            if _path_exists(path):
                _remove_entry(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if snapshot.kind == "symlink":
                path.symlink_to(snapshot.target, target_is_directory=snapshot.target_is_directory)
            else:
                setup_codex_links._create_link(path, snapshot.target)  # pylint: disable=protected-access
        except BaseException as error:  # noqa: BLE001 - 他のlegacy linkの復元を継続する
            errors.append(f"{snapshot.path}: {error}")
    if errors:
        raise OSError(" / ".join(errors))


def _append_exception_message(error: BaseException, message: str) -> None:
    current = str(error)
    error.args = (f"{current}\n{message}" if current else message,)


def _restore_transaction(
    *,
    cache_root: Path,
    cache_snapshot: _CacheSnapshot,
    backup_root: Path | None,
    cache_backup_taken: bool,
    versions_path: Path,
    ledger_snapshot: _FileSnapshot,
    legacy_snapshots: tuple[_LegacyLinkSnapshot, ...],
) -> list[str]:
    errors: list[str] = []
    if cache_backup_taken and backup_root is not None:
        try:
            _restore_cache_snapshot(cache_root, cache_snapshot, backup_root)
        except BaseException as error:  # noqa: BLE001 - 変更境界の復元を継続する
            errors.append(f"cache復元: {error}")
    try:
        _restore_ledger(versions_path, ledger_snapshot)
    except BaseException as error:  # noqa: BLE001 - 他の管理状態の復元を継続する
        errors.append(f"version台帳復元: {error}")
    try:
        _restore_legacy_links(legacy_snapshots)
    except BaseException as error:  # noqa: BLE001 - 他の管理状態の復元を継続する
        errors.append(f"legacy skillリンク復元: {error}")
    return errors


def _verify_expected_state(plugin_id: str, version: str) -> None:
    after = _codex_json(["plugin", "list", "--json"])
    after_known, installed = _installed(after, plugin_id) if after is not None else (False, None)
    if not after_known or installed is None or installed.get("version") != version or installed.get("enabled") is not True:
        raise RuntimeError("Codex plugin更新後の状態が期待値と一致しない")


def _sync_local_plugin(
    root: Path,
    source: Path,
    marketplace_name: str,
    plugin_name: str,
    version: str,
    current: dict[str, Any] | None,
    notices: list[post_apply_outcome.PostApplyNotice],
) -> bool:
    cache_root = _cache_root(marketplace_name, plugin_name)
    versions_path = _versions_path(marketplace_name, plugin_name)
    ledger_snapshot = _ledger_snapshot(versions_path)
    legacy_snapshots = _legacy_link_snapshots(root)
    source_entries = _source_entries(source, plugin_name)
    ledger_versions = _read_versions(versions_path)
    cache_versions = _cache_versions(cache_root)
    versions = ledger_versions | cache_versions | {version}
    _validate_existing_versions(cache_root, versions, plugin_name)

    cache_needs_change = any(
        not _version_is_source_connected(cache_root / item, source_entries, plugin_name) for item in versions
    )
    ledger_needs_change = _ledger_needs_write(versions, ledger_snapshot)
    legacy_needs_change = bool(legacy_snapshots)
    needs_plugin_add = current is None or current.get("enabled") is not True
    version_changed = current is not None and current.get("version") != version
    if not (cache_needs_change or ledger_needs_change or legacy_needs_change or needs_plugin_add or version_changed):
        return False

    cache_snapshot = _CacheSnapshot(_path_exists(cache_root), _cache_entries(cache_root))
    backup_root: Path | None = None
    cache_backup_taken = False
    stage_root: Path | None = None
    preserve_stage_root = False
    transaction_error: BaseException | None = None
    try:
        prepared_root: Path | None = None
        if cache_needs_change or needs_plugin_add:
            transaction_parent = _codex_home() / "plugins"
            transaction_parent.mkdir(parents=True, exist_ok=True)
            stage_root = Path(tempfile.mkdtemp(prefix=".agent-toolkit-", dir=transaction_parent))
            prepared_root = _prepare_source_versions(source_entries, versions, stage_root, cache_root)
            backup_root = stage_root / "cache-backup"
            cache_backup_taken = True
            _move_cache_to_backup(cache_root, backup_root)

        if needs_plugin_add:
            if not _command(["plugin", "add", f"{plugin_name}@{marketplace_name}"]):
                raise RuntimeError("Codex plugin addに失敗")
            _append_restart_notice_if_daemon_running(notices)

        if prepared_root is not None:
            _install_prepared_versions(prepared_root, cache_root)

        if needs_plugin_add or cache_needs_change or version_changed:
            _verify_expected_state(f"{plugin_name}@{marketplace_name}", version)
            if version_changed:
                _append_restart_notice_if_daemon_running(notices)

        if ledger_needs_change:
            _write_versions(versions_path, versions)
        if legacy_needs_change:
            _remove_legacy_links(root)
        if backup_root is not None:
            _restore_unmanaged_cache_entries(backup_root, cache_root, versions)
        return True
    except BaseException as error:
        transaction_error = error
        restore_errors = _restore_transaction(
            cache_root=cache_root,
            cache_snapshot=cache_snapshot,
            backup_root=backup_root,
            cache_backup_taken=cache_backup_taken,
            versions_path=versions_path,
            ledger_snapshot=ledger_snapshot,
            legacy_snapshots=legacy_snapshots,
        )
        if restore_errors:
            _append_exception_message(error, "復元失敗: " + " / ".join(restore_errors))
        if backup_root is not None:
            try:
                backup_entries_remain = backup_root.exists() and any(backup_root.iterdir())
            except BaseException as inspection_error:  # noqa: BLE001 - 未確認の退避物を削除しない
                preserve_stage_root = True
                _append_exception_message(error, f"cache退避物の確認失敗により一時領域を保持: {stage_root}: {inspection_error}")
            else:
                if backup_entries_remain:
                    preserve_stage_root = True
                    _append_exception_message(error, f"未復元のcache退避物を保持: {stage_root}")
        raise
    finally:
        if stage_root is not None and not preserve_stage_root:
            try:
                shutil.rmtree(stage_root)
            except BaseException as cleanup_error:  # noqa: BLE001 - 元の処理失敗を保持する
                if transaction_error is None:
                    raise
                _append_exception_message(transaction_error, f"一時領域の回収失敗: {cleanup_error}")


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
        source = _plugin_source(root, marketplace_name, plugin_name)
        if source is None:
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
        local_changed = _sync_local_plugin(root, source, marketplace_name, plugin_name, version, current, notices)
        return _outcome(changed or local_changed, notices)
    except Exception as error:
        _append_notices_to_exception(error, notices)
        raise
