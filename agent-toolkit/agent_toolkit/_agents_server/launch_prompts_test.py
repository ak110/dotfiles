"""`_agents_server/launch_prompts.py`の振る舞いを検証する。"""

from __future__ import annotations

import os
import pathlib
import shlex
import subprocess
import sys

import pytest

from agent_toolkit._agents_server import launch_prompts
from agent_toolkit._common import plugin_roots


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
