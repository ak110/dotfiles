"""SessionStartとSubagentStartの登録を確かめる。"""

from __future__ import annotations

import json
import pathlib

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_hooks_json_registers_rules_context_without_matcher() -> None:
    hooks = json.loads((_PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    for event in ("SessionStart", "SubagentStart"):
        assert "matcher" not in hooks[event][0]
        assert hooks[event][0]["hooks"][0]["command"].endswith("hook.py rules_context")
