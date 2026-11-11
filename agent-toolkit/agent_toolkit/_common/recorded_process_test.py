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
from agent_toolkit._testing import late_descendant


@pytest.fixture(autouse=True)
def _state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)


@contextlib.contextmanager
def _sleeper(tmp_path: pathlib.Path) -> Generator[subprocess.Popen[bytes]]:
    with late_descendant.held_process(tmp_path) as process:
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


def test_root_exit_with_unreclaimed_descendant_is_failure_and_retry_reclaims(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    with _sleeper(tmp_path) as root, _sleeper(tmp_path) as child:
        identity = _identity(root)
        real_stop_tree = process_tree.stop_tree
        _stop_root_only(monkeypatch, root, child, [])

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


def test_unconfirmed_generation_stop_is_kept_until_record_is_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """生成の停止を確認できない記録は、残した対象を回収しても再試行だけでは停止済みにしない。"""
    with _sleeper(tmp_path) as root, _sleeper(tmp_path) as child:
        identity = _identity(root)
        record_path = reclaim_records.path(identity.pid, identity.create_time)
        real_stop_tree = process_tree.stop_tree
        _stop_root_only(monkeypatch, root, child, ["一時停止できない"])

        first = recorded_process.stop_recorded_executor(identity)

        assert first is not None and "生成の停止を確認できない: 一時停止できない" in first
        assert child.poll() is None
        monkeypatch.setattr(process_tree, "stop_tree", real_stop_tree)

        # 生存する所有対象は回収し直すが、起点から辿れない子孫の有無は再試行で確かめられない。
        second = recorded_process.stop_recorded_executor(identity)
        assert second is not None and str(record_path) in second
        assert child.wait(timeout=10) is not None
        assert reclaim_records.pending(identity.pid, identity.create_time)
        third = recorded_process.stop_recorded_executor(identity)
        assert third is not None and str(record_path) in third

        # 運用者が残存を確かめて記録を削除すると、停止済みとして扱う。
        record_path.unlink()
        assert not reclaim_records.pending(identity.pid, identity.create_time)
        assert recorded_process.stop_recorded_executor(identity) is None


def test_root_and_descendant_reclaimed_returns_none_without_record(tmp_path: pathlib.Path) -> None:
    child_source = late_descendant.held_source(tmp_path / "child.release")
    script = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child_source!r}]).wait(timeout=30)"
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


def test_recorded_target_with_reused_pid_is_not_signalled(tmp_path: pathlib.Path) -> None:
    with _sleeper(tmp_path) as unrelated:
        gone = session_registry.ProcessIdentity(2**22 + 12345, 1.0)
        reclaim_records.save(gone.pid, gone.create_time, [], [], [])
        record_path = reclaim_records.path(gone.pid, gone.create_time)
        reused = [
            {
                "pid": unrelated.pid,
                "create_time": psutil.Process(unrelated.pid).create_time() - 1000,
                "start_id": "linux:00000000-0000-0000-0000-000000000001:1",
            }
        ]
        record_path.write_text(json.dumps({"targets": reused, "unresolved": [], "unreaped": reused}), encoding="utf-8")

        assert not reclaim_records.pending(gone.pid, gone.create_time)
        assert recorded_process.stop_recorded_executor(gone) is None
        assert unrelated.poll() is None
        assert not record_path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="親が回収しないzombieはPOSIXの状態")
def test_unreaped_direct_child_blocks_stop_until_reaped(tmp_path: pathlib.Path) -> None:
    """回収を確認できなかった直接の子は、zombie表示でも同じPID世代が消えるまで停止済みにしない。

    run-skillの停止結果の保存から再開前の停止までを通す。別の親が回収しないzombieは、
    親でない側からは強制終了でも消せず、親が回収するまで残る。
    """
    # 親の終了による孤児の回収はPID 1に依存し、--init無しのコンテナー（PID 1がtail）では回収されない。
    # 回収の時機を実行環境から切り離すため、親自身が標準入力の合図で子を待機して回収する。
    script = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'pass']); print(child.pid, flush=True); "
        "sys.stdin.readline(); child.wait(); " + late_descendant.held_source(tmp_path / "parent.release")
    )
    with subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE) as parent:
        try:
            assert parent.stdout is not None
            zombie = psutil.Process(int(parent.stdout.readline()))
            deadline = time.monotonic() + 10
            while zombie.status() != psutil.STATUS_ZOMBIE and time.monotonic() < deadline:
                time.sleep(0.05)
            assert zombie.status() == psutil.STATUS_ZOMBIE
            identity = session_registry.ProcessIdentity(zombie.pid, zombie.create_time())
            # 停止で直接の子の待機が時間切れになった結果を、実行の識別へ保存する。
            held = subprocess.Popen.__new__(subprocess.Popen)
            held.pid = zombie.pid
            failure = process_tree.ReclaimError([f"{zombie.pid}: child"], [], context="run-skill", child=held)
            recorded_process.retain_unreclaimed(identity, failure)

            assert reclaim_records.pending(identity.pid, identity.create_time)
            reason = recorded_process.stop_recorded_executor(identity)
            assert reason is not None and f"{zombie.pid}: 回収を確認できない直接の子" in reason
            assert reclaim_records.pending(identity.pid, identity.create_time)

            # 親がzombieを回収すると、同じ識別の停止は記録を消して成功する。
            assert parent.stdin is not None
            parent.stdin.write(b"reap\n")
            parent.stdin.flush()
            deadline = time.monotonic() + 10
            while psutil.pid_exists(zombie.pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert recorded_process.stop_recorded_executor(identity) is None
            assert not reclaim_records.path(identity.pid, identity.create_time).exists()
        finally:
            if parent.poll() is None:
                parent.kill()
            parent.wait()


def test_unreadable_record_is_reported_as_unconfirmed() -> None:
    gone = session_registry.ProcessIdentity(2**22 + 23456, 1.0)
    record_path = reclaim_records.path(gone.pid, gone.create_time)
    record_path.parent.mkdir(parents=True)
    record_path.write_text("not json", encoding="utf-8")

    assert reclaim_records.pending(gone.pid, gone.create_time)
    failure = recorded_process.stop_recorded_executor(gone)
    assert failure is not None and "回収待ちを確認できない" in failure
