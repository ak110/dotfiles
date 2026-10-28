"""モデルを起動せず、公開stdio終了から所有プロセス階層の回収までを観測する。"""

import asyncio
import contextlib
import json
import os
import pathlib
import select
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable

import psutil
import pytest

from agent_toolkit._agents_server import lifetime
from agent_toolkit._common import process_tree

_SCRIPT = """
import asyncio, contextlib, importlib.util, json, os, pathlib, signal, subprocess, sys, time

mode, directory, root, failure = sys.argv[1:]
directory = pathlib.Path(directory)
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
    time.sleep(60)
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
            time.sleep(60)
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
            time.sleep(60)
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
            while any(target.is_running() and target.status() != psutil.STATUS_ZOMBIE for target in targets):
                assert time.monotonic() < deadline, "所有階層の資源が解放されない"
                time.sleep(0.02)
            if "lock" not in failure and "blocked" not in failure:
                assert len(list(tmp_path.glob("closed-*.json"))) == 4
        finally:
            process_tree.reclaim_processes(targets, timeout=0.5)
            with contextlib.suppress(psutil.NoSuchProcess):
                assert all(not target.is_running() or target.status() == psutil.STATUS_ZOMBIE for target in targets)


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
