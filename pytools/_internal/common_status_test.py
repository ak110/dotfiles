"""共通回収の状態分類と、所有Popenのwait回収を実OSから隔離して検証する。"""

import subprocess
import typing

import psutil
import pytest
from agent_toolkit._testing.process_observation import ObservedProcess

from pytools._internal import common


@pytest.mark.parametrize("observation", ["running", "zombie", "dead", "pid-reused", "access-denied"])
def test_stop_process_tree_keeps_owned_wait_and_reports_only_live_descendants(monkeypatch, observation):
    child = ObservedProcess(state=observation, arguments=("fake-descendant",))
    parent = ObservedProcess(pid=2147483001, state="zombie", descendants=[child])

    class OwnedPopen:
        pid = parent.pid
        args = ("fake-owner",)
        returncode: int | None = None
        waits = []

        def wait(self, *, timeout=None):
            self.waits.append(timeout)
            self.returncode = 0
            return 0

        def poll(self):
            return self.returncode

        def kill(self):
            pytest.fail("終了した所有Popenへ強制終了を追加した")

    owned = OwnedPopen()
    clock = [0.0]

    def advance(_seconds, **_kwargs):
        clock[0] += 5.0

    monkeypatch.setattr(psutil, "Process", lambda _pid, **_kwargs: parent)
    monkeypatch.setattr(common.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(common.time, "sleep", advance)
    residual = common.stop_process_tree(typing.cast(subprocess.Popen, owned))
    assert residual == ([f"{child.pid}: fake-descendant"] if observation == "running" else [])
    assert child.calls == (["terminate", "kill"] if observation == "running" else ["terminate"])
    assert len(owned.waits) == 1 and owned.returncode == 0
