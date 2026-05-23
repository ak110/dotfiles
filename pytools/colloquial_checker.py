# PYTHON_ARGCOMPLETE_OK
"""口語的な日本語表現を検査するCLIのラッパー。

agent-toolkitプラグイン同梱の`check_colloquial.py`をimportして`main()`を呼び出す。
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import sys
from typing import Any

from pytools._internal.cli import enable_completion

# pytools/ の親が dotfiles ルートに相当する。
_SCRIPT_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "agent-toolkit"
    / "skills"
    / "writing-standards"
    / "scripts"
    / "check_colloquial.py"
)


def main() -> int:
    """口語的な日本語表現を検査するラッパーのエントリポイント。"""
    # シェル補完（argcomplete）は pytools 側で完結させるため、check_colloquialと同形の
    # 最小parserを用意して`enable_completion`に渡す。argcompleteは`_ARGCOMPLETE`が
    # 設定された補完起動時のみ完了候補を出力して exit し、通常実行では no-op となる。
    completion_parser = argparse.ArgumentParser()
    completion_parser.add_argument("paths", nargs="+", type=pathlib.Path)
    enable_completion(completion_parser)

    spec = importlib.util.spec_from_file_location("check_colloquial", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.main())


if __name__ == "__main__":
    sys.exit(main())
