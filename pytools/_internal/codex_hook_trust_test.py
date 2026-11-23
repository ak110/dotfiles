from pytools._internal import codex_hook_trust


def test_updates_only_enabled_hooks_from_the_deployed_bundle(tmp_path):
    cache = tmp_path / "cache"
    source = cache / "hooks" / "hooks.codex.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    bundle = codex_hook_trust.bundle_hash(cache)
    assert bundle is not None
    declaration = "sha256:" + "1" * 64
    plugin = "agent-toolkit@ak110-dotfiles"
    key = f"{plugin}:hooks/hooks.codex.json:SessionStart:0:0"
    entry = {
        "pluginId": plugin,
        "source": "plugin",
        "enabled": True,
        "isManaged": False,
        "trustStatus": "modified",
        "key": key,
        "sourcePath": str(source),
        "currentHash": f"{declaration}/{bundle}",
    }
    result = {"data": [{"hooks": [entry, {**entry, "pluginId": "other@market"}, {**entry, "enabled": False}]}]}
    assert codex_hook_trust.approval_updates(result, plugin, cache, bundle) == {
        key: {"trusted_hash": declaration, "trusted_content_hash": bundle}
    }
    source.write_text('{"changed":true}', encoding="utf-8")
    changed = codex_hook_trust.bundle_hash(cache)
    assert changed is not None and changed != bundle
    assert codex_hook_trust.approval_updates(result, plugin, cache, changed) is None
