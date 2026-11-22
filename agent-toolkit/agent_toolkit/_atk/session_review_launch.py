"""hook終了後もWindowsの振り返り起動を回収する。"""

import pathlib
import subprocess
import sys

from agent_toolkit._common import session_review, windows_job


def main() -> int:
    """短命なhookでなく本プロセスが、振り返りCLIの終了を保存する。"""
    target = pathlib.Path(__file__).resolve().parents[1] / "atk.py"
    code = 1
    try:
        # 子の実行時間の上限はrun-skillが持つ。ここは子の終了を回収するだけ。
        code = subprocess.run(
            [sys.executable, str(target), *sys.argv[1:]],
            stdin=subprocess.DEVNULL,
            creationflags=windows_job.CREATE_NO_WINDOW,
            check=False,
        ).returncode
        return code
    finally:
        session_review.launch_exited(code)


if __name__ == "__main__":
    sys.exit(main())
