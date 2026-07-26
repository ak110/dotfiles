"""pyfltrへ登録したリポジトリ固有linterの契約テスト。"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_custom_linter_paths_and_filename_contracts() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    commands = config["tool"]["pyfltr"]["custom-commands"]

    expected = {
        "check-cmd-encoding": False,
        "check-templates": True,
        "require-ps1-bom": True,
        "powershell-analyzer": True,
        "claude-plugin-validate": True,
        "check-agent-toolkit-total-size": False,
    }
    for name, pass_filenames in expected.items():
        definition = commands[name]
        assert definition["type"] == "linter"
        assert definition.get("pass-filenames", True) is pass_filenames
        executable = definition["path"]
        if executable.startswith("scripts/"):
            path = REPO_ROOT / executable
            assert path.is_file()
            assert os.access(path, os.X_OK)


def test_validate_claude_plugins_processes_each_file(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    stub = bin_dir / "claude"
    stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\n', encoding="utf-8")
    stub.chmod(0o755)
    env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "CALL_LOG": str(log)}

    result = subprocess.run(
        [str(REPO_ROOT / "scripts/validate-claude-plugins.sh"), "first.json", "second.json"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert log.read_text(encoding="utf-8").splitlines() == [
        "plugin validate first.json",
        "plugin validate second.json",
    ]
