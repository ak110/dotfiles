"""共有依存環境のpyfltrを、その公開console entry pointから起動する。"""

import importlib.metadata
import os
from typing import cast

from agent_toolkit._common import inherited_venv


def main() -> int | None:
    """lockfileで準備したpyfltrへ、CLIとMCPの引数をそのまま渡す。"""
    # pyfltrが対象リポジトリのuvを起動するときは、そのリポジトリの環境を使う。
    inherited_venv.strip_inherited_venv(os.environ)
    entry = next(
        candidate
        for candidate in importlib.metadata.distribution("pyfltr").entry_points
        if candidate.group == "console_scripts" and candidate.name == "pyfltr"
    )
    return cast("int | None", entry.load()())


if __name__ == "__main__":
    raise SystemExit(main())
