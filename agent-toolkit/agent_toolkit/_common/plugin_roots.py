"""参照するプラグインの実体を、開発配置かホストの導入情報から解決する。"""

import functools
import json
import os
import pathlib

from agent_toolkit._common import host_homes, process_tree


class PluginRootError(ValueError):
    """導入情報と正式配置からプラグインrootを一意に確定できない。"""


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
        raise PluginRootError(f"Antigravityの正式な{name}生成rootが無いか論理名が異なる: {sibling}")
    return None


@functools.lru_cache(maxsize=8)
def resolve_plugin_root(name: str, toolkit_root: pathlib.Path, host: str = "claude") -> pathlib.Path:
    """導入版を一意に解決し、実在するskillsを持つrootだけを返す。"""
    if name == "agent-toolkit":
        return toolkit_root
    sibling = sibling_plugin_root(name, toolkit_root)
    if sibling is not None:
        return sibling
    if host == "codex":
        result = process_tree.run_with_timeout(
            ["codex", "plugin", "list", "--json"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        if result.returncode:
            raise PluginRootError(f"Codexの{name}導入情報の取得が失敗した: 終了コード{result.returncode}")
        installed = json.loads(result.stdout).get("installed", [])
        marketplaces = {
            item["marketplaceName"]
            for item in installed
            if item.get("name") == "agent-toolkit" and item.get("installed") is True and item.get("enabled") is True
        }
        if len(marketplaces) != 1:
            raise PluginRootError("Codexのagent-toolkitの有効なmarketplaceが一意でない")
        items = [
            item
            for item in installed
            if item.get("name") == name
            and item.get("marketplaceName") in marketplaces
            and item.get("installed") is True
            and item.get("enabled") is True
        ]
        home = pathlib.Path(os.environ.get("CODEX_HOME", pathlib.Path.home() / ".codex"))
        roots = {home / "plugins/cache" / item["marketplaceName"] / name / item["version"] for item in items}
    else:
        registry = host_homes.claude_config_dir() / "plugins/installed_plugins.json"
        data = json.loads(registry.read_text(encoding="utf-8"))
        plugins = data.get("plugins", {})
        marketplaces = {key.split("@", 1)[1] for key in plugins if key.startswith("agent-toolkit@")}
        if len(marketplaces) != 1:
            raise PluginRootError("Claude Codeのagent-toolkitのmarketplaceが一意でない")
        roots = {
            pathlib.Path(item["installPath"])
            for key, items in plugins.items()
            if key == name + "@" + next(iter(marketplaces))
            for item in items
            if isinstance(item.get("installPath"), str)
        }
    if len(roots) != 1:
        raise PluginRootError(f"{host}の{name}導入先が一意でない: {len(roots)}件")
    root = next(iter(roots)).resolve()
    if not root.is_dir() or not (root / "skills").is_dir():
        raise PluginRootError(f"{host}の{name}導入先の実体またはskillsが無い: {root}")
    return root
