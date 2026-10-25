"""起動層とatkの子プロセス起動が子孫へ渡す環境を観測する試験用の入口。

`scripts/launch.py`はplugin root配下のファイルを起動対象に受け付ける。atkが子を起動するときと同じく
`strip_inherited_venv`で起動元の仮想環境を除いた環境で、受け取ったargvを子として起動し、その終了コードを返す。
"""

import os
import subprocess
import sys

from agent_toolkit._common.inherited_venv import strip_inherited_venv

if __name__ == "__main__":
    environment = dict(os.environ)
    strip_inherited_venv(environment)
    sys.exit(subprocess.run(sys.argv[1:], env=environment, check=False).returncode)
