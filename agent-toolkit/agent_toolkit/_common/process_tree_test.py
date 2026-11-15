"""子孫の世代・所有・失敗時の判定は、OS境界の状態を制御して検証する。

実プロセスはkernel handle、psutilの世代キャッシュ、遅れて生成された子孫と
実際の終了通知・資源解放を確認する代表例に限定する。
"""

import contextlib
import ctypes
import ctypes.wintypes
import os
import pathlib
import subprocess
import sys
import time
import typing
from collections.abc import Generator
from unittest import mock

import psutil
import pytest

from agent_toolkit._common import process_tree
from agent_toolkit._testing import late_descendant

pytestmark = pytest.mark.windows


def test_retained_root_cohort_can_finish_normally(host_environ, monkeypatch):
    """既知cohortの全終了を検収し、不要な全OS再走査によるtimeoutを防ぐ。"""
    with (
        subprocess.Popen(
            [sys.executable, "-c", "import sys; sys.stdin.readline()"], stdin=subprocess.PIPE, env=host_environ()
        ) as parent,
        process_tree.ProcessIdentityPins() as pins,
    ):
        known = process_tree.collect_descendants(parent.pid, pins=pins, include_root=True)
        assert parent.pid in {process.pid for process in known}
        assert parent.stdin is not None
        parent.stdin.close()
        assert parent.wait(timeout=10) == 0
        monkeypatch.setattr(process_tree, "_scan_descendants", lambda *_args, **_kwargs: pytest.fail("終了済み所有の再走査"))
        assert process_tree.stop_owned_process(parent, context="既知の起点", known=known, pins=pins) is None
        assert not process_tree.wait_until_released(known, timeout=1)


@pytest.mark.parametrize("ownership", ["unknown", "known", "finished"])
@pytest.mark.parametrize("depth", [1, 2])
def test_owned_stop_after_parent_exit_preserves_known_or_unconfirmed_ownership(ownership, depth, host_environ):
    """起点をwaitした後も既知の子を回収し、未観測の所有を成功へ変換しない。"""
    leaf = "import time; time.sleep(30)"
    middle = (
        f"import subprocess,sys; child=subprocess.Popen([sys.executable,'-c',{leaf!r}]); "
        "print(child.pid,flush=True); sys.stdin.readline()"
    )
    source = (
        middle
        if depth == 1
        else (
            f"import subprocess,sys; middle=subprocess.Popen([sys.executable,'-c',{middle!r}], "
            "stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True); "
            "print(middle.stdout.readline(),end='',flush=True); sys.stdin.readline(); "
            "middle.stdin.close(); middle.wait(timeout=10)"
        )
    )
    with contextlib.ExitStack() as cleanup:
        parent = cleanup.enter_context(
            subprocess.Popen(
                [sys.executable, "-c", source],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=host_environ(),
            )
        )
        assert parent.stdout is not None and parent.stdin is not None
        child = psutil.Process(int(parent.stdout.readline()))
        cleanup.callback(process_tree.stop_tree, None, known=[child], timeout=1)
        with process_tree.ProcessIdentityPins() as pins:
            known = process_tree.collect_descendants(parent.pid, pins=pins)
            assert child.pid in {target.pid for target in known}
            parent.stdin.write("exit\n")
            parent.stdin.flush()
            assert parent.wait(timeout=10) == 0
            if ownership == "finished":
                assert not process_tree.stop_tree(None, known=known, pins=pins, timeout=1).residual
            result = process_tree.stop_owned_process(
                parent, context="起点終了試験", known=() if ownership == "unknown" else known, pins=pins
            )
            if ownership == "unknown":
                assert result is not None and result.unresolved
                assert result.retry(timeout=0.1)
                assert child.is_running()
            else:
                assert result is None
                assert not process_tree.wait_until_released(known, timeout=1)


@pytest.mark.parametrize("has_descendant", [False, True], ids=["exited-leaf", "unretained-parent"])
def test_stop_tree_keeps_unretained_generation_unresolved(monkeypatch, has_descendant):
    """世代を保持できない親は、snapshotの子の有無にかかわらず未確定として残す。"""
    root, leaf, descendant = (
        mock.Mock(pid=900001),
        mock.Mock(pid=900002, info={"ppid": 900001}),
        mock.Mock(pid=900003, info={"ppid": 900002}),
    )
    for index, process in enumerate((root, leaf, descendant), start=1):
        process.create_time.return_value = float(index)
        process.is_running.return_value = False
        process.cmdline.return_value = ["owned"]
    pins = mock.Mock()
    pins.retain.side_effect = lambda process: process is root
    entries = [leaf, descendant] if has_descendant else [leaf]
    _mock_process_snapshot(monkeypatch, entries)
    monkeypatch.setattr(sys, "platform", "win32")
    result = process_tree.stop_tree(root, pins=pins, timeout=0)
    assert {process.pid for process in result.targets} == {root.pid, leaf.pid}
    assert not result.residual
    assert result.unresolved
    descendant.suspend.assert_not_called()
    descendant.kill.assert_not_called()


def test_stop_tree_retains_leaf_before_it_spawns_and_exits(monkeypatch):
    """初回列挙の直後に子を生成して終了するleafも、保持した世代から子を回収する。"""
    root = mock.Mock(pid=900001)
    spawner = mock.Mock(pid=900002, info={"ppid": root.pid})
    holder = mock.Mock(pid=900003, info={"ppid": spawner.pid})
    for number, process in enumerate((root, spawner, holder), 1):
        process.create_time.return_value = float(number)
        process.is_running.return_value = True
        process.status.return_value = psutil.STATUS_RUNNING
        process.cmdline.return_value = ["owned"]

        def kill(target=process, **_kwargs):
            target.is_running.return_value = False

        process.kill.side_effect = kill
    held: set[int] = set()
    before_spawn: list[int] = []
    entries = [spawner]
    pins = mock.Mock()

    def retain(process, **_kwargs):
        if process.is_running():
            held.add(process.pid)
        return process.pid in held

    pins.retain.side_effect = retain
    original = process_tree._scan_descendants  # pylint: disable=protected-access

    def scan(parents, **kwargs):
        found = original(parents, **kwargs)
        if not before_spawn:
            before_spawn.extend(sorted(held))
            entries.append(holder)
            spawner.is_running.return_value = False
        return found

    monkeypatch.setattr(process_tree, "_scan_descendants", scan)
    _mock_process_snapshot(monkeypatch, entries)
    monkeypatch.setattr(sys, "platform", "win32")
    result = process_tree.stop_tree(root, pins=pins, timeout=0)
    assert before_spawn == [root.pid, spawner.pid]
    assert {process.pid for process in result.targets} == {root.pid, spawner.pid, holder.pid}
    assert not result.residual and not result.unresolved
    assert not holder.is_running()


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_stop_tree_does_not_adopt_children_of_an_exited_pid_generation(monkeypatch, platform):
    """再利用した親も終了して一覧に無い場合、その子を古い所有対象の子と誤認しない。"""
    kernel = mock.Mock()
    kernel.OpenProcess.return_value = 0
    monkeypatch.setattr(ctypes, "WinDLL", mock.Mock(return_value=kernel), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87, raising=False)
    monkeypatch.setattr(sys, "platform", platform)
    parent = mock.Mock(pid=900001)
    parent.create_time.return_value = 1.0
    parent.is_running.return_value = False
    parent.status.return_value = psutil.STATUS_DEAD
    parent.cmdline.return_value = ["owned"]
    foreign = mock.Mock(pid=900002, info={"ppid": parent.pid})
    foreign.create_time.return_value = 3.0
    foreign.status.return_value = psutil.STATUS_RUNNING
    foreign.is_running.return_value = True
    foreign.cmdline.return_value = ["unrelated"]
    _mock_process_snapshot(monkeypatch, [foreign])
    result = process_tree.stop_tree(parent, timeout=0)
    assert foreign not in result.targets
    foreign.suspend.assert_not_called()
    foreign.terminate.assert_not_called()
    foreign.kill.assert_not_called()


@pytest.mark.parametrize(
    ("same_generation", "handle", "running"), [(False, 0, False), (False, 123, False), (True, 123, True), (True, 123, False)]
)
def test_windows_parent_handle_is_checked_and_closed(monkeypatch, caplog, same_generation, handle, running):
    """handle取得後に同じ親か確認し、列挙の例外時にもhandleを閉じる。"""
    kernel = mock.Mock()
    kernel.OpenProcess.return_value = handle

    def times(_handle, creation, *_args, **_kwargs):
        ticks = 116444736000000000 + (10000000 if same_generation else 20000000)
        timestamp = ctypes.cast(creation, ctypes.POINTER(ctypes.wintypes.FILETIME)).contents
        timestamp.dwLowDateTime = ticks & 0xFFFFFFFF
        timestamp.dwHighDateTime = ticks >> 32
        return True

    kernel.GetProcessTimes.side_effect = times
    monkeypatch.setattr(ctypes, "WinDLL", mock.Mock(return_value=kernel), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    process = mock.Mock(pid=42)
    process.create_time.return_value = 1.0
    process.is_running.return_value = running
    # pylint: disable=protected-access
    with pytest.raises(RuntimeError, match="列挙失敗"), process_tree._retain_windows_process(process) as retained:
        assert retained is same_generation
        kernel.CloseHandle.assert_not_called()
        raise RuntimeError("列挙失敗")
    kernel.OpenProcess.assert_called_once_with(0x1000, False, process.pid)
    if not handle:
        assert "reason=open-process error=87" in caplog.text
    elif not same_generation:
        assert "reason=creation-time" in caplog.text
        assert "handle_created_at=2.0" in caplog.text
    else:
        assert "Windows親世代保持診断" not in caplog.text
    if handle:
        kernel.CloseHandle.assert_called_once_with(handle)
    else:
        kernel.CloseHandle.assert_not_called()


def test_windows_retention_diagnostic_keeps_snapshot_children(monkeypatch, caplog):
    """保持失敗の対象とその子だけを、同じ列挙時点の識別情報で診断する。"""
    parent = mock.Mock(pid=900001)
    parent.create_time.return_value = 1.0
    parent.is_running.return_value = False
    parent.cmdline.return_value = ["owned"]
    child = mock.Mock(pid=900002, info={"ppid": parent.pid})
    child.create_time.return_value = 2.0
    unrelated = mock.Mock(pid=900003, info={"ppid": 0})
    pins = mock.Mock()
    pins.retain.return_value = False
    _mock_process_snapshot(monkeypatch, [child, unrelated])
    monkeypatch.setattr(process_tree, "identity", lambda pid, **_kwargs: {"pid": pid, "status": "observed"})
    monkeypatch.setattr(sys, "platform", "win32")
    result = process_tree.stop_tree(parent, pins=pins, timeout=0)
    assert result.targets == [parent]
    assert not result.residual
    assert result.unresolved == ["pid 900001 の親世代を終了前に保持できなかった"]
    assert "stage=known" in caplog.text
    assert "snapshot_present=False" in caplog.text
    assert "'snapshot_ppid': 900001" in caplog.text
    assert "900002" in caplog.text
    assert "900003" not in caplog.text
    for process in (child, unrelated):
        process.suspend.assert_not_called()
        process.terminate.assert_not_called()
        process.kill.assert_not_called()


def _mock_process_snapshot(monkeypatch, entries):
    # 模擬PIDの世代はcreate_timeで制御し、実OSの/procを読まない。
    monkeypatch.setattr(process_tree.process_identity, "start_id", lambda _process: None)

    def pids(**_kwargs):
        return [process.pid for process in entries]

    def process(pid, **_kwargs):
        result = next((item for item in entries if item.pid == pid), None)
        if result is None:
            raise psutil.NoSuchProcess(pid)
        result.ppid.return_value = result.info["ppid"]
        return result

    monkeypatch.setattr(psutil, "pids", pids)
    monkeypatch.setattr(psutil, "Process", process)


def _fake_process(pid, ppid):
    """OS操作だけを代用し、列挙・所有・停止結果の製品判定は実行する。"""
    process = mock.Mock(pid=pid, info={"ppid": ppid})
    process.create_time.return_value = float(pid)
    process.is_running.return_value = True
    process.status.return_value = psutil.STATUS_RUNNING
    process.cmdline.return_value = ["owned"]

    def stop(**_kwargs):
        process.is_running.return_value = False
        process.status.return_value = psutil.STATUS_DEAD

    process.terminate.side_effect = process.kill.side_effect = stop
    return process


@pytest.mark.skipif(sys.platform != "win32", reason="終了済み親のWindows kernel handleを検証する")
@pytest.mark.cpu_limit(
    4.75,
    reason=("Windowsの実kernel handleを保持した終了済み親から子を回収する代表例。親1と子1が必要で、CPU合計最大実測2.141秒。"),
)
def test_stop_tree_reclaims_children_of_exited_parent_with_retained_handle(tmp_path):
    """終了済みでも同じ親世代のhandleを保持している場合、その子を辿って回収する。"""
    with _reporting_root(tmp_path) as parent:
        descendants = parent.children(recursive=True)
        try:
            # 終了後も親のkernel objectが存在する、CIで観測した初期条件を保つ。
            with process_tree._retain_windows_process(parent) as retained:  # pylint: disable=protected-access
                assert retained
                parent.kill()
                parent.wait(timeout=5)
                result = process_tree.stop_tree(parent, timeout=1)
                assert not result.residual and not result.unresolved
                assert not _still_holding_resources(descendants)
        finally:
            process_tree.stop_tree(None, known=descendants, timeout=1)


@pytest.mark.cpu_limit(
    4.75,
    reason=("実OSの列挙後に生成した子孫の資源解放と、木の外の1プロセスの生存を同時に確認する代表例。Windows CPU合計2.219秒。"),
)
def test_stop_tree_keeps_unrelated_process_while_reclaiming_late_descendants(tmp_path, monkeypatch):
    """列挙後に生じた子孫を回収し、無関係な実プロセスを生存させる。"""
    with late_descendant.held_process(tmp_path) as unrelated:
        with process_tree.process_scope(late_descendant.cli_command(tmp_path)) as owned:
            late_descendant.wait_spawner(tmp_path)
            late_descendant.arm(tmp_path, monkeypatch.setattr)
            assert process_tree.stop_process_tree(owned) == []
        late_descendant.assert_released(tmp_path)
        assert unrelated.poll() is None


@pytest.mark.cpu_limit(
    4.5,
    reason=(
        "psutilの実Processキャッシュへ旧世代を与え、現在の実子を回収するAPI境界。自然なPID再利用を待たず親1と"
        "子1で確認し、Windows CPU合計2.031秒。"
    ),
)
def test_stop_tree_reclaims_child_despite_cached_pid_generation(tmp_path, monkeypatch):
    """PID再利用前の列挙キャッシュがあっても、現在の子を回収する。"""
    with _reporting_root(tmp_path) as root:
        descendants = root.children(recursive=True)
        # psutil 7.2のprocess_iterはPIDごとにProcessを再利用し、Windowsではppidもキャッシュする。
        # PIDの自然な再利用を待たず、旧世代のキャッシュだけを持ち込む。
        for child in descendants:
            stale = psutil.Process(child.pid)
            monkeypatch.setattr(stale, "_create_time", 0.0)
            monkeypatch.setattr(stale, "_ident", (child.pid, 0.0))
            monkeypatch.setattr(stale, "_ppid", 99999999)
            monkeypatch.setitem(vars(psutil)["_pmap"], child.pid, stale)
        try:
            result = process_tree.stop_tree(root, timeout=1.0)
            assert {child.pid for child in descendants} <= {target.pid for target in result.targets}
            assert not result.residual and not result.unresolved
            assert not _still_holding_resources(descendants)
        finally:
            process_tree.stop_tree(None, known=descendants, timeout=1.0)


@pytest.mark.parametrize("parent_exits", [False, True])
@pytest.mark.parametrize("residual", [False, True])
def test_process_scope_exception_reclaims_known_cohort(tmp_path, monkeypatch, parent_exits, residual):
    """本体例外時は起点の終了後も既知子孫を回収し、残存は再試行可能にする。"""
    release = tmp_path / "child.release"
    known: list[psutil.Process] = []
    stop = process_tree.stop_owned_process
    pins = process_tree.ProcessIdentityPins()

    def leave_owned(_process, **kwargs):
        return process_tree.ReclaimError(["owned child"], known, context=kwargs["context"])

    try:
        with (
            pytest.raises(process_tree.ReclaimError if residual else ValueError) as raised,
            process_tree.process_scope(
                [sys.executable, "-c", _parent_source(late_descendant.held_source(release), tmp_path / "parent.release")],
                known=known,
                pins=pins,
            ) as parent,
        ):
            _wait_for_ready_marker(release.with_suffix(".ready"))
            # 起点終了と模擬残存の再試行まで、生成側が観測したWindowsの親世代を保持する。
            known.extend(process_tree.collect_descendants(parent.pid, pins=pins, include_root=True))
            assert any(target.pid != parent.pid for target in known)
            if parent_exits:
                parent.kill()
                parent.wait(timeout=10)
            if residual:
                monkeypatch.setattr(process_tree, "stop_owned_process", leave_owned)
            raise ValueError("本体失敗")
        if residual:
            assert isinstance(raised.value, process_tree.ReclaimError)
            assert not raised.value.retry()
        assert not process_tree.wait_until_released(known, timeout=1)
    finally:
        monkeypatch.setattr(process_tree, "stop_owned_process", stop)
        try:
            assert not process_tree.stop_tree(None, known=known, pins=pins, timeout=1).residual
        finally:
            pins.close()


def test_process_scope_reaps_child_after_stop_failure(monkeypatch):
    """停止処理が例外を返しても、所有する子の終了状態とパイプを回収する。"""
    process = mock.Mock()
    process.poll.return_value = None
    monkeypatch.setattr(subprocess, "Popen", mock.Mock(return_value=process))
    monkeypatch.setattr(process_tree, "collect_descendants", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(process_tree, "stop_owned_process", mock.Mock(side_effect=RuntimeError("停止失敗")))
    with pytest.raises(RuntimeError, match="停止失敗"), process_tree.process_scope([]):
        pass
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=process_tree.RECLAIM_TIMEOUT_SECONDS)
    for stream in (process.stdin, process.stdout, process.stderr):
        stream.close.assert_called_once_with()


def test_stop_process_tree_waits_for_owned_exit_status(monkeypatch):
    """資源の解放が先に観測されても、Popenの終了状態を上限付きで回収する。"""
    process = mock.Mock(pid=123)
    process.poll.side_effect = [None, 0, 0]
    monkeypatch.setattr(process_tree, "stop_tree_by_pid", mock.Mock(return_value=process_tree.TreeStopResult([], [], [])))
    assert process_tree.stop_process_tree(process) == []
    process.wait.assert_called_once_with(timeout=process_tree.RECLAIM_TIMEOUT_SECONDS)


# 主スレッドだけを`pthread_exit`で終了し、作業スレッドを残す子。SIGTERMを無視するため、
# スレッドグループのリーダーはzombieと表示されたまま、強制終了するまで回収できない。
_LEADER_EXITED_CHILD = """
import ctypes, pathlib, signal, sys, threading, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
def hold():
    deadline = time.monotonic() + 120
    while not pathlib.Path(sys.argv[1] + '.release').exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
threading.Thread(target=hold).start()
pathlib.Path(sys.argv[1]).touch()
ctypes.CDLL(None).pthread_exit(None)
"""


@pytest.mark.skipif(sys.platform != "linux", reason="pthread_exitで主スレッドだけを終了させる再現はLinux限定")
@pytest.mark.cpu_limit(
    1.25,
    reason=(
        "Linuxのpthread_exitで主スレッドだけがzombieとなる実子を回収する固有API境界。既存CIの通常上限"
        "0.5秒未満での成功を基準に有限の余裕を持つ。"
    ),
)
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
        f"pathlib.Path({str(ready_marker)!r}).touch(); " + late_descendant.held_source(ready_marker.with_suffix(".release"))
    )


def _parent_source(child_source: str, release: pathlib.Path) -> str:
    """子プロセスを1つ起動してから待機するプロセスのソースを返す。"""
    return f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child_source!r}]); " + late_descendant.held_source(
        release
    )


@contextlib.contextmanager
def _spawn_parent(child_source: str, release: pathlib.Path) -> Generator[subprocess.Popen]:
    """子プロセスの起動を確認できたところで起点プロセスを返す。"""
    with process_tree.process_scope([sys.executable, "-c", _parent_source(child_source, release)]) as process:
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
def _spawn_parent_fixture(tmp_path):
    with contextlib.ExitStack() as owned:
        number = 0

        def factory(child_source: str | None = None) -> subprocess.Popen:
            nonlocal number
            number += 1
            source = (
                child_source if child_source is not None else late_descendant.held_source(tmp_path / f"child-{number}.release")
            )
            return owned.enter_context(_spawn_parent(source, tmp_path / f"parent-{number}.release"))

        yield factory


@pytest.mark.cpu_limit(
    1.25,
    reason=(
        "起点の終了後、実際に列挙した子の世代を使って既知対象を回収する代表例。既存CIの通常CPU上限0.5秒未満での成功を基準"
        "にする。"
    ),
)
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


@pytest.mark.cpu_limit(
    1.25,
    reason=(
        "POSIXの実SIGTERM無視から強制終了へ移る固有境界を親1と子1で確認する。既存CIの通常CPU上限0.5秒未満での成功を基準にする。"
    ),
)
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


@pytest.mark.parametrize(
    ("failure", "survives", "expected"),
    [
        (psutil.AccessDenied(0), True, "を一時停止できない"),
        (psutil.AccessDenied(0), False, None if sys.platform == "win32" else "を一時停止できない"),
        (psutil.NoSuchProcess(0), False, None),
    ],
    ids=["suspend-denied-and-survives", "suspend-denied-then-stopped", "vanished"],
)
def test_stop_tree_distinguishes_suspend_failure_from_vanished_target(monkeypatch, failure, survives, expected):
    """OSの一時停止失敗と回収結果を制御し、終了済みと未確定の判定を分ける。"""
    root, child = _fake_process(900001, 0), _fake_process(900002, 900001)
    _mock_process_snapshot(monkeypatch, [child])
    root.suspend.side_effect = failure
    if survives:
        root.terminate.side_effect = root.kill.side_effect = None
    result = process_tree.stop_tree(root, pins=mock.Mock(retain=mock.Mock(return_value=True)), timeout=0)
    if expected is None:
        assert not result.unresolved and not result.residual
    else:
        assert [reason for reason in result.unresolved if expected in reason and str(root.pid) in reason]
        assert [item.pid for item in result.residual] == ([root.pid] if survives else [])
    assert not child.is_running()


def test_stop_tree_keeps_suspended_targets_when_listing_fails(monkeypatch):
    """2回目の列挙失敗でも、既に所有した対象を終了させて未確定を保持する。"""
    root, child = _fake_process(900001, 0), _fake_process(900002, 900001)
    _mock_process_snapshot(monkeypatch, [child])
    original = process_tree._scan_descendants  # pylint: disable=protected-access
    calls = 0

    def scan(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise psutil.AccessDenied(0)
        return original(*args, **kwargs)

    monkeypatch.setattr(process_tree, "_scan_descendants", scan)
    result = process_tree.stop_tree(root, pins=mock.Mock(retain=mock.Mock(return_value=True)), timeout=0)
    assert [reason for reason in result.unresolved if "子孫を列挙できない" in reason]
    assert {item.pid for item in result.targets} == {root.pid, child.pid}
    assert not root.is_running() and not child.is_running()


def test_stop_tree_reports_round_limit_and_still_stops_found_targets(monkeypatch):
    """列挙の反復上限でも、見つけた所有対象を終了させ未確定を報告する。"""
    root, child = _fake_process(900001, 0), _fake_process(900002, 900001)
    _mock_process_snapshot(monkeypatch, [child])
    monkeypatch.setattr(process_tree, "_FREEZE_MAX_ROUNDS", 1)
    result = process_tree.stop_tree(root, pins=mock.Mock(retain=mock.Mock(return_value=True)), timeout=0)
    assert [reason for reason in result.unresolved if "回の一時停止と列挙の後も" in reason]
    assert "生成の停止を確認できない" in result.descriptions()[-1]
    assert not root.is_running() and not child.is_running()


def test_stop_tree_does_not_leave_suspended_targets_on_exception(monkeypatch):
    """記述で例外が起きても、所有する対象を終了させてから例外を伝える。"""
    root, child = _fake_process(900001, 0), _fake_process(900002, 900001)
    _mock_process_snapshot(monkeypatch, [child])
    monkeypatch.setattr(process_tree, "describe", mock.Mock(side_effect=RuntimeError("記述の失敗")))
    with pytest.raises(RuntimeError, match="記述の失敗"):
        process_tree.stop_tree(root, pins=mock.Mock(retain=mock.Mock(return_value=True)), timeout=0)
    assert not root.is_running() and not child.is_running()


@contextlib.contextmanager
def _reporting_root(tmp_path: pathlib.Path) -> Generator[psutil.Process]:
    """子を1つ起動したPython本体を、本体自身が申告したPIDで返す。

    Windowsの仮想環境の`python.exe`は本体を子として起動する中継役であり、`Popen.pid`は本体の親になる。
    中継役は本体の終了に合わせて終了するため、起点そのものの扱いを確かめる試験は本体を起点にする。
    """
    marker = tmp_path / "root.pid"
    source = (
        "import os, pathlib, subprocess, sys, time; "
        f"child=subprocess.Popen([sys.executable, '-c', {late_descendant.held_source(tmp_path / 'reported.release')!r}]); "
        # 書きかけを読まないよう、一時ファイルへ書いてから置き換える。
        f"pathlib.Path({str(marker)!r} + '.tmp').write_text(str(os.getpid())); "
        f"os.replace({str(marker)!r} + '.tmp', {str(marker)!r}); "
        + late_descendant.held_source(tmp_path / "reported-root.release")
    )
    with process_tree.process_scope([sys.executable, "-c", source]):
        _wait_for_ready_marker(marker)
        root = psutil.Process(int(marker.read_text()))
        deadline = time.monotonic() + 30.0
        while not root.children(recursive=True) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert root.children(recursive=True), "起点の子プロセス起動を確認できませんでした"
        try:
            yield root
        finally:
            with contextlib.suppress(psutil.Error, OSError):
                root.kill()


def test_stop_tree_keeps_processes_outside_the_tree_and_the_excluded_root(monkeypatch):
    """起点を除く指定では、子だけを終了させ、起点と無関係な対象へ作用しない。"""
    root, child, other = _fake_process(900001, 0), _fake_process(900002, 900001), _fake_process(900003, 0)
    _mock_process_snapshot(monkeypatch, [child, other])
    result = process_tree.stop_tree(root, include_root=False, pins=mock.Mock(retain=mock.Mock(return_value=True)), timeout=0)
    assert [item.pid for item in result.targets] == [child.pid]
    assert not child.is_running()
    for outside in (root, other):
        assert outside.is_running()
        outside.suspend.assert_not_called()
        outside.terminate.assert_not_called()
        outside.kill.assert_not_called()


_GRACEFUL_SOURCE = """
import os, pathlib, signal, subprocess, sys, time

directory = pathlib.Path(sys.argv[1])
def handle(_signum, _frame):
    (directory / "graceful").touch()
    if len(sys.argv) > 2:
        # 終了猶予中に資源を保持する子を起動し、起動したPIDを記録して準備を上限付きで待ってから終了する。
        # 停止処理は猶予中に見つけた子へすぐ終了を要求するため、子が準備前に終了することもある。
        child = subprocess.Popen([sys.executable, sys.argv[2], "holder", str(directory)])
        (directory / "spawned.pid.tmp").write_text(str(child.pid))
        os.replace(directory / "spawned.pid.tmp", directory / "spawned.pid")
        deadline = time.monotonic() + 10
        # 準備前に終了した子は準備の印を作らないため、子の生存も待機の条件に含める。
        while child.poll() is None and not (directory / "holder.pid").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        deadline = time.monotonic() + 10
        while child.poll() is None and not (directory / "graceful.release").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
    sys.exit(0)
signal.signal(signal.SIGTERM, handle)
(directory / "ready").touch()
deadline = time.monotonic() + 30
while not (directory / "parent.release").exists():
    assert time.monotonic() < deadline
    time.sleep(0.01)
"""


@pytest.mark.skipif(os.name == "nt", reason="SIGTERMのハンドラーによる正常終了はPOSIX固有の契約")
@pytest.mark.parametrize("spawn_in_grace", [False, True], ids=["graceful", "spawn-in-grace"])
@pytest.mark.cpu_limit(
    1.25,
    reason=(
        "POSIXのSIGTERMハンドラーと猶予中の子生成を各1系列で検証する。既存CIの通常CPU上限0.5秒未満での成功を"
        "基準に有限の余裕を持つ。"
    ),
)
def test_stop_tree_keeps_posix_graceful_exit_and_reclaims_children_started_in_grace(tmp_path: pathlib.Path, spawn_in_grace):
    """POSIXではSIGTERMのハンドラーを実行させ、その終了猶予中に起動した子孫も回収する。"""
    script = late_descendant.prepare(tmp_path)
    source = tmp_path / "graceful.py"
    source.write_text(_GRACEFUL_SOURCE, encoding="utf-8")
    command = [sys.executable, str(source), str(tmp_path)] + ([str(script)] if spawn_in_grace else [])
    with process_tree.process_scope(command) as process:
        _wait_for_ready_marker(tmp_path / "ready")
        # 猶予中に起動した子の準備完了まで猶予を保つ。CIの高負荷時はPythonの起動に数秒かかり、
        # 猶予が先に尽きると子が準備前に強制終了され、猶予中の生成を観測できない。
        result = process_tree.stop_tree(psutil.Process(process.pid), timeout=late_descendant.READY_TIMEOUT_SECONDS)
        process.wait(timeout=5)
    assert (tmp_path / "graceful").exists()
    assert not result.unresolved
    if spawn_in_grace:
        # 猶予中に起動した子は、準備の前後どちらで終了を要求されても停止処理の完了時点で終了している。
        spawned = int((tmp_path / "spawned.pid").read_text())
        assert spawned in {item.pid for item in result.targets}
        assert not _still_holding_resources([item for item in result.targets if item.pid == spawned])
        if (tmp_path / "holder.pid").exists():
            late_descendant.assert_released(tmp_path)


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


@pytest.mark.cpu_limit(
    2.5,
    reason=(
        "実Popenの時間上限とpipe保持を含む孫の解放が、TimeoutExpiredより先に成立する代表1系列。準備完了後"
        "に計時しWindows CPU合計1.125秒。"
    ),
)
def test_run_with_timeout_stops_descendants(monkeypatch, tmp_path):
    """孫の準備後に計時し、時間切れの報告より先に子と孫が解放される。"""
    marker = tmp_path / "child.ready"
    started: list[psutil.Process] = []

    class ReadyPopen(subprocess.Popen):
        def communicate(self, input=None, timeout=None):
            if timeout is not None:
                _wait_for_ready_marker(marker)
                started.extend([psutil.Process(self.pid), *process_tree.collect_descendants(self.pid)])
            return super().communicate(input, timeout=timeout)

    child = late_descendant.held_source(marker.with_suffix(".release"))
    monkeypatch.setattr(subprocess, "Popen", ReadyPopen)
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            process_tree.run_with_timeout(
                [sys.executable, "-c", _parent_source(child, tmp_path / "parent.release")],
                timeout=0.05,
                capture_output=True,
            )
        assert started and not _still_holding_resources(started)
    finally:
        process_tree.reclaim_descendants(started, timeout=0.1)


def test_run_with_timeout_raises_reclaim_error_and_retry_reclaims_same_target(monkeypatch):
    """時間上限と回収不能を区別し、保持した同じ所有対象を再試行へ渡す。"""
    process = mock.Mock(pid=900001, args=["owned"], stdin=None, stdout=None, stderr=None)
    process.poll.return_value = None
    process.communicate.side_effect = subprocess.TimeoutExpired(["owned"], 0.05)
    owned = _fake_process(900002, 900001)

    def leave_survivor(_process, **kwargs):
        process.poll.return_value = 0
        return process_tree.ReclaimError(["900002: survivor"], [owned], context=kwargs["context"])

    monkeypatch.setattr(subprocess, "Popen", mock.Mock(return_value=process))
    monkeypatch.setattr(process_tree, "stop_owned_process", leave_survivor)
    with pytest.raises(process_tree.ReclaimError) as raised:
        process_tree.run_with_timeout(["owned"], timeout=0.05)
    error = raised.value
    assert not isinstance(error, (subprocess.SubprocessError, OSError))
    assert isinstance(error.__cause__, subprocess.TimeoutExpired)
    assert error.processes == [owned]

    def reclaim(root, **kwargs):
        assert root is None and kwargs["known"] == [owned]
        owned.kill()
        return process_tree.TreeStopResult([owned], [], [])

    monkeypatch.setattr(process_tree, "stop_tree", reclaim)
    assert error.retry() == []
    assert not owned.is_running()


def test_process_scope_raises_reclaim_error_when_child_remains(monkeypatch):
    """停止結果の回収不能を、警告だけの正常終了へ変換しない。"""
    process = mock.Mock(pid=900001, stdin=None, stdout=None, stderr=None)
    process.poll.return_value = None

    def leave_residual(_process, **kwargs):
        process.poll.return_value = 0
        return process_tree.ReclaimError(["900002: residual"], [], context=kwargs["context"])

    monkeypatch.setattr(subprocess, "Popen", mock.Mock(return_value=process))
    monkeypatch.setattr(process_tree, "stop_owned_process", leave_residual)
    with pytest.raises(process_tree.ReclaimError), process_tree.process_scope([]):
        pass


@pytest.mark.parametrize(
    ("failing_call", "final_wait_error"),
    [
        ("wait", subprocess.TimeoutExpired("child", 5)),
        ("wait", OSError("wait failed")),
        ("kill", OSError("kill failed")),
    ],
    ids=["wait-timeout", "wait-oserror", "kill-oserror"],
)
def test_unconfirmed_final_wait_keeps_reclaim_error(monkeypatch, failing_call, final_wait_error):
    """停止しない直接の子の最終待機・終了要求の失敗が、回収不能を通常の時間上限やOSErrorへ置き換えない。"""
    process = mock.Mock(pid=1234, args=["child"], stdin=None, stdout=None, stderr=None)
    process.poll.return_value = None
    process.communicate.side_effect = subprocess.TimeoutExpired("child", 1)
    getattr(process, failing_call).side_effect = final_wait_error
    owned = mock.Mock(pid=1234)

    def leave_child(_process: subprocess.Popen, **kwargs) -> process_tree.ReclaimError:
        return process_tree.ReclaimError(["1234: child"], [owned], context=kwargs["context"])

    monkeypatch.setattr(subprocess, "Popen", mock.Mock(return_value=process))
    monkeypatch.setattr(process_tree, "stop_owned_process", leave_child)
    with pytest.raises(process_tree.ReclaimError) as raised:
        process_tree.run_with_timeout(["child"], timeout=1)
    # 時間上限の境界と終了処理の両方の停止結果を同じ例外へ合流させ、再試行に使う所有対象を保持する。
    assert raised.value.processes == [owned]
    assert raised.value.residual == ["1234: child"]


def test_retry_keeps_unconfirmed_generation_stop(monkeypatch):
    """生成の停止を確認できなかった回収不能は、所有対象の再停止が成功しても回収済みへ変えない。"""
    target = mock.Mock(pid=1234)
    error = process_tree.ReclaimError(["1234: child"], [target], context="fixture")
    calls: list[list[object]] = []

    def stop_tree(root, **kwargs) -> process_tree.TreeStopResult:
        assert root is None
        calls.append(list(kwargs["known"]))
        unresolved = ["一時停止できない"] if len(calls) == 1 else []
        return process_tree.TreeStopResult([target], [], unresolved)

    monkeypatch.setattr(process_tree, "stop_tree", stop_tree)
    assert error.retry() == ["生成の停止を確認できない: 一時停止できない"]
    assert error.retry() == ["生成の停止を確認できない: 一時停止できない"]
    # 未確認の間は残存一覧に無い子孫も辿れるよう、所有対象全体を次の再試行へ渡す。
    assert calls == [[target], [target]]


def test_retry_keeps_unreaped_direct_child(monkeypatch):
    """zombie表示で生存判定から外れた直接の子は、回収を待てるまで再試行の残存に残る。"""

    class StuckChild:
        """回収の待機が時間切れになり続ける直接の子。``reaped``を立てると回収できる。"""

        pid = 4321
        args = ["fixture-child"]

        def __init__(self) -> None:
            self.reaped = False

        def wait(self, timeout=None):
            if not self.reaped:
                raise subprocess.TimeoutExpired(self.args, timeout or 0)
            return 0

        def kill(self) -> None:
            pass

        def poll(self):
            return 0 if self.reaped else None

    child = StuckChild()
    monkeypatch.setattr(process_tree, "RECLAIM_TIMEOUT_SECONDS", 0)
    # zombie表示の子は`stop_tree`の残存に含まれない。
    monkeypatch.setattr(process_tree, "stop_tree", lambda *_args, **_kwargs: process_tree.TreeStopResult([], [], []))
    error = process_tree.ReclaimError(
        ["4321: ['fixture-child']"], [], context="fixture", child=typing.cast(subprocess.Popen[typing.Any], child)
    )
    assert error.retry() == ["4321: ['fixture-child']"]
    child.reaped = True
    assert not error.retry()
    assert error.child is None


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
