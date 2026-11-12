"""SessionStartとSubagentStartの登録を確かめる。"""

from __future__ import annotations

import json
import pathlib
import shlex

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_hooks_json_registers_rules_context_without_matcher() -> None:
    hooks = json.loads((_PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    for event in ("SessionStart", "SubagentStart"):
        registrations = [
            (group, shlex.split(hook["command"]))
            for group in hooks[event]
            for hook in group["hooks"]
            if hook["type"] == "command" and "rules_context" in shlex.split(hook["command"])
        ]
        assert registrations, event
        for group, command in registrations:
            assert "matcher" not in group
            assert "agent_toolkit/hook.py" in command
            assert command[-4:] == ["rules_context", "||", "exit", "1"]
