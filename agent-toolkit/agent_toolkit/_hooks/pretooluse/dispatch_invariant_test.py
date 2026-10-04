"""両ホストのhook登録とagents_serverツール名の整合を検証する。"""

import json
import pathlib
import re

from agent_toolkit._hooks.pretooluse import agent_checks

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[3]


def test_pretooluse_matcher_covers_agents_server_tool_names() -> None:
    claude_matcher = json.loads((_PLUGIN_ROOT / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0][
        "matcher"
    ]
    codex_matcher = json.loads((_PLUGIN_ROOT / "hooks/hooks.codex.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"][0][
        "matcher"
    ]
    assert claude_matcher == "*"
    for tool_name in agent_checks.AGENTS_SERVER_HOOK_TOOL_NAMES:
        if tool_name.startswith("mcp__agents_server__"):
            assert codex_matcher == "*" or re.fullmatch(codex_matcher, tool_name), tool_name
