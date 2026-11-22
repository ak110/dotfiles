import subprocess
import sys
import time
from unittest import mock

import psutil
import pytest

from agent_toolkit._common import process_tree

pytestmark = pytest.mark.windows


def _fake_process(pid: int) -> mock.Mock:
    process = mock.Mock(pid=pid)
    process.create_time.return_value = float(pid)
    return process


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("unrelated", [10, 1000])
def test_scan_queries_do_not_grow_with_host_processes(monkeypatch, unrelated, platform):
    """子孫の走査は、親PIDの照会とProcessの作成をホストのプロセス数に比例させない。

    Windowsの`Process.ppid()`は呼ぶたびに全プロセスのsnapshotを取るため、全PIDへ呼ぶと走査がプロセス数の2乗になる。
    Windowsは全プロセスの親PIDを一括で得る。Linuxは辿る親とその子の/procだけを読む。
    """
    root, child = _fake_process(900001), _fake_process(900002)
    parents = {root.pid: 1, child.pid: root.pid, **{910000 + index: 1 for index in range(unrelated)}}
    processes = {pid: _fake_process(pid) for pid in parents} | {root.pid: root, child.pid: child}
    snapshots = mock.Mock(side_effect=lambda: dict(parents))
    constructed = mock.Mock(side_effect=processes.__getitem__)
    proc_reads: list[int] = []

    def proc_children(pid):
        proc_reads.append(pid)
        return [item for item, ppid in parents.items() if ppid == pid]

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(process_tree, "_kernel_lists_children", lambda: True)
    monkeypatch.setattr(process_tree, "_proc_children", proc_children)
    monkeypatch.setattr(process_tree, "_proc_ppid", parents.get)
    monkeypatch.setattr(process_tree.process_identity, "start_id", lambda _process: None)
    monkeypatch.setattr(psutil, "_ppid_map", snapshots)
    monkeypatch.setattr(psutil, "Process", constructed)
    found = process_tree._scan_descendants([root], retain=lambda _process: True)  # pylint: disable=protected-access
    assert found == [child]
    if platform == "win32":
        assert snapshots.call_count == 2
        assert not proc_reads
    else:
        snapshots.assert_not_called()
        assert set(proc_reads) == {root.pid, child.pid}
    assert sorted(call.args[0] for call in constructed.call_args_list) == [root.pid, child.pid]


@pytest.mark.skipif(
    not process_tree._children_readable(),  # pylint: disable=protected-access
    reason="Linuxの/proc/<pid>/task/<tid>/childrenで子孫を辿る経路を検証する",
)
def test_periodic_descendant_collection_does_not_list_host_processes(monkeypatch):
    with process_tree.process_scope(
        [sys.executable, "-c", "import subprocess, sys; subprocess.run([sys.executable, '-c', 'input()'])"],
        stdin=subprocess.PIPE,
    ) as process:
        root = psutil.Process(process.pid)
        deadline = time.monotonic() + 30
        while not root.children(recursive=True) and time.monotonic() < deadline:
            time.sleep(0.05)
        expected = sorted(child.pid for child in root.children(recursive=True))
        assert expected, "孫プロセスの起動を確認できなかった"
        with monkeypatch.context() as patched:
            patched.setattr(psutil, "_ppid_map", lambda: pytest.fail("全プロセスの親PIDを一括で読んだ"))
            patched.setattr(psutil, "pids", lambda: pytest.fail("全PIDを列挙した"))
            patched.setattr(psutil, "process_iter", lambda *_args, **_kwargs: pytest.fail("全プロセスを列挙した"))
            collected = sorted(child.pid for child in process_tree.collect_descendants(process.pid))
            scanned = sorted(
                child.pid
                for child in process_tree._scan_descendants([root], retain=lambda _process: True)  # pylint: disable=protected-access
            )
        assert collected == scanned == expected
        assert process.stdin is not None
        process.stdin.close()


def test_short_command_lists_host_processes_a_bounded_number_of_times(monkeypatch):
    ppid_map = vars(psutil)["_ppid_map"]
    calls = mock.Mock(side_effect=ppid_map)
    monkeypatch.setattr(psutil, "_ppid_map", calls)
    monkeypatch.setattr(psutil, "pids", lambda: pytest.fail("全PIDを列挙した"))
    result = process_tree.run_with_timeout([sys.executable, "-c", "print('ok')"], capture_output=True, text=True, timeout=30)
    assert (result.returncode, result.stdout.strip()) == (0, "ok")
    assert calls.call_count <= 6


def test_parent_pids_falls_back_to_public_api_when_private_function_is_missing(monkeypatch):
    monkeypatch.setattr(process_tree, "_kernel_lists_children", lambda: False)
    monkeypatch.delattr(psutil, "_ppid_map", raising=False)
    monkeypatch.setattr(psutil, "pids", lambda: [11, 12, 13])

    def process(pid):
        if pid == 13:
            raise psutil.NoSuchProcess(pid)
        return mock.Mock(ppid=mock.Mock(return_value=pid - 10))

    monkeypatch.setattr(psutil, "Process", process)
    assert process_tree._parent_pids({11}) == {11: 1, 12: 2}  # pylint: disable=protected-access
