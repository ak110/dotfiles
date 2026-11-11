"""stdio監視の追跡済み・新規子孫・親消滅の状態境界を検証する。"""

import os
import sys
import threading
import typing

import psutil
import pytest

from agent_toolkit._agents_server import lifetime
from agent_toolkit._testing.process_observation import ObservedProcess


@pytest.mark.parametrize("route", ["tracked", "discovered", "parent"])
@pytest.mark.parametrize("observation", ["running", "zombie", "dead", "pid-reused", "access-denied"])
def test_lifetime_monitor_filters_children_and_stops_for_ended_parent(monkeypatch, route, observation):
    """signal・threadを開始せず、stdioの判定を模擬して監視ループの1巡を検証する。"""
    guard = lifetime.ServerLifetime()
    # constructorの状態を使い、監視しない実stdioの複製descriptorだけを先に解放する。
    os.close(guard._stdin_fd)  # pylint: disable=protected-access
    parent = ObservedProcess(pid=2147483001, state=observation if route == "parent" else "running")
    child = ObservedProcess(state=observation)
    callbacks = []

    class OneCycle:
        calls = 0

        def wait(self, _timeout):
            self.calls += 1
            return self.calls > 1

        def is_set(self):
            return self.calls > 1

    # startは実OSへsignal handlerを設置するため、分類を所有する監視ループへ直接接続する。
    guard._process = typing.cast(psutil.Process, ObservedProcess(pid=2147483002))  # pylint: disable=protected-access
    guard._parent = typing.cast(psutil.Process, parent)  # pylint: disable=protected-access
    guard._targets = {(child.pid, 42.0): typing.cast(psutil.Process, child)} if route == "tracked" else {}  # pylint: disable=protected-access
    guard._finished = typing.cast(threading.Event, OneCycle())  # pylint: disable=protected-access
    guard._stdin_poll = None  # pylint: disable=protected-access
    guard._pipe_closed = None  # pylint: disable=protected-access
    guard.set_stop_callback(lambda: callbacks.append("stop"))

    def unexpected_exit(code, **_kwargs):
        pytest.fail(f"模擬監視でプロセス終了へ進んだ: {code}")

    monkeypatch.setattr(lifetime.os, "_exit", unexpected_exit)
    monkeypatch.setattr(lifetime.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        lifetime.process_tree, "collect_descendants", lambda _pid, **_kwargs: [child] if route == "discovered" else []
    )
    with guard._process_pins:  # pylint: disable=protected-access
        guard._watch()  # pylint: disable=protected-access
    # Windowsでは観測済みの親世代を、親の終了後も最終回収まで保持する。
    retained_parent = sys.platform == "win32" and route == "tracked"
    expected_targets = {(child.pid, 42.0): child} if retained_parent or route != "parent" and observation == "running" else {}
    assert guard._targets == expected_targets  # pylint: disable=protected-access
    stopped = route == "parent" and observation != "running"
    assert guard.stopping is stopped
    assert callbacks == (["stop"] if stopped else [])


def test_lifetime_monitor_collects_descendants_once_per_interval(monkeypatch):
    """親の確認は毎巡行い、全プロセスを走査する子孫の列挙は間隔ごとに1回だけ行う。"""
    guard = lifetime.ServerLifetime()
    os.close(guard._stdin_fd)  # pylint: disable=protected-access
    cycles = 25  # 0.1秒刻みで2.5秒分
    clock = {"now": 100.0}
    parent_checks = []
    collects = []

    class Cycles:
        calls = 0

        def wait(self, _timeout):
            self.calls += 1
            clock["now"] = 100.0 + (self.calls - 1) * lifetime.POLL_INTERVAL
            return self.calls > cycles

        def is_set(self):
            return self.calls > cycles

    guard._finished = typing.cast(threading.Event, Cycles())  # pylint: disable=protected-access
    guard._stdin_poll = None  # pylint: disable=protected-access
    guard._pipe_closed = None  # pylint: disable=protected-access
    monkeypatch.setattr(lifetime.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(guard, "_collect", lambda: collects.append(clock["now"]))
    monkeypatch.setattr(guard, "_parent_gone", lambda: parent_checks.append(clock["now"]) is not None)
    guard._watch()  # pylint: disable=protected-access
    guard._process_pins.close()  # pylint: disable=protected-access
    assert len(parent_checks) == cycles
    assert collects == pytest.approx([100.0, 101.0, 102.0])
