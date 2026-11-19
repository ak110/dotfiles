"""プラグインを別プロセスで実行する。"""

import pathlib
import subprocess
import sys


def run_plugin(name: str, text: str) -> str:
    """プラグインのファイルを子プロセスで実行し、標準出力を返す。"""
    script = pathlib.Path(__file__).with_name(f"{name}.py")
    return subprocess.run(
        [sys.executable, str(script)],
        input=text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout
