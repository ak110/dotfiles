"""laneが使う内部実行`run_command.execute`の記録と終了状態を検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import signal
import subprocess
import sys
import time

import psutil
import pytest

from agent_toolkit._atk import run_command
from agent_toolkit._common import process_tree
from agent_toolkit._testing import git_repository, late_descendant


def _holds_resources(process: psutil.Process) -> bool:
    """対象が資源を保持しているかを返す。生存確認と状態取得の間に終了した対象は解放済みとして扱う。"""
    try:
        return process.is_running() and process.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
    except psutil.NoSuchProcess:
        return False


@pytest.mark.skipif(os.name != "posix", reason="ホストからのプロセスグループSIGINTを再現する")
def test_execute_host_interrupt_reclaims_detached_descendant(tmp_path: pathlib.Path) -> None:
    """子が先に消える順序でも所有を失わないよう、呼出側と検証子の中断境界を分ける。"""
    parent_pid, child_pid = tmp_path / "parent.pid", tmp_path / "child.pid"
    verifier = tmp_path / "verifier.py"
    verifier.write_text(
        "import os,pathlib,subprocess,sys,time\n"
        f"parent=pathlib.Path({str(parent_pid)!r}); pending=parent.with_suffix('.pending')\n"
        "pending.write_text(str(os.getpid())); pending.replace(parent)\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)\n"
        f"marker=pathlib.Path({str(child_pid)!r}); pending=marker.with_suffix('.pending')\n"
        "pending.write_text(str(child.pid)); pending.replace(marker)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import contextlib,pathlib,signal,sys,time,psutil\n"
        "from agent_toolkit._atk import run_command\n"
        "def interrupted(number,frame):\n"
        " deadline=time.monotonic()+0.5\n"
        " with contextlib.suppress(psutil.NoSuchProcess):\n"
        f"  parent=psutil.Process(int(pathlib.Path({str(parent_pid)!r}).read_text()))\n"
        "  while parent.is_running() and parent.status()!=psutil.STATUS_ZOMBIE and time.monotonic()<deadline: "
        "time.sleep(0.01)\n"
        " raise KeyboardInterrupt\n"
        "signal.signal(signal.SIGINT,interrupted)\n"
        f"run_command.execute([sys.executable,{str(verifier)!r}],pathlib.Path({str(tmp_path)!r}),30)\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PYFLTR_CACHE_DIR": str(tmp_path / "pyfltr"),
        "PYTHONPATH": str(pathlib.Path(run_command.__file__).resolve().parents[2]),
    }
    known: list[psutil.Process] = []
    with (
        (tmp_path / "driver.stdout").open("wb") as out,
        (tmp_path / "driver.stderr").open("wb") as err,
        process_tree.process_scope(
            [sys.executable, str(driver)], env=environment, stdout=out, stderr=err, start_new_session=True
        ) as owned,
    ):
        try:
            deadline = time.monotonic() + 15
            while not child_pid.exists() and owned.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert child_pid.exists()
            child = psutil.Process(int(child_pid.read_text(encoding="utf-8")))
            known = process_tree.collect_descendants(owned.pid)
            os.killpg(owned.pid, signal.SIGINT)
            assert owned.wait(timeout=15) != 0
            assert not _holds_resources(child)
            assert not any(_holds_resources(process) for process in known)
        finally:
            process_tree.stop_tree(None, known=known)


@pytest.fixture(autouse=True)
def _agent_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """既存の結果検証はエージェント向けの出力を使う。"""
    monkeypatch.setattr(run_command, "is_human_terminal", lambda: False)


@pytest.mark.parametrize("changed", [None, "worktree", "head"])
def test_reuse_requires_unchanged_execution_state(monkeypatch, tmp_path, changed):
    """実行後に元の版へ戻しても、実行中に版を変更した記録は再利用しない。"""
    repo = git_repository.init_repository(tmp_path / "repo", files={"value.txt": "original"}, commit_message="初期状態")
    original = git_repository.git_output(repo, "rev-parse", "HEAD")
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(run_command.managed_temp, "create_output_temp", lambda _prefix: output)
    if changed == "worktree":
        argv = [sys.executable, "-c", "import pathlib; pathlib.Path('value.txt').write_text('changed', encoding='utf-8')"]
    elif changed == "head":
        argv = ["git", "commit", "--quiet", "--allow-empty", "-m", "実行中の版変更"]
    else:
        argv = [sys.executable, "-c", "pass"]
    observed, code, failure = run_command.execute(argv, repo, 30)
    assert code == 0 and failure is None
    git_repository.run_git(repo, "reset", "--hard", original)
    reused = run_command.reusable_success(pathlib.Path(observed["record_path"]), argv, repo)
    assert (reused is not None) == (changed is None)


@pytest.mark.parametrize("agent", [False, True], ids=["human", "agent"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_execute_reports_running_state_to_human_terminal(monkeypatch, tmp_path, capsys, agent, exit_code):
    """子を終了させる前に実行状態が届き、両出力と終了値は保存される。"""
    release = tmp_path / "release"
    observed = []

    class GatedPopen(subprocess.Popen):
        def wait(self, timeout=None):
            if not release.exists():
                assert self.poll() is None
                observed.append(capsys.readouterr())
                release.touch()
            return super().wait(timeout=timeout)

    script = (
        "import pathlib,sys,time; release=pathlib.Path(sys.argv[1]); "
        "exec('while not release.exists(): time.sleep(0.01)'); "
        "print('child-out'); print('child-err', file=sys.stderr); sys.exit(int(sys.argv[2]))"
    )
    monkeypatch.setattr(run_command, "is_human_terminal", lambda: not agent)
    monkeypatch.setattr(run_command.managed_temp, "create_output_temp", lambda _prefix: tmp_path)
    monkeypatch.setattr(run_command.subprocess, "Popen", GatedPopen)
    monkeypatch.setattr(run_command, "_git_state", lambda _cwd: (None, None))
    metadata, code, failure = run_command.execute([sys.executable, "-c", script, str(release), str(exit_code)], tmp_path, 30)
    assert code == exit_code and failure is None
    assert observed and not observed[0].out
    if agent:
        assert not observed[0].err
    else:
        assert "実行中" in observed[0].err and str(release) in observed[0].err
    assert pathlib.Path(metadata["stdout_path"]).read_bytes() == b"child-out\n"
    assert pathlib.Path(metadata["stderr_path"]).read_bytes() == b"child-err\n"
    assert json.loads(pathlib.Path(metadata["record_path"]).read_text(encoding="utf-8")) == metadata


def test_execute_timeout_stops_grandchildren_before_recording(monkeypatch, tmp_path):
    """停止確認後に記録を確定し、遅れて孫が出力を書かない。"""
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    started: list[psutil.Process] = []

    class ReadyPopen(subprocess.Popen):
        def wait(self, timeout=None):
            if not started and timeout is not None:
                deadline = time.monotonic() + 30
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert ready.exists(), "孫の準備が時間内に完了しなかった"
                started.extend([psutil.Process(self.pid), *process_tree.collect_descendants(self.pid)])
            return super().wait(timeout=timeout)

    child = (
        f"import pathlib,time; release=pathlib.Path({str(release)!r}); pathlib.Path({str(ready)!r}).touch(); "
        "deadline=time.monotonic()+30; "
        "exec('while not release.exists():\\n assert time.monotonic()<deadline\\n time.sleep(0.01)'); "
        "print('late', flush=True)"
    )
    parent = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}]).wait(timeout=30)"
    argv = (
        [sys.executable, "-c", parent]
        if os.name == "nt"
        else ["sh", "-c", shlex.join([sys.executable, "-c", child]) + " & wait"]
    )
    monkeypatch.setattr(run_command.managed_temp, "create_output_temp", lambda _prefix: tmp_path)
    monkeypatch.setattr(run_command, "_git_state", lambda _cwd: (None, None))
    monkeypatch.setattr(subprocess, "Popen", ReadyPopen)
    try:
        metadata, code, failure = run_command.execute(argv, tmp_path, 0.05)
        assert code == 124 and failure is None
        assert started
        assert not [p for p in started if _holds_resources(p)]
        output = pathlib.Path(metadata["stdout_path"])
        before = output.read_bytes()
        release.touch()
        assert output.read_bytes() == before
        assert metadata["stdout_bytes"] == len(before)
    finally:
        process_tree.reclaim_descendants(started, timeout=0.1)


def test_execute_reports_residual_descendants_in_failure(monkeypatch, tmp_path):
    """回収できない子孫の診断を公開記録のfailureへ残す。"""
    release = tmp_path / "residual.release"
    argv = [sys.executable, "-c", late_descendant.held_source(release)]
    wait = subprocess.Popen.wait

    def ready_wait(self, timeout=None):
        if self.args == argv and timeout is not None:
            deadline = time.monotonic() + 30
            while not release.with_suffix(".ready").exists():
                assert self.poll() is None and time.monotonic() < deadline
                time.sleep(0.01)
        return wait(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", ready_wait)
    real_stop = process_tree.stop_process_tree

    def stop_with_residual(process):
        real_stop(process)
        return ["12345: residual-command"]

    monkeypatch.setattr(process_tree, "stop_process_tree", stop_with_residual)
    monkeypatch.setattr(run_command.managed_temp, "create_output_temp", lambda _prefix: tmp_path)
    metadata, code, failure = run_command.execute(argv, tmp_path, 0.05)
    assert code == 124 and failure is not None and "12345: residual-command" in failure
    assert metadata["failure"] == failure


def _saved(metadata: dict, name: str) -> bytes:
    return pathlib.Path(metadata[name]).read_bytes()


def test_execute_strips_inherited_venv(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """子へ渡る環境から仮想環境だけを除き、無関係の値とPATHの順序を保つ。"""
    venv = tmp_path / "inherited"
    path = os.pathsep.join([str(tmp_path / "before"), str(venv / "bin"), "", str(venv / "Scripts"), os.defpath])
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    monkeypatch.setenv("PATH", path)
    monkeypatch.setenv("PRESERVE_VALUE", "保持する")
    metadata, code, failure = run_command.execute(
        [sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"], tmp_path, 60
    )
    assert code == 0 and failure is None
    child = json.loads(_saved(metadata, "stdout_path"))
    assert "VIRTUAL_ENV" not in child
    assert child["PATH"] == os.pathsep.join([str(tmp_path / "before"), "", os.defpath])
    assert child["PRESERVE_VALUE"] == "保持する"
    assert os.environ["VIRTUAL_ENV"] == str(venv)


@pytest.mark.parametrize("verification", [False, True])
def test_verification_owns_shared_slot_and_child_inherits(tmp_path: pathlib.Path, verification: bool) -> None:
    """検証指定だけが共有枠を所有し、子の入れ子APIは同じ所有者で終了する。

    実行枠は呼出元のスレッドで入れ子を数えるため、pytest自身の保持と分けて別プロセスで観測する。
    """
    probe = (
        "import json,os; from pyfltr.command import execution_slot; "
        "owner=execution_slot.read_owner(); "
        "print(json.dumps({'owner': owner.pid if owner else None, 'parent': os.getppid()})); "
        "exec('with execution_slot.hold(label=\"nested\"):\\n assert execution_slot.read_owner() is not None')"
    )
    driver = (
        "import json, pathlib, sys; from agent_toolkit._atk import run_command; "
        "metadata, code, failure = run_command.execute([sys.executable, '-c', sys.argv[1]], pathlib.Path(sys.argv[2]), 60, "
        "verification=sys.argv[3] == 'True'); "
        "print(json.dumps({'code': code, 'failure': failure, 'stdout': pathlib.Path(metadata['stdout_path']).read_text()}))"
    )
    environment = {
        **os.environ,
        "PYFLTR_CACHE_DIR": str(tmp_path / "pyfltr"),
        "PYTHONPATH": str(pathlib.Path(run_command.__file__).resolve().parents[2]),
    }
    completed = subprocess.run(
        [sys.executable, "-c", driver, probe, str(tmp_path), str(verification)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.splitlines()[-1])
    assert result["code"] == 0, result
    observed = json.loads(result["stdout"])
    assert observed["owner"] == (observed["parent"] if verification else None)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_execute_records_exit_modes_and_streams(tmp_path: pathlib.Path, exit_code: int) -> None:
    """終了値と両streamを保存し、記録JSONに同じ値を残す。"""
    argv = [sys.executable, "-c", f"import sys; print('out'); print('err', file=sys.stderr); sys.exit({exit_code})"]
    metadata, code, failure = run_command.execute(argv, tmp_path, 60)
    assert code == exit_code and failure is None
    assert metadata["argv"] == argv and metadata["cwd"] == str(tmp_path)
    assert metadata["child_exit_code"] == metadata["wrapper_exit_code"] == exit_code
    assert _saved(metadata, "stdout_path") == b"out\n"
    assert _saved(metadata, "stderr_path") == b"err\n"
    assert json.loads(pathlib.Path(metadata["record_path"]).read_text(encoding="utf-8")) == metadata


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal終了の契約")
def test_signal_exit_returns_128_plus_signal(tmp_path: pathlib.Path) -> None:
    metadata, code, _failure = run_command.execute(
        [sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"], tmp_path, 60
    )
    assert code == metadata["wrapper_exit_code"] == 128 + signal.SIGTERM
    assert metadata["child_exit_code"] == -signal.SIGTERM
    assert metadata["signal"] == signal.SIGTERM


def test_start_failure_returns_125_with_empty_saved_streams(tmp_path: pathlib.Path) -> None:
    metadata, code, failure = run_command.execute([str(tmp_path / "missing-command")], tmp_path, 60)
    assert code == metadata["wrapper_exit_code"] == 125 and failure
    assert metadata["child_exit_code"] is None
    assert _saved(metadata, "stdout_path") == b""
    assert _saved(metadata, "stderr_path") == b""
