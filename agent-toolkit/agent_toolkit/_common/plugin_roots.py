"""参照するプラグインの実体を、開発配置かホストの導入情報から解決する。"""

import contextlib
import contextvars
import functools
import hashlib
import json
import os
import pathlib
import re
import runpy
import shlex
import shutil
import subprocess
import sys
from collections.abc import Generator
from typing import Any

from agent_toolkit._common import host_homes, process_tree
from agent_toolkit._common.session_state import read_state, update_state


class PluginRootError(ValueError):
    """導入情報と正式配置からプラグインrootを一意に確定できない。"""


_inventories: contextvars.ContextVar[dict[tuple[str, pathlib.Path], object] | None] = contextvars.ContextVar(
    "plugin_root_inventories", default=None
)
_reusable: contextvars.ContextVar[dict | None] = contextvars.ContextVar("plugin_root_reusable", default=None)
_INVENTORY_STATE_KEY = "plugin_inventory"


def _managed_settings_directories() -> list[pathlib.Path]:
    if sys.platform == "darwin":
        return [pathlib.Path("/Library/Application Support/ClaudeCode")]
    if sys.platform == "win32":
        return [pathlib.Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "ClaudeCode"]
    directories = [pathlib.Path("/etc/claude-code")]
    if os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"):
        directories.append(pathlib.Path("/mnt/c/Program Files/ClaudeCode"))
    return directories


def _inventory_signature(host: str, project: pathlib.Path) -> str | None:
    """設定合成を複製せず、公開一覧をファイル設定源の変更時に再取得する。"""
    if host != "claude":
        return None
    executable = shutil.which(host)
    if executable is None:
        return None
    config = host_homes.claude_config_dir()
    try:
        paths = {pathlib.Path(__file__), pathlib.Path(executable), *config.glob("*.json")}
        paths.update(config / "plugins" / name for name in ("installed_plugins.json", "known_marketplaces.json"))
        # registry/MDMの変更はsession内では検出しない。管理者変更は次のsessionで新規取得する。
        for directory in _managed_settings_directories():
            paths.add(directory / "managed-settings.json")
            paths.update((directory / "managed-settings.d").glob("*.json"))
        for directory in (project, *project.parents):
            paths.update(directory / ".claude" / name for name in ("settings.json", "settings.local.json"))
            git = directory / ".git"
            paths.add(git)
            if git.is_file():
                pointer = git.read_text(encoding="utf-8").strip()
                if not pointer.startswith("gitdir: "):
                    return None
                gitdir = (directory / pointer[8:]).resolve()
                common = gitdir / "commondir"
                paths.add(common)
                if common.is_file():
                    main = (gitdir / common.read_text(encoding="utf-8").strip()).resolve().parent
                    paths.add(main / ".claude/settings.local.json")
        metadata: list[tuple] = []
        for path in sorted(paths):
            try:
                stat = path.stat()
                # 同長の短時間書換えではmtime/ctimeも同値になるFSがある。選別入力は内容でも比較する。
                content = (
                    hashlib.sha256(path.read_bytes()).hexdigest()
                    if path.name
                    in {
                        "settings.json",
                        "settings.local.json",
                        "installed_plugins.json",
                        "known_marketplaces.json",
                        "managed-settings.json",
                    }
                    or path.parent.name == "managed-settings.d"
                    else None
                )
                metadata.append(
                    (
                        str(path),
                        str(path.resolve()),
                        stat.st_dev,
                        stat.st_ino,
                        stat.st_size,
                        stat.st_mtime_ns,
                        stat.st_ctime_ns,
                        content,
                    )
                )
            except FileNotFoundError:
                metadata.append((str(path), None))
        # 環境は選別へ作用しうる。保存するのはhashだけで、設定本文や資格情報は複製しない。
        value = [host, sys.platform, str(project), str(config), sorted(os.environ.items()), metadata]
        return hashlib.sha256(json.dumps(value).encode("utf-8")).hexdigest()
    except OSError, UnicodeError, ValueError:
        return None


@contextlib.contextmanager
def plugin_resolution_scope(*, session_id: str = "") -> Generator[None]:
    """要求内の一覧を共有し、hookでは入力不変の公開一覧を既存状態から再利用する。"""
    if _inventories.get() is not None:
        yield
        return
    token = _inventories.set({})
    saved = read_state(session_id).get(_INVENTORY_STATE_KEY, {}) if session_id else {}
    reusable = saved.copy() if isinstance(saved, dict) and saved.get("session_id") == session_id else {}
    persistent_token = _reusable.set(reusable if session_id else None)
    try:
        yield
    finally:
        if session_id and reusable != saved:
            reusable["session_id"] = session_id
            update_state(session_id, lambda state: {**state, _INVENTORY_STATE_KEY: reusable})
        _reusable.reset(persistent_token)
        _inventories.reset(token)


def _plugin_inventory(host: str, project: pathlib.Path) -> object:
    cache = _inventories.get()
    key = host, project
    if cache is not None and key in cache:
        return cache[key]
    if host in {"agy", "opencode"}:
        data = _native_inventory(host)
        if cache is not None:
            cache[key] = data
        return data
    reusable = _reusable.get()
    signature = _inventory_signature(host, project) if reusable is not None else None
    if (
        reusable is not None
        and signature is not None
        and reusable.get("signature") == signature
        and isinstance(reusable.get("data"), list)
    ):
        data = reusable["data"]
        if cache is not None:
            cache[key] = data
        return data
    data = (
        {"installed": codex_installed_plugins(host, project)}
        if host == "codex"
        else _host_json(host, [host, "plugin", "list", "--json"], project)
    )
    if cache is not None:
        cache[key] = data
    if (
        reusable is not None
        and signature is not None
        and signature == _inventory_signature(host, project)
        and isinstance(data, list)
    ):
        reusable.clear()
        reusable.update(signature=signature, data=data)
    return data


def codex_installed_plugins(executable: str, project: pathlib.Path | None = None) -> list[object]:
    """Codexに設定済みのmarketplaceごとに導入済みpluginを取得し、連結した`installed`の一覧を返す。

    marketplaceを指定しない`codex plugin list`は、設定済みmarketplaceに加えてリモートカタログ（chatgpt.com）へ接続する。
    codex-cli 0.165.0では、応答しないプロキシ経由でこの照会が約30秒かかり、`--disable remote_plugin`を付けても接続は続いた。
    marketplaceごとの照会と`codex plugin marketplace list`は接続せず、それぞれ0.1秒程度で終わった。
    リモートカタログ由来のplugin（`openai-curated-remote`）は設定済みmarketplaceの一覧に現れず、配布物の導入元にならない。
    そのため設定済みmarketplaceの全件を照会すれば、導入元のmarketplaceが一意かどうかの判定を保てる。
    """
    listing = _host_json("codex", [executable, "plugin", "marketplace", "list", "--json"], project)
    marketplaces = listing.get("marketplaces") if isinstance(listing, dict) else None
    if not isinstance(marketplaces, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"] for item in marketplaces
    ):
        raise PluginRootError("codexのmarketplace一覧の形式が不正。codex plugin marketplace list --jsonを確認する")
    installed: list[object] = []
    for marketplace in marketplaces:
        command = [executable, "plugin", "list", "--json", "--marketplace", marketplace["name"]]
        data = _host_json("codex", command, project)
        items = data.get("installed") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise PluginRootError(f"Codexの導入情報の形式が不正。{shlex.join(['codex', *command[1:]])}を確認する")
        installed.extend(items)
    return installed


def _host_json(host: str, command: list[str], project: pathlib.Path | None) -> object:
    """ホストCLIの照会を時間上限付きで実行し、JSONを返す。

    失敗は、`host`をコマンド名にした照会の起動形を次の操作として示す`PluginRootError`にする。
    """
    shown = shlex.join([host, *command[1:]])
    try:
        result = process_tree.run_with_timeout(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            cwd=str(project) if project is not None else None,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PluginRootError(f"{host}の導入情報の取得失敗: {error}。{shown}を確認する") from error
    if result.returncode:
        raise PluginRootError(f"{host}の導入情報の取得失敗: 終了コード{result.returncode}。{shown}を確認する")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PluginRootError(f"{host}の導入情報のJSONが不正。{shown}を確認する") from error


def _native_recovery(host: str) -> str:
    """生成元の公開コマンドが存在する配置だけ、再生成コマンドを案内する。"""
    label = "OpenCode" if host == "opencode" else "Antigravity"
    if generator := shutil.which("update-dotfiles"):
        args = [str(pathlib.Path(generator).resolve()), "--mode", "full"]
        command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
        return f"{label}の資料登録を生成元の公開コマンド`{command}`で再生成し、startを再実行する"
    # 単体plugin導入先にはdotfilesのコマンドが無い。稼働配布物の公開コマンドから導入元を確認する。
    entry = pathlib.Path(__file__).resolve().parents[2] / "scripts" / ("atk.cmd" if os.name == "nt" else "atk")
    inspection = "稼働配布物の導入元を確認し、"
    if entry.is_file():
        args = [str(entry), "info"]
        command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
        inspection = f"`{command}`で稼働配布物の所在を確認し、"
    return (
        f"{label}の資料登録を生成したupdate-dotfilesはPATHに無い。{inspection}"
        "導入元が提供する公開インストーラーで資料登録を再生成してからstartを再実行する"
    )


def _native_inventory(host: str) -> object:
    """各ホストの正式導入先から、登録された資料rootを読む。"""
    if host == "opencode":
        config = pathlib.Path(
            os.environ.get("OPENCODE_CONFIG_DIR")
            or pathlib.Path(os.environ.get("XDG_CONFIG_HOME") or pathlib.Path.home() / ".config") / "opencode"
        ).expanduser()
        path = config / "agent-toolkit.md"
        try:
            body = path.read_text(encoding="utf-8")
            if not body.startswith('<atk-auto source="dotfiles" kind="opencode-host">\n'):
                raise ValueError("管理対象のroot対応表ではない")
            # OpenCode向け導入器はskillsを名前空間付きで複製し、付属資料はこの原本rootから参照する。
            return re.findall(r"(agent-[a-z-]+) plugin root: `([^`\n]+)`", body)
        except (OSError, UnicodeError, ValueError) as error:
            raise PluginRootError(f"OpenCodeの資料root対応表を読めない: {path}: {error}。" + _native_recovery(host)) from error
    config = pathlib.Path.home() / ".gemini/config"
    path = config / "plugins.json"
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
        settings = json.loads((config / "config.json").read_text(encoding="utf-8")) if (config / "config.json").exists() else {}
        if not isinstance(registry, dict) or not isinstance(registry.get("entries"), list) or not isinstance(settings, dict):
            raise ValueError("登録の形式が不正")
        return registry["entries"], settings.get("plugins", {})
    except (OSError, UnicodeError, ValueError) as error:
        raise PluginRootError(f"Antigravityの資料root登録を読めない: {path}: {error}。" + _native_recovery(host)) from error


def _native_plugin_root(name: str, host: str, data: Any) -> pathlib.Path:
    """native導入の明示rootを選び、他ホストの設定を代替に使わない。"""
    if host == "opencode":
        roots = {pathlib.Path(path) for plugin, path in data if plugin == name}
        action = _native_recovery(host)
    else:
        entries, settings = data
        directory = name + "-agy"
        action = "~/.gemini/config/plugins.jsonとconfig.jsonの登録・有効設定を確認する。" + _native_recovery(host)
        if not isinstance(settings, dict):
            raise PluginRootError(f"Antigravityのplugins設定が不正。{action}")
        preference = settings.get(directory, {})
        if not isinstance(preference, dict) or preference.get("enabled") is False:
            raise PluginRootError(f"Antigravityの{directory}は無効または設定が不正。{action}")
        # 正式生成器は親ディレクトリと生成物の名前を明示したentryを登録する。
        roots = {
            pathlib.Path(entry["path"]) / directory
            for entry in entries
            if isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and isinstance(entry.get("include_only"), list)
            and directory in entry["include_only"]
        }
    if len(roots) != 1:
        raise PluginRootError(f"{host}の{name}資料rootが一意でない: {len(roots)}件。{action}")
    root = next(iter(roots))
    if not root.is_absolute():
        raise PluginRootError(f"{host}の{name}資料rootが絶対パスでない: {root}。{action}")
    try:
        manifest = root / ("plugin.json" if host == "agy" else ".claude-plugin/plugin.json")
        if json.loads(manifest.read_text(encoding="utf-8")).get("name") != name or not (root / "skills").is_dir():
            raise ValueError("manifestの論理名またはskillsが不正")
    except (OSError, UnicodeError, ValueError, AttributeError) as error:
        raise PluginRootError(f"{host}の{name}資料rootの実体が不正: {root}: {error}。{action}") from error
    return root.resolve()


def sibling_plugin_root(name: str, toolkit_root: pathlib.Path) -> pathlib.Path | None:
    """開発時と公式agy生成時の同階層rootで、manifestの論理名と指定名が一致するか確かめる。"""
    native = toolkit_root / "plugin.json"
    agy = (
        toolkit_root.name == "agent-toolkit-agy"
        and native.is_file()
        and json.loads(native.read_text(encoding="utf-8")).get("name") == "agent-toolkit"
    )
    sibling = toolkit_root.parent / (name + "-agy" if agy else name)
    manifest = sibling / ".claude-plugin/plugin.json"
    if (
        manifest.is_file()
        and (sibling / "skills").is_dir()
        and json.loads(manifest.read_text(encoding="utf-8")).get("name") == name
    ):
        return sibling.resolve()
    if agy:
        raise PluginRootError(f"Antigravityの正式な{name}生成rootが無いか論理名が異なる: {sibling}。" + _native_recovery("agy"))
    return None


@functools.cache
def _claude_selector():
    # 個人binへ単独複製するshimが同じ定義を使えるよう、stdlibだけの選別を同梱scriptで所有する。
    script = pathlib.Path(__file__).resolve().parents[2] / "scripts/atk-hook"
    return runpy.run_path(str(script))["select_claude_plugin"]


def _codex_component(item: dict, field: str) -> str:
    value = item.get(field)
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or any(character in value for character in "/\\:\x00")
        or value.strip() != value
    ):
        raise PluginRootError(f"Codexの{item.get('name')}導入情報の{field}が欠落または不正。codex plugin list --jsonを確認する")
    return value


def resolve_plugin_root(
    name: str, toolkit_root: pathlib.Path, host: str = "claude", project: pathlib.Path | str | None = None
) -> pathlib.Path:
    """導入版を一意に解決し、実在するskillsを持つrootだけを返す。"""
    if name == "agent-toolkit":
        return toolkit_root
    sibling = sibling_plugin_root(name, toolkit_root)
    if sibling is not None:
        return sibling
    # cwdとホストの設定はリクエストごとに異なる。実体検証の結果をcacheせず、一覧だけを要求内で共有する。
    project = pathlib.Path(project or pathlib.Path.cwd()).resolve()
    if host not in {"claude", "codex", "agy", "opencode"}:
        raise PluginRootError(f"{host}の{name}の導入情報の解決は未対応。対応hostを指定して再実行する")
    data = _plugin_inventory(host, project)
    if host in {"agy", "opencode"}:
        return _native_plugin_root(name, host, data)
    if host == "codex":
        if not isinstance(data, dict) or not isinstance(data.get("installed"), list):
            raise PluginRootError("Codexの導入情報の形式が不正。codex plugin list --jsonを確認する")
        installed = data["installed"]
        if any(not isinstance(item, dict) for item in installed):
            raise PluginRootError("Codexの導入情報の項目が不正。codex plugin list --jsonを確認する")
        for item in installed:
            if (
                item.get("name") not in ("agent-toolkit", name)
                or item.get("installed") is not True
                or item.get("enabled") is not True
            ):
                continue
            for field in ("name", "marketplaceName"):
                _codex_component(item, field)
        marketplaces = {
            item["marketplaceName"]
            for item in installed
            if item.get("name") == "agent-toolkit" and item.get("installed") is True and item.get("enabled") is True
        }
        if len(marketplaces) != 1:
            raise PluginRootError("Codexのagent-toolkitの有効なmarketplaceが一意でない。codex plugin list --jsonを確認する")
        items = [
            item
            for item in installed
            if item.get("name") == name
            and item.get("marketplaceName") in marketplaces
            and item.get("installed") is True
            and item.get("enabled") is True
        ]
        home = pathlib.Path(os.environ.get("CODEX_HOME", pathlib.Path.home() / ".codex"))
        roots = {home / "plugins/cache" / item["marketplaceName"] / name / _codex_component(item, "version") for item in items}
    else:
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise PluginRootError("Claude Codeの導入情報の形式が不正。claude plugin list --jsonを確認する")
        # 稼働版が保持されたrootなら、その導入元のmarketplaceを優先する。
        held = {
            item["id"].split("@", 1)[1]
            for item in data
            if isinstance(item.get("id"), str)
            and item["id"].startswith("agent-toolkit@")
            and isinstance(item.get("installPath"), str)
            and pathlib.Path(item["installPath"]).resolve() == toolkit_root.resolve()
        }
        marketplaces = held or {
            item["id"].split("@", 1)[1]
            for item in data
            if isinstance(item.get("id"), str)
            and item["id"].startswith("agent-toolkit@")
            and item.get("enabled") is True
            and (
                item.get("scope") == "user"
                or (
                    item.get("scope") in ("project", "local")
                    and isinstance(item.get("projectPath"), str)
                    and pathlib.Path(item["projectPath"]).resolve() == project
                )
            )
        }
        if len(marketplaces) != 1:
            raise PluginRootError(
                "Claude Codeのagent-toolkitの有効なmarketplaceが一意でない。claude plugin list --jsonを確認する"
            )
        try:
            return _claude_selector()(data, name + "@" + next(iter(marketplaces)), project)
        except ValueError as error:
            raise PluginRootError(str(error)) from error
    if len(roots) != 1:
        raise PluginRootError(f"{host}の{name}導入先が一意でない: {len(roots)}件。{host} plugin list --jsonを確認する")
    root = next(iter(roots)).resolve()
    if not root.is_dir() or not (root / "skills").is_dir():
        raise PluginRootError(
            f"{host}の{name}導入先の実体またはskillsが無い: {root}。{host} plugin list --jsonで導入状態を確認し再導入する"
        )
    return root
