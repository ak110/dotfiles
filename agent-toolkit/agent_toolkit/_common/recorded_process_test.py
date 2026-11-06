"""記録済み実行の停止で、起点の終了と子孫の回収を区別し、起点の消滅後も同じ識別で回収を再試行できることを検証する。"""

from __future__ import annotations

import contextlib
import json
import pathlib
import subprocess
import sys
import time
from collections.abc import Generator

import psutil
import pytest

from agent_toolkit._common import process_tree, reclaim_records, recorded_process, session_registry, state_paths


@pytest.fixture(autouse=True)
def _state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)


@contextlib.contextmanager
def _sleeper() -> Generator[subprocess.Popen[bytes]]:
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def _identity(process: subprocess.Popen[bytes]) -> session_registry.ProcessIdentity:
    identity = session_registry.ProcessIdentity.of(process.pid)
    assert identity is not None
    return identity


def _stop_root_only(
    monkeypatch: pytest.MonkeyPatch, root: subprocess.Popen[bytes], child: subprocess.Popen[bytes], unresolved: list[str]
) -> None:
    """起点だけを止め、子が残る（または生成停止を確認できない）停止結果を返す共通処理へ差し替える。"""
    child_process = psutil.Process(child.pid)

    def stop_tree(*_args: object, **_kwargs: object) -> process_tree.TreeStopResult:
        root.kill()
        root.wait()
        residual = [] if unresolved else [child_process]
        return process_tree.TreeStopResult([child_process], residual, list(unresolved))

    monkeypatch.setattr(process_tree, "stop_tree", stop_tree)


@pytest.mark.parametrize("unresolved", [[], ["一時停止できない"]], ids=["residual", "unresolved"])
def test_root_exit_with_unreclaimed_descendant_is_failure_and_retry_reclaims(
    monkeypatch: pytest.MonkeyPatch, unresolved: list[str]
) -> None:
    with _sleeper() as root, _sleeper() as child:
        identity = _identity(root)
        real_stop_tree = process_tree.stop_tree
        _stop_root_only(monkeypatch, root, child, unresolved)

        failure = recorded_process.stop_recorded_executor(identity)

        assert failure is not None and "回収を確認できない" in failure
        assert not identity.exists()
        assert child.poll() is None
        assert reclaim_records.pending(identity.pid, identity.create_time)
        monkeypatch.setattr(process_tree, "stop_tree", real_stop_tree)

        # 起点は消滅したが、残した所有対象を同じ識別の再呼び出しで回収する。
        assert recorded_process.stop_recorded_executor(identity) is None
        assert child.wait(timeout=10) is not None
        assert not reclaim_records.path(identity.pid, identity.create_time).exists()


def test_root_and_descendant_reclaimed_returns_none_without_record() -> None:
    script = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); time.sleep(120)"
    )
    with subprocess.Popen([sys.executable, "-c", script]) as root:
        identity = _identity(root)
        try:
            deadline = time.monotonic() + 10
            while not (children := psutil.Process(root.pid).children()) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert children, "子プロセスが起動しなかった"
            child = children[0]
            assert recorded_process.stop_recorded_executor(identity) is None
            assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
            assert not reclaim_records.path(identity.pid, identity.create_time).exists()
        finally:
            if root.poll() is None:
                root.kill()
            root.wait()


def test_recorded_target_with_reused_pid_is_not_signalled() -> None:
    with _sleeper() as unrelated:
        gone = session_registry.ProcessIdentity(2**22 + 12345, 1.0)
        reclaim_records.save(gone.pid, gone.create_time, [], [])
        record_path = reclaim_records.path(gone.pid, gone.create_time)
        unrelated_created = psutil.Process(unrelated.pid).create_time()
        record_path.write_text(
            json.dumps({"targets": [{"pid": unrelated.pid, "create_time": unrelated_created - 1000}], "unresolved": []}),
            encoding="utf-8",
        )

        assert not reclaim_records.pending(gone.pid, gone.create_time)
        assert recorded_process.stop_recorded_executor(gone) is None
        assert unrelated.poll() is None
        assert not record_path.exists()


def test_unreadable_record_is_reported_as_unconfirmed() -> None:
    gone = session_registry.ProcessIdentity(2**22 + 23456, 1.0)
    record_path = reclaim_records.path(gone.pid, gone.create_time)
    record_path.parent.mkdir(parents=True)
    record_path.write_text("not json", encoding="utf-8")

    assert reclaim_records.pending(gone.pid, gone.create_time)
    failure = recorded_process.stop_recorded_executor(gone)
    assert failure is not None and "回収待ちを確認できない" in failure
