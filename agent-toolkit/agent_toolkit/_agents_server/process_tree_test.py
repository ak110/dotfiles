"""`process_tree`の子孫回収を、実際に起動したプロセスで検証する。

`psutil`の戻り値を模擬すると、列挙の起点と終了要求の順序という本質的な契約を検査できない。
このためテストは実プロセスを起動し、回収後の生存状態で判定する。
"""

import contextlib
import os
import subprocess
import sys
import time

import psutil
import pytest

from agent_toolkit._agents_server import process_tree

_CHILD_SOURCE = "import time; time.sleep(120)"
_STUBBORN_CHILD_SOURCE = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"


def _parent_source(child_source: str) -> str:
    """子プロセスを1つ起動してから待機するプロセスのソースを返す。"""
    return f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child_source!r}]); time.sleep(120)"


def _spawn_parent(child_source: str) -> subprocess.Popen:
    """子プロセスの起動を確認できたところで起点プロセスを返す。"""
    process = subprocess.Popen([sys.executable, "-c", _parent_source(child_source)])  # pylint: disable=consider-using-with
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        with contextlib.suppress(psutil.Error, OSError):
            if psutil.Process(process.pid).children(recursive=True):
                return process
        time.sleep(0.05)
    _force_stop(process)
    raise AssertionError("起点プロセスの子プロセス起動を確認できませんでした")


def _force_stop(process: subprocess.Popen) -> None:
    """テストの後始末として、起点プロセスとその時点の子孫を終了させる。"""
    with contextlib.suppress(psutil.Error, OSError):
        for child in psutil.Process(process.pid).children(recursive=True):
            with contextlib.suppress(psutil.Error, OSError):
                child.kill()
    with contextlib.suppress(OSError):
        process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=30)


@pytest.fixture(name="spawn_parent")
def _spawn_parent_fixture():
    started: list[subprocess.Popen] = []

    def factory(child_source: str = _CHILD_SOURCE) -> subprocess.Popen:
        process = _spawn_parent(child_source)
        started.append(process)
        return process

    yield factory
    for process in started:
        _force_stop(process)


def test_descendants_are_reclaimed_after_the_starting_point_ends(spawn_parent):
    """終了要求より前に列挙した子孫を、起点プロセスの終了後に回収できる。"""
    parent = spawn_parent()
    descendants = process_tree.collect_descendants(parent.pid)
    assert descendants, "起点プロセスの子孫を列挙できませんでした"
    parent.kill()
    parent.wait(timeout=30)

    residual = process_tree.reclaim_descendants(descendants, timeout=5.0)

    assert residual == []
    assert not _still_holding_resources(descendants)


def test_descendants_ignoring_termination_are_force_stopped(spawn_parent):
    """終了要求へ応じない子孫は、上限の経過後に強制終了される。"""
    if os.name == "nt":
        pytest.skip("SIGTERMの無視はPOSIX固有の前提")
    parent = spawn_parent(_STUBBORN_CHILD_SOURCE)
    descendants = process_tree.collect_descendants(parent.pid)
    assert descendants

    residual = process_tree.reclaim_descendants(descendants, timeout=0.5)

    assert residual == []
    assert not _still_holding_resources(descendants)


def test_collect_descendants_returns_empty_for_unknown_starting_point():
    """起点が`None`または不在の場合は例外を送出せず空の一覧を返す。"""
    assert not process_tree.collect_descendants(None)
    assert not process_tree.collect_descendants(_unused_pid())


def test_reclaim_descendants_accepts_already_finished_targets(spawn_parent):
    """終了済みの対象だけを渡しても残存なしを返す。"""
    parent = spawn_parent()
    descendants = process_tree.collect_descendants(parent.pid)
    assert descendants
    assert process_tree.reclaim_descendants(descendants, timeout=5.0) == []

    assert process_tree.reclaim_descendants(descendants, timeout=0.5) == []


def test_reclaim_descendants_returns_empty_list_without_targets():
    """対象が無い場合は待機せず空の一覧を返す。"""
    assert process_tree.reclaim_descendants([]) == []


def _unused_pid() -> int:
    """現在は使われていないPIDを返す。"""
    for candidate in range(2**22 - 1, 2**21, -1):
        if not psutil.pid_exists(candidate):
            return candidate
    raise AssertionError("未使用のPIDを見つけられませんでした")


def _still_holding_resources(descendants: list[psutil.Process]) -> list[int]:
    """資源を保持したままの対象のPIDを返す。回収済みの判定に用いる。

    親による回収を待つだけの状態（zombie）はポートもロックも保持しないため除く。
    """
    holding: list[int] = []
    for process in descendants:
        try:
            if process.status() != psutil.STATUS_ZOMBIE:
                holding.append(process.pid)
        except (psutil.Error, OSError):
            continue
    return holding
