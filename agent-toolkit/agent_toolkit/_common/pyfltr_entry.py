"""共有依存環境のpyfltrを、その公開console entry pointから起動する。"""

import importlib.metadata
import os
import sys
from pathlib import Path
from typing import cast

from agent_toolkit._common import inherited_venv


def main() -> int | None:
    """lockfileで準備したpyfltrへ、CLIとMCPの引数をそのまま渡す。"""
    # pyfltrが対象リポジトリのuvを起動するときは、そのリポジトリの環境を使う。
    inherited_venv.strip_inherited_venv(os.environ)
    # 同一プロセスのpyfltrは同梱ツールを使い、uv runnerは対象projectの環境を選ぶ。
    scripts = Path(sys.executable).absolute().parent
    remaining = [
        entry for entry in os.environ.get("PATH", "").split(os.pathsep) if not entry or Path(entry).absolute() != scripts
    ]
    os.environ["PATH"] = os.pathsep.join([str(scripts), *remaining])
    entry = next(
        candidate
        for candidate in importlib.metadata.distribution("pyfltr").entry_points
        if candidate.group == "console_scripts" and candidate.name == "pyfltr"
    )
    return cast("int | None", entry.load()())


if __name__ == "__main__":
    raise SystemExit(main())
