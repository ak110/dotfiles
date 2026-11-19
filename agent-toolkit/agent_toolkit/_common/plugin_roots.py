"""参照するプラグインの実体を、開発配置かホストの導入情報から解決する。"""

import contextlib
import contextvars
import dataclasses
import functools
import json
import os
import pathlib
import re
import runpy
import shlex
import shutil
import subprocess
from collections.abc import Generator, Iterable
from typing import Any

from agent_toolkit._common import process_tree, runtime_environment


class PluginRootError(ValueError):
    """導入情報と正式配置からプラグインrootを一意に確定できない。"""


@dataclasses.dataclass(frozen=True)
class SelectedInstallation:
    """Claude Codeの導入一覧から選んだ導入先。資料が欠けた場合に修復する導入を示す。"""

    plugin_id: str
    scope: str
    project: pathlib.Path
    root: pathlib.Path

    def missing_error(self, missing: Iterable[pathlib.Path]) -> PluginRootError:
        """不足資料と、選んだscopeのまま導入を確認・修復する公開CLIを示す。"""
        relative = ", ".join(str(path.relative_to(self.root)) for path in missing)
        return PluginRootError(
            f"Claude Codeの{self.plugin_id}で選択した導入先に必須資料が無い: scope={self.scope} "
            f"project={self.project} 導入先={self.root} 不足資料={relative}。"
            "別のscopeの導入へは切り替えない。"
            f"`{self.project}`をcwdにして`claude plugin list --json`で{self.plugin_id}の導入scopeと導入先を確認し、"
            f"同じcwdで`claude plugin update {self.plugin_id} --scope {self.scope}`で更新するか、"
            f"不要な導入なら`claude plugin uninstall {self.plugin_id} --scope {self.scope}`で外す。修復後にstartを再実行する"
        )


_inventories: contextvars.ContextVar[dict[tuple[str, pathlib.Path], object] | None] = contextvars.ContextVar(
    "plugin_root_inventories", default=None
)
_selected: contextvars.ContextVar[dict[pathlib.Path, SelectedInstallation] | None] = contextvars.ContextVar(
    "plugin_root_selected", default=None
)


def raise_for_incomplete_installation(missing: Iterable[pathlib.Path]) -> None:
    """要求内で選んだ導入先の配下に欠けた資料があれば、修復する導入を示して失敗させる。

    導入先の外の欠落は文書の誤りとして、呼出し元の診断へ任せる。
    """
    paths = tuple(missing)
    for root, installation in (_selected.get() or {}).items():
        inside = [path for path in paths if path.is_relative_to(root)]
        if inside:
            raise installation.missing_error(inside)


def _remember_claude_selection(data: list, plugin_id: str, project: pathlib.Path, root: pathlib.Path) -> None:
    """選別結果のrootを指し、projectへ適用される導入一覧の項目のうち、選別が採るscopeを記録する。"""
    selected = _selected.get()
    if selected is None:
        return
    scopes = [
        item["scope"]
        for item in data
        if item.get("id") == plugin_id
        and isinstance(item.get("scope"), str)
        and (
            item["scope"] == "user"
            or (isinstance(item.get("projectPath"), str) and pathlib.Path(item["projectPath"]).resolve() == project)
        )
        # select_claude_plugin と同じく、配布元フォルダーの読込を示す`readFromFolder`をinstallPathより優先する。
        and isinstance(path := item.get("readFromFolder") or item.get("installPath"), str)
        and pathlib.Path(path).resolve() == root
    ]
    # 同じrootを複数scopeが指す場合（同じ版のcacheや`readFromFolder`）は、select_claude_pluginと同じく
    # local、project、userの順に優先し、選別が採らなかったscopeの修復を案内しない。
    if precedence := [scope for scope in ("local", "project", "user") if scope in scopes]:
        selected[root] = SelectedInstallation(plugin_id, precedence[0], project, root)


@contextlib.contextmanager
def plugin_resolution_scope() -> Generator[None]:
    """1回の要求の中で、hostの導入一覧と選んだ導入先を共有する。"""
    if _inventories.get() is not None:
        yield
        return
    token = _inventories.set({})
    selected_token = _selected.set({})
    try:
        yield
    finally:
        _selected.reset(selected_token)
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
    data = (
        {"installed": codex_installed_plugins(host, project)}
        if host == "codex"
        else _host_json(host, [host, "plugin", "list", "--json"], project)
    )
    if cache is not None:
        cache[key] = data
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
            env=dict(runtime_environment.environment()),
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
    if generator := shutil.which("update-dotfiles", path=runtime_environment.environment().get("PATH", os.defpath)):
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
            runtime_environment.environment().get("OPENCODE_CONFIG_DIR")
            or pathlib.Path(runtime_environment.environment().get("XDG_CONFIG_HOME") or runtime_environment.home() / ".config")
            / "opencode"
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
    config = runtime_environment.home() / ".gemini/config"
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
    caller = runtime_environment.current()
    project = pathlib.Path(project or (caller.cwd if caller else pathlib.Path.cwd())).resolve()
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
        home = pathlib.Path(runtime_environment.environment().get("CODEX_HOME", runtime_environment.home() / ".codex"))
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
            and any(
                isinstance(item.get(field), str) and pathlib.Path(item[field]).resolve() == toolkit_root.resolve()
                for field in ("installPath", "readFromFolder")
            )
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
        plugin_id = name + "@" + next(iter(marketplaces))
        try:
            selected = _claude_selector()(data, plugin_id, project)
        except ValueError as error:
            raise PluginRootError(str(error)) from error
        _remember_claude_selection(data, plugin_id, project, selected)
        return selected
    if len(roots) != 1:
        raise PluginRootError(f"{host}の{name}導入先が一意でない: {len(roots)}件。{host} plugin list --jsonを確認する")
    root = next(iter(roots)).resolve()
    if not root.is_dir() or not (root / "skills").is_dir():
        raise PluginRootError(
            f"{host}の{name}導入先の実体またはskillsが無い: {root}。{host} plugin list --jsonで導入状態を確認し再導入する"
        )
    return root
