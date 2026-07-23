#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytilpack[quart]>=1.47.0"]
# ///
"""Claude Code向けmanifestからCodex向けplugin JSONを生成する。"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pytools._internal import claude_common  # pylint: disable=wrong-import-position  # noqa: E402

PLUGIN_SOURCE = Path("agent-toolkit/.claude-plugin/plugin.json")
MARKETPLACE_SOURCE = Path(".claude-plugin/marketplace.json")
HOOKS_SOURCE = Path("agent-toolkit/hooks/hooks.json")
PLUGIN_TARGET = Path("agent-toolkit/.codex-plugin/plugin.json")
MARKETPLACE_TARGET = Path(".agents/plugins/marketplace.json")
HOOKS_TARGET = Path("agent-toolkit/hooks/hooks.codex.json")

# 初期リリースでは入力契約まで確認できたhookがないため空とする。
CODEX_HOOK_ALLOWLIST: dict[str, tuple[str, ...]] = {}
CODEX_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "SessionStart",
    "SessionEnd",
}


def _load(root: Path, relative: Path) -> dict[str, Any]:
    value = json.loads((root / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON objectが必要: {relative}")
    return value


def _outputs(root: Path) -> dict[Path, str]:
    plugin = _load(root, PLUGIN_SOURCE)
    marketplace = _load(root, MARKETPLACE_SOURCE)
    hooks = _load(root, HOOKS_SOURCE)
    entries = [item for item in marketplace.get("plugins", []) if item.get("name") == plugin.get("name")]
    if len(entries) != 1:
        raise ValueError("Claude Code marketplaceのagent-toolkit entryは1件である必要がある")
    entry = entries[0]
    for key in ("version", "description"):
        if entry.get(key) != plugin.get(key):
            raise ValueError(f"正本間で{key}が一致しない")

    selected: dict[str, list[dict[str, Any]]] = {}
    source_hooks = hooks.get("hooks", {})
    for event, commands in CODEX_HOOK_ALLOWLIST.items():
        if event not in CODEX_EVENTS or event not in source_hooks:
            raise ValueError(f"未知のCodex hookイベント: {event}")
        projected = []
        for group in source_hooks[event]:
            handlers = group.get("hooks", [])
            chosen = [handler for handler in handlers if handler.get("command") in commands]
            if len(chosen) != len(commands):
                continue
            projected.append({**group, "hooks": chosen})
        if not projected:
            raise ValueError(f"許可済みhandlerが正本に存在しない: {event}")
        selected[event] = projected

    codex_plugin = {
        key: plugin[key]
        for key in ("name", "version", "description", "author", "homepage", "repository", "license", "keywords")
    }
    codex_plugin["skills"] = "./skills/"
    codex_plugin["hooks"] = "./hooks/hooks.codex.json" if selected else {"hooks": {}}
    codex_plugin["interface"] = {
        "displayName": "agent-toolkit",
        "shortDescription": "コード、文書、計画、レビューの作業指針",
        "developerName": "aki",
        "category": "Developer Tools",
        "capabilities": ["Skills"],
    }
    codex_marketplace = {
        "name": marketplace["name"],
        "interface": {"displayName": "ak110 dotfiles"},
        "plugins": [
            {
                "name": plugin["name"],
                "source": {"source": "local", "path": "./agent-toolkit"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Developer Tools",
            }
        ],
    }
    result = {
        PLUGIN_TARGET: json.dumps(codex_plugin, ensure_ascii=False, indent=2) + "\n",
        MARKETPLACE_TARGET: json.dumps(codex_marketplace, ensure_ascii=False, indent=2) + "\n",
    }
    if selected:
        result[HOOKS_TARGET] = json.dumps({"hooks": selected}, ensure_ascii=False, indent=2) + "\n"
    return result


def sync(root: Path = REPO_ROOT, *, check: bool = False) -> bool:
    """派生JSONを同期し、差分があった場合は`True`を返す。"""
    expected = _outputs(root)
    stale = [path for path, content in expected.items() if not (root / path).exists() or (root / path).read_text() != content]
    if HOOKS_TARGET not in expected and (root / HOOKS_TARGET).exists():
        stale.append(HOOKS_TARGET)
    if check:
        for path in stale:
            print(f"同期が必要: {path}")
        return bool(stale)
    for path, content in expected.items():
        if path in stale and not claude_common.atomic_write_text(root / path, content, tag="codex plugin manifests"):
            raise OSError(f"Codex plugin manifestの書き込みに失敗: {path}")
    if HOOKS_TARGET not in expected and (root / HOOKS_TARGET).exists():
        (root / HOOKS_TARGET).unlink()
    return bool(stale)


def main() -> int:
    """CLI引数を解析して同期または差分検査を実行する。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = sync(check=args.check)
    return int(changed) if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
