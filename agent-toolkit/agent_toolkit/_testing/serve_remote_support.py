import asyncio
import pathlib
import sys
import typing
from collections.abc import Awaitable, Callable

from agent_toolkit._testing import late_descendant


def disconnecting_resident_ssh(
    release: pathlib.Path, started: list[tuple[typing.Any, ...]]
) -> Callable[..., Awaitable[asyncio.subprocess.Process]]:
    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*cmd: typing.Any, **kwargs: typing.Any) -> asyncio.subprocess.Process:
        if "serve" not in cmd:
            return await real_exec(sys.executable, "-c", "print('[]')", **kwargs)
        started.append(cmd)
        script = "import os,sys; os.close(1); sys.stdin.read(); " + late_descendant.held_source(release)
        return await real_exec(sys.executable, "-c", script, **kwargs)

    return fake_exec
