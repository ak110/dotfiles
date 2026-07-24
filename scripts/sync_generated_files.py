#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""リポジトリ内の自動生成ファイルを一括同期する。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATORS = (
    "scripts/sync_codex_plugin_manifests.py",
    "scripts/gen-completions.py",
    "scripts/gen-install-files.py",
    "scripts/sync_codex_agents.py",
)


def run_generator(path: str) -> int:
    """個別生成器を現在のPythonで実行する。"""
    return subprocess.run([sys.executable, path], cwd=REPO_ROOT, check=False).returncode


def main() -> int:
    """全生成器を固定順で実行し、失敗を集約する。"""
    failures: list[str] = []
    for generator in GENERATORS:
        if run_generator(generator) != 0:
            failures.append(generator)
    if failures:
        print("生成に失敗: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
