#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""管理対象一時領域CLIの互換入口。"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _atk.managed_temp.cli import main  # noqa: E402  # pylint: disable=wrong-import-position

if __name__ == "__main__":
    sys.exit(main())
