"""管理対象一時領域CLIの旧呼び出し方を受け付ける。"""

from __future__ import annotations

import sys

from agent_toolkit._atk.managed_temp.cli import main  # noqa: E402  # pylint: disable=wrong-import-position

if __name__ == "__main__":
    sys.exit(main())
