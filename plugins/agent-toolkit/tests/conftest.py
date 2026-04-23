"""pytest conftest: scripts/ ディレクトリを sys.path に追加する。

`_stop_gate_test.py` など、scripts/ 配下のモジュールを直接 import するテストのため。
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
