"""所有managerと実cwd保持子の、準備と後始末の失敗経路を検証する。"""

import contextlib
import os
import pathlib
import subprocess
from collections.abc import Generator
from typing import Any

import pytest

from agent_toolkit._testing import session_release_support as subject


@pytest.mark.parametrize("failure", ["prepare", "close"])
@pytest.mark.asyncio
async def test_retained_executor_releases_child_when_manager_fails(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """yield前の準備とmanager解放の例外で、所有済み実子と全pipeを回収する。"""
    acquired: list[subprocess.Popen[Any]] = []
    real_scope = subject.process_tree.process_scope

    @contextlib.contextmanager
    def scope(*args: Any, **kwargs: Any) -> Generator[subprocess.Popen[Any]]:
        with real_scope(*args, **kwargs) as process:
            acquired.append(process)
            yield process

    class FailingBackend(subject.FakeBackend):
        async def start(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise RuntimeError("所有managerの準備失敗")

    class FailingManager(subject.server_manager.AgentsServerManager):
        async def close(self) -> None:
            await super().close()
            raise RuntimeError("所有managerの解放失敗")

    monkeypatch.setattr(subject.process_tree, "process_scope", scope)
    if failure == "prepare":
        monkeypatch.setattr(subject, "FakeBackend", FailingBackend)
    else:
        monkeypatch.setattr(subject.server_manager, "AgentsServerManager", FailingManager)
    with pytest.raises(RuntimeError, match="所有manager"):
        async with subject.retained_executor(tmp_path, dict(os.environ), monkeypatch):
            assert failure == "close"
    assert len(acquired) == 1
    child = acquired[0]
    assert child.poll() is not None
    assert child.stdin is not None and child.stdin.closed
    assert child.stdout is not None and child.stdout.closed
