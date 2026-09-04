#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytilpack[quart]>=1.47.0"]
# ///
"""Claude Code向けmanifestからAgent Plugins・Codex向けJSONを生成する。"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pytools._internal import claude_common  # pylint: disable=wrong-import-position  # noqa: E402

PLUGIN_SOURCE = Path("agent-toolkit/.claude-plugin/plugin.json")
MARKETPLACE_SOURCE = Path(".claude-plugin/marketplace.json")
HOOKS_SOURCE = Path("agent-toolkit/hooks/hooks.json")
MCP_SOURCE = Path("agent-toolkit/.mcp.json")
MCP_CODEX_TARGET = Path("agent-toolkit/.mcp.codex.json")
AGENT_PLUGIN_TARGET = Path("agent-toolkit/plugin.json")
AGENT_MCP_TARGET = Path("agent-toolkit/mcp.json")
PLUGIN_TARGET = Path("agent-toolkit/.codex-plugin/plugin.json")
MARKETPLACE_TARGET = Path(".agents/plugins/marketplace.json")
HOOKS_TARGET = Path("agent-toolkit/hooks/hooks.codex.json")
OPTIONAL_TARGETS = frozenset((MCP_CODEX_TARGET, AGENT_MCP_TARGET, HOOKS_TARGET))
SHARED_MCP_SERVER_NAMES = frozenset({"pyfltr", "agents_server"})


def _hook_command(name: str) -> str:
    return f"uv run --no-project --script ${{CLAUDE_PLUGIN_ROOT}}/scripts/hook.py {name}"


CODEX_PERMISSION_REQUEST_COMMAND = _hook_command("permissionrequest_codex")
CODEX_USER_PROMPT_SUBMIT_COMMAND = _hook_command("user_prompt_submit")
CODEX_PRE_TOOL_USE_COMMAND = _hook_command("pretooluse")
CODEX_POST_TOOL_USE_COMMAND = _hook_command("posttooluse")
CODEX_SUBAGENT_STOP_COMMAND = _hook_command("subagent_stop_advisor")
CODEX_SESSION_END_COMMAND = _hook_command("session_end_cleanup")
CODEX_QUALITY_CHECKPOINT_COMMAND = _hook_command("quality_checkpoint")

# CodexのSessionEndは同期実行のため上限が短い。投影時に明示して超過を避ける。
CODEX_SESSION_END_TIMEOUT_SECONDS = 3


class CodexHookProjection(NamedTuple):
    """Codexへ射影するhandlerと、ホスト差に合わせた上書き値。

    `matcher`が`None`の場合は正本のmatcherをそのまま引き継ぐ。
    Claude向けの空matcher（全ツール対象）をそのまま配布すると、
    入力契約を確認していないCodexのツールでもhandlerが起動するため、
    ツール名を限定する場合は明示する。
    """

    commands: tuple[str, ...]
    matcher: str | None = None
    timeout: int | None = None
    output_command: str | None = None

    def project(self, group: dict[str, Any], handlers: list[dict[str, Any]]) -> dict[str, Any]:
        """正本のmatcher groupへ上書き値を適用した射影結果を返す。"""
        chosen = []
        for handler in handlers:
            projected_handler = dict(handler)
            if self.output_command is not None:
                projected_handler["command"] = self.output_command
            if self.timeout is not None:
                projected_handler["timeout"] = self.timeout
            chosen.append(projected_handler)
        projected = {**group, "hooks": chosen}
        if self.matcher is not None:
            projected["matcher"] = self.matcher
        return projected


CODEX_HOOK_ALLOWLIST: dict[str, CodexHookProjection] = {
    "PreToolUse": CodexHookProjection(
        (CODEX_PRE_TOOL_USE_COMMAND,),
        matcher="Bash|Edit|Write|mcp__agents_server__start|mcp__agents_server__start_explore|mcp__agents_server__start_shell|mcp__agents_server__send_message|mcp__agents_server__kill",
    ),
    "PostToolUse": CodexHookProjection(
        (CODEX_POST_TOOL_USE_COMMAND,),
        matcher="Edit|Write|mcp__agents_server__start|mcp__agents_server__start_explore|mcp__agents_server__start_shell|mcp__agents_server__wait|mcp__agents_server__send_message|mcp__agents_server__kill",
    ),
    "PermissionRequest": CodexHookProjection(
        (_hook_command("permissionrequest"),),
        matcher="Bash",
        output_command=CODEX_PERMISSION_REQUEST_COMMAND,
    ),
    "UserPromptSubmit": CodexHookProjection((CODEX_USER_PROMPT_SUBMIT_COMMAND,)),
    "SubagentStop": CodexHookProjection((CODEX_SUBAGENT_STOP_COMMAND,)),
    "SessionEnd": CodexHookProjection((CODEX_SESSION_END_COMMAND,), timeout=CODEX_SESSION_END_TIMEOUT_SECONDS),
}
CODEX_ONLY_HOOKS: dict[str, list[dict[str, Any]]] = {
    "SessionStart": [
        {
            "matcher": "compact",
            "hooks": [{"type": "command", "command": CODEX_QUALITY_CHECKPOINT_COMMAND}],
        }
    ]
}
# Codex 0.147.0が発火するhookイベント。handlerを持たないイベントは生成しない。
CODEX_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "PostCompact",
    "PermissionRequest",
}
AGENT_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
AGENT_MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
PLUGIN_METADATA_FIELDS = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)
STDIO_SOURCE_FIELDS = {"command", "args", "env", "cwd"}


def _replace_plugin_root(value: str) -> str:
    return value.replace("${CLAUDE_PLUGIN_ROOT}", "${PLUGIN_ROOT}")


def _load(root: Path, relative: Path) -> dict[str, Any]:
    value = json.loads((root / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON objectが必要: {relative}")
    return value


def _agent_mcp(source: dict[str, Any]) -> dict[str, Any]:
    if set(source) != {"mcpServers"} or not isinstance(source["mcpServers"], dict):
        raise ValueError("MCP正本はmcpServersだけを持つJSON objectである必要がある")

    servers: dict[str, dict[str, Any]] = {}
    for name, value in source["mcpServers"].items():
        if not isinstance(value, dict):
            raise ValueError(f"MCP serverはJSON objectである必要がある: {name}")
        unknown = set(value) - STDIO_SOURCE_FIELDS
        if unknown:
            fields = ", ".join(sorted(unknown))
            raise ValueError(f"stdioへ変換できないMCP field: {name}: {fields}")
        command = value.get("command")
        args = value.get("args", [])
        env = value.get("env")
        cwd = value.get("cwd")
        if not isinstance(command, str):
            raise ValueError(f"MCP commandは文字列である必要がある: {name}")
        command = _replace_plugin_root(command)
        if not command:
            raise ValueError(f"MCP commandは空文字列にできない: {name}")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise ValueError(f"MCP argsは文字列の配列である必要がある: {name}")
        args = [_replace_plugin_root(item) for item in args]
        if env is not None and (
            not isinstance(env, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in env.items())
        ):
            raise ValueError(f"MCP envは文字列を値に持つJSON objectである必要がある: {name}")
        if env is not None:
            env = {key: _replace_plugin_root(item) for key, item in env.items()}
        if env is not None and {"PLUGIN_ROOT", "PLUGIN_DATA"} & set(env):
            raise ValueError(f"MCP envにAgent Pluginsの予約名は指定できない: {name}")
        if cwd is not None:
            if not isinstance(cwd, str):
                raise ValueError(f"MCP cwdは文字列である必要がある: {name}")
            cwd = _replace_plugin_root(cwd)
            if not (
                cwd.startswith("./")
                or cwd == "${PLUGIN_ROOT}"
                or cwd.startswith("${PLUGIN_ROOT}/")
                or cwd == "${PLUGIN_DATA}"
                or cwd.startswith("${PLUGIN_DATA}/")
            ):
                raise ValueError(f"MCP cwdはAgent Plugins schemaのpatternに一致する必要がある: {name}")

        server: dict[str, Any] = {"type": "stdio", "command": command}
        if "args" in value:
            server["args"] = args
        if env is not None:
            server["env"] = env
        if cwd is not None:
            server["cwd"] = cwd
        servers[name] = server
    return {"$schema": AGENT_MCP_SCHEMA, "mcpServers": servers}


def _outputs(root: Path) -> dict[Path, str]:
    plugin = _load(root, PLUGIN_SOURCE)
    marketplace = _load(root, MARKETPLACE_SOURCE)
    entries = [item for item in marketplace.get("plugins", []) if item.get("name") == plugin.get("name")]
    if len(entries) != 1:
        raise ValueError("Claude Codeのmarketplaceのagent-toolkitエントリは1件である必要がある")
    entry = entries[0]
    for key in ("version", "description"):
        if entry.get(key) != plugin.get(key):
            raise ValueError(f"正本間で{key}が一致しない")

    selected: dict[str, list[dict[str, Any]]] = {}
    if (root / HOOKS_SOURCE).exists():
        hooks = _load(root, HOOKS_SOURCE)
        source_hooks = hooks.get("hooks", {})
        collisions = (set(CODEX_ONLY_HOOKS) & set(source_hooks)) | (set(CODEX_ONLY_HOOKS) & set(CODEX_HOOK_ALLOWLIST))
        if collisions:
            events = ", ".join(sorted(collisions))
            raise ValueError(f"Codex専用hookイベントが共有射影と衝突: {events}")
        for event, projection in CODEX_HOOK_ALLOWLIST.items():
            if event not in CODEX_EVENTS or event not in source_hooks:
                raise ValueError(f"未知のCodex hookイベント: {event}")
            projected = []
            for group in source_hooks[event]:
                handlers = group.get("hooks", [])
                chosen = [handler for handler in handlers if handler.get("command") in projection.commands]
                if len(chosen) != len(projection.commands):
                    continue
                projected.append(projection.project(group, chosen))
            if not projected:
                raise ValueError(f"許可済みハンドラーが正本に存在しない: {event}")
            selected[event] = projected

        selected.update(CODEX_ONLY_HOOKS)

    metadata = {key: plugin[key] for key in PLUGIN_METADATA_FIELDS}
    agent_plugin = {"$schema": AGENT_PLUGIN_SCHEMA, **metadata}
    codex_plugin = dict(metadata)
    codex_plugin["skills"] = "./skills/"
    codex_plugin["hooks"] = "./hooks/hooks.codex.json" if selected else {"hooks": {}}
    if (root / MCP_SOURCE).exists():
        codex_plugin["mcpServers"] = "./.mcp.codex.json"
    codex_plugin["interface"] = {
        "displayName": "agent-toolkit",
        "shortDescription": "コード、文書、計画、レビューの作業指針",
        "longDescription": (
            "コード、文書、計画、レビューの各工程に共通の作業指針を提供する。"
            "計画の起草から実装、レビュー、AWI処理までを一貫した手順として扱う。"
        ),
        "developerName": "aki",
        "category": "Developer Tools",
        "capabilities": ["Skills"],
        "defaultPrompt": [
            "このリポジトリの変更を計画にまとめて",
            "直前の変更をレビューして",
            "溜まっているAWIを処理して",
        ],
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
        AGENT_PLUGIN_TARGET: json.dumps(agent_plugin, ensure_ascii=False, indent=2) + "\n",
        PLUGIN_TARGET: json.dumps(codex_plugin, ensure_ascii=False, indent=2) + "\n",
        MARKETPLACE_TARGET: json.dumps(codex_marketplace, ensure_ascii=False, indent=2) + "\n",
    }
    if (root / MCP_SOURCE).exists():
        source = _load(root, MCP_SOURCE)
        # 正本全体のschemaを先に検証する。Codex向けへ射影しないClaude専用serverも
        # 不正な定義を残したままにしないため、allowlist適用前に検査する。
        _agent_mcp(source)
        servers = source.get("mcpServers")
        if not isinstance(servers, dict):
            raise ValueError("MCP正本はmcpServers objectを持つ必要がある")
        shared = {name: value for name, value in servers.items() if name in SHARED_MCP_SERVER_NAMES}
        shared_source = {"mcpServers": shared}
        projected = json.dumps(_agent_mcp(shared_source), ensure_ascii=False, indent=2) + "\n"
        result[MCP_CODEX_TARGET] = projected
        result[AGENT_MCP_TARGET] = projected
    if selected:
        result[HOOKS_TARGET] = json.dumps({"hooks": selected}, ensure_ascii=False, indent=2) + "\n"
    return result


def _existing_outputs(root: Path, expected: dict[Path, str]) -> dict[Path, str]:
    """既知の派生JSONのうち、現存する内容を返す。"""
    paths = set(expected) | OPTIONAL_TARGETS
    return {path: (root / path).read_text(encoding="utf-8") for path in paths if (root / path).exists()}


def _differences(expected: dict[Path, str], existing: dict[Path, str]) -> tuple[Path, ...]:
    """期待集合と現存集合の内容差、欠落、optional targetの残存を返す。"""
    paths = set(expected) | OPTIONAL_TARGETS
    return tuple(sorted((path for path in paths if expected.get(path) != existing.get(path)), key=str))


def sync(root: Path = REPO_ROOT) -> bool:
    """派生JSONを同期し、差分があった場合は`True`を返す。"""
    expected = _outputs(root)
    stale = _differences(expected, _existing_outputs(root, expected))
    for path, content in expected.items():
        if path in stale and not claude_common.atomic_write_text(root / path, content, tag="plugin manifests"):
            raise OSError(f"派生JSONの書き込みに失敗: {path}")
    for path in OPTIONAL_TARGETS - set(expected):
        (root / path).unlink(missing_ok=True)
    return bool(stale)


def check(root: Path = REPO_ROOT) -> bool:
    """派生JSONを変更せず、期待内容と一致する場合は`True`を返す。"""
    expected = _outputs(root)
    return not _differences(expected, _existing_outputs(root, expected))


def main(argv: list[str] | None = None) -> int:
    """通常同期又は非変更検査を実行する。"""
    parser = argparse.ArgumentParser(description="Agent Plugins・Codex向け派生JSONを同期する。")
    parser.add_argument("--check", action="store_true", help="派生JSONを変更せず整合性だけを検査する")
    args = parser.parse_args(argv)
    if args.check:
        return 0 if check(REPO_ROOT) else 1
    sync(REPO_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
