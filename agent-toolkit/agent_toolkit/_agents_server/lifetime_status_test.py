"""stdio監視の追跡済み・新規子孫・親消滅の状態境界を検証する。"""

import threading
import typing

import psutil
import pytest

from agent_toolkit._agents_server import lifetime
from agent_toolkit._testing.process_observation import ObservedProcess


@pytest.mark.parametrize("route", ["tracked", "discovered", "parent"])
@pytest.mark.parametrize("observation", ["running", "zombie", "dead", "pid-reused", "access-denied"])
def test_lifetime_monitor_filters_children_and_stops_for_ended_parent(monkeypatch, route, observation):
    """実signal・stdio・threadを起動せず、同じ監視ループの1巡で停止通知まで検証する。"""
    guard = lifetime.ServerLifetime.__new__(lifetime.ServerLifetime)
    parent = ObservedProcess(pid=2147483001, state=observation if route == "parent" else "running")
    child = ObservedProcess(state=observation)
    callbacks = []

    class OneCycle:
        calls = 0

        def wait(self, _timeout):
            self.calls += 1
            return self.calls > 1

    # startは実OSへsignal handlerを設置するため、分類を所有する監視ループへ直接接続する。
    guard._process = typing.cast(psutil.Process, ObservedProcess(pid=2147483002))  # pylint: disable=protected-access
    guard._parent = typing.cast(psutil.Process, parent)  # pylint: disable=protected-access
    guard._targets = {(child.pid, 42.0): typing.cast(psutil.Process, child)} if route == "tracked" else {}  # pylint: disable=protected-access
    guard._finished = typing.cast(threading.Event, OneCycle())  # pylint: disable=protected-access
    guard._deadline = None  # pylint: disable=protected-access
    guard._timeout = 15.0  # pylint: disable=protected-access
    guard._exitcode = 0  # pylint: disable=protected-access
    guard._cancel_transport = False  # pylint: disable=protected-access
    guard._stdin_poll = None  # pylint: disable=protected-access
    guard._pipe_closed = None  # pylint: disable=protected-access
    guard._notified = False  # pylint: disable=protected-access
    guard.set_stop_callback(lambda: callbacks.append("stop"))

    def unexpected_exit(code, **_kwargs):
        pytest.fail(f"模擬監視でプロセス終了へ進んだ: {code}")

    monkeypatch.setattr(lifetime.os, "_exit", unexpected_exit)
    monkeypatch.setattr(lifetime.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        lifetime.process_tree, "collect_descendants", lambda _pid, **_kwargs: [child] if route == "discovered" else []
    )
    guard._watch()  # pylint: disable=protected-access
    expected_targets = {(child.pid, 42.0): child} if route != "parent" and observation == "running" else {}
    assert guard._targets == expected_targets  # pylint: disable=protected-access
    stopped = route == "parent" and observation != "running"
    assert guard.stopping is stopped
    assert callbacks == (["stop"] if stopped else [])
