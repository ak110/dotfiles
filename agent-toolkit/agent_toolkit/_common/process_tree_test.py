"""`process_tree`の子孫回収を、実際に起動したプロセスで検証する。

`psutil`の戻り値を模擬すると、列挙の起点と終了要求の順序という本質的な契約を確かめられない。
このためテストは実プロセスを起動し、回収後の生存状態で判定する。
"""

import contextlib
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Generator

import psutil
import pytest

from agent_toolkit._common import process_tree

_CHILD_SOURCE = "import time; time.sleep(120)"


def _stubborn_child_source(ready_marker: pathlib.Path) -> str:
    """SIGTERMの無視を設定してから準備完了の印を書き、待機する子プロセスのソースを返す。

    印は設定の完了を示す同期点であり、テストは印を確かめてから終了を要求する。
    設定より先に終了要求が届くと子は終了要求で終わり、強制終了の分岐を通らないまま合格するためである。
    """
    return (
        "import pathlib, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(ready_marker)!r}).touch(); time.sleep(120)"
    )


def _parent_source(child_source: str) -> str:
    """子プロセスを1つ起動してから待機するプロセスのソースを返す。"""
    return f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child_source!r}]); time.sleep(120)"


@contextlib.contextmanager
def _spawn_parent(child_source: str) -> Generator[subprocess.Popen]:
    """子プロセスの起動を確認できたところで起点プロセスを返す。"""
    with process_tree.process_scope([sys.executable, "-c", _parent_source(child_source)]) as process:
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            ready = False
            with contextlib.suppress(psutil.Error, OSError):
                ready = bool(psutil.Process(process.pid).children(recursive=True))
            if ready:
                yield process
                return
            time.sleep(0.05)
        raise AssertionError("起点プロセスの子プロセス起動を確認できませんでした")


@pytest.fixture(name="spawn_parent")
def _spawn_parent_fixture():
    with contextlib.ExitStack() as owned:

        def factory(child_source: str = _CHILD_SOURCE) -> subprocess.Popen:
            return owned.enter_context(_spawn_parent(child_source))

        yield factory


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


def test_descendants_ignoring_termination_are_force_stopped(spawn_parent, tmp_path: pathlib.Path):
    """終了要求へ応じない子孫は、上限の経過後に強制終了される。"""
    if os.name == "nt":
        pytest.skip("SIGTERMの無視はPOSIX固有の前提")
    ready_marker = tmp_path / "sigterm-ignored"
    parent = spawn_parent(_stubborn_child_source(ready_marker))
    _wait_for_ready_marker(ready_marker)
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


def _wait_for_ready_marker(ready_marker: pathlib.Path) -> None:
    """子孫が準備完了の印を書くまで待つ。上限までに現れない場合は失敗させる。"""
    deadline = time.monotonic() + 30.0
    while not ready_marker.exists():
        if time.monotonic() >= deadline:
            raise AssertionError("子孫プロセスがSIGTERMの無視を設定したことを確認できませんでした")
        time.sleep(0.05)


def _still_holding_resources(descendants: list[psutil.Process]) -> list[int]:
    """資源を保持したままの対象のPIDを返す。回収済みの判定に用いる。

    親による回収を待つだけの状態（zombie）はポートもロックも保持しないため除く。
    """
    holding: list[int] = []
    for process in descendants:
        try:
            if process.status() != psutil.STATUS_ZOMBIE:
                holding.append(process.pid)
        except psutil.Error, OSError:
            continue
    return holding


def test_run_with_timeout_stops_descendants(monkeypatch, tmp_path):
    """孫の準備後に計時し、時間切れの報告より先に子と孫が解放される。"""
    marker = tmp_path / "ready"
    started: list[psutil.Process] = []

    class ReadyPopen(subprocess.Popen):
        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                _wait_for_ready_marker(marker)
                started.extend([psutil.Process(self.pid), *process_tree.collect_descendants(self.pid)])
            return super().communicate(input, timeout=timeout)

    child = f"import pathlib, time; pathlib.Path({str(marker)!r}).touch(); time.sleep(120)"
    monkeypatch.setattr(subprocess, "Popen", ReadyPopen)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            process_tree.run_with_timeout([sys.executable, "-c", _parent_source(child)], timeout=0.05, capture_output=True)
        assert started and not _still_holding_resources(started)
    finally:
        process_tree.reclaim_descendants(started, timeout=0.1)


def test_stop_process_tree_reports_residual(monkeypatch, spawn_parent):
    """回収失敗の応答を、停止前のPIDと起動コマンドで返す。"""
    parent = spawn_parent()
    target = process_tree.collect_descendants(parent.pid)[0]
    expected = process_tree.describe(target)

    def fail_to_reclaim(targets, **kwargs):
        del kwargs
        assert target in targets
        parent.kill()
        return [target]

    monkeypatch.setattr(process_tree, "reclaim_processes", fail_to_reclaim)
    try:
        assert process_tree.stop_process_tree(parent) == [expected]
    finally:
        with contextlib.suppress(psutil.Error, OSError):
            target.kill()
            target.wait(timeout=5)
