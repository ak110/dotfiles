"""hook終了後もWindowsの振り返り起動を回収する。"""

import pathlib
import subprocess
import sys

from agent_toolkit._common import session_review, windows_job


def main() -> int:
    target = pathlib.Path(__file__).resolve().parents[1] / "atk.py"
    code = 1
    try:
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
