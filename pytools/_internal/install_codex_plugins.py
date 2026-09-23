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

from pytools._internal import claude_common, log_format, post_apply_outcome, setup_codex_links

logger = logging.getLogger(__name__)
CODEX_HOME = Path.home() / ".codex"
_TIMEOUT = 60.0
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9._+-]+\Z")
_AUTO_RESTART_ENV = "DOTFILES_CODEX_DAEMON_AUTO_RESTART"
_CODEX_PLUGIN_RESTART_NOTICE = post_apply_outcome.PostApplyNotice(
    message=(
        "Codex pluginを変更しました。実行中のCodexセッションを終了してから、"
        "次のコマンドでapp-server daemonを再起動してください。"
    ),
    command="codex app-server daemon restart",
)
_CODEX_HOOK_TRUST_NOTICE = post_apply_outcome.PostApplyNotice(
    message=(
        "Codexプラグインの導入又は更新でHook定義が変わった場合は、定義を確認して信頼してください。"
        "信頼後に新しいセッションを開始し、SessionStartの規範注入を確認してください。"
        "再信頼の操作だけではSessionStartの規範注入を検収できません。"
    ),
    command="/hooks",
)
_CODEX_EXECUTABLE: contextvars.ContextVar[Path] = contextvars.ContextVar("codex_executable", default=Path("codex"))

# Codexでは使用しないため、導入済みなら除去するプラグイン。
_UNUSED_PLUGINS: tuple[str, ...] = ("compact-plus@compact-plus",)
_EXPECTED_HOOK_EVENTS = {
    "sessionStart",
    "subagentStart",
    "preToolUse",
    "postToolUse",
    "permissionRequest",
    "userPromptSubmit",
    "subagentStop",
    "sessionEnd",
}


def _codex_json(args: list[str]) -> dict[str, Any] | None:
    result = claude_common.run_subprocess([str(_CODEX_EXECUTABLE.get()), *args], timeout=_TIMEOUT, tag="codex")
    if result is None or result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _command(args: list[str]) -> bool:
    result = claude_common.run_subprocess([str(_CODEX_EXECUTABLE.get()), *args], timeout=_TIMEOUT, tag="codex")
    return result is not None and result.returncode == 0


def _hooks_list() -> dict[str, Any] | None:
    """短命app-serverから現在の作業ディレクトリに対するhook登録を取得する。"""
    try:
        process = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with
            [str(_CODEX_EXECUTABLE.get()), "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return None
    assert process.stdin is not None
    assert process.stdout is not None
    process_stdin = process.stdin
    process_stdout = process.stdout
    messages: queue.Queue[str | None] = queue.Queue()

    def read_messages() -> None:
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

    try:
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
        with contextlib.suppress(OSError):
            process_stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        reader.join(timeout=2)


def _hook_trust_notice_required(result: dict[str, Any] | None) -> bool:
    """期待する8イベントが登録済みで、hook信頼だけが未完了の場合に真を返す。"""
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


def _target(root: Path) -> tuple[str, str, str] | None:
    try:
        marketplace = json.loads((root / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        if not isinstance(marketplace, dict):
            return None
        marketplace_name = marketplace["name"]
        entries = marketplace["plugins"]
        if not isinstance(entries, list):
            return None
        entry = next(
            (item for item in entries if isinstance(item, dict) and item.get("name") == "agent-toolkit"),
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


def _restart_daemon_after_plugin_update(notices: list[post_apply_outcome.PostApplyNotice]) -> None:
    """稼働中daemonへ、設定に応じた自動再起動又は手動案内を適用する。"""
    if not _command(["app-server", "daemon", "version"]):
        return
    if os.environ.get(_AUTO_RESTART_ENV) != "1":
        notices.append(_CODEX_PLUGIN_RESTART_NOTICE)
        return
    result = claude_common.run_subprocess(
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
            f"app-server daemonの自動再起動に失敗 (exit {exit_code if exit_code is not None else 'codeなし'})",
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
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


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
    """plugin有効化前に安定したhook入口をPATH上へ配置する。"""
    changed = False
    for name in ("atk-hook", "atk-hook.cmd") if os.name == "nt" else ("atk-hook",):
        source = root / "bin" / name
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


def _sync_local_plugin(
    root: Path,
    marketplace_name: str,
    plugin_name: str,
    version: str,
    current: dict[str, Any] | None,
    notices: list[post_apply_outcome.PostApplyNotice],
) -> bool:
    plugin_id = f"{plugin_name}@{marketplace_name}"
    needs_plugin_add = current is None or current.get("enabled") is not True or current.get("version") != version
    wrapper = _hook_bin() / ("atk-hook.cmd" if os.name == "nt" else "atk-hook")
    first_transition = not wrapper.exists()
    wrapper_changed = _install_hook_wrapper(root)
    if needs_plugin_add:
        old_cache = None
        if first_transition and current is not None and isinstance(current.get("version"), str):
            old_cache = _codex_home() / "plugins" / "cache" / marketplace_name / plugin_name / current["version"]
        with tempfile.TemporaryDirectory(prefix="atk-hook-migration-") as temporary:
            saved = Path(temporary) / "old-plugin"
            if old_cache is not None and old_cache.is_dir():
                shutil.copytree(old_cache, saved)
            if not _command(["plugin", "add", plugin_id]):
                raise RuntimeError("Codex plugin addに失敗")
            if old_cache is not None and saved.is_dir() and not old_cache.exists():
                old_cache.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(saved, old_cache)
        _verify_expected_state(plugin_id, version)
        new_cache = _codex_home() / "plugins" / "cache" / marketplace_name / plugin_name / version
        if not (new_cache / "agent_toolkit" / "hook.py").is_file():
            raise RuntimeError("Codex plugin更新後のhook実体を確認できない")
        if _hook_trust_notice_required(_hooks_list()):
            notices.insert(0, _CODEX_HOOK_TRUST_NOTICE)
        _restart_daemon_after_plugin_update(notices)
    removed_legacy_links = _remove_legacy_links(root)
    return needs_plugin_add or wrapper_changed or removed_legacy_links


def _remove_unused_plugins() -> post_apply_outcome.PostApplyOutcome:
    """Codexで使用しない導入済みpluginを除去する。"""
    changed = False
    notices: list[post_apply_outcome.PostApplyNotice] = []
    for plugin_id in _UNUSED_PLUGINS:
        installed_data = _codex_json(["plugin", "list", "--json"])
        if installed_data is None:
            logger.warning(log_format.format_status(plugin_id, "plugin一覧の取得に失敗したためスキップ"))
            continue
        state_known, installed = _installed(installed_data, plugin_id)
        if not state_known:
            logger.warning(log_format.format_status(plugin_id, "plugin一覧の構造が不正なためスキップ"))
            continue
        if installed is None:
            continue
        if not _command(["plugin", "remove", plugin_id]):
            logger.warning(log_format.format_status(plugin_id, "plugin除去に失敗したため続行"))
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
    codex = claude_common.resolve_executable("codex")
    if codex is None:
        logger.info(log_format.format_status("codex plugins", "codex CLIが見つからずスキップ"))
        return _outcome(False, [])

    token = _CODEX_EXECUTABLE.set(codex)
    notices: list[post_apply_outcome.PostApplyNotice] = []
    try:
        unused_outcome = _remove_unused_plugins()
        changed = unused_outcome.changed
        notices.extend(unused_outcome.notices)
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
    finally:
        _CODEX_EXECUTABLE.reset(token)
