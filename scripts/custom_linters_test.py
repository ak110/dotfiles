"""validate-claude-pluginsの複数ファイル処理を確かめる。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


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
        "plugin validate --strict first.json",
        "plugin validate --strict second.json",
    ]
