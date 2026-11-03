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
from unittest import mock

import psutil
import pytest

from agent_toolkit._common import process_tree
from agent_toolkit._testing import late_descendant

_CHILD_SOURCE = "import time; time.sleep(120)"


def test_process_scope_reaps_child_after_stop_failure(monkeypatch):
    """停止処理が例外を返しても、所有する子の終了状態とパイプを回収する。"""
    process = mock.Mock()
    process.poll.return_value = None
    monkeypatch.setattr(subprocess, "Popen", mock.Mock(return_value=process))
    monkeypatch.setattr(process_tree, "stop_process_tree", mock.Mock(side_effect=RuntimeError("停止失敗")))
    with pytest.raises(RuntimeError, match="停止失敗"), process_tree.process_scope([]):
        pass
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=process_tree.RECLAIM_TIMEOUT_SECONDS)
    for stream in (process.stdin, process.stdout, process.stderr):
        stream.close.assert_called_once_with()


def test_stop_process_tree_waits_for_owned_exit_status(monkeypatch):
    """資源の解放が先に観測されても、Popenの終了状態を上限付きで回収する。"""
    process = mock.Mock(pid=123)
    process.poll.return_value = 0
    monkeypatch.setattr(process_tree, "stop_tree_by_pid", mock.Mock(return_value=process_tree.TreeStopResult([], [], [])))
    assert process_tree.stop_process_tree(process) == []
    process.wait.assert_called_once_with(timeout=process_tree.RECLAIM_TIMEOUT_SECONDS)


# 主スレッドだけを`pthread_exit`で終了し、作業スレッドを残す子。SIGTERMを無視するため、
# スレッドグループのリーダーはzombieと表示されたまま、強制終了するまで回収できない。
_LEADER_EXITED_CHILD = """
import ctypes, pathlib, signal, sys, threading, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
threading.Thread(target=time.sleep, args=(120,)).start()
pathlib.Path(sys.argv[1]).touch()
ctypes.CDLL(None).pthread_exit(None)
"""


@pytest.mark.skipif(sys.platform != "linux", reason="pthread_exitで主スレッドだけを終了させる再現はLinux限定")
def test_stop_process_tree_reaps_owned_child_whose_leader_is_zombie(tmp_path: pathlib.Path) -> None:
    """リーダーがzombie表示でも作業スレッドが残る所有する子を、強制終了して回収し、残存を報告しない。"""
    ready = tmp_path / "ready"
    with subprocess.Popen([sys.executable, "-c", _LEADER_EXITED_CHILD, str(ready)]) as process:
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not (
                ready.exists() and psutil.Process(process.pid).status() == psutil.STATUS_ZOMBIE
            ):
                time.sleep(0.01)
            assert psutil.Process(process.pid).status() == psutil.STATUS_ZOMBIE, "リーダーがzombie表示にならなかった"
            assert process.poll() is None

            assert process_tree.stop_process_tree(process) == []
            assert process.poll() is not None
        finally:
            if process.poll() is None:
                process.kill()


def _stubborn_child_source(ready_marker: pathlib.Path) -> str:
    """SIGTERMの無視を設定してから準備完了の印を書き、待機する子プロセスのソースを返す。

    印は設定の完了を示す同期点であり、テストは印を確かめてから終了を要求する。
    設定より先に終了要求が届くと子は終了要求で終わり、強制終了の分岐を実行せずに合格するためである。
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


def test_stop_process_tree_reclaims_descendant_started_after_first_listing(tmp_path: pathlib.Path, monkeypatch):
    """終了処理の最初の列挙の後に起動した、ファイルを保持する子孫も終了させて解放する。"""
    with process_tree.process_scope(late_descendant.cli_command(tmp_path)) as process:
        late_descendant.wait_spawner(tmp_path)
        late_descendant.arm(tmp_path, monkeypatch.setattr)
        assert process_tree.stop_process_tree(process) == []
    late_descendant.assert_released(tmp_path)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (psutil.AccessDenied(0), "を一時停止できない"),
        (psutil.NoSuchProcess(0), None),
    ],
    ids=["suspend-denied", "vanished"],
)
def test_stop_tree_distinguishes_suspend_failure_from_vanished_target(spawn_parent, monkeypatch, failure, expected):
    """一時停止の拒否は未確定として報告し、消滅した対象は正常として扱い、いずれも所有対象を終了させる。"""
    parent = spawn_parent()
    targets = [psutil.Process(parent.pid), *process_tree.collect_descendants(parent.pid)]
    original = psutil.Process.suspend

    def suspend(self, **kwargs):
        del kwargs
        if self.pid == parent.pid:
            raise failure
        return original(self)

    monkeypatch.setattr(psutil.Process, "suspend", suspend)
    result = process_tree.stop_tree(psutil.Process(parent.pid), timeout=1.0)
    if expected is None:
        assert not result.unresolved
    else:
        assert [reason for reason in result.unresolved if expected in reason and str(parent.pid) in reason]
    assert not result.residual
    assert not _still_holding_resources(targets)


def test_stop_tree_keeps_suspended_targets_when_listing_fails(spawn_parent, monkeypatch):
    """2回目の列挙が失敗しても、それまでに一時停止した対象を所有から外さずに終了させ、未確定として報告する。"""
    parent = spawn_parent()
    targets = [psutil.Process(parent.pid), *process_tree.collect_descendants(parent.pid)]
    original = process_tree._scan_descendants  # pylint: disable=protected-access  # 列挙だけを失敗させる注入点
    calls: list[int] = []

    def scan(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise psutil.AccessDenied(0)
        return original(*args, **kwargs)

    monkeypatch.setattr(process_tree, "_scan_descendants", scan)
    result = process_tree.stop_tree(psutil.Process(parent.pid), timeout=1.0)
    assert [reason for reason in result.unresolved if "子孫を列挙できない" in reason]
    assert {item.pid for item in result.targets} >= {item.pid for item in targets}
    assert not _still_holding_resources(targets)


def test_stop_tree_reports_round_limit_and_still_stops_found_targets(spawn_parent, monkeypatch):
    """一時停止と列挙の反復上限に達した場合は未確定として報告し、見つけた対象は終了させる。"""
    parent = spawn_parent()
    targets = [psutil.Process(parent.pid), *process_tree.collect_descendants(parent.pid)]
    monkeypatch.setattr(process_tree, "_FREEZE_MAX_ROUNDS", 1)
    result = process_tree.stop_tree(psutil.Process(parent.pid), timeout=1.0)
    assert [reason for reason in result.unresolved if "回の一時停止と列挙の後も" in reason]
    assert "生成の停止を確認できない" in result.descriptions()[-1]
    assert not _still_holding_resources(targets)


def test_stop_tree_does_not_leave_suspended_targets_on_exception(spawn_parent, monkeypatch):
    """一時停止の後に例外が起きても、停止中の対象を残さず強制終了してから例外を伝播させる。"""
    parent = spawn_parent()
    targets = [psutil.Process(parent.pid), *process_tree.collect_descendants(parent.pid)]
    monkeypatch.setattr(process_tree, "describe", mock.Mock(side_effect=RuntimeError("記述の失敗")))
    with pytest.raises(RuntimeError, match="記述の失敗"):
        process_tree.stop_tree(psutil.Process(parent.pid))
    deadline = time.monotonic() + 10.0
    while _still_holding_resources(targets) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _still_holding_resources(targets)


def test_stop_tree_keeps_processes_outside_the_tree_and_the_excluded_root(spawn_parent):
    """起点を除く指定では起点を一時停止も終了もせず、木の外のプロセスも維持して、子孫だけを終了させる。"""
    parent = spawn_parent()
    other = spawn_parent()
    descendants = process_tree.collect_descendants(parent.pid)
    result = process_tree.stop_tree(psutil.Process(parent.pid), include_root=False, timeout=1.0)
    assert parent.pid not in {item.pid for item in result.targets}
    assert not _still_holding_resources(descendants)
    assert parent.poll() is None
    assert psutil.Process(parent.pid).status() != psutil.STATUS_STOPPED
    assert other.poll() is None
    assert process_tree.collect_descendants(other.pid)


_GRACEFUL_SOURCE = """
import pathlib, signal, subprocess, sys, time
directory = pathlib.Path(sys.argv[1])
def handle(_signum, _frame):
    (directory / "graceful").touch()
    if len(sys.argv) > 2:
        # 終了猶予中に資源を保持する子を起動し、その準備を待ってから終了する。
        subprocess.Popen([sys.executable, sys.argv[2], "holder", str(directory)])
        while not (directory / "holder.pid").exists():
            time.sleep(0.01)
        time.sleep(1.0)
    sys.exit(0)
signal.signal(signal.SIGTERM, handle)
(directory / "ready").touch()
time.sleep(120)
"""


@pytest.mark.skipif(os.name == "nt", reason="SIGTERMのハンドラーによる正常終了はPOSIX固有の契約")
@pytest.mark.parametrize("spawn_in_grace", [False, True], ids=["graceful", "spawn-in-grace"])
def test_stop_tree_keeps_posix_graceful_exit_and_reclaims_children_started_in_grace(tmp_path: pathlib.Path, spawn_in_grace):
    """POSIXではSIGTERMのハンドラーを実行させ、その終了猶予中に起動した子孫も回収する。"""
    script = late_descendant.prepare(tmp_path)
    source = tmp_path / "graceful.py"
    source.write_text(_GRACEFUL_SOURCE, encoding="utf-8")
    command = [sys.executable, str(source), str(tmp_path)] + ([str(script)] if spawn_in_grace else [])
    with process_tree.process_scope(command) as process:
        _wait_for_ready_marker(tmp_path / "ready")
        result = process_tree.stop_tree(psutil.Process(process.pid), timeout=5.0)
        process.wait(timeout=5)
    assert (tmp_path / "graceful").exists()
    assert not result.unresolved
    if spawn_in_grace:
        late_descendant.assert_released(tmp_path)


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
            if process.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
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

    def fail_to_release(targets, **kwargs):
        del kwargs
        assert target in targets
        return [item for item in targets if item.pid == target.pid]

    monkeypatch.setattr(process_tree, "_wait_until_released", fail_to_release)
    try:
        assert process_tree.stop_process_tree(parent) == [expected]
    finally:
        with contextlib.suppress(psutil.Error, OSError):
            target.kill()
            target.wait(timeout=5)


def test_identity_limits_fields_and_keeps_unavailable(monkeypatch):
    """引数や環境を照会せず、起動時刻・親PIDと採取不能の理由を返す。"""
    process = mock.Mock(pid=123)
    process.create_time.return_value = 12.5
    process.ppid.return_value = 99
    process.status.return_value = "running"
    monkeypatch.setattr(psutil, "Process", mock.Mock(return_value=process))
    assert process_tree.identity(123) == {"pid": 123, "created_at": 12.5, "ppid": 99, "status": "running"}
    process.cmdline.assert_not_called()
    process.environ.assert_not_called()
    process.ppid.side_effect = psutil.AccessDenied(123)
    assert process_tree.identity(123) == {"pid": 123, "created_at": 12.5, "unavailable": "AccessDenied"}
    monkeypatch.setattr(psutil, "Process", mock.Mock(side_effect=psutil.NoSuchProcess(123)))
    assert process_tree.identity(123) == {"pid": 123, "unavailable": "NoSuchProcess"}
