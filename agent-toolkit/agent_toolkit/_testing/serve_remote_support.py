"""`atk serve`の常駐SSHを子プロセスで代替する、計画ファイル画面とセッション画面の試験の補助。"""

import asyncio
import pathlib
import sys
import typing
from collections.abc import Awaitable, Callable

from agent_toolkit._testing import late_descendant


def disconnecting_resident_ssh(
    release: pathlib.Path, started: list[tuple[typing.Any, ...]]
) -> Callable[..., Awaitable[asyncio.subprocess.Process]]:
    """`asyncio.create_subprocess_exec`の代わりに、接続断を起こす常駐SSHを起動する関数を返す。

    常駐SSHは、標準出力を閉じて接続断を起こし、標準入力の終端を通知してから`release`の解放まで保持する子プロセスで代替し、
    起動したコマンドを`started`へ記録する。
    常駐SSHの前の単発SSH（Pythonの版の照会と、起動できるかの確認）は、導入済みの版が無い一覧を出力して成功する。
    """
    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*cmd: typing.Any, **kwargs: typing.Any) -> asyncio.subprocess.Process:
        if "serve" not in cmd:
            return await real_exec(sys.executable, "-c", "print('[]')", **kwargs)
        started.append(cmd)
        script = "import os,sys; os.close(1); sys.stdin.read(); " + late_descendant.held_source(release)
        return await real_exec(sys.executable, "-c", script, **kwargs)

    return fake_exec
