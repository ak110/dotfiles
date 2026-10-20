"""agent-toolkitのuvプロジェクト指定が作業ディレクトリを保持することを検証する。"""

# bootstrapの所有境界へ故障を注入し、解放状態も直接検証する。
# pylint: disable=protected-access

import contextlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import venv
from collections.abc import Callable
from types import ModuleType
from typing import Any

import psutil
import pytest

from agent_toolkit._common import process_tree

_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent


@pytest.fixture(name="launcher")
def _launcher(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """公開bootstrapの実装を、sys.pathの変更も試験内へ分離して読み込む。"""
    monkeypatch.setattr(sys, "path", sys.path.copy())
    spec = importlib.util.spec_from_file_location("tested_launch", _PROJECT_ROOT / "scripts" / "launch.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("stage", ["signal", "reader", "wait", "stop"])
def test_agents_server_launcher_reclaims_resources_on_failure(
    stage: str,
    launcher: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    request: pytest.FixtureRequest,
) -> None:
    """準備・待機・停止の失敗でも子、開始済みreader、stderr、一時記録とsignal設定を回収する。"""
    if stage == "signal" and os.name == "nt":
        pytest.skip("POSIXのsignal handler準備の故障を注入する")
    rescue = contextlib.ExitStack()
    request.addfinalizer(rescue.close)
    processes: list[subprocess.Popen] = []
    readers: list[threading.Thread] = []
    original_start = threading.Thread.start
    original_signal = launcher.signal.signal
    original_stop = launcher._stop_server_process
    previous = {
        name: launcher.signal.getsignal(getattr(launcher.signal, name))
        for name in launcher._FORWARDED_SIGNALS
        if hasattr(launcher.signal, name)
    }

    class ObservedProcess(subprocess.Popen):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            processes.append(self)
            assert self.stderr is not None
            rescue.callback(self.stderr.close)
            rescue.callback(process_tree.stop_process_tree, self)

        def wait(self, timeout: float | None = None) -> int:
            if stage == "wait" and timeout is None:
                raise RuntimeError("wait失敗")
            return super().wait(timeout=timeout)

    def start(reader: threading.Thread) -> None:
        readers.append(reader)
        if stage == "reader":
            raise RuntimeError("reader失敗")
        original_start(reader)

    def set_signal(signum: int, handler: Any) -> Any:
        if stage == "signal" and signum == launcher.signal.SIGTERM and callable(handler):
            raise RuntimeError("signal失敗")
        return original_signal(signum, handler)

    def stop(process: subprocess.Popen) -> None:
        original_stop(process)
        if stage == "stop":
            raise RuntimeError("stop失敗")

    monkeypatch.setattr(launcher.subprocess, "Popen", ObservedProcess)
    monkeypatch.setattr(launcher.threading.Thread, "start", start)
    monkeypatch.setattr(launcher.signal, "signal", set_signal)
    monkeypatch.setattr(launcher, "_stop_server_process", stop)
    monkeypatch.setattr(launcher.tempfile, "tempdir", str(tmp_path))
    command = [sys.executable, "-c", "import time; time.sleep(120)"]
    if stage == "stop":
        command = [sys.executable, "-c", "pass"]
    with pytest.raises(RuntimeError, match=stage):
        launcher._run_agents_server(command, dict(os.environ))
    assert len(processes) == 1 and processes[0].poll() is not None
    assert processes[0].stderr is not None and processes[0].stderr.closed
    assert all(not reader.is_alive() for reader in readers)
    assert not list(tmp_path.glob("agent-toolkit-agents-server-launch-*.json"))
    assert previous == {name: launcher.signal.getsignal(getattr(launcher.signal, name)) for name in previous}


@pytest.mark.skipif(os.name == "nt", reason="独立POSIX groupとSIGTERM無視を実processで検証する")
@pytest.mark.parametrize("orphaned", [False, True])
def test_launcher_stops_stubborn_group_with_bounded_wait(
    orphaned: bool, launcher: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """有限wait後の強制終了と、親終了後にstderrを保持する同じgroupの子孫回収を確かめる。"""
    marker = tmp_path / "ready"
    source = (
        "import os,pathlib,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(120)"
    )
    if orphaned:
        source = f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {source!r}])"
    monkeypatch.setattr(launcher, "_STOP_TIMEOUT", 0.1)
    outcome = contextlib.nullcontext() if orphaned else pytest.raises(subprocess.TimeoutExpired)
    acquired: list[subprocess.Popen] = []
    children: list[psutil.Process] = []
    with contextlib.ExitStack() as rescue:
        with outcome, launcher._server_lifetime([sys.executable, "-c", source], dict(os.environ), [], bytearray()) as process:
            acquired.append(process)
            deadline = time.monotonic() + 5
            while not marker.exists():
                assert time.monotonic() < deadline, "子のsignal設定が完了しませんでした"
                time.sleep(0.01)
            child = psutil.Process(int(marker.read_text(encoding="utf-8")))
            children.append(child)
            rescue.callback(process_tree.reclaim_descendants, [child])
            if orphaned:
                assert process.wait(timeout=5) == 0
        assert len(acquired) == len(children) == 1
        assert acquired[0].poll() is not None
        assert acquired[0].stderr is not None and acquired[0].stderr.closed
        deadline = time.monotonic() + 5
        while True:
            try:
                if children[0].status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            assert time.monotonic() < deadline, "子プロセスの終了状態へ到達しませんでした"
            time.sleep(0.01)


@pytest.mark.usefixtures("share_package_caches")
def test_project_launch_preserves_cwd_and_uses_plugin_environment(tmp_path: pathlib.Path) -> None:
    """`--project`は呼出元のcwdを変えず、plugin rootの仮想環境を選ぶ。"""
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(_PROJECT_ROOT),
            "--locked",
            "--no-default-groups",
            "python",
            "-c",
            "import json, os, sys; print(json.dumps({'cwd': os.getcwd(), 'prefix': sys.prefix}))",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    observed = json.loads(result.stdout)
    assert pathlib.Path(observed["cwd"]) == tmp_path
    assert pathlib.Path(observed["prefix"]).resolve().is_relative_to((_PROJECT_ROOT / ".venv").resolve())


def test_public_launcher_preserves_argument_error_exit_status(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
) -> None:
    """実CLIのargparse終了2をlauncherの終了状態まで伝播する。"""
    env = host_environ()
    env["AGENT_TOOLKIT_RUNTIME_DATA"] = str(tmp_path / "runtime")
    result = subprocess.run(
        [sys.executable, str(_PROJECT_ROOT / "scripts" / "launch.py"), "agent_toolkit/atk.py", "__invalid_launch_test__"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 2
    assert b"__invalid_launch_test__" in result.stderr


@pytest.mark.parametrize("project_environment", [None, "", "別の仮想環境"])
def test_public_run_command_restores_callers_project_environment(
    tmp_path: pathlib.Path,
    host_environ: Callable[[], dict[str, str]],
    project_environment: str | None,
) -> None:
    """実launcherとrun-commandで、同期先の分離と子への値・未設定の配送を同時に確認する。"""
    env = host_environ()
    env["AGENT_TOOLKIT_RUNTIME_DATA"] = str(tmp_path / "runtime")
    other = tmp_path / "別の仮想環境"
    venv.EnvBuilder(with_pip=False).create(other)
    marker = other / "package.dist-info" / "RECORD"
    marker.parent.mkdir()
    marker.write_text("呼出元のメタデータ", encoding="utf-8")
    before = {path.relative_to(other): path.read_bytes() for path in other.rglob("*") if path.is_file()}
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    expected = str(other) if project_environment else project_environment
    if expected is not None:
        env["UV_PROJECT_ENVIRONMENT"] = expected
    result = subprocess.run(
        [
            sys.executable,
            str(_PROJECT_ROOT / "scripts" / "launch.py"),
            "agent_toolkit/atk.py",
            "run-command",
            "--cwd",
            str(tmp_path),
            "--timeout",
            "30",
            "--",
            sys.executable,
            "-c",
            "import json, os, sys; print(json.dumps({'project': os.environ.get('UV_PROJECT_ENVIRONMENT'), "
            "'cwd': os.getcwd(), 'argv': sys.argv[1:]})); print('子の診断', file=sys.stderr); sys.exit(7)",
            "空 白",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )
    assert result.returncode == 7, result.stderr
    record = json.loads(result.stdout)
    assert record["child_exit_code"] == record["wrapper_exit_code"] == 7
    observed = json.loads(pathlib.Path(record["stdout_path"]).read_text(encoding="utf-8"))
    assert observed == {"project": expected, "cwd": str(tmp_path), "argv": ["空 白"]}
    assert pathlib.Path(record["stderr_path"]).read_text(encoding="utf-8").strip() == "子の診断"
    assert {path.relative_to(other): path.read_bytes() for path in other.rglob("*") if path.is_file()} == before
