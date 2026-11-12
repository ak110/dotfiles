"""`_agents_server/launch_prompts.py`の振る舞いを検証する。"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shlex
import subprocess
import sys
from types import ModuleType

import pytest

from agent_toolkit._agents_server import claude_options, launch_prompts
from agent_toolkit._common import plugin_roots
from agent_toolkit._testing.agents_server_claude_support import _capture_options


@pytest.mark.skipif(os.name == "nt", reason="POSIX shellによる実行の受入例")
def test_python_runtime_information_runs_json_without_python_on_path(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配送された起動部分から空白・引用符を含むパスのPythonでJSONを照会する。"""
    executable = tmp_path / "Python's runtime with spaces"
    executable.symlink_to(sys.executable)
    monkeypatch.setattr(sys, "executable", str(executable))
    instructions = launch_prompts.python_runtime_instructions()
    prefix = "POSIX shellでの起動部分: "
    command = next(line.removeprefix(prefix) for line in instructions.splitlines() if line.startswith(prefix))
    code = 'import json; print(json.loads("[3, 7]")[1])'
    result = subprocess.run(
        ["/bin/sh", "-c", command + " -c " + shlex.quote(code)],
        env={"PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == "7\n"
    assert not result.stderr


@pytest.mark.parametrize("existing", [False, True])
def test_plugin_resource_instructions_reject_invalid_toolkit_root(tmp_path: pathlib.Path, existing: bool) -> None:
    """所在案内は、実在するskillsを持たないtoolkitを配送しない。"""
    root = tmp_path / "toolkit"
    if existing:
        root.mkdir()
    with pytest.raises(plugin_roots.PluginRootError, match="agent-toolkitの実在するskills"):
        launch_prompts.plugin_resource_instructions(root, "codex", str(tmp_path))


def _load_prompt_consumer(path: pathlib.Path, name: str) -> ModuleType:
    """実際のimport時の結線を、既存モジュールを再初期化せず試験用の名前空間へ読み込む。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.usefixtures("agents_server_isolation")
def test_shared_mode_documents_reach_sdk_with_distinct_inputs(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """区分別の文書入力が実際の読込・import・合成を通り、別区分の本文を含まずSDKへ届く。"""
    package = tmp_path / "package"
    modules = package / "agent_toolkit" / "_agents_server"
    modules.mkdir(parents=True)
    share = package / "share"
    share.mkdir()
    inputs = {
        name: f"文書識別:{name}:{tmp_path.name}"
        for name in ("delegate", "explore", "shell", "write", "base", "delegate-notice", "auto-resume")
    }
    for name, marker in inputs.items():
        (share / f"agents-server-{name}.md").write_text(f"# 入力文書\n\n{marker}\n", encoding="utf-8")
    rules = share / "rules"
    rules.mkdir()
    result_marker = "返却契約識別:" + tmp_path.name
    rules_marker = "通常規範識別:" + tmp_path.name
    (rules / "rules-subagent.md").write_text(
        f"{rules_marker}\n<!-- subagent-result-contract:start -->\n{result_marker}\n<!-- subagent-result-contract:end -->\n",
        encoding="utf-8",
    )
    (rules / "rules-subagent.claude-code.md").write_text("Claude固有識別", encoding="utf-8")
    for source in (launch_prompts, claude_options):
        assert source.__file__ is not None
        path = pathlib.Path(source.__file__)
        (modules / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    prompts = _load_prompt_consumer(modules / "launch_prompts.py", "mode_contract_prompts")
    monkeypatch.setitem(sys.modules, launch_prompts.__name__, prompts)
    consumer = _load_prompt_consumer(modules / "claude_options.py", "mode_contract_options")
    captured = _capture_options(monkeypatch)
    monkeypatch.setattr(consumer, "_parent_settings", lambda: None)

    for mode in ("delegate", "explore", "shell", "write"):
        consumer._build_options(str(tmp_path), "model", "medium", launch_kind=mode)  # pylint: disable=protected-access
        delivered = captured["system_prompt"]
        assert isinstance(delivered, str)
        assert inputs[mode] in delivered, mode
        for other in {"delegate", "explore", "shell", "write"} - {mode}:
            assert inputs[other] not in delivered, (mode, other)
        assert inputs["delegate-notice"] in delivered
        assert inputs["auto-resume"] in delivered
        assert result_marker in delivered
        assert (inputs["base"] in delivered) is (mode == "delegate")
        assert (rules_marker in delivered) is (mode == "delegate")
