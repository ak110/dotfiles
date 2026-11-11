# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""開始前待機のPOSIX端末入力と後段への入力保持を検証する。"""

import contextlib
import os
import pathlib
import select
import subprocess
import sys
import time
from collections.abc import Callable, Generator
from typing import Any
lazy import pty

import pytest

from agent_toolkit import atk
from agent_toolkit._common import process_tree
from agent_toolkit._testing.process_loop_watch_support import _isolate_process_loop_commands as _isolate_process_loop_commands
from agent_toolkit._testing.process_loop_watch_support import _log_lines
from agent_toolkit.atk_test import _setup_notes

_START_WAIT_SCRIPT = """
import os, pathlib, subprocess, sys, time
mode, notes, grace = sys.argv[1], pathlib.Path(sys.argv[2]), float(sys.argv[3])
if mode in ("ctty", "background", "closed", "ctty-early", "ctty-twice"):
    # start_new_session後に端末を開き、前景のプロセスグループが持つ制御端末にする。
    os.close(os.open(os.ttyname(0), os.O_RDWR))
if mode == "background":
    # 同じ端末の別プロセスグループで待機させ、このプロセスは前景に残る。
    subprocess.run([sys.executable, __file__, "plain", str(notes), str(grace)], process_group=0, check=True)
    print("after:" + repr(sys.stdin.readline()), flush=True)
    raise SystemExit(0)
if mode == "closed":
    stdin, sys.stdin = sys.stdin, open(os.devnull, encoding="utf-8")
    sys.stdin.close()
from agent_toolkit._atk.wi import process_loop_watch as watch
watch._START_GRACE_SEC = grace
watch._submission_active = lambda _targets: True
if mode == "ctty-early":
    # 開始前待機の外で端末へ届いたEnterを、次の開始前待機の解除に使わないことを確かめる。
    print("ready", flush=True)
    deadline = time.monotonic() + 30
    while not pathlib.Path(os.environ["EARLY_RELEASE"]).exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
rounds = 2 if mode == "ctty-twice" else 1
for _ in range(rounds):
    started = time.monotonic()
    result = watch.wait_for_start_permission(notes, {"github.com/example/myrepo"}, watch.StartGate(notes))
    print(f"result={result} elapsed={time.monotonic() - started:.1f}", flush=True)
if mode in ("ctty-early", "ctty-twice"):
    print("waits-done", flush=True)
if mode == "closed":
    sys.stdin = stdin
if mode != "plain":
    print("after:" + repr(sys.stdin.readline()), flush=True)
"""


@contextlib.contextmanager
def _start_wait_process(tmp_path: pathlib.Path, mode: str, grace: float, stdin: Any) -> Generator[subprocess.Popen[bytes]]:
    """開始前待機を別プロセスで動かし、待機の後に同じ標準入力を後段として読む。"""
    script = tmp_path / "start_wait.py"
    script.write_text(_START_WAIT_SCRIPT, encoding="utf-8")
    notes = _setup_notes(tmp_path)
    env = {**os.environ, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1", "PYTHONPATH": str(pathlib.Path(atk.__file__).parents[1])}
    env["EARLY_RELEASE"] = str(tmp_path / "early.release")
    with process_tree.process_scope(
        [sys.executable, str(script), mode, str(notes), str(grace)],
        stdin=stdin,
        stdout=stdin if isinstance(stdin, int) else subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    ) as process:
        yield process


@contextlib.contextmanager
def _pseudo_terminal() -> Generator[tuple[int, int, Callable[[], None]]]:
    """両端を獲得直後から所有し、子へ渡したslaveだけを早期解放できるようにする。"""
    with contextlib.ExitStack() as master_owner, contextlib.ExitStack() as slave_owner:
        master, slave = pty.openpty()
        master_owner.callback(os.close, master)
        slave_owner.callback(os.close, slave)
        yield master, slave, slave_owner.close


def _read_until(master: int, marker: str, buffer: bytearray, *, timeout: float = 60.0) -> None:
    """PTYの出力に`marker`が現れるまで読む。上限までに現れなければ読んだ出力とともに失敗する。"""
    end = time.monotonic() + timeout
    while marker.encode() not in buffer:
        remaining = end - time.monotonic()
        assert remaining > 0, buffer.decode(errors="replace")
        readable, _, _ = select.select([master], [], [], remaining)
        if not readable:
            continue
        try:
            chunk = os.read(master, 4096)
        except OSError:
            chunk = b""
        assert chunk, buffer.decode(errors="replace")
        buffer.extend(chunk)


@pytest.mark.skipif(os.name != "posix", reason="POSIXの擬似端末でだけ前景TTYを作成できる")
def test_enter_on_posix_pty(tmp_path: pathlib.Path) -> None:
    """前景TTYでは案内を表示し、Enterで解除してログへ残し、後の入力を後段へ渡す。"""
    with (
        _pseudo_terminal() as (master, slave, release_slave),
        _start_wait_process(tmp_path, "ctty", 600.0, slave) as process,
    ):
        release_slave()
        buffer = bytearray()
        _read_until(master, "Enterで今回の待機を解除", buffer)
        os.write(master, b"\n")
        _read_until(master, "result=True", buffer)
        os.write(master, b"child-input\n")
        _read_until(master, "after:'child-input\\n'", buffer)
        assert process.wait(timeout=60) == 0
    output = buffer.decode(errors="replace")
    assert "Enterの入力により開始前の待機を解除しました" in output
    assert any("event=start_wait_released trigger=enter" in line for line in _log_lines())


@pytest.mark.skipif(os.name != "posix", reason="POSIXの擬似端末で前景TTYを作成できる")
@pytest.mark.parametrize("mode", ["early", "twice"])
def test_enter_does_not_carry_over_to_next_start_wait(tmp_path: pathlib.Path, mode: str) -> None:
    """待機の外で届いたEnterと、1回の待機中の重ね押しの2回目を、次の開始前待機の解除に使わない。"""
    grace = 2.0
    with (
        _pseudo_terminal() as (master, slave, release_slave),
        _start_wait_process(tmp_path, f"ctty-{mode}", grace, slave) as process,
    ):
        release_slave()
        buffer = bytearray()
        if mode == "early":
            _read_until(master, "ready", buffer)
            os.write(master, b"\n")
            (tmp_path / "early.release").touch()
        else:
            _read_until(master, "Enterで今回の待機を解除", buffer)
            os.write(master, b"\n\n")
            _read_until(master, "result=True", buffer)
        _read_until(master, "待機条件が解除されました", buffer)
        # 全ての待機の結果行を出力し終えた印を読んでから後段の入力を書く。先に書くと端末のエコーが結果行へ混ざる。
        _read_until(master, "waits-done", buffer)
        os.write(master, b"child-input\n")
        _read_until(master, "after:'child-input\\n'", buffer)
        assert process.wait(timeout=60) == 0
    output = buffer.decode(errors="replace")
    assert output.count("Enterの入力により") == (1 if mode == "twice" else 0)
    elapsed = [float(part.split()[0]) for part in output.split("elapsed=")[1:]]
    assert elapsed[-1] >= grace - 0.5


@pytest.mark.skipif(os.name != "posix", reason="POSIXの擬似端末とプロセスグループで入力の境界を作成する")
@pytest.mark.parametrize("mode", ["pipe", "eof", "closed", "no-controlling-terminal", "background"])
def test_non_interactive_input_keeps_automatic_wait(tmp_path: pathlib.Path, mode: str) -> None:
    """非TTY・EOF・閉鎖済み・入力不能・バックグラウンドジョブでは入力を読まず、期限で解除して入力を後段へ残す。"""
    grace = 2.0
    if mode == "pipe":
        with _start_wait_process(tmp_path, "pipe", grace, subprocess.PIPE) as process:
            stdout, _ = process.communicate(b"\n", timeout=60)
            output = stdout.decode(errors="replace")
            assert "after:'\\n'" in output
    else:
        with _pseudo_terminal() as (master, slave, release_slave):
            script_mode = {"eof": "ctty", "closed": "closed", "no-controlling-terminal": "noctty", "background": "background"}
            with _start_wait_process(tmp_path, script_mode[mode], grace, slave) as process:
                release_slave()
                buffer = bytearray()
                _read_until(master, "開始前に待機します", buffer)
                # EOFは端末の行頭でのVEOF（Ctrl-D）として送る。
                os.write(master, b"\x04" if mode == "eof" else b"\n")
                _read_until(master, "result=True", buffer)
                if mode == "eof":
                    os.write(master, b"\n")
                _read_until(master, "after:'\\n'", buffer)
                assert process.wait(timeout=60) == 0
        output = buffer.decode(errors="replace")
    assert "待機条件が解除されました" in output
    assert "Enterの入力により" not in output
    assert ("Enterで今回の待機を解除" in output) is (mode == "eof")
    elapsed = float(output.split("elapsed=", 1)[1].split()[0])
    assert elapsed >= grace - 0.5
