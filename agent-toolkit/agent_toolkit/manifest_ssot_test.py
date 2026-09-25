"""Claude Code向け正本manifest間のSSOT整合性のテスト。

version / description / nameを`agent-toolkit/.claude-plugin/plugin.json`と
`.claude-plugin/marketplace.json`の2箇所で重複管理しているため、
片方だけ更新して配布されない事故を防ぐ。
Codex向け派生manifestは`scripts/sync_codex_plugin_manifests.py`が検証する。
"""

import json
import pathlib

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PLUGIN_MANIFEST = _PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
_MARKETPLACE_MANIFEST = _PLUGIN_ROOT.parent / ".claude-plugin" / "marketplace.json"


def test_plugin_manifest_matches_marketplace() -> None:
    """marketplaceの該当エントリがplugin.jsonと同じversion・description・nameを持つ。"""
    plugin_manifest = json.loads(_PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    marketplace = json.loads(_MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))

    entries = [p for p in marketplace["plugins"] if p["name"] == plugin_manifest["name"]]
    assert len(entries) == 1, f"marketplace.json に {plugin_manifest['name']} のエントリが 1 件ではない"
    entry = entries[0]

    # 不一致が出たら両側のversion・description・nameを揃える。
    assert entry["version"] == plugin_manifest["version"], (
        f"version 不一致: plugin.json={plugin_manifest['version']} marketplace.json={entry['version']}"
    )
    assert entry["description"] == plugin_manifest["description"], (
        "description 不一致: plugin.json と marketplace.json を揃えること"
    )
    assert entry["name"] == plugin_manifest["name"]
