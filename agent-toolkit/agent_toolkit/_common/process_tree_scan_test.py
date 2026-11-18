"""子孫の走査と子プロセスの起動が、ホストのプロセス数に比例して照会を増やさないことを確かめる。"""

import sys
from unittest import mock

import psutil
import pytest

from agent_toolkit._common import process_tree

pytestmark = pytest.mark.windows


def _fake_process(pid: int) -> mock.Mock:
    process = mock.Mock(pid=pid)
    process.create_time.return_value = float(pid)
    return process


@pytest.mark.parametrize("unrelated", [10, 1000])
def test_scan_queries_do_not_grow_with_host_processes(monkeypatch, unrelated):
    """子孫の走査は全プロセスの親PIDを一括で得て、子の候補にだけProcessを作る。

    Windowsの`Process.ppid()`は呼ぶたびに全プロセスのsnapshotを取るため、全PIDへ呼ぶと走査がプロセス数の2乗になる。
    """
    root, child = _fake_process(900001), _fake_process(900002)
    parents = {root.pid: 1, child.pid: root.pid, **{910000 + index: 1 for index in range(unrelated)}}
    processes = {pid: _fake_process(pid) for pid in parents} | {root.pid: root, child.pid: child}
    snapshots = mock.Mock(side_effect=lambda: dict(parents))
    constructed = mock.Mock(side_effect=processes.__getitem__)
    monkeypatch.setattr(process_tree.process_identity, "start_id", lambda _process: None)
    monkeypatch.setattr(psutil, "_ppid_map", snapshots)
    monkeypatch.setattr(psutil, "Process", constructed)
    found = process_tree._scan_descendants([root], retain=lambda _process: True)  # pylint: disable=protected-access
    assert found == [child]
    # 初回の一覧と、子の候補を作った後の確認の一覧の2回。
    assert snapshots.call_count == 2
    assert sorted(call.args[0] for call in constructed.call_args_list) == [root.pid, child.pid]


def test_short_command_does_not_list_host_processes(monkeypatch):
    """子孫を残さず終わる短いコマンドの起動・終了では、ホストの全プロセスを照会しない。"""

    def list_all(*_args, **_kwargs):
        pytest.fail("ホストの全プロセスを照会した")

    monkeypatch.setattr(psutil, "_ppid_map", list_all)
    monkeypatch.setattr(psutil, "pids", list_all)
    result = process_tree.run_with_timeout([sys.executable, "-c", "print('ok')"], capture_output=True, text=True, timeout=30)
    assert (result.returncode, result.stdout.strip()) == (0, "ok")


def test_parent_pids_falls_back_to_public_api_when_private_function_is_missing(monkeypatch):
    """psutilが非公開の一括取得関数を持たない版でも、公開APIだけで親PIDの対応を作る。"""
    monkeypatch.delattr(psutil, "_ppid_map", raising=False)
    monkeypatch.setattr(psutil, "pids", lambda: [11, 12, 13])

    def process(pid):
        if pid == 13:
            raise psutil.NoSuchProcess(pid)
        return mock.Mock(ppid=mock.Mock(return_value=pid - 10))

    monkeypatch.setattr(psutil, "Process", process)
    assert process_tree._parent_pids() == {11: 1, 12: 2}  # pylint: disable=protected-access
