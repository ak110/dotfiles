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
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from agent_toolkit._common import dotfiles_mode

from pytools._internal import (
    codex_processes,
    common,
    deferred_updates,
    log_format,
    post_apply_outcome,
    setup_codex_links,
)

logger = logging.getLogger(__name__)
CODEX_HOME = Path.home() / ".codex"
_TIMEOUT = 60.0
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9._+-]+\Z")
_AUTO_RESTART_ENV = "DOTFILES_CODEX_DAEMON_AUTO_RESTART"
# 自動更新タイマーを持たないホストで、延期した更新を反映する操作。
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
        "Codexプラグインの導入または更新でHook定義が変わった場合は、定義を確認して信頼してください。"
        "信頼後に新しいセッションを開始し、SessionStartの規範注入を確認してください。"
        "再信頼の操作だけではSessionStartの規範注入を検収できません。"
    ),
    command="/hooks",
)
_CODEX_EXECUTABLE: contextvars.ContextVar[Path] = contextvars.ContextVar("codex_executable", default=Path("codex"))

# Codexでは使用しないため、導入済みなら除去するプラグイン。
_UNUSED_PLUGINS: tuple[str, ...] = ("compact-plus@compact-plus",)
_MANAGED_PLUGINS: tuple[str, ...] = ("agent-basics", "agent-toolkit")
_EXPECTED_HOOK_EVENTS = {
    "sessionStart",
    "subagentStart",
    "preToolUse",
    "postToolUse",
    "permissionRequest",
    "userPromptSubmit",
    "stop",
    "subagentStop",
    "sessionEnd",
}


def _codex_json(args: list[str]) -> dict[str, Any] | None:
    result = common.run_subprocess([str(_CODEX_EXECUTABLE.get()), *args], timeout=_TIMEOUT, tag="codex")
    if result is None or result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _command(args: list[str]) -> bool:
    result = common.run_subprocess([str(_CODEX_EXECUTABLE.get()), *args], timeout=_TIMEOUT, tag="codex")
    return result is not None and result.returncode == 0


def _hooks_list() -> dict[str, Any] | None:
    """短命app-serverから現在の作業ディレクトリに対するhook登録を取得する。"""
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
            return _query_hooks(process)
    except OSError:
        return None


def _query_hooks(process: subprocess.Popen[str]) -> dict[str, Any] | None:
    """読取準備から応答待機までのパイプとスレッドを所有する。"""
    reader: threading.Thread | None = None
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process_stdin = process.stdin
        process_stdout = process.stdout
        messages: queue.Queue[str | None] = queue.Queue()

        def read_messages() -> None:
            """stdoutは読み取りを終えた側が閉じ、主スレッドの回収を読み取り待ちで妨げない。"""
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
        return response(2)

    finally:
        # 途中のclose失敗でも、残るパイプと読取スレッドを回収する。
        with contextlib.ExitStack() as cleanup:
            if reader is not None and reader.ident is not None:
                cleanup.callback(reader.join, timeout=2)
            elif process.stdout is not None:
                cleanup.callback(process.stdout.close)
            if process.stdin is not None:
                cleanup.callback(process.stdin.close)
            residual = common.stop_process_tree(process, stdin_grace_seconds=2)
            if residual:
                logger.warning("Codex App Serverの子孫を回収できない: %s", residual)


def _hook_trust_notice_required(result: dict[str, Any] | None) -> bool:
    """期待する9イベントが登録済みで、hook信頼だけが未完了の場合に真を返す。"""
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        return False
    item = data[0]
    hooks = item.get("hooks")
    if item.get("warnings") != [] or item.get("errors") != [] or not isinstance(hooks, list):
        return False
    if not all(isinstance(hook, dict) for hook in hooks):
        return False
    return (
        {hook.get("eventName") for hook in hooks} == _EXPECTED_HOOK_EVENTS
        and all(hook.get("enabled") is True for hook in hooks)
        and all(hook.get("trustStatus") == "untrusted" for hook in hooks)
    )


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
    except OSError, json.JSONDecodeError, KeyError, IndexError, StopIteration, TypeError, ValueError:
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


def _outcome(
    changed: bool,
    notices: list[post_apply_outcome.PostApplyNotice] | tuple[post_apply_outcome.PostApplyNotice, ...],
    *,
    deferred: bool = False,
) -> post_apply_outcome.PostApplyOutcome:
    """案内を初出順で重複排除した結果を返す。"""
    return post_apply_outcome.PostApplyOutcome(changed=changed, notices=tuple(dict.fromkeys(notices)), deferred=deferred)


def _append_restart_notice_if_daemon_running(notices: list[post_apply_outcome.PostApplyNotice]) -> None:
    """稼働中のCodex daemonがある場合だけ再起動案内を追加する。"""
    if _command(["app-server", "daemon", "version"]):
        notices.append(_CODEX_PLUGIN_RESTART_NOTICE)


def _restart_daemon_after_plugin_update(notices: list[post_apply_outcome.PostApplyNotice]) -> None:
    """稼働中daemonへ、設定に応じた自動再起動または手動案内を適用する。"""
    if not _command(["app-server", "daemon", "version"]):
        return
    if os.environ.get(_AUTO_RESTART_ENV) != "1":
        notices.append(_CODEX_PLUGIN_RESTART_NOTICE)
        return
    result = common.run_subprocess(
        [str(_CODEX_EXECUTABLE.get()), "app-server", "daemon", "restart"],
        timeout=_TIMEOUT,
        tag="codex",
    )
    exit_code = None if result is None else result.returncode
    if exit_code == 0:
        logger.info(log_format.format_status("codex plugins", "app-server daemonを自動再起動 (exit 0)"))
        return
    logger.warning(
        log_format.format_status(
            "codex plugins",
            f"app-server daemonの自動再起動に失敗 (exit {exit_code if exit_code is not None else 'codeなし'}: "
            f"{common.format_cli_error(result)})。"
            "実行中のCodexセッションを終了し、codex app-server daemon restartを実行してください。",
        )
    )
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
    after = _codex_json(["plugin", "list", "--json"])
    after_known, installed = _installed(after, plugin_id) if after is not None else (False, None)
    if not after_known or installed is None or installed.get("version") != version or installed.get("enabled") is not True:
        raise RuntimeError("Codex plugin更新後の状態が期待値と一致しない")


def _hook_bin() -> Path:
    return Path.home() / ".local" / "bin"


def _install_hook_wrapper(root: Path) -> bool:
    """plugin有効化前に安定したhook起動コマンドをPATH上へ配置する。"""
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
    daemon自動再起動の指定は管理daemonを更新後に再起動する許可であり、通常セッションと`agents_server`の
    `codex app-server --stdio`が使う版を回収する許可を含まない。このため指定の下では管理daemonとその補助実行体を除き、
    `session`の役割（判定素材を取得できないプロセスを含む）だけを数える。
    """
    if os.environ.get(_AUTO_RESTART_ENV) == "1":
        return tuple(process.label for process in codex_processes.codex_processes() if process.role == "session")
    return codex_processes.running_codex_processes()


def _snapshot_plugin_caches(cache_root: Path, saved_root: Path) -> tuple[str, ...]:
    """CLI更新前の通常versionディレクトリを退避する。"""
    if not cache_root.is_dir():
        return ()
    versions = []
    for path in cache_root.iterdir():
        if not path.is_dir() or _is_link(path) or not _valid_version_name(path.name):
            continue
        shutil.copytree(path, saved_root / path.name, symlinks=True)
        versions.append(path.name)
    return tuple(versions)


def _restore_plugin_caches(
    cache_root: Path,
    saved_root: Path,
    versions: tuple[str, ...],
    *,
    installed_version: str | None,
) -> None:
    """CLIが削除したversionを戻し、失敗時は退避済みの全versionを元へ戻す。"""
    for version in versions:
        if version == installed_version:
            continue
        source = saved_root / version
        destination = cache_root / version
        if installed_version is None and _path_exists(destination):
            if _is_link(destination):
                _unlink(destination)
            else:
                shutil.rmtree(destination)
        elif _path_exists(destination):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=True)


def _sync_local_plugin(
    root: Path,
    marketplace_name: str,
    plugin_name: str,
    version: str,
    current: dict[str, Any] | None,
    notices: list[post_apply_outcome.PostApplyNotice],
) -> tuple[bool, bool]:
    """Local marketplaceのpluginを目標版へ同期し、変更の有無を返す。"""
    plugin_id = f"{plugin_name}@{marketplace_name}"
    needs_plugin_add = current is None or current.get("enabled") is not True or current.get("version") != version
    toolkit_plugin = plugin_name == "agent-toolkit"
    wrapper_changed = _install_hook_wrapper(root) if toolkit_plugin else False
    if needs_plugin_add:
        cache_root = _codex_home() / "plugins" / "cache" / marketplace_name / plugin_name
        saved_root = Path(tempfile.mkdtemp(prefix="codex-plugin-cache-"))
        try:
            saved_versions = _snapshot_plugin_caches(cache_root, saved_root)
        except OSError:
            shutil.rmtree(saved_root, ignore_errors=True)
            raise
        if not _command(["plugin", "add", plugin_id]):
            try:
                _restore_plugin_caches(cache_root, saved_root, saved_versions, installed_version=None)
            except OSError as error:
                raise RuntimeError(
                    f"Codex plugin addと旧cacheの復旧に失敗。退避先を保持: {saved_root}: {error}。"
                    "退避先を変更せず、表示された内容とパスをdotfilesの作者へ報告してください。"
                ) from error
            shutil.rmtree(saved_root)
            raise RuntimeError("Codex plugin addに失敗")
        try:
            _verify_expected_state(plugin_id, version)
            new_cache = _codex_home() / "plugins" / "cache" / marketplace_name / plugin_name / version
            if not toolkit_plugin and not any(path.is_file() for path in (new_cache / "skills").glob("*/SKILL.md")):
                raise RuntimeError("Codex plugin更新後のskills実体を確認できない")
            if toolkit_plugin and not (new_cache / "agent_toolkit" / "hook.py").is_file():
                raise RuntimeError("Codex plugin更新後のhook実体を確認できない")
            if toolkit_plugin and _hook_trust_notice_required(_hooks_list()):
                notices.insert(0, _CODEX_HOOK_TRUST_NOTICE)
            if toolkit_plugin:
                _restart_daemon_after_plugin_update(notices)
        finally:
            try:
                # 既存セッションが参照する全旧パスを関数の終了時に成立させるため、復旧は保護区間の終端で行う。
                _restore_plugin_caches(cache_root, saved_root, saved_versions, installed_version=version)
            except OSError as error:
                raise RuntimeError(
                    f"Codex plugin更新後の旧cache復旧に失敗。退避先を保持: {saved_root}: {error}。"
                    "退避先を変更せず、表示された内容とパスをdotfilesの作者へ報告してください。"
                ) from error
            else:
                shutil.rmtree(saved_root)
    removed_legacy_links = _remove_legacy_links(root) if toolkit_plugin else False
    return needs_plugin_add or wrapper_changed or removed_legacy_links, False


def _remove_unused_plugins() -> post_apply_outcome.PostApplyOutcome:
    """Codexで使用しない導入済みpluginを除去する。"""
    changed = False
    deferred = False
    notices: list[post_apply_outcome.PostApplyNotice] = []
    for plugin_id in _UNUSED_PLUGINS:
        installed_data = _codex_json(["plugin", "list", "--json"])
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
                    + deferred_updates.retry_guidance(_MANUAL_RETRY),
                )
            )
            notices.append(
                post_apply_outcome.PostApplyNotice(
                    message=(
                        f"Codexが稼働中のため、使用しない{plugin_id}の除去を延期しました（稼働中: {running_text}）。"
                        "利用中のセッションを守るため導入済みの実体を保持しています。"
                        + deferred_updates.retry_guidance(_MANUAL_RETRY)
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
    rendered = []
    for notice in unique_notices:
        rendered.append(notice.message)
        if notice.command is not None:
            rendered.append(notice.command)
    _append_exception_message(error, "post-apply案内:\n" + "\n".join(rendered))


def run() -> post_apply_outcome.PostApplyOutcome:
    """marketplaceを登録してagent-toolkitを導入・更新する。

    導入・更新と状態確認の失敗は警告を出力してスキップと数え、配布先ファイルの書き込みの失敗は例外として失敗と数える。
    """
    if dotfiles_mode.is_minimal():
        return post_apply_outcome.PostApplyOutcome()
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
        changed = unused_outcome.changed
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
            logger.error(
                log_format.format_status(
                    "codex plugins",
                    f"marketplace登録先が異なる: {registered_root}。登録先をこの作業ツリーへ変更する場合は"
                    f'codex plugin marketplace remove "{marketplace_name}"を実行し、'
                    f'codex plugin marketplace add "{root}"で登録してupdate-dotfilesを再実行してください。',
                )
            )
            return _outcome(changed, notices, deferred=unused_deferred)

        before = _codex_json(["plugin", "list", "--json"])
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
        deferred = False
        for _name, plugin_name, version in resolved_targets:
            _known, current = _installed(before, f"{plugin_name}@{marketplace_name}")
            plugin_changed, plugin_deferred = _sync_local_plugin(root, marketplace_name, plugin_name, version, current, notices)
            local_changed = local_changed or plugin_changed
            deferred = deferred or plugin_deferred
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
