#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytilpack[quart]>=1.47.0"]
# ///
"""Claude Code向けmanifestからCodex向けplugin JSONを生成する。"""

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
MCP_SOURCE = Path("agent-toolkit/.mcp.json")
PLUGIN_TARGET = Path("agent-toolkit/.codex-plugin/plugin.json")
MARKETPLACE_TARGET = Path(".agents/plugins/marketplace.json")
HOOKS_TARGET = Path("agent-toolkit/hooks/hooks.codex.json")

CODEX_PERMISSION_REQUEST_COMMAND = (
    "uv run --no-project --script ${CLAUDE_PLUGIN_ROOT}/scripts/claude_hook.py permissionrequest_codex"
)
CODEX_USER_PROMPT_SUBMIT_COMMAND = (
    "uv run --no-project --script ${CLAUDE_PLUGIN_ROOT}/scripts/claude_hook.py user_prompt_submit"
)
CODEX_STOP_COMMAND = "uv run --no-project --script ${CLAUDE_PLUGIN_ROOT}/scripts/claude_hook.py stop_advisor"
CODEX_HOOK_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "PermissionRequest": (CODEX_PERMISSION_REQUEST_COMMAND,),
    "UserPromptSubmit": (CODEX_USER_PROMPT_SUBMIT_COMMAND,),
    "Stop": (CODEX_STOP_COMMAND,),
}
CODEX_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "Stop",
    "SubagentStop",
    "SessionStart",
    "SessionEnd",
    "PermissionRequest",
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
    if (root / MCP_SOURCE).exists():
        codex_plugin["mcpServers"] = "./.mcp.json"
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


def sync(root: Path = REPO_ROOT) -> bool:
    """派生JSONを同期し、差分があった場合は`True`を返す。"""
    expected = _outputs(root)
    stale = [path for path, content in expected.items() if not (root / path).exists() or (root / path).read_text() != content]
    if HOOKS_TARGET not in expected and (root / HOOKS_TARGET).exists():
        stale.append(HOOKS_TARGET)
    for path, content in expected.items():
        if path in stale and not claude_common.atomic_write_text(root / path, content, tag="codex plugin manifests"):
            raise OSError(f"Codex plugin manifestの書き込みに失敗: {path}")
    if HOOKS_TARGET not in expected and (root / HOOKS_TARGET).exists():
        (root / HOOKS_TARGET).unlink()
    return bool(stale)


def main() -> int:
    """Codex向け派生manifestを冪等同期する。"""
    sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
