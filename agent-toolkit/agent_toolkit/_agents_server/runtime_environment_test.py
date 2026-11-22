"""並行会話とバックグラウンドtask・同期workerで環境の帰属を保つ。"""

import asyncio
import os
import pathlib

import pytest

from agent_toolkit._agents_server import child_environment
from agent_toolkit._common import runtime_environment


@pytest.mark.asyncio
async def test_parallel_callers_keep_environment_in_tasks_and_workers(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CALLER_MARKER", "host")
    monkeypatch.setattr(child_environment, "_INHERITED", {"LEGACY_AUTH_MARKER": "legacy"})
    gate = asyncio.Event()

    async def observe() -> tuple[str, str, pathlib.Path]:
        await gate.wait()
        env = await asyncio.to_thread(child_environment.inherited)
        assert "LEGACY_AUTH_MARKER" not in env
        return env["CALLER_MARKER"], env["HTTP_PROXY"], runtime_environment.home()

    tasks = []
    for name in ("first", "second"):
        home = tmp_path / name
        env = {"HOME": str(home), "USERPROFILE": str(home), "CALLER_MARKER": name, "HTTP_PROXY": f"proxy-{name}"}
        with runtime_environment.bind(runtime_environment.CallerContext(env, tmp_path)):
            tasks.append(asyncio.create_task(observe()))
        env["CALLER_MARKER"] = "changed-after-bind"
    gate.set()
    assert await asyncio.gather(*tasks) == [
        ("first", "proxy-first", tmp_path / "first"),
        ("second", "proxy-second", tmp_path / "second"),
    ]
    assert os.environ["CALLER_MARKER"] == "host"
    assert runtime_environment.current() is None
