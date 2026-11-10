"""隔離した実行枠の競合と中断を、コマンド記録の待ち区間へ接続する。"""

import collections.abc
import contextlib
import dataclasses
import json
import pathlib
import subprocess
import sys
import time

import pytest

from agent_toolkit._atk import run_command
from agent_toolkit._common import process_tree


@pytest.mark.parametrize("failed_write", [1, 2])
def test_timing_save_failure_returns_wrapper_failure(monkeypatch, tmp_path, failed_write):
    """計測の保存失敗も有限実行の125と診断で返す。"""
    original = run_command.atomic_write
    writes = 0

    def save(path, content, **kwargs):
        nonlocal writes
        if path.name == "timing.json":
            writes += 1
            if writes == failed_write:
                raise OSError("journal unavailable")
        return original(path, content, **kwargs)

    monkeypatch.setattr(run_command, "atomic_write", save)
    monkeypatch.setattr(run_command.managed_temp, "create_output_temp", lambda _prefix: tmp_path)
    observed, code, failure = run_command.execute([sys.executable, "-c", "pass"], tmp_path, 10)
    assert code == observed["wrapper_exit_code"] == 125
    assert failure and "journal unavailable" in failure
    assert observed["record_path"] is None


def test_journal_keeps_initial_wait_once(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """通知境界を差し替え、開始前の待機の開始・完了を重複計上せず1区間として保存する。"""

    @contextlib.contextmanager
    def hold(*, label: str, on_wait_interval, **kwargs) -> collections.abc.Generator[None]:
        del kwargs
        assert label
        wait = run_command.execution_slot.SlotWait("initial", 0, "start")
        on_wait_interval(wait)
        on_wait_interval(dataclasses.replace(wait, finished_at="end", elapsed=0.1, outcome="acquired"))
        yield

    monkeypatch.setattr(run_command.execution_slot, "hold", hold)
    monkeypatch.setattr(run_command.managed_temp, "create_output_temp", lambda _prefix: tmp_path)
    observed, code, failure = run_command.execute([sys.executable, "-c", "pass"], tmp_path, 10, verification=True)
    assert code == 0 and failure is None
    waits = observed["timing"]["slot_waits"]
    assert [(wait["kind"], wait["sequence"]) for wait in waits] == [("initial", 0)]
    assert all(wait["elapsed"] == 0.1 and wait["finished_at"] == "end" for wait in waits)
    assert json.loads((tmp_path / "timing.json").read_text(encoding="utf-8")) == observed["timing"]


@pytest.mark.parametrize("interrupt", [False, True])
def test_slot_wait_journal_survives_completion_and_interruption(
    tmp_path: pathlib.Path, interrupt: bool, host_environ: collections.abc.Callable[[], dict[str, str]]
) -> None:
    """開始・終了通知を同じ区間へまとめ、起動前の中断を本体0秒へ読み替えない。"""
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import functools,json,pathlib,sys,time\n"
        "from pyfltr.command import execution_slot\n"
        "from agent_toolkit._atk import run_command\n"
        "root=pathlib.Path(sys.argv[2])\n"
        "if sys.argv[1]=='holder':\n"
        "    with execution_slot.hold(label='holder'):\n"
        "        (root/'ready').touch()\n"
        "        deadline=time.monotonic()+20\n"
        "        while not (root/'release').exists():\n"
        "            if time.monotonic()>deadline: raise TimeoutError('release')\n"
        "            time.sleep(0.01)\n"
        "else:\n"
        "    run_command.managed_temp.create_output_temp=lambda prefix: root\n"
        "    def competing(owner):\n"
        "        (root/'competing').touch()\n"
        "        if sys.argv[1]=='interrupt': raise KeyboardInterrupt\n"
        "    execution_slot.hold=functools.partial(execution_slot.hold,on_wait=competing)\n"
        "    try:\n"
        "        result=run_command.execute([sys.executable,'-c','print(42)'],root,10,verification=True)\n"
        "    except KeyboardInterrupt:\n"
        "        raise SystemExit(130)\n"
        "    print(json.dumps(result[0]))\n",
        encoding="utf-8",
    )
    environment = {
        **host_environ(),
        "PYFLTR_CACHE_DIR": str(tmp_path / "cache"),
        "PYTHONPATH": str(pathlib.Path(run_command.__file__).resolve().parents[2]),
    }
    with subprocess.Popen([sys.executable, str(driver), "holder", str(tmp_path)], env=environment) as holder:
        try:
            deadline = time.monotonic() + 15
            while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
                assert holder.poll() is None
                time.sleep(0.01)
            assert (tmp_path / "ready").exists()
            with subprocess.Popen(
                [sys.executable, str(driver), "interrupt" if interrupt else "run", str(tmp_path)],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            ) as contender:
                try:
                    journal = tmp_path / "timing.json"
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        # journalの開始通知は取得試行より先に出る。保持者の解放は実際の競合到達後に行う。
                        if (tmp_path / "competing").exists():
                            break
                        assert contender.poll() is None
                        time.sleep(0.01)
                    assert (tmp_path / "competing").exists() and journal.exists()
                    (tmp_path / "release").touch()
                    stdout, stderr = contender.communicate(timeout=20)
                    assert contender.returncode == (130 if interrupt else 0), stderr
                    timing = json.loads(journal.read_text(encoding="utf-8"))
                    assert len(timing["slot_waits"]) == 1
                    wait = timing["slot_waits"][0]
                    assert wait["kind"] == "initial" and wait["sequence"] == 0
                    assert wait["finished_at"] is not None and wait["elapsed"] > 0
                    assert wait["outcome"] == ("interrupted" if interrupt else "acquired")
                    if interrupt:
                        assert timing["command_started_at"] is None and timing["command_elapsed"] is None
                    else:
                        result = json.loads(stdout)
                        assert result["timing"] == timing
                        assert result["child_exit_code"] == 0
                        assert pathlib.Path(result["stdout_path"]).read_text(encoding="utf-8") == "42\n"
                        assert timing["elapsed"] >= timing["command_elapsed"]
                finally:
                    process_tree.stop_process_tree(contender)
        finally:
            (tmp_path / "release").touch()
            process_tree.stop_process_tree(holder)
