"""モデルを起動せず、公開stdio終了から所有プロセス階層の回収までを観測する。"""

import asyncio
import contextlib
import ctypes
import ctypes.wintypes
import json
import os
import pathlib
import select
import signal
import subprocess
import sys
import threading
import time
import types
from collections.abc import Callable
from unittest import mock

import psutil
import pytest

from agent_toolkit._agents_server import lifetime
from agent_toolkit._common import process_tree
from agent_toolkit._testing import late_descendant


def test_collected_windows_parent_is_pinned_before_it_exits(monkeypatch):
    """収集後に親が終了しても、保持した親世代から遅い子を回収する。"""
    monitor = lifetime.ServerLifetime()
    root = mock.Mock(pid=900001)
    parent = mock.Mock(pid=900002)
    child = mock.Mock(pid=900003, info={"ppid": parent.pid})
    for number, process in enumerate((root, parent, child), 1):
        process.create_time.return_value = float(number)
        process.is_running.return_value = True
        process.status.return_value = psutil.STATUS_RUNNING
        process.cmdline.return_value = ["owned"]
    monitor._process = root  # pylint: disable=protected-access
    monkeypatch.setattr(root, "children", mock.Mock(return_value=[parent]))
    monkeypatch.setattr(psutil, "Process", mock.Mock(return_value=root))
    monkeypatch.setattr(psutil, "process_iter", lambda *_args, **_kwargs: iter([child]))
    monkeypatch.setattr(process_tree, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(lifetime, "sys", types.SimpleNamespace(platform="win32"))
    kernel = mock.Mock()
    kernel.OpenProcess.side_effect = lambda _access, _inherit, pid: 0 if pid == parent.pid and not parent.is_running() else pid

    def times(handle, creation, *_args, **_kwargs):
        ticks = 116444736000000000 + (handle - root.pid + 1) * 10000000
        timestamp = ctypes.cast(creation, ctypes.POINTER(ctypes.wintypes.FILETIME)).contents
        timestamp.dwLowDateTime = ticks & 0xFFFFFFFF
        timestamp.dwHighDateTime = ticks >> 32
        return True

    kernel.GetProcessTimes.side_effect = times
    monkeypatch.setattr(ctypes, "WinDLL", mock.Mock(return_value=kernel), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87, raising=False)

    def kill_child():
        child.is_running.return_value = False

    child.kill.side_effect = kill_child
    try:
        monitor._collect()  # pylint: disable=protected-access
        parent.is_running.return_value = False
        parent.status.return_value = psutil.STATUS_DEAD
        root.children.return_value = []
        monitor._collect()  # pylint: disable=protected-access
        monitor._reclaim()  # pylint: disable=protected-access
        child.kill.assert_called_once_with()
        kernel.CloseHandle.assert_not_called()
    finally:
        pins = getattr(monitor, "_process_pins", None)
        if pins is not None:
            pins.close()
        os.close(monitor._stdin_fd)  # pylint: disable=protected-access
    assert sorted(call.args[0] for call in kernel.CloseHandle.call_args_list) == sorted((root.pid, parent.pid, child.pid))


def _holds_resources(process: psutil.Process) -> bool:
    """対象が資源を保持しているかを返す。生存確認と状態取得の間に終了した対象は解放済みとして扱う。"""
    try:
        return process.is_running() and process.status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
    except psutil.NoSuchProcess:
        return False


_SCRIPT = """
import asyncio, contextlib, importlib.util, json, os, pathlib, signal, subprocess, sys, time

mode, directory, root, failure = sys.argv[1:]
directory = pathlib.Path(directory)
def hold(name):
    deadline = time.monotonic() + 120
    while not (directory / (name + '.release')).exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
def mark(name, value):
    (directory / name).write_text(json.dumps(value), encoding="utf-8")
def launch(role, *, inner=False):
    command = [sys.executable, __file__, role, str(directory), root, failure]
    return subprocess.Popen(command, stdin=subprocess.PIPE if inner else None,
                            stdout=subprocess.DEVNULL if inner else None,
                            start_new_session=os.name != "nt")
if mode == "owner":
    child = launch("launcher" if failure.startswith("launcher-") else "server")
    mark("owner-child.json", child.pid)
    sys.exit(child.wait())
if mode == "leaf":
    if "ignore" in failure:
        for name in ("SIGTERM", "SIGINT", "SIGHUP"):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), signal.SIG_IGN)
    mark("leaf-" + str(os.getpid()) + ".json", os.getpid())
    hold("leaf")
    sys.exit(0)
if mode == "launcher":
    spec = importlib.util.spec_from_file_location("probe_launch", pathlib.Path(root) / "scripts/launch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._SERVER_SHUTDOWN_TIMEOUT = 8
    module._STOP_TIMEOUT = .2
    sys.exit(module._run_agents_server(
        [sys.executable, __file__, "server", str(directory), root, failure],
        dict(os.environ), record_diagnostics=False))
from agent_toolkit._agents_server import lifetime, logging_config, mcp_tools
if failure.endswith("-io"):
    original_record = logging_config.record_instance_event
    def record(event, **kwargs):
        if event == "signal_received":
            hold("signal-io")
        return original_record(event, **kwargs)
    logging_config.record_instance_event = record
lifetime.SHUTDOWN_TIMEOUT = .5
original_activate = mcp_tools._MANAGER.activate_async
class Backend:
    def __init__(self, name):
        self.name = name
    async def close(self):
        mark("closed-" + self.name + ".json", True)
        await asyncio.Event().wait()
async def activate():
    await original_activate()
    children = []
    if mode == "server":
        for name in ("claude", "codex", "agy", "opencode"):
            children.append(launch("leaf", inner=True))
            mcp_tools._MANAGER._backends[name] = Backend(name)
        if "nested" in failure:
            children.append(launch("inner-launcher", inner=True))
    else:
        children.append(launch("leaf", inner=True))
    mark(mode + "-children.json", [child.pid for child in children])
    if failure.endswith("-lock") and mode == "server":
        logging_config._INSTANCE_LOCK.acquire()
    if "blocked" in failure and mode == "server":
        def block_loop():
            mark("blocked-active.json", True)
            hold("event-loop")
        asyncio.get_running_loop().call_soon(block_loop)
    mark(mode + "-ready.json", True)
mcp_tools._MANAGER.activate_async = activate
if mode == "inner-launcher":
    spec = importlib.util.spec_from_file_location("probe_launch", pathlib.Path(root) / "scripts/launch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.exit(module._run_agents_server(
        [sys.executable, __file__, "inner", str(directory), root, failure],
        dict(os.environ), record_diagnostics=False))
sys.exit(mcp_tools.serve())
"""


def _await_file(path: pathlib.Path, process: subprocess.Popen[bytes]) -> object:
    deadline = time.monotonic() + 15
    while not path.exists():
        assert process.poll() is None, f"準備前に終了した: {process.returncode}"
        assert time.monotonic() < deadline, f"準備通知がない: {path}"
        time.sleep(0.02)
    # JSONの完成は置換で公開される本番通知とは別に、試験用ファイルの内容で同期する。
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            assert time.monotonic() < deadline
            time.sleep(0.01)


@pytest.mark.skipif(os.name == "nt", reason="実POSIXのsignal・親終了と起動階層の回収を観測する")
@pytest.mark.parametrize(
    ("failure", "stop"),
    [
        ("direct", "eof"),
        ("launcher-normal", "eof"),
        ("direct", "parent"),
        ("launcher-parent", "parent"),
        ("direct", "signal"),
        ("launcher-ignore", "signal"),
        ("direct-lock", "signal"),
        ("direct-blocked", "signal"),
        ("direct-lock", "sigint"),
        ("direct-lock", "sighup"),
        ("direct-blocked", "parent"),
        ("direct-blocked", "eof"),
        ("direct-io", "signal"),
        ("direct-nested", "eof"),
    ],
)
def test_stdio_shutdown_reclaims_owned_hierarchy(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]], failure: str, stop: str
) -> None:
    """全backendへの停止開始、診断停止、内側MCPと別groupの子孫を有限に回収する。"""
    script = tmp_path / "lifetime_probe.py"
    script.write_text(_SCRIPT, encoding="utf-8")
    root = pathlib.Path(__file__).resolve().parents[2]
    env = host_environ()
    env.update(XDG_STATE_HOME=str(tmp_path / "state"), LOCALAPPDATA=str(tmp_path / "state"))
    for name in ("AGENT_TOOLKIT_OWNER_SESSION", "CLAUDE_CODE_SESSION_ID"):
        env.pop(name, None)
    targets: list[psutil.Process] = []
    with process_tree.process_scope(
        [sys.executable, str(script), "owner", str(tmp_path), str(root), failure],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        try:
            server_children = _await_file(tmp_path / "server-children.json", process)
            assert isinstance(server_children, list) and len(server_children) >= 4
            _await_file(tmp_path / "server-ready.json", process)
            if "nested" in failure:
                _await_file(tmp_path / "inner-children.json", process)
            if "blocked" in failure:
                _await_file(tmp_path / "blocked-active.json", process)
            deadline = time.monotonic() + 15
            while len(list(tmp_path.glob("leaf-*.json"))) < (5 if "nested" in failure else 4):
                assert time.monotonic() < deadline, "fake CLIの準備通知がない"
                time.sleep(0.02)
            targets = process_tree.collect_descendants(process.pid)
            assert len(targets) >= 5
            if "lock" not in failure and "blocked" not in failure:
                assert process.stdin is not None and process.stdout is not None
                process.stdin.write(
                    b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}\n'
                )
                process.stdin.flush()
                assert select.select([process.stdout], [], [], 15)[0], "initialize応答がない"
                assert json.loads(process.stdout.readline())["id"] == 1
            if stop == "eof":
                assert process.stdin is not None
                process.stdin.close()
                expected = 0
            elif stop == "parent":
                process.kill()
                expected = -signal.SIGKILL
            else:
                owner_child = _await_file(tmp_path / "owner-child.json", process)
                assert isinstance(owner_child, int)
                signum = getattr(signal, {"signal": "SIGTERM", "sigint": "SIGINT", "sighup": "SIGHUP"}[stop])
                os.kill(owner_child, signum)
                expected = 128 + signum
            returncode = process.wait(timeout=15)
            assert returncode == expected
            deadline = time.monotonic() + 15
            while any(_holds_resources(target) for target in targets):
                assert time.monotonic() < deadline, "所有階層の資源が解放されない"
                time.sleep(0.02)
            if "lock" not in failure and "blocked" not in failure:
                assert len(list(tmp_path.glob("closed-*.json"))) == 4
        finally:
            process_tree.reclaim_processes(targets, timeout=0.5)
            with contextlib.suppress(psutil.NoSuchProcess):
                assert all(
                    not target.is_running() or target.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
                    for target in targets
                )


@pytest.mark.asyncio
async def test_shutdown_wait_is_bounded_when_task_ignores_cancellation() -> None:
    """taskの取消し受理を完了待機の前提にせず、後続の所有資源回収へ進む。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def unresponsive() -> None:
        started.set()
        while not release.is_set():
            with contextlib.suppress(asyncio.CancelledError):
                await release.wait()

    task = asyncio.create_task(unresponsive())
    try:
        await started.wait()
        [result] = await asyncio.wait_for(lifetime.wait_for_shutdown((task,), timeout=0.01), 1)
        assert isinstance(result, TimeoutError)
        assert not task.done()
    finally:
        release.set()
        await task


def test_monitor_start_failure_restores_signal_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    """監視threadの準備失敗で、半端なsignal handlerを残さない。"""
    previous = {
        signum: signal.getsignal(signum)
        for name in ("SIGINT", "SIGTERM", "SIGHUP")
        if (signum := getattr(signal, name, None)) is not None
    }

    def fail_start(_thread: threading.Thread, **_kwargs: object) -> None:
        raise RuntimeError("monitor start failed")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    guard = lifetime.ServerLifetime()
    with pytest.raises(RuntimeError, match="monitor start failed"):
        guard.start()
    assert {signum: signal.getsignal(signum) for signum in previous} == previous


_LATE_SCRIPT = """
import pathlib, subprocess, sys, time
import psutil
from agent_toolkit._agents_server import lifetime
from agent_toolkit._testing import late_descendant

mode, directory = sys.argv[1], pathlib.Path(sys.argv[2])
if len(sys.argv) > 3:
    # spawnerの一時停止と終了を拒否させ、生成の停止を確認できない対象が残る結果が診断へ残ることを観測する。
    original = {name: getattr(psutil.Process, name) for name in ("suspend", "terminate", "kill")}
    def denying(name):
        def call(self, **kwargs):
            spawner = directory / "spawner.pid"
            if spawner.exists() and self.pid == int(spawner.read_text(encoding="utf-8")):
                if name == "suspend":
                    raise psutil.AccessDenied(self.pid)
                return None
            return original[name](self)
        return call
    for name in original:
        setattr(psutil.Process, name, denying(name))
# finishでは期限による強制終了が回収と重ならない長さにし、期限到達の試験では短くする。
monitor = lifetime.ServerLifetime(timeout=30.0 if mode == "finish" else 0.5)
# 定期収集がspawnerを先に所有すると、終了処理の最初の列挙より前に一時停止され同期点を作れない。
# 定期収集の間隔内に起動した子孫と同じ状態を決定的に作るため、定期収集を止める。
collect = monitor._collect
monitor._collect = lambda: None
monitor.start()
subprocess.Popen(late_descendant.cli_command(directory))
late_descendant.wait_spawner(directory)
if mode == "finish":
    late_descendant.arm(directory, setattr, thread_name="MainThread")
    monitor.finish()
    sys.exit(0)
# 期限到達の回収は、定期収集の後に起動して未収集のまま残る子孫を対象にする。
# 定期収集を1回だけ行ってCLIとspawnerを所有させ、その後にholderを起動させる。
# 修正前の期限回収は列挙せず定期収集済みの対象だけを終了させるため、回収より前に生成を終えておく。
collect()
late_descendant.trigger(directory)
monitor.request_stop()
exec(late_descendant.held_source(directory / "deadline.release"))
sys.exit(3)
"""


@pytest.mark.windows
@pytest.mark.parametrize("mode", ["finish", "deadline"])
def test_lifetime_reclaims_descendant_started_after_first_listing(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]], mode: str
) -> None:
    """正常終了と期限到達のどちらでも、最初の列挙の後に起動した子孫を終了させてファイルを解放する。"""
    script = tmp_path / "late_lifetime.py"
    script.write_text(_LATE_SCRIPT, encoding="utf-8")
    with process_tree.process_scope(
        [sys.executable, str(script), mode, str(tmp_path)], env=host_environ(), stdin=subprocess.PIPE
    ) as process:
        # 期限到達では監視スレッドが回収後に終了コード0で即時終了し、保持後の3へ到達しない。
        assert process.wait(timeout=60) == 0
    late_descendant.assert_released(tmp_path)


@pytest.mark.parametrize("mode", ["finish", "deadline"])
def test_lifetime_reports_unresolved_generation_stop(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]], mode: str
) -> None:
    """生成の停止を確認できない回収結果を、通常終了と期限到達のどちらでも次の操作付きの診断として残す。"""
    script = tmp_path / "late_lifetime.py"
    script.write_text(_LATE_SCRIPT, encoding="utf-8")
    with process_tree.process_scope(
        [sys.executable, str(script), mode, str(tmp_path), "deny-suspend"],
        env=host_environ(),
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        try:
            # communicateの早期EOFで、spawner準備前に期限回収を始めない。
            process.wait(timeout=60)
            assert process.stderr is not None
            stderr = process.stderr.read()
            assert process.returncode == 0
        finally:
            # 終了を拒否させたspawnerを試験の後始末として終了させる。
            with contextlib.suppress(psutil.Error, OSError, ValueError):
                psutil.Process(int((tmp_path / "spawner.pid").read_text(encoding="utf-8"))).kill()
    message = stderr.decode("utf-8", errors="replace")
    assert "生成の停止を確認できない" in message
    assert "次の操作" in message
