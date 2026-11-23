"""dotfiles同梱のCodex pluginを自動導入・更新する。"""

import contextlib
import contextvars
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

from agent_toolkit._common import dotfiles_mode

from pytools._internal import (
    codex_hook_trust,
    codex_processes,
    common,
    external_web_guidance,
    log_format,
    plugin_cache_versions,
    post_apply_outcome,
    setup_codex_links,
)

logger = logging.getLogger(__name__)
CODEX_HOME = Path.home() / ".codex"
_TIMEOUT = 60.0
# nativeのoperation lock最大375秒、停止猶予最大300秒、強制終了確認10秒、起動30秒と応答の余裕。
# https://github.com/openai/codex/tree/rust-v0.166.0/codex-rs/app-server-daemon
# `atk-hook`の復旧が中断して残す`.<version>-restore-*`などの`.`で始まる名前は版として扱わない。
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")

_REMOVING_SUFFIX = "~removing"

_SAVED_CACHE_RETRY = (
    "次回のupdate-dotfilesが退避先から戻します。解消しない場合は退避先を変更せず、"
    "表示された内容とパスをdotfilesの作者へ報告してください。"
)

_MANUAL_RETRY = "Codexのセッションを全て終了してから`update-dotfiles`を再実行してください。"
_CODEX_PLUGIN_RESTART_NOTICE = post_apply_outcome.PostApplyNotice(
    message=(
        "Codex pluginを変更しました。実行中のCodexセッションを終了してから、"
        "次のコマンドでapp-server daemonを再起動してください。"
    ),
    command="codex app-server daemon restart",
)
_CODEX_HOOK_TRUST_NOTICE = post_apply_outcome.PostApplyNotice(
    message=(
        "Codexフックの信頼を自動登録できませんでした。Codexの入力欄で`/hooks`を実行し、agent-toolkitの未信頼・変更済みフックの定義を確認して信頼してください。"
        "その後、そのセッションを終了して新しいセッションを開始してください。"
        "セッションの開始時（SessionStart）に、入口のスキル`agent-toolkit:atk-mode`を起動する案内が届きます。"
        "新しいセッションでエージェントに「起動時にatk-modeを起動する案内を受け取ったか確認して」と尋ねてください。"
    ),
    command="/hooks",
)
_CODEX_EXECUTABLE: contextvars.ContextVar[Path] = contextvars.ContextVar("codex_executable", default=Path("codex"))

# Codexでは使用しないため、導入済みなら除去するプラグイン。
_UNUSED_PLUGINS: tuple[str, ...] = ("compact-plus@compact-plus",)
_MANAGED_PLUGINS: tuple[str, ...] = ("agent-basics", "agent-toolkit")


def _codex_json(args: list[str]) -> dict[str, Any] | None:
    result = common.run_subprocess([str(_CODEX_EXECUTABLE.get()), *args], timeout=_TIMEOUT, tag="codex")
    if result is None or result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def plugin_list_args(marketplace: str) -> list[str]:
    """対象を指定したmarketplaceに限定してplugin一覧をJSONで取得する`codex`の引数を返す。

    marketplaceを指定しない`codex plugin list`はリモートカタログ（chatgpt.com）へ接続して応答を待つ。
    codex-cli 0.165.0では、応答しないプロキシ経由で約30秒かかり、`--marketplace`を付けると接続せず0.1秒で終わった。
    導入状態の確認に必要なのは対象marketplaceのローカル状態だけなので、引数にmarketplace名を必須とする。
    """
    return ["plugin", "list", "--json", "--marketplace", marketplace]


def _marketplace_of(plugin_id: str) -> str:
    return plugin_id.rpartition("@")[2]


def _command(args: list[str]) -> bool:
    result = common.run_subprocess([str(_CODEX_EXECUTABLE.get()), *args], timeout=_TIMEOUT, tag="codex")
    return result is not None and result.returncode == 0


def _hooks_list(*, registration: tuple[str, Path, str] | None = None) -> dict[str, Any] | None:
    try:
        with common.process_scope(
            [str(_CODEX_EXECUTABLE.get()), "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            close_streams=False,
        ) as process:
            return _query_hooks(process, registration=registration)
    except OSError:
        return None


def _query_hooks(process: subprocess.Popen[str], *, registration: tuple[str, Path, str] | None = None) -> dict[str, Any] | None:
    """読取準備から応答待機までのパイプとスレッドを所有する。"""
    reader: threading.Thread | None = None
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process_stdin = process.stdin
        process_stdout = process.stdout
        messages: queue.Queue[str | None] = queue.Queue()

        def read_messages() -> None:
            with process_stdout:
                for line in process_stdout:
                    messages.put(line)
                messages.put(None)

        reader = threading.Thread(target=read_messages, daemon=True)
        reader.start()

        def send(message: dict[str, Any]) -> None:
            process_stdin.write(json.dumps(message) + "\n")
            process_stdin.flush()

        def response(request_id: int) -> dict[str, Any] | None:
            deadline = time.monotonic() + _TIMEOUT
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                try:
                    line = messages.get(timeout=remaining)
                except queue.Empty:
                    return None
                if line is None:
                    return None
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict) and message.get("id") == request_id:
                    result = message.get("result")
                    return result if isinstance(result, dict) else None

        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "dotfiles-post-apply", "version": "1"}, "capabilities": {}},
            }
        )
        if response(1) is None:
            return None
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "hooks/list", "params": {}})
        listing = response(2)
        if registration is None:
            return listing
        updates = codex_hook_trust.approval_updates(listing, *registration)
        if not updates:
            return listing
        send(
            {
                "id": 3,
                "method": "config/batchWrite",
                "params": {"edits": [{"keyPath": "hooks.state", "value": updates, "mergeStrategy": "upsert"}]},
            }
        )
        written = response(3)
        if written is None or written.get("status") != "ok":
            return listing
        return {"data": []}

    finally:
        # 途中のclose失敗でも、残るパイプと読取スレッドを回収する。
        with contextlib.ExitStack() as cleanup:
            if reader is not None and reader.ident is not None:
                cleanup.callback(reader.join, timeout=2)
            elif process.stdout is not None:
                cleanup.callback(process.stdout.close)
            if process.stdin is not None:
                cleanup.callback(process.stdin.close)
            residual, owned = common.stop_owned_process(process, stdin_grace_seconds=2)
            if residual:
                raise common.ProcessReclaimError(residual, owned, child=process)


def _hook_trust_notice_required(result: dict[str, Any] | None, plugin_id: str) -> bool:
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list):
        return False
    for item in data:
        if not isinstance(item, dict) or not isinstance(hooks := item.get("hooks"), list):
            continue

        if any(
            isinstance(hook, dict)
            and hook.get("pluginId") == plugin_id
            and hook.get("source") == "plugin"
            and hook.get("enabled") is True
            and hook.get("isManaged") is False
            and hook.get("trustStatus") in ("untrusted", "modified")
            for hook in hooks
        ):
            return True
    return False


def _target(root: Path, expected_name: str = "agent-toolkit") -> tuple[str, str, str] | None:
    try:
        marketplace = json.loads((root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        if not isinstance(marketplace, dict):
            return None
        marketplace_name = marketplace["name"]
        entries = marketplace["plugins"]
        if not isinstance(entries, list):
            return None
        entry = next(
            (item for item in entries if isinstance(item, dict) and item.get("name") == expected_name),
            None,
        )
        source = entry.get("source") if isinstance(entry, dict) else None
        if not isinstance(source, dict) or source.get("source") != "local" or not isinstance(source.get("path"), str):
            return None
        plugin_root = (root / source["path"]).resolve()
        plugin_root.relative_to(root.resolve())
        plugin = json.loads((plugin_root / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
        if not isinstance(plugin, dict):
            return None
        plugin_name = plugin["name"]
        version = plugin["version"]
        if not all(isinstance(value, str) for value in (marketplace_name, plugin_name, version)):
            return None
        if (
            not isinstance(entry, dict)
            or entry.get("name") != plugin_name
            or plugin_name != expected_name
            or not _valid_version_name(version)
        ):
            return None
        return marketplace_name, plugin_name, version
    except OSError, json.JSONDecodeError, KeyError, IndexError, StopIteration, TypeError:
        return None


def _plugin_source(root: Path, marketplace_name: str, plugin_name: str) -> Path | None:
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
    except OSError, json.JSONDecodeError, KeyError, IndexError, StopIteration, TypeError, ValueError:
        return None


def _marketplace_root(data: dict[str, Any], name: str) -> Path | None:
    """公開されたlocal sourceを登録元とし、Codexが複製した表示用rootと区別する。"""
    for item in data.get("marketplaces", []):
        if item.get("name") == name and isinstance(item.get("root"), str):
            source = item.get("marketplaceSource")
            if isinstance(source, dict) and source.get("sourceType") == "local" and isinstance(source.get("source"), str):
                original = Path(source["source"])
                if original.is_absolute():
                    return original.resolve()
            return Path(item["root"]).resolve()
    return None


def _migrate_cached_marketplace(root: Path, name: str, registered_root: Path) -> bool:
    """CodexのGit複製を使う既存登録をlocal配布元へ移し、追加失敗時は元の登録を復元する。"""
    if not registered_root.is_relative_to((_codex_home() / ".tmp" / "marketplaces").resolve()):
        return False
    try:
        config = tomllib.loads((_codex_home() / "config.toml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    original = config.get("marketplaces", {}).get(name)
    if not isinstance(original, dict) or original.get("source_type") != "git" or not isinstance(original.get("source"), str):
        return False
    if set(original) - {"source_type", "source", "ref", "sparse_paths"}:
        raise RuntimeError("marketplace登録に未対応の設定があるため、元の登録を保持した")
    restore = ["plugin", "marketplace", "add", original["source"]]
    if reference := original.get("ref"):
        if not isinstance(reference, str):
            raise RuntimeError("marketplaceのrefが文字列ではないため元の登録を保持した")
        restore.extend(("--ref", reference))
    sparse_paths = original.get("sparse_paths", [])
    if not isinstance(sparse_paths, list) or any(not isinstance(path, str) for path in sparse_paths):
        raise RuntimeError("marketplaceのsparse_pathsが不正なため元の登録を保持した")
    for path in sparse_paths:
        restore.extend(("--sparse", path))
    if not _command(["plugin", "marketplace", "remove", name]):
        raise RuntimeError("旧Git marketplaceの登録を変更できなかった。元の登録を確認する")
    if _command(["plugin", "marketplace", "add", str(root)]):
        return True
    if _command(restore):
        raise RuntimeError("local marketplaceの追加に失敗した。元のGit登録とref・sparse_pathsを復元済み")
    raise RuntimeError(
        f"local marketplaceの追加と元のGit登録の復元に失敗した。{_codex_home() / 'config.toml'}のmarketplaces.{name}を復旧する"
    )


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


def _outcome(
    changed: bool,
    notices: list[post_apply_outcome.PostApplyNotice] | tuple[post_apply_outcome.PostApplyNotice, ...],
    *,
    deferred: bool = False,
) -> post_apply_outcome.PostApplyOutcome:
    return post_apply_outcome.PostApplyOutcome(changed=changed, notices=tuple(dict.fromkeys(notices)), deferred=deferred)


def _append_restart_notice_if_daemon_running(notices: list[post_apply_outcome.PostApplyNotice]) -> None:
    """稼働中のCodex daemonがある場合だけ再起動案内を追加する。"""
    if _command(["app-server", "daemon", "version"]):
        notices.append(_CODEX_PLUGIN_RESTART_NOTICE)


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", CODEX_HOME))


def _valid_version_name(value: str) -> bool:
    return _VERSION_PATTERN.fullmatch(value) is not None


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink() or setup_codex_links._is_link_like(path)  # pylint: disable=protected-access


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    return path.is_junction()


def _unlink(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    else:
        path.rmdir()


def _append_exception_message(error: BaseException, message: str) -> None:
    current = str(error)
    error.args = (f"{current}\n{message}" if current else message,)


def _verify_expected_state(plugin_id: str, version: str) -> None:
    after = _codex_json(plugin_list_args(_marketplace_of(plugin_id)))
    after_known, installed = _installed(after, plugin_id) if after is not None else (False, None)
    if not after_known or installed is None or installed.get("version") != version or installed.get("enabled") is not True:
        raise RuntimeError("Codex plugin更新後の状態が期待値と一致しない")


def _hook_bin() -> Path:
    return Path.home() / ".local" / "bin"


def _install_hook_wrapper(root: Path) -> bool:
    changed = False
    for name in ("atk-hook", "atk-hook.cmd") if os.name == "nt" else ("atk-hook",):
        source = root / "agent-toolkit" / "scripts" / name
        destination = _hook_bin() / name
        data = source.read_bytes()
        if destination.is_file() and destination.read_bytes() == data:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if name == "atk-hook":
            destination.chmod(0o755)
        changed = True
    return changed


def _codex_processes_using_plugin() -> tuple[str, ...]:
    """除去対象のplugin実体を参照し得る稼働中のCodexプロセスの表示ラベルを返す。

    `plugin remove`はhook・スキル・MCP実体を回収するため、参照するプロセスが1件でもあれば除去を延期する。
    参照の有無はホストの役割と無関係に、プロセスの役割だけで判定する。
    管理daemon自体の生存と、pluginを使うセッションの生存を分けて判定する。
    `session`の役割（判定素材を取得できないプロセスを含む）だけを数える。
    """
    return tuple(process.label for process in codex_processes.codex_processes() if process.role == "session")


def _saved_cache_root(marketplace_name: str, plugin_name: str) -> Path:
    """CLI更新中に旧versionを退避する場所を返す。

    cacheと同じ`CODEX_HOME`の配下に置き、通常は版ディレクトリの名前の変更だけで退避・復元する。
    場所を固定し、中断した実行が残した退避は次回の実行が戻す。
    """
    return _codex_home() / ".dotfiles-plugin-cache-saved" / marketplace_name / plugin_name


def _prune_retired_versions(cache_root: Path, keep: set[str]) -> bool:
    """後継versionの導入から猶予期間を過ぎ、稼働中のCodexが使わない旧versionを削除し、削除の有無を返す。

    稼働中のCodexは起動時に現行だったversionを使い続けるため、最も早く起動したCodexより後に後継が導入されたversionも残す。
    削除は旧versionを`~removing`の名前へ移してから行う。Windowsでは使用中のファイルを含む版の移動が失敗するため、
    その版は残し、移した後に削除が途中で止まっても欠けた版を元のパスに残さない。
    """
    for leftover in cache_root.glob(f"*{_REMOVING_SUFFIX}"):
        _remove_detached(leftover)
    if not cache_root.is_dir():
        return False
    versions = [
        path for path in cache_root.iterdir() if _valid_version_name(path.name) and not _is_link(path) and path.is_dir()
    ]
    keep_paths = {(cache_root / name).resolve() for name in keep}
    manifest = ".codex-plugin/plugin.json"
    deadline = time.time() - plugin_cache_versions.GRACE_DAYS * 86400
    expired = plugin_cache_versions.expired(versions, keep_paths, deadline, manifest)
    if not expired:
        return False
    unused = plugin_cache_versions.expired(versions, keep_paths, min(deadline, _earliest_codex_start()), manifest)
    for version_dir in expired:
        if version_dir not in unused:
            logger.info(
                log_format.format_status(
                    log_format.home_short(version_dir),
                    "稼働中のCodexが起動時に使った可能性があるため旧versionを保持。"
                    "そのCodexの終了後に実行するupdate-dotfilesで削除するため、対応不要です（処理は継続した）。",
                ),
                extra=log_format.LOG_ONLY,
            )
    removed = False
    for version_dir in unused:
        detached = version_dir.with_name(version_dir.name + _REMOVING_SUFFIX)
        try:
            version_dir.rename(detached)
        except OSError as error:
            logger.info(
                log_format.format_status(
                    log_format.home_short(version_dir),
                    f"名前を変更できないため旧versionを保持: {error}。"
                    "使用中の場合は使用の終了後に実行するupdate-dotfilesで削除するため、対応不要です（処理は継続した）。",
                ),
                extra=log_format.LOG_ONLY,
            )
            continue
        removed = True
        if _remove_detached(detached):
            logger.info(log_format.format_status(log_format.home_short(version_dir), "旧versionを削除"))
    return removed


def _earliest_codex_start() -> float:
    """plugin実体を使い得る稼働中のCodexのうち、最も早い起動時刻を返す。

    起動時刻を取得できないCodexがあれば、全versionを使い得るものとして負の無限大を返す。
    """
    earliest = float("inf")
    for process in codex_processes.codex_processes():
        if process.role == "update-loop":
            continue
        if process.started_at is None:
            return float("-inf")
        earliest = min(earliest, process.started_at)
    return earliest


def _remove_detached(path: Path) -> bool:
    """元のパスから切り離した旧versionを削除し、成否を返す。失敗しても次回の実行が削除するため、警告して続行する。"""
    try:
        _remove_tree(path)
    except OSError as error:
        logger.warning(
            log_format.format_status(
                log_format.home_short(path),
                f"旧versionの削除に失敗: {error}。利用中の版には影響しません。"
                "次回のupdate-dotfilesが再び削除します（処理は継続した）。",
            )
        )
        return False
    return True


def _snapshot_plugin_caches(cache_root: Path, saved_root: Path, target_version: str) -> None:
    """CLI更新前の通常versionディレクトリを退避する。

    目標versionはCLIが置き換えるため元の場所に残して複製し、`plugin add`の失敗時に戻せるようにする。
    使用中で移動できない版も元の場所に残して複製し、復元で補えるよう`~copied`の印を付ける。
    """
    if not cache_root.is_dir():
        return
    saved_root.mkdir(parents=True, exist_ok=True)
    for path in cache_root.iterdir():
        if not path.is_dir() or _is_link(path) or not _valid_version_name(path.name):
            continue
        if path.name != target_version:
            # Windowsは使用中のファイルを含むディレクトリを移動できないため、移動できない版は複製する。
            with contextlib.suppress(OSError):
                path.rename(saved_root / path.name)
            if path.exists():
                (saved_root / f"{path.name}~copied").touch()
        if path.exists():
            shutil.copytree(path, saved_root / path.name, symlinks=True)


def _restore_plugin_caches(cache_root: Path, saved_root: Path, *, installed_version: str | None, rollback: bool) -> None:
    """退避したversionを元のパスへ戻し、退避先を削除する。

    `rollback`では元のパスに現れた内容を退避した内容で置き換え、それ以外では既存の内容を残す。
    置き換えの途中で失敗した版は印を残し、次回の実行も途中まで消えた元のパスより退避した内容を優先する。
    複製で退避した使用中の版（`~copied`の印）は、CLIが元のパスを途中まで消しても使用中のファイルが残って置き換えられないため、
    元のパスに無いファイルだけを退避した内容から補う。
    `installed_version`の退避はCLIが作成した内容を残すため戻さない。
    """
    if not saved_root.is_dir():
        return
    for source in saved_root.iterdir():
        if not source.is_dir():
            continue
        destination = cache_root / source.name
        replace_marker = saved_root / f"{source.name}~replace"
        copied_marker = saved_root / f"{source.name}~copied"
        staging = cache_root / f"{source.name}~restoring"
        if _path_exists(staging):
            _remove_tree(staging)
        copied = copied_marker.exists()
        if (rollback or replace_marker.exists()) and not copied and _path_exists(destination):
            replace_marker.touch()
            _remove_tree(destination)
        if source.name == installed_version or _path_exists(destination):
            if copied and source.name != installed_version:
                _complete_from_saved(source, destination)
            replace_marker.unlink(missing_ok=True)
            copied_marker.unlink(missing_ok=True)
            shutil.rmtree(source)
            continue
        cache_root.mkdir(parents=True, exist_ok=True)
        try:
            source.rename(destination)
        except OSError:
            # cacheが別ボリュームへのリンクやマウントの配下にあると名前の変更ができないため、
            # 複製を仕上げてから元のパスへ置き、その後に退避を削除する。
            shutil.copytree(source, staging, symlinks=True)
            staging.rename(destination)
            replace_marker.unlink(missing_ok=True)
            copied_marker.unlink(missing_ok=True)
            shutil.rmtree(source)
        else:
            replace_marker.unlink(missing_ok=True)
            copied_marker.unlink(missing_ok=True)

    for orphan_marker in (*saved_root.glob("*~replace"), *saved_root.glob("*~copied")):
        orphan_marker.unlink()
    saved_root.rmdir()
    for parent in (saved_root.parent, saved_root.parent.parent):
        with contextlib.suppress(OSError):
            parent.rmdir()


def _complete_from_saved(source: Path, destination: Path) -> None:
    for directory, _subdirectories, files in source.walk():
        target = destination / directory.relative_to(source)
        target.mkdir(exist_ok=True)
        for name in files:
            if _path_exists(target / name):
                continue
            staging = target / f"{name}~restoring"
            staging.unlink(missing_ok=True)
            shutil.copy2(directory / name, staging, follow_symlinks=False)
            staging.replace(target / name)


def _remove_tree(path: Path) -> None:
    if _is_link(path):
        _unlink(path)
    else:
        shutil.rmtree(path)


def _sync_local_plugin(
    root: Path,
    marketplace_name: str,
    plugin_name: str,
    version: str,
    current: dict[str, Any] | None,
    notices: list[post_apply_outcome.PostApplyNotice],
) -> tuple[bool, bool]:
    """Local marketplaceのpluginを目標版へ同期し、変更の有無を返す。"""
    cache_root = _codex_home() / "plugins" / "cache" / marketplace_name / plugin_name
    saved_root = _saved_cache_root(marketplace_name, plugin_name)
    try:
        _restore_plugin_caches(cache_root, saved_root, installed_version=None, rollback=False)
    except OSError as error:
        raise RuntimeError(
            f"前回の実行が退避した旧cacheの復旧に失敗。退避先を保持: {saved_root}: {error}。{_SAVED_CACHE_RETRY}"
        ) from error
    if current is not None and current.get("enabled") is False:
        return False, False

    pruned = _prune_retired_versions(cache_root, {version} if current is None else {version, current["version"]})
    plugin_id = f"{plugin_name}@{marketplace_name}"
    needs_plugin_add = current is None or current.get("version") != version
    toolkit_plugin = plugin_name == "agent-toolkit"
    wrapper_changed = _install_hook_wrapper(root) if toolkit_plugin else False
    if needs_plugin_add:
        source = _plugin_source(root, marketplace_name, plugin_name) if toolkit_plugin else None
        expected_bundle = codex_hook_trust.bundle_hash(source) if source is not None else None
        try:
            _snapshot_plugin_caches(cache_root, saved_root, version)
        except OSError:
            _restore_plugin_caches(cache_root, saved_root, installed_version=None, rollback=False)
            raise
        if not _command(["plugin", "add", plugin_id]):
            try:
                _restore_plugin_caches(cache_root, saved_root, installed_version=None, rollback=True)
            except OSError as error:
                raise RuntimeError(
                    f"Codex plugin addと旧cacheの復旧に失敗。退避先を保持: {saved_root}: {error}。{_SAVED_CACHE_RETRY}"
                ) from error
            raise RuntimeError("Codex plugin addに失敗")
        try:
            _verify_expected_state(plugin_id, version)
            new_cache = _codex_home() / "plugins" / "cache" / marketplace_name / plugin_name / version
            if not toolkit_plugin and not any(path.is_file() for path in (new_cache / "skills").glob("*/SKILL.md")):
                raise RuntimeError("Codex plugin更新後のskills実体を確認できない")
            if toolkit_plugin and not (new_cache / "agent_toolkit" / "hook.py").is_file():
                raise RuntimeError("Codex plugin更新後のhook実体を確認できない")
            if toolkit_plugin:
                listing = (
                    _hooks_list(registration=(plugin_id, new_cache, expected_bundle))
                    if expected_bundle is not None
                    else _hooks_list()
                )
                if _hook_trust_notice_required(listing, plugin_id):
                    notices.insert(0, _CODEX_HOOK_TRUST_NOTICE)
        finally:
            try:
                _restore_plugin_caches(cache_root, saved_root, installed_version=version, rollback=False)
            except OSError as error:
                raise RuntimeError(
                    f"Codex plugin更新後の旧cache復旧に失敗。退避先を保持: {saved_root}: {error}。{_SAVED_CACHE_RETRY}"
                ) from error
    removed_legacy_links = _remove_legacy_links(root) if toolkit_plugin else False
    return needs_plugin_add or wrapper_changed or removed_legacy_links or pruned, False


def _remove_unused_plugins() -> post_apply_outcome.PostApplyOutcome:
    """Codexで使用しない導入済みpluginを除去する。"""
    changed = False
    deferred = False
    notices: list[post_apply_outcome.PostApplyNotice] = []
    for plugin_id in _UNUSED_PLUGINS:
        installed_data = _codex_json(plugin_list_args(_marketplace_of(plugin_id)))
        if installed_data is None:
            logger.warning(
                log_format.format_status(
                    plugin_id,
                    "plugin一覧の取得に失敗したためスキップ。導入済みpluginを保持して処理は継続します。"
                    "codex plugin list --jsonで原因を確認し、解消後にupdate-dotfilesを再実行してください。",
                )
            )
            continue
        state_known, installed = _installed(installed_data, plugin_id)
        if not state_known:
            logger.warning(
                log_format.format_status(
                    plugin_id,
                    "plugin一覧の構造が不正なためスキップ。導入済みpluginを保持して処理は継続します。"
                    "codex plugin list --jsonの出力とCodexの版を添えてdotfilesの作者へ報告してください。",
                )
            )
            continue
        if installed is None:
            continue
        # removeも旧版と同じくplugin実体を回収するため、参照し得るCodexが稼働中なら更新と同じく延期する。
        if running := _codex_processes_using_plugin():
            running_text = codex_processes.format_running_processes(running)
            logger.warning(
                log_format.format_status(
                    plugin_id,
                    f"Codexが稼働中のため使用しないpluginの除去を延期: {running_text}。導入済みの実体を保持しました。"
                    + _MANUAL_RETRY,
                )
            )
            notices.append(
                post_apply_outcome.PostApplyNotice(
                    message=(
                        f"Codexが稼働中のため、使用しない{plugin_id}の除去を延期しました（稼働中: {running_text}）。"
                        "利用中のセッションを守るため導入済みの実体を保持しています。" + _MANUAL_RETRY
                    ),
                )
            )
            deferred = True
            continue
        if not _command(["plugin", "remove", plugin_id]):
            logger.warning(
                log_format.format_status(
                    plugin_id,
                    f"plugin除去に失敗したため続行。使用しない場合はcodex plugin remove {plugin_id}を実行してください。",
                )
            )
            continue
        changed = True
        _append_restart_notice_if_daemon_running(notices)
    return _outcome(changed, notices, deferred=deferred)


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
        except OSError, ValueError:
            continue
        _unlink(path)
        changed = True
    return changed


def _append_notices_to_exception(error: Exception, notices: list[post_apply_outcome.PostApplyNotice]) -> None:
    unique_notices = tuple(dict.fromkeys(notices))
    if not unique_notices:
        return
    _append_exception_message(error, "post-apply案内:\n" + "\n".join(notice.render() for notice in unique_notices))


def run() -> post_apply_outcome.PostApplyOutcome:
    """marketplaceを登録してagent-toolkitを導入・更新する。

    導入・更新と状態確認の失敗は警告を出力してスキップと数え、配布先ファイルの書き込みの失敗は例外として失敗と数える。
    """
    if dotfiles_mode.is_minimal():
        return post_apply_outcome.PostApplyOutcome()
    external_changed = external_web_guidance.run("codex")
    codex = common.resolve_executable("codex")
    if codex is None:
        logger.info(
            log_format.format_status(
                "codex plugins", "codex CLIが見つからずスキップ。Codexを使わない場合は対応不要です（処理は継続した）。"
            )
        )
        return _outcome(False, [])

    token = _CODEX_EXECUTABLE.set(codex)
    notices: list[post_apply_outcome.PostApplyNotice] = []
    try:
        unused_outcome = _remove_unused_plugins()
        changed = unused_outcome.changed or external_changed
        notices.extend(unused_outcome.notices)
        unused_deferred = unused_outcome.deferred
        root = common.find_dotfiles_root()
        if root is None:
            return _outcome(changed, notices, deferred=unused_deferred)
        # dotfilesの作業ツリーをmarketplaceとして登録し、その版を期待値にする。単体インストーラー
        # （`install-claude.sh`・`install-claude.ps1`）は作業ツリーを前提にできないため、GitHubのmarketplaceを使う。
        targets = tuple(_target(root, name) for name in _MANAGED_PLUGINS)
        if any(target is None for target in targets):
            logger.warning(
                log_format.format_status(
                    "codex plugins",
                    "Codex plugin manifestが不正なためスキップ。update-dotfilesを再実行し、解消しない場合は"
                    f"{root / '.agents/plugins/marketplace.json'}と同ファイルのsourceが指すplugin.jsonの内容を"
                    "dotfilesの作者へ報告してください。",
                )
            )
            return _outcome(changed, notices, deferred=unused_deferred)
        resolved_targets = tuple(target for target in targets if target is not None)
        marketplace_name = resolved_targets[0][0]
        if any(_plugin_source(root, name, plugin) is None for name, plugin, _version in resolved_targets):
            logger.warning(
                log_format.format_status(
                    "codex plugins",
                    f"Codex pluginのlocal sourceが不正なためスキップ。{root / '.agents/plugins/marketplace.json'}の"
                    "sourceが作業ツリー内の実在するディレクトリを指すか確認し、update-dotfilesを再実行してください。",
                )
            )
            return _outcome(changed, notices, deferred=unused_deferred)
        marketplace_data = _codex_json(["plugin", "marketplace", "list", "--json"])
        if marketplace_data is None:
            return _outcome(changed, notices, deferred=unused_deferred)
        registered_root = _marketplace_root(marketplace_data, marketplace_name)
        if registered_root is None:
            if not _command(["plugin", "marketplace", "add", str(root)]):
                return _outcome(changed, notices, deferred=unused_deferred)
            changed = True
        elif registered_root != root.resolve():
            if _migrate_cached_marketplace(root, marketplace_name, registered_root):
                changed = True
            else:
                logger.error(
                    log_format.format_status(
                        "codex plugins",
                        f"marketplace登録先が異なる: {registered_root}。登録先をこの作業ツリーへ変更する場合は"
                        f'codex plugin marketplace remove "{marketplace_name}"を実行し、'
                        f'codex plugin marketplace add "{root}"で登録してupdate-dotfilesを再実行してください。',
                    )
                )
                return _outcome(changed, notices, deferred=unused_deferred)

        before = _codex_json(plugin_list_args(marketplace_name))
        if before is None:
            logger.error(
                log_format.format_status(
                    "codex plugins",
                    "更新前のplugin状態を取得できないため中止。codex plugin list --jsonで状態を確認し、"
                    "取得できる状態にしてからupdate-dotfilesを再実行してください。",
                )
            )
            return _outcome(changed, notices, deferred=unused_deferred)
        if not all(_installed(before, f"{plugin}@{marketplace_name}")[0] for _name, plugin, _version in resolved_targets):
            logger.error(
                log_format.format_status(
                    "codex plugins",
                    "更新前のplugin状態を取得できないため中止。codex plugin list --jsonで状態を確認し、"
                    "取得できる状態にしてからupdate-dotfilesを再実行してください。",
                )
            )
            return _outcome(changed, notices, deferred=unused_deferred)
        local_changed = False
        plugin_updated = False
        deferred = False
        for _name, plugin_name, version in resolved_targets:
            _known, current = _installed(before, f"{plugin_name}@{marketplace_name}")
            plugin_changed, plugin_deferred = _sync_local_plugin(root, marketplace_name, plugin_name, version, current, notices)
            plugin_updated = plugin_updated or (plugin_changed and (current is None or current.get("version") != version))
            local_changed = local_changed or plugin_changed
            deferred = deferred or plugin_deferred
        if plugin_updated:
            _append_restart_notice_if_daemon_running(notices)
        return _outcome(changed or local_changed, notices, deferred=deferred or unused_deferred)
    except RuntimeError as error:
        # 導入・更新とその後の状態確認の失敗。設定ファイルとランチャーの書き込みの失敗（OSError）は失敗として送出する。
        logger.warning(
            log_format.format_status(
                "codex plugins",
                f"導入または更新に失敗: {error}。codex plugin list --jsonで状態を確認し、"
                "表示された原因を解消してからupdate-dotfilesを再実行してください。",
            )
        )
        return _outcome(False, notices)
    except Exception as error:
        _append_notices_to_exception(error, notices)
        raise
    finally:
        _CODEX_EXECUTABLE.reset(token)
