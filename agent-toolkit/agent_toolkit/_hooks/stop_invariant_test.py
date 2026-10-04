"""既存成果物と、生成・登録される契約との整合を検証する。"""

import importlib
import json
import shlex

from agent_toolkit._hooks.stop_test import _HOOKS_PATH


def test_registered_stop_checks_document_delegated_execution() -> None:
    manifest = json.loads(_HOOKS_PATH.read_text(encoding="utf-8"))
    module_names: list[str] = []
    for event_name in ("Stop", "SubagentStop"):
        for matcher_group in manifest["hooks"][event_name]:
            for hook in matcher_group["hooks"]:
                module_name = shlex.split(hook["command"])[-1]
                module = importlib.import_module(f"agent_toolkit._hooks.{module_name}")
                module_names.extend(getattr(module, "CHECK_MODULE_NAMES", (module_name,)))

    for module_name in module_names:
        docstring = importlib.import_module(f"agent_toolkit._hooks.{module_name}").__doc__ or ""
        assert any(line.startswith("委譲先での実行可否:") for line in docstring.splitlines()), module_name
